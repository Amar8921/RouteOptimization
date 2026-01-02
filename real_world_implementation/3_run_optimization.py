import pandas as pd
import os
import sys
from geopy.geocoders import Nominatim
import time
import json
import math
import folium
import requests
import html

# Add parent directory to path to import optimizer
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from distance import create_distance_matrix
from optimizer import optimize_routes

def get_real_road_geometry(coords):
    if len(coords) < 2: return coords, 0, 0
    full_path, total_dist, total_duration, chunk_size = [], 0, 0, 40
    for i in range(0, len(coords) - 1, chunk_size - 1):
        chunk = coords[i:i + chunk_size]
        loc_string = ";".join([f"{lon},{lat}" for lat, lon in chunk])
        url = f"http://router.project-osrm.org/route/v1/driving/{loc_string}?overview=full&geometries=geojson"
        try:
            r = requests.get(url, timeout=15)
            data = r.json()
            if data['code'] == 'Ok':
                geom = data['routes'][0]['geometry']['coordinates']
                path_chunk = [[p[1], p[0]] for p in geom]
                full_path.extend(path_chunk if not full_path else path_chunk[1:])
                total_dist += data['routes'][0]['distance']
                total_duration += data['routes'][0]['duration']
            else: full_path.extend([[p[0], p[1]] for p in chunk])
        except: full_path.extend([[p[0], p[1]] for p in chunk])
    return full_path, total_dist, total_duration

def run_optimization():
    print("🚀 Starting Multi-School Route Optimization (Numbered Stops)...")
    data_dir, outputs_dir = os.path.join(current_dir, 'data'), os.path.join(current_dir, 'outputs')
    if not os.path.exists(outputs_dir): os.makedirs(outputs_dir)
    
    school_df = pd.read_csv(os.path.join(data_dir, 'raw_school.csv'))
    all_stops_df = pd.read_csv(os.path.join(data_dir, 'geocoded_stops.csv'))
    all_students_df = pd.read_csv(os.path.join(data_dir, 'raw_students.csv'))
    all_staff_df = pd.read_csv(os.path.join(data_dir, 'raw_staff.csv'))
    all_vehicles_df = pd.read_csv(os.path.join(data_dir, 'raw_vehicles.csv'))

    geolocator = Nominatim(user_agent="school_optimizer_v14")

    for _, s_row in school_df.iterrows():
        school_id, school_name = s_row['SchoolID'], s_row['SchoolName']
        print(f"\n🏫 Processing: {school_name}")
        
        school_vehicles = all_vehicles_df[all_vehicles_df['SchoolID'] == school_id].copy()
        if school_vehicles.empty: continue
        school_students = all_students_df[all_students_df['SchoolID'] == school_id].copy()
        school_staff = all_staff_df[all_staff_df['SchoolID'] == school_id].copy()

        try:
            loc = geolocator.geocode(f"{s_row['Address1']}, {s_row['Place']}, Qatar", country_codes="qa")
            s_lat, s_lon = loc.latitude, loc.longitude
        except: s_lat, s_lon = 25.2854, 51.5310 

        # 1. Aggregate Stops
        stops_distinct = all_stops_df.groupby(['StopName', 'final_lat', 'final_lon']).agg({'RouteStopMapIID': list}).reset_index()
        id_to_group = {iid: row['StopName'] for _, row in stops_distinct.iterrows() for iid in row['RouteStopMapIID']}
        
        school_students['GroupKey'] = school_students['PickupStopMapID'].map(id_to_group)
        school_staff['GroupKey'] = school_staff['PickupStopMapID'].map(id_to_group)
        student_data = school_students.groupby('GroupKey').agg({'StudentID': [('count', 'size'), ('ids', lambda x: ", ".join(x.astype(str).unique()))]}); student_data.columns = ['student_count', 'student_ids']
        staff_data = school_staff.groupby('GroupKey').agg({'StaffID': [('count', 'size'), ('ids', lambda x: ", ".join(x.astype(str).unique()))]}); staff_data.columns = ['staff_count', 'staff_ids']
        
        stops_final = pd.merge(stops_distinct, student_data, left_on='StopName', right_index=True, how='left')
        stops_final = pd.merge(stops_final, staff_data, left_on='StopName', right_index=True, how='left')
        stops_final[['student_count', 'staff_count']] = stops_final[['student_count', 'staff_count']].fillna(0).astype(int)
        stops_final['total_demand'] = stops_final['student_count'] + stops_final['staff_count']
        stops_final[['student_ids', 'staff_ids']] = stops_final[['student_ids', 'staff_ids']].fillna("")

        active_stops = stops_final[(stops_final['total_demand'] > 0) & (stops_final['final_lat'] > 24.5)].copy()
        if active_stops.empty: continue

        model_data = [{'stop_id': 0, 'name': 'SCHOOL', 'lat': float(s_lat), 'lon': float(s_lon), 'student_count': 0, 'staff_count': 0, 'demand': 0, 'student_ids': "", 'staff_ids': ""}]
        for idx, row in active_stops.iterrows():
            model_data.append({'stop_id': idx, 'name': row['StopName'], 'lat': float(row['final_lat']), 'lon': float(row['final_lon']), 'student_count': int(row['student_count']), 'staff_count': int(row['staff_count']), 'demand': int(row['total_demand']), 'student_ids': row['student_ids'], 'staff_ids': row['staff_ids']})
        
        df_model = pd.DataFrame(model_data)
        fleet_list = [{'name': str(v.get('VehicleRegistrationNumber', f"V{i+1}")), 'capacity': int(v.get('MaximumSeatingCapacity', 30))} for i, v in school_vehicles.iterrows()]
        
        # 2. STRICT SPLITTING logic
        min_v_cap = min(f['capacity'] for f in fleet_list)
        split_limit = min(25, min_v_cap)

        split_rows = []
        for idx, row in df_model.iterrows():
            if idx == 0: split_rows.append(row); continue
            if row['demand'] > split_limit:
                num_parts = math.ceil(row['demand'] / split_limit)
                for i in range(num_parts):
                    new_part = row.copy()
                    new_part['demand'] = row['demand'] // num_parts + (1 if i < row['demand'] % num_parts else 0)
                    new_part['student_count'] = row['student_count'] // num_parts + (1 if i < row['student_count'] % num_parts else 0)
                    new_part['staff_count'] = row['staff_count'] // num_parts + (1 if i < row['staff_count'] % num_parts else 0)
                    new_part['name'] = f"{row['name']} (Part {i+1})"
                    if i > 0: new_part['student_ids'] = ""; new_part['staff_ids'] = ""
                    split_rows.append(new_part)
            else:
                split_rows.append(row)
        df_model_split = pd.DataFrame(split_rows).reset_index(drop=True)

        # 3. Simulate Fleet Trips
        total_pax = df_model_split['demand'].sum()
        extended_fleet = list(fleet_list)
        mult = 1
        while sum(f['capacity'] for f in extended_fleet) < total_pax and mult < 6:
            mult += 1
            for f in fleet_list: extended_fleet.append({'name': f"{f['name']} (Trip {mult})", 'capacity': f['capacity']})

        coords = list(zip(df_model_split['lat'], df_model_split['lon']))
        dist_matrix = create_distance_matrix(coords)
        print(f"   🤖 Solving VRP: {len(df_model_split)} nodes, {len(extended_fleet)} vehicles...")
        routes = optimize_routes(dist_matrix, list(df_model_split['demand']), [f['capacity'] for f in extended_fleet])
        if not routes: continue

        # 4. Generate Integrated Report
        m = folium.Map(location=[s_lat, s_lon], zoom_start=11, tiles='cartodbpositron')
        folium.Marker([s_lat, s_lon], icon=folium.Icon(color='red', icon='school', prefix='fa'), tooltip=f"<b>{school_name}</b>").add_to(m)
        colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf', '#e6194b', '#3cb44b', '#ffe119']
        
        dashboard_data = {"school_name": school_name, "routes": []}
        global_stop_markers = {} # (lat, lon) -> list of stop details

        for i, route_info in enumerate(routes):
            v_info = extended_fleet[route_info['vehicle_id']]
            v_info['bus_name_safe'] = v_info['name'].replace("'", "").replace('"', "")
            color = colors[i % len(colors)]
            node_coords = [coords[step['node']] for step in route_info['route']]
            road_path, road_dist, road_duration = get_real_road_geometry(node_coords)
            
            # --- OFFSET LOGIC: Prevent lines from overlapping on same roads ---
            # Shift lat/lon by a few meters based on route index
            offset = 0.00003 * (i - len(routes)/2) 
            offset_path = [[p[0] + offset, p[1] + offset] for p in road_path]

            avg_speed_kmh = (road_dist / road_duration * 3.6) if road_duration > 0 else 0
            route_pax_count = sum(df_model_split.iloc[step['node']]['demand'] for step in route_info['route'])
            
            line = folium.PolyLine(
                offset_path, 
                color=color, 
                weight=4, 
                opacity=0.7,
                tooltip=f"Bus {v_info['name']} ({route_pax_count} pax)"
            )
            line.add_to(m)
            # Tag the road line with the bus name for filtering
            m.get_root().script.add_child(folium.Element(f"setTimeout(() => {{ if (typeof {line.get_name()} !== 'undefined') {line.get_name()}.options.bus_name = '{v_info['bus_name_safe']}'; }}, 100);"))
            
            route_stops_for_manifest = []
            stop_seq_num = 0
            for step_idx, step in enumerate(route_info['route']):
                node_idx = step['node']
                row = df_model_split.iloc[node_idx]
                
                # Add to manifest
                route_stops_for_manifest.append({
                    "name": row['name'], "students": int(row['student_count']), 
                    "staff": int(row['staff_count']), "pax": int(row['demand']), 
                    "s_ids": str(row['student_ids']), "st_ids": str(row['staff_ids']), 
                    "distance": step['cumulative_distance']
                })
                
                if node_idx > 0:
                    stop_seq_num += 1
                    pos = (float(row['lat']), float(row['lon']))
                    if pos not in global_stop_markers:
                        global_stop_markers[pos] = []
                    
                    global_stop_markers[pos].append({
                        "bus": v_info['name'],
                        "color": color,
                        "seq": stop_seq_num,
                        "pax": int(row['demand']),
                        "name": row['name'].split(" (Part")[0]
                    })

            dashboard_data["routes"].append({
                "vehicle_name": v_info['name'], "max_cap": int(v_info['capacity']),
                "total_pax": int(route_pax_count),
                "stop_count": int(stop_seq_num),
                "total_dist": f"{road_dist/1000:.2f} km" if road_dist > 0 else f"{route_info['distance_meters']/1000:.2f} km",
                "avg_speed": f"{avg_speed_kmh:.1f} km/h",
                "stops": route_stops_for_manifest
            })

        # --- DRAW GLOBAL CLUSTERED MARKERS ---
        for pos, bus_visits in global_stop_markers.items():
            # If multiple buses, we'll use a neutral color for the circle but show all bus info
            main_color = bus_visits[0]['color'] if len(bus_visits) == 1 else '#333333'
            stop_name = bus_visits[0]['name']
            
            # Combine sequence labels (e.g. "B1: 1, B2: 4")
            labels = []
            for v in bus_visits:
                labels.append(f"{v['seq']}")
            numbers_label = ", ".join(labels)

            popup_html = f"<div style='font-family:Inter; padding:5px;'><b>{stop_name}</b><hr>"
            for v in bus_visits:
                popup_html += f"<div style='margin-bottom:5px;'><span style='color:{v['color']}; font-weight:bold;'>Bus {v['bus']}</span>: Stop #{v['seq']} ({v['pax']} pax)</div>"
            popup_html += "</div>"

            cm = folium.CircleMarker(
                pos, radius=7, color=main_color, weight=2, fill=True, fill_color='white', fill_opacity=1,
                popup=folium.Popup(popup_html, max_width=300)
            )
            cm.add_to(m)
            
            # Tag marker with all buses that visit it
            bus_names_js = json.dumps([v['bus'].replace("'", "").replace('"', "") for v in bus_visits])
            m.get_root().script.add_child(folium.Element(f"setTimeout(() => {{ if (typeof {cm.get_name()} !== 'undefined') {cm.get_name()}.options.bus_names = {bus_names_js}; }}, 100);"))

            # The Label Marker
            lbl = folium.Marker(
                pos,
                icon=folium.DivIcon(
                    icon_size=(40, 20), icon_anchor=(20, 26),
                    html=f"""<div id="lbl-{id(pos)}" style="font-family:'Inter'; font-size:10px; font-weight:bold; color:white; background-color:{main_color}; border:1.5px solid white; border-radius:10px; padding:2px 6px; display:inline-block; white-space:nowrap; box-shadow:0 1px 3px rgba(0,0,0,0.4); pointer-events:none;">{numbers_label}</div>"""
                )
            )
            lbl.add_to(m)
            m.get_root().script.add_child(folium.Element(f"setTimeout(() => {{ if (typeof {lbl.get_name()} !== 'undefined') {lbl.get_name()}.options.bus_names = {bus_names_js}; }}, 100);"))


        # --- FILTER UI & LOGIC INJECTION ---
        all_bus_names = sorted(list(set(v['bus'].replace("'", "").replace('"', "") for visits in global_stop_markers.values() for v in visits)))
        options_html = "".join([f'<option value="{name}">{name}</option>' for name in all_bus_names])
        
        filter_html = f"""
        <div id="bus-filter-container" style="
            position: fixed; top: 10px; right: 50px; z-index: 9999; 
            background: rgba(255, 255, 255, 0.95); padding: 12px; border-radius: 10px; 
            box-shadow: 0 4px 15px rgba(0,0,0,0.15); font-family: 'Inter', sans-serif;
            border: 1px solid #e2e8f0; backdrop-filter: blur(4px);
        ">
            <div style="font-size: 10px; font-weight: 800; color: #475569; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.8px;">Bus Route Filter</div>
            <select id="bus-select" onchange="window.filterBus(this.value)" style="
                padding: 8px 12px; border-radius: 6px; border: 1px solid #cbd5e1;
                font-size: 13px; color: #1e293b; background: white; cursor: pointer; outline: none; width: 160px;
                font-family: 'Inter', sans-serif;
            ">
                <option value="all">Show All Routes</option>
                {options_html}
            </select>
        </div>
        """
        m.get_root().html.add_child(folium.Element(filter_html))

        filter_js = f"""
        window.filterBus = function(busName) {{
            var mapInstance = null;
            // Robust check for the Leaflet map instance
            for (var key in window) {{
                if (key.startsWith('map_') && window[key] instanceof L.Map) {{
                    mapInstance = window[key];
                    break;
                }}
            }}
            if (!mapInstance) {{
                console.error("Map instance not found");
                return;
            }}

            mapInstance.eachLayer(function(layer) {{
                // Check if this layer has our custom bus tagging
                var hasBusTag = layer.options && (layer.options.bus_name || layer.options.bus_names);
                
                if (hasBusTag) {{
                    var isMatch = false;
                    if (busName === 'all') {{
                        isMatch = true;
                    }} else if (layer.options.bus_name === busName) {{
                        isMatch = true;
                    }} else if (layer.options.bus_names && layer.options.bus_names.indexOf(busName) !== -1) {{
                        isMatch = true;
                    }}

                    if (isMatch) {{
                        if (layer.setStyle) layer.setStyle({{opacity: 0.8, fillOpacity: 0.6}});
                        if (layer.setOpacity) layer.setOpacity(1.0);
                        if (layer.getElement && layer.getElement()) layer.getElement().style.opacity = '1';
                    }} else {{
                        if (layer.setStyle) layer.setStyle({{opacity: 0.05, fillOpacity: 0.02}});
                        if (layer.setOpacity) layer.setOpacity(0.1);
                        if (layer.getElement && layer.getElement()) layer.getElement().style.opacity = '0.05';
                    }}
                }}
            }});
        }};
        """
        m.get_root().script.add_child(folium.Element(filter_js))

        safe_name = school_name.replace(" ", "_").replace(",", "").replace("-", "_").replace("__", "_")
        map_content = html.escape(m.get_root().render())
        dashboard_html = f"""
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Fleet Report - {school_name}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet"><style>
body{{font-family:'Inter',sans-serif;margin:0;display:flex;height:100vh;background:#f4f7f9;overflow:hidden;}}
#sidebar{{width:350px;background:white;border-right:1px solid #e1e8ed;display:flex;flex-direction:column;}}
#bus-list{{flex:1;overflow-y:auto;}} 
.bus-card{{padding:15px 20px;border-bottom:1px solid #f0f3f5;cursor:pointer;}}
.bus-card.active{{border-left:5px solid #3498db;background:#ebf5fb;}}
#detail-view{{flex:1;display:flex;flex-direction:column;padding:25px;position:relative;}}
.nav-tabs{{display:flex;gap:10px;margin-bottom:20px;}}
.tab-btn{{padding:10px 20px;border:none;border-radius:8px;cursor:pointer;background:#e2e8f0;font-weight:600;}}
.tab-btn.active{{background:#3498db;color:white;}}
.summary-table{{width:100%;border-collapse:collapse;background:white;border-radius:12px;overflow:hidden;box-shadow:0 4px 6px rgba(0,0,0,0.05);}}
.summary-table th, .summary-table td{{padding:15px;text-align:left;border-bottom:1px solid #f1f5f9;}}
.summary-table th{{background:#1e293b;color:white;font-size:11px;}}
.timeline{{position:relative;padding-left:40px;margin-top:20px;border-left:2px solid #e2e8f0;margin-left:15px;}}
.stop-item{{position:relative;margin-bottom:15px;background:white;padding:15px;border-radius:10px;box-shadow:0 2px 4px rgba(0,0,0,0.03);}}
.stop-item::before{{content:attr(data-step);position:absolute;left:-51px;top:20px;width:20px;height:20px;border-radius:50%;background:#3498db;color:white;font-size:10px;font-weight:bold;text-align:center;line-height:20px;border:3px solid white;box-shadow:0 0 0 2px #3498db;}}
.pax-pill{{padding:3px 8px;border-radius:4px;font-size:10px;font-weight:700;margin-right:5px;}}
.student{{background:#e0f2fe;color:#0369a1;}} .staff{{background:#dcfce7;color:#15803d;}}
</style></head><body>
<div id="sidebar">
    <div style="padding:20px;background:#1e293b;color:white;"><h3>Report Center</h3><p style="font-size:11px;opacity:0.7;">{school_name}</p></div>
    <div id="bus-list"></div>
</div>
<div id="detail-view">
    <div class="nav-tabs">
        <button class="tab-btn active" onclick="switchTab('fleet', this)">Summary View</button>
        <button class="tab-btn" onclick="switchTab('map', this)">Road Map</button>
        <button class="tab-btn" id="manifest-tab" style="display:none;" onclick="switchTab('manifest', this)">Manifest Details</button>
    </div>
    <div id="fleet-pane" style="display:block;">
        <table class="summary-table">
            <thead><tr><th>Bus Plate</th><th>Max Capacity</th><th>Total Stops</th><th>Occupied</th><th>Utilization%</th><th>Distance</th><th>Avg Speed</th></tr></thead>
            <tbody id="fleet-body"></tbody>
        </table>
    </div>
    <div id="map-pane" style="display:none; height:100%;"><iframe srcdoc="{map_content}" style="width:100%; height:100%; border:none;"></iframe></div>
    <div id="manifest-pane" style="display:none; overflow-y:auto;"><div class="timeline" id="timeline"></div></div>
</div>
<script>
const data = {json.dumps(dashboard_data)};
function switchTab(t, btn){{
    ['fleet','map','manifest'].forEach(x => document.getElementById(x+'-pane').style.display = 'none');
    document.getElementById(t+'-pane').style.display = 'block';
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
}}
data.routes.forEach(r => {{
    const util = ((r.total_pax / r.max_cap)*100).toFixed(1);
    document.getElementById('fleet-body').innerHTML += `<tr><td><b>${{r.vehicle_name}}</b></td><td>${{r.max_cap}} Seats</td><td>${{r.stop_count}} Stops</td><td><b style="color:${{r.total_pax > r.max_cap ? 'red' : '#27ae60'}}">${{r.total_pax}} Pax</b></td><td>${{util}}%</td><td>${{r.total_dist}}</td><td>${{r.avg_speed}}</td></tr>`;
    const card = document.createElement('div'); card.className = 'bus-card';
    card.innerHTML = `<h4>${{r.vehicle_name}}</h4><p>${{r.total_pax}} / ${{r.max_cap}} Pax • ${{r.total_dist}}</p>`;
    card.onclick = () => {{
        document.querySelectorAll('.bus-card').forEach(c => c.classList.remove('active')); card.classList.add('active');
        document.getElementById('manifest-tab').style.display = 'inline';
        switchTab('manifest', document.getElementById('manifest-tab'));
        const tl = document.getElementById('timeline'); tl.innerHTML = '';
        let stepCount = 0;
        r.stops.forEach((s, idx) => {{
            if (s.name !== "SCHOOL" || s.distance > 0) {{
                stepCount++;
                tl.innerHTML += `<div class="stop-item" data-step="${{stepCount}}"><h4>${{s.name}}</h4><p style="font-size:11px;color:#94a3b8;">${{s.distance.toFixed(2)}} km</p>
                ${{s.students > 0 ? `<span class="pax-pill student">${{s.students}} Students</span>` : ''}}
                ${{s.staff > 0 ? `<span class="pax-pill staff">${{s.staff}} Staff</span>` : ''}}
                <div style="font-size:10px;color:#64748b;margin-top:5px;">IDs: ${{s.s_ids}} ${{s.st_ids}}</div></div>`;
            }} else {{
                tl.innerHTML += `<div class="stop-item" data-step="S"><h4>HOME (DEPOT)</h4><p style="font-size:11px;color:#94a3b8;">Start Point</p></div>`;
            }}
        }});
    }}; document.getElementById('bus-list').appendChild(card);
}});
</script></body></html>
        """
        with open(os.path.join(outputs_dir, f'report_{safe_name}.html'), 'w', encoding='utf-8') as f: f.write(dashboard_html)
    print("\n🎉 Map enriched: Numbered stops everywhere (Tooltips, Popups, Timeline).")

if __name__ == "__main__":
    run_optimization()
