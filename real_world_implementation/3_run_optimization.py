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
    if len(coords) < 2: return coords, 0
    full_path, total_dist, chunk_size = [], 0, 40
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
            else: full_path.extend([[p[0], p[1]] for p in chunk])
        except: full_path.extend([[p[0], p[1]] for p in chunk])
    return full_path, total_dist

def run_optimization():
    print("🚀 Starting Multi-School Route Optimization (Strict & Unified)...")
    data_dir, outputs_dir = os.path.join(current_dir, 'data'), os.path.join(current_dir, 'outputs')
    if not os.path.exists(outputs_dir): os.makedirs(outputs_dir)
    
    school_df = pd.read_csv(os.path.join(data_dir, 'raw_school.csv'))
    all_stops_df = pd.read_csv(os.path.join(data_dir, 'geocoded_stops.csv'))
    all_students_df = pd.read_csv(os.path.join(data_dir, 'raw_students.csv'))
    all_staff_df = pd.read_csv(os.path.join(data_dir, 'raw_staff.csv'))
    all_vehicles_df = pd.read_csv(os.path.join(data_dir, 'raw_vehicles.csv'))

    geolocator = Nominatim(user_agent="school_optimizer_v12")

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
        
        # 2. STRICT SPLITTING logic: Must split passengers proportionally
        min_v_cap = min(f['capacity'] for f in fleet_list)
        split_limit = min(25, min_v_cap) # Safety cap

        split_rows = []
        for idx, row in df_model.iterrows():
            if idx == 0: split_rows.append(row); continue
            if row['demand'] > split_limit:
                num_parts = math.ceil(row['demand'] / split_limit)
                for i in range(num_parts):
                    new_part = row.copy()
                    # CRITICAL FIX: Split the counts proportionally
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
        folium.Marker([s_lat, s_lon], icon=folium.Icon(color='red', icon='school', prefix='fa')).add_to(m)
        colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']
        
        dashboard_data = {"school_name": school_name, "routes": []}
        for i, route_info in enumerate(routes):
            v_info = extended_fleet[route_info['vehicle_id']]
            color = colors[i % len(colors)]
            node_coords = [coords[step['node']] for step in route_info['route']]
            road_path, road_dist = get_real_road_geometry(node_coords)
            folium.PolyLine(road_path, color=color, weight=4, opacity=0.8).add_to(m)
            
            stops = []
            for step in route_info['route']:
                node_idx = step['node']
                row = df_model_split.iloc[node_idx]
                stops.append({"name": row['name'], "students": int(row['student_count']), "staff": int(row['staff_count']), "pax": int(row['demand']), "s_ids": str(row['student_ids']), "st_ids": str(row['staff_ids']), "distance": step['cumulative_distance']})
                if node_idx > 0: folium.CircleMarker([row['lat'], row['lon']], radius=4, color=color, fill=True, popup=f"{row['name']} ({row['demand']} pax)").add_to(m)

            dashboard_data["routes"].append({
                "vehicle_name": v_info['name'], "max_cap": v_info['capacity'],
                "total_pax": sum(s['pax'] for s in stops),
                "total_dist": f"{road_dist/1000:.2f} km" if road_dist > 0 else f"{route_info['distance_meters']/1000:.2f} km",
                "stops": stops
            })

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
.stop-item::before{{content:'';position:absolute;left:-51px;top:20px;width:12px;height:12px;border-radius:50%;background:#3498db;border:3px solid white;box-shadow:0 0 0 2px #3498db;}}
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
            <thead><tr><th>Bus Plate</th><th>Max Capacity</th><th>Occupied</th><th>Utilization%</th><th>Distance</th></tr></thead>
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
    document.getElementById('fleet-body').innerHTML += `<tr><td><b>${{r.vehicle_name}}</b></td><td>${{r.max_cap}} Seats</td><td><b style="color:${{r.total_pax > r.max_cap ? 'red' : '#27ae60'}}">${{r.total_pax}} Pax</b></td><td>${{util}}%</td><td>${{r.total_dist}}</td></tr>`;
    const card = document.createElement('div'); card.className = 'bus-card';
    card.innerHTML = `<h4>${{r.vehicle_name}}</h4><p>${{r.total_pax}} / ${{r.max_cap}} Pax • ${{r.total_dist}}</p>`;
    card.onclick = () => {{
        document.querySelectorAll('.bus-card').forEach(c => c.classList.remove('active')); card.classList.add('active');
        document.getElementById('manifest-tab').style.display = 'inline';
        switchTab('manifest', document.getElementById('manifest-tab'));
        const tl = document.getElementById('timeline'); tl.innerHTML = '';
        r.stops.forEach(s => {{
            if (s.name !== "SCHOOL" || s.distance > 0) {{
                tl.innerHTML += `<div class="stop-item"><h4>${{s.name}}</h4><p style="font-size:11px;color:#94a3b8;">${{s.distance.toFixed(2)}} km</p>
                ${{s.students > 0 ? `<span class="pax-pill student">${{s.students}} Students</span>` : ''}}
                ${{s.staff > 0 ? `<span class="pax-pill staff">${{s.staff}} Staff</span>` : ''}}
                <div style="font-size:10px;color:#64748b;margin-top:5px;">IDs: ${{s.s_ids}} ${{s.st_ids}}</div></div>`;
            }} else {{
                tl.innerHTML += `<div class="stop-item"><h4>${{s.name}} (DEPOT)</h4><p style="font-size:11px;color:#94a3b8;">Start Point</p></div>`;
            }}
        }});
    }}; document.getElementById('bus-list').appendChild(card);
}});
</script></body></html>
        """
        out_file = os.path.join(outputs_dir, f'report_{safe_name}.html')
        with open(out_file, 'w', encoding='utf-8') as f: f.write(dashboard_html)
    print("\n🎉 Fixed! Capacity is now strictly proportional and reports are unified.")

if __name__ == "__main__":
    run_optimization()
