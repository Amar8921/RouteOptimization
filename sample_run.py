import pandas as pd
import folium
from distance import create_distance_matrix
from optimizer import optimize_routes
import os

def format_time(minutes):
    """Converts minutes from 6:00 AM to a HH:MM AM/PM string."""
    base_hour = 6
    hour = base_hour + (minutes // 60)
    mins = minutes % 60
    period = "AM" if hour < 12 else "PM"
    if hour > 12: hour -= 12
    return f"{hour:02d}:{mins:02d} {period}"

def run_demo():
    print("Loading data...")
    stops_df = pd.read_csv("data/stops.csv")
    students_df = pd.read_csv("data/students.csv")
    staff_df = pd.read_csv("data/staff.csv")
    vehicles_df = pd.read_csv("data/vehicles.csv")

    # Group demand by stop_id
    student_demand = students_df.groupby("stop_id").size()
    staff_demand = staff_df.groupby("stop_id").size()
    
    # Combine demands
    total_demand = student_demand.add(staff_demand, fill_value=0).astype(int)
    
    # Check which stops have staff (for time window differentiation)
    stops_with_staff = set(staff_df['stop_id'].unique())

    all_stops = []
    demands = []
    time_windows = []
    
    # Time Windows (in minutes from 6:00 AM)
    # Depot (School): 6:00 AM - 9:00 AM (0 - 180 mins)
    # Staff Stops: 6:15 AM - 8:00 AM (15 - 120 mins)
    # Student-only Stops: 6:30 AM - 8:15 AM (30 - 135 mins)
    
    for i, row in stops_df.iterrows():
        all_stops.append((row['lat'], row['lon']))
        demands.append(total_demand.get(row['stop_id'], 0))
        
        if row['stop_id'] == 0: # Depot
            time_windows.append((0, 180))
        elif row['stop_id'] in stops_with_staff:
            time_windows.append((15, 120))
        else:
            time_windows.append((30, 135))

    # Create distance matrix
    print("Calculating distance matrix...")
    dist_matrix = create_distance_matrix(all_stops)
    
    # Create travel time matrix (assuming 30 km/h -> 2 mins per km)
    # We add 2 mins for each stop for pickup time
    speed_km_min = 30 / 60 
    travel_time_matrix = []
    for row in dist_matrix:
        time_row = []
        for d in row:
            if d == 0:
                time_row.append(0)
            else:
                # Travel time + 2 mins stop service time
                time_row.append(int(d / speed_km_min) + 2)
        travel_time_matrix.append(time_row)
    
    # Prepare capacities
    capacities = list(vehicles_df['capacity'])

    print("Optimizing routes with Time Windows...")
    optimized_routes = optimize_routes(
        dist_matrix, 
        demands, 
        capacities, 
        time_windows, 
        travel_time_matrix
    )

    if not optimized_routes:
        print("No solution found!")
        return

    print(f"Solution found: {len(optimized_routes)} active routes.")

    # Visualize on Map
    print("Generating map...")
    school_lat, school_lon = all_stops[0]
    m = folium.Map(location=[school_lat, school_lon], zoom_start=13)

    folium.Marker(
        [school_lat, school_lon],
        popup="School (Depot)",
        icon=folium.Icon(color='red', icon='university', prefix='fa')
    ).add_to(m)

    colors = ['blue', 'green', 'purple', 'orange', 'darkred', 'cadetblue']
    
    for i, route_info in enumerate(optimized_routes):
        route_nodes = route_info['route']
        route_coords = [all_stops[step['node']] for step in route_nodes]
        
        color = colors[i % len(colors)]
        folium.PolyLine(
            route_coords,
            color=color,
            weight=5,
            opacity=0.8,
            tooltip=f"Bus {route_info['vehicle_id'] + 1} Route"
        ).add_to(m)
        
        for step in route_nodes:
            node_idx = step['node']
            if node_idx == 0: continue # Skip school markers (already added)
            
            lat, lon = all_stops[node_idx]
            stop_id = stops_df.iloc[node_idx]['stop_id']
            demand = demands[node_idx]
            arrival = format_time(step['arrival_time'])
            dist = step['cumulative_distance']
            
            folium.CircleMarker(
                [lat, lon],
                radius=8,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.6,
                popup=f"<b>Stop ID: {stop_id}</b><br>Passengers: {demand}<br>Arrival: {arrival}<br>Distance from Start: {dist:.2f} km"
            ).add_to(m)

    m.save("route_map.html")
    print("Map saved to route_map.html")
    
    # Print route details
    for route in optimized_routes:
        print(f"\nBus {route['vehicle_id'] + 1}:")
        for step in route['route']:
            node_idx = step['node']
            stop_id = stops_df.iloc[node_idx]['stop_id']
            name = "School" if stop_id == 0 else f"Stop {stop_id}"
            time_str = format_time(step['arrival_time'])
            dist = step['cumulative_distance']
            print(f"  {time_str} -> {name} ({dist:.2f} km)")
        print(f"  Total Distance: {route['distance_meters'] / 1000:.2f} km")

if __name__ == "__main__":
    run_demo()
