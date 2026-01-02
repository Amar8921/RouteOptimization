import math
import requests
import json
import time

def haversine(lat1, lon1, lat2, lon2):
    """Fallback: Great circle distance in kilometers."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1 
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    return 2 * math.asin(math.sqrt(a)) * 6371

def create_distance_matrix(locations):
    """
    Creates a road-accurate distance matrix using OSRM Table API.
    locations: List of (lat, lon) tuples.
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

    # Try to get real road distances from OSRM Table API
    # Public OSRM limit is usually 100 nodes per request
    max_nodes = 100
    if size <= max_nodes:
        try:
            loc_string = ";".join([f"{lon},{lat}" for lat, lon in locations])
            url = f"http://router.project-osrm.org/table/v1/driving/{loc_string}?annotations=distance"
            
            response = requests.get(url, timeout=15)
            data = response.json()
            
            if data.get('code') == 'Ok' and 'distances' in data:
                # OSRM returns distances in METERS. Convert to KM.
                road_distances = data['distances']
                for i in range(size):
                    for j in range(size):
                        if road_distances[i][j] is not None:
                            matrix[i][j] = road_distances[i][j] / 1000.0
                print(f"✅ Success: Fetched {size}x{size} real road distance matrix from OSRM.")
            else:
                print("⚠️ OSRM Table API returned error. Using Haversine fallback.")
        except Exception as e:
            print(f"⚠️ OSRM connection failed: {e}. Using Haversine fallback.")
    else:
        print(f"ℹ️ Node count ({size}) exceeds public OSRM Table limit. Using Haversine for performance.")
        
    return matrix
