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
    Creates a road-accurate distance matrix using OSRM Table API with chunking for large datasets.
    locations: List of (lat, lon) tuples.
    use_osrm_for_large: If True, chunking will be used for schools > 100 nodes.
    Returns: Matrix of distances in kilometers.
    """
    size = len(locations)
    if size == 0: return []
    
    # Initialize matrix with Haversine as a baseline fallback
    matrix = [[0.0] * size for _ in range(size)]
    for i in range(size):
        for j in range(size):
            if i != j:
                matrix[i][j] = haversine(locations[i][0], locations[i][1], locations[j][0], locations[j][1])

    # OSRM Table API limit (public server is ~100 nodes per request total)
    # To handle large matrices, we request blocks. Max nodes in one request (sources + destinations)
    MAX_NODES = 100 

    if size <= MAX_NODES:
        # Simple case: Single request
        try:
            loc_string = ";".join([f"{lon},{lat}" for lat, lon in locations])
            url = f"http://127.0.0.1:5000/table/v1/driving/{loc_string}?annotations=distance"
            response = requests.get(url, timeout=5)
            data = response.json()
            if data.get('code') == 'Ok':
                road_distances = data['distances']
                for i in range(size):
                    for j in range(size):
                        if road_distances[i][j] is not None:
                            matrix[i][j] = road_distances[i][j] / 1000.0
                print(f"✅ Success: Fetched {size}x{size} distance matrix.")
                return matrix
        except Exception as e:
            print(f"⚠️ OSRM failed ({e}). Using Haversine.")
            return matrix
    else:
        # Complex case: Chunking for Large Schools (e.g. 500+ nodes)
        if not use_osrm_for_large:
            print(f"ℹ️ Node count ({size}) is large. Using Haversine for performance (use_osrm_for_large=False).")
            return matrix

        print(f"🚀 Processing LARGE dataset ({size} nodes). Fetching road distances in chunks...")
        # We split into blocks. e.g. if size=500, we do 5x5 = 25 requests of 100x100
        # Wait, OSRM limit is 'sources + destinations' OR 'total elements'. 
        # For public OSRM, the limit is usually 100 coordinates total in the URL.
        # Local OSRM can handle much larger chunks
        CHUNK = 500
        num_chunks = (size + CHUNK - 1) // CHUNK
        
        total_reqs = num_chunks * num_chunks
        completed = 0
        
        try:
            for row_chunk in range(num_chunks):
                for col_chunk in range(num_chunks):
                    row_start, row_end = row_chunk * CHUNK, min((row_chunk + 1) * CHUNK, size)
                    col_start, col_end = col_chunk * CHUNK, min((col_chunk + 1) * CHUNK, size)
                    
                    # Nodes for this specific chunk
                    subset_nodes = locations[row_start:row_end] + locations[col_start:col_end]
                    # Sources are the first part, Destinations are the second part
                    sources_idx = ";".join([str(i) for i in range(len(locations[row_start:row_end]))])
                    dest_idx = ";".join([str(i) for i in range(len(locations[row_start:row_end]), len(subset_nodes))])
                    
                    loc_string = ";".join([f"{lon},{lat}" for lat, lon in subset_nodes])
                    url = f"http://127.0.0.1:5000/table/v1/driving/{loc_string}?sources={sources_idx}&destinations={dest_idx}&annotations=distance"
                    
                    response = requests.get(url, timeout=20)
                    data = response.json()
                    
                    if data.get('code') == 'Ok':
                        dist_chunk = data['distances']
                        for i, road_row in enumerate(dist_chunk):
                            for j, val in enumerate(road_row):
                                if val is not None:
                                    matrix[row_start + i][col_start + j] = val / 1000.0
                    
                    completed += 1
                    sys.stdout.write(f"\r   Progress: {completed}/{total_reqs} chunks fetched... ")
                    sys.stdout.flush()
                    # Small sleep to be polite to public OSRM server
                    time.sleep(0.5)
            
            print(f"\n✅ Large Matrix Complete: Fully road-accurate distances for {size} nodes.")
        except Exception as e:
            print(f"\n⚠️ Chunked OSRM failed at {completed}/{total_reqs} ({e}). Remaining will use Haversine.")
            
    return matrix
