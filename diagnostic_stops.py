import pandas as pd
import os

# Path to the data
data_dir = r"d:\Antigravity\RouteOptimzation\real_world_implementation\data"
stops_path = os.path.join(data_dir, "geocoded_stops.csv")

if not os.path.exists(stops_path):
    print(f"File not found: {stops_path}")
else:
    df = pd.read_csv(stops_path)
    
    # Check if 'final_lat' and 'final_lon' exist, otherwise fallback to 'Latitude'
    lat_col = 'final_lat' if 'final_lat' in df.columns else 'Latitude'
    lon_col = 'final_lon' if 'final_lon' in df.columns else 'Longitude'
    
    print(f"🔍 Analyzing stops using columns: {lat_col}, {lon_col}")
    
    # Group by coordinates and list distinct stop names
    duplicates = df.groupby([lat_col, lon_col])['StopName'].unique().reset_index()
    
    # Filter for coordinates shared by more than 1 name
    multi_name_coords = duplicates[duplicates['StopName'].apply(len) > 1]
    
    if multi_name_coords.empty:
        print("✅ No duplicate coordinates with different names found.")
    else:
        print(f"⚠️ Found {len(multi_name_coords)} coordinates shared by multiple StopNames:\n")
        for idx, row in multi_name_coords.iterrows():
            print(f"📍 Location: ({row[lat_col]}, {row[lon_col]})")
            print(f"   Names: {', '.join(row['StopName'])}")
            print("-" * 30)

    # Also check for stop names that have multiple coordinates (potential inconsistency)
    name_duplicates = df.groupby('StopName')[[lat_col, lon_col]].nunique().reset_index()
    multi_coord_names = name_duplicates[(name_duplicates[lat_col] > 1) | (name_duplicates[lon_col] > 1)]
    
    if not multi_coord_names.empty:
        print(f"\n⚠️ Found {len(multi_coord_names)} StopNames with multiple different coordinates:")
        for idx, row in multi_coord_names.iterrows():
            print(f"📝 StopName: {row['StopName']}")
            # Get the coordinates for this name
            coords = df[df['StopName'] == row['StopName']][[lat_col, lon_col]].drop_duplicates()
            for c_idx, c_row in coords.iterrows():
                print(f"   -> ({c_row[lat_col]}, {c_row[lon_col]})")
            print("-" * 30)
