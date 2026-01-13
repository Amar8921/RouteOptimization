import math
import requests
import json
import time
import sys

def haversine(lat1, lon1, lat2, lon2):
    """Fallback: Great circle distance in kilometers."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1 
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return 2 * math.asin(math.sqrt(a)) * 6371

def create_distance_matrix(locations, use_osrm_for_large=False):
    """
    Creates road-accurate distance and duration matrices using OSRM Table API.
    locations: List of (lat, lon) tuples.
    use_osrm_for_large: If True, chunking will be used for schools > MAX_NODES.
    Returns: (distance_matrix, duration_matrix) in kilometers and minutes.
    """
    size = len(locations)
    if size == 0: return [], []
    
    # Initialize matrices with Haversine as a baseline fallback
    dist_matrix = [[0.0] * size for _ in range(size)]
    dur_matrix = [[0.0] * size for _ in range(size)]
    
    for i in range(size):
        for j in range(size):
            if i != j:
                d = haversine(locations[i][0], locations[i][1], locations[j][0], locations[j][1])
                dist_matrix[i][j] = d
                dur_matrix[i][j] = (d / 30.0) * 60.0 # Estimate: 30km/h average

    # OSRM Table API limit
    MAX_NODES = 100 

    if size <= MAX_NODES:
        try:
            loc_string = ";".join([f"{lon},{lat}" for lat, lon in locations])
            url = f"http://127.0.0.1:5000/table/v1/driving/{loc_string}?annotations=distance,duration"
            response = requests.get(url, timeout=5)
            data = response.json()
            if data.get('code') == 'Ok':
                road_distances = data['distances']
                road_durations = data['durations']
                for i in range(size):
                    for j in range(size):
                        if road_distances[i][j] is not None:
                            dist_matrix[i][j] = road_distances[i][j] / 1000.0
                        if road_durations[i][j] is not None:
                            dur_matrix[i][j] = road_durations[i][j] / 60.0 # Convert seconds to minutes
                print(f"✅ Success: Fetched {size}x{size} distance/duration matrices.")
                return dist_matrix, dur_matrix
        except Exception as e:
            print(f"⚠️ OSRM failed ({e}). Using Haversine/Estimates.")
            return dist_matrix, dur_matrix
    else:
        if not use_osrm_for_large:
            print(f"ℹ️ Node count ({size}) is large. Using Haversine (use_osrm_for_large=False).")
            return dist_matrix, dur_matrix

        print(f"🚀 Processing LARGE dataset ({size} nodes). Fetching road data in chunks...")
        CHUNK = 50 # Reduced chunk size for combined annotations to avoid URL length limits
        num_chunks = (size + CHUNK - 1) // CHUNK
        total_reqs = num_chunks * num_chunks
        completed = 0
        
        try:
            for row_chunk in range(num_chunks):
                for col_chunk in range(num_chunks):
                    row_start, row_end = row_chunk * CHUNK, min((row_chunk + 1) * CHUNK, size)
                    col_start, col_end = col_chunk * CHUNK, min((col_chunk + 1) * CHUNK, size)
                    
                    subset_nodes = locations[row_start:row_end] + locations[col_start:col_end]
                    sources_idx = ";".join([str(i) for i in range(len(locations[row_start:row_end]))])
                    dest_idx = ";".join([str(i) for i in range(len(locations[row_start:row_end]), len(subset_nodes))])
                    
                    loc_string = ";".join([f"{lon},{lat}" for lat, lon in subset_nodes])
                    url = f"http://127.0.0.1:5000/table/v1/driving/{loc_string}?sources={sources_idx}&destinations={dest_idx}&annotations=distance,duration"
                    
                    response = requests.get(url, timeout=20)
                    data = response.json()
                    
                    if data.get('code') == 'Ok':
                        dist_chunk = data['distances']
                        dur_chunk = data['durations']
                        for i in range(len(dist_chunk)):
                            for j in range(len(dist_chunk[0])):
                                if dist_chunk[i][j] is not None:
                                    dist_matrix[row_start + i][col_start + j] = dist_chunk[i][j] / 1000.0
                                if dur_chunk[i][j] is not None:
                                    dur_matrix[row_start + i][col_start + j] = dur_chunk[i][j] / 60.0
                    
                    completed += 1
                    sys.stdout.write(f"\r   Progress: {completed}/{total_reqs} chunks fetched... ")
                    sys.stdout.flush()
            
            print(f"\n✅ Large matrices complete for {size} nodes.")
        except Exception as e:
            print(f"\n⚠️ OSRM failed at {completed}/{total_reqs} ({e}). Fallback used.")
            
    return dist_matrix, dur_matrix

