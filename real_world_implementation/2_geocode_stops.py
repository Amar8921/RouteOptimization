import pandas as pd
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from geopy.exc import GeocoderTimedOut
import time
import os
import re

# CONFIG
USER_AGENT = "school_bus_optimizer_v2"
QATAR_BOUNDS = {
    'min_lat': 24.0, 'max_lat': 26.5,
    'min_lon': 50.0, 'max_lon': 52.0
}

def is_in_qatar(lat, lon):
    if not lat or not lon: return False
    return (QATAR_BOUNDS['min_lat'] <= lat <= QATAR_BOUNDS['max_lat']) and \
           (QATAR_BOUNDS['min_lon'] <= lon <= QATAR_BOUNDS['max_lon'])

def clean_stop_name(name):
    """
    Removes common direction words to increase search hit rate.
    """
    if not isinstance(name, str): return ""
    name = name.upper()
    # Remove noise words
    noise = ["SIDE", "OPP", "OPPOSITE", "NEAR", "BEHIND", "NEXT TO", "PICKUP", "DROP", "POINT", "AREA"]
    for word in noise:
        name = name.replace(word, "")
    
    # Remove special chars and extra spaces
    name = re.sub(r'[^A-Z0-9\s]', ' ', name)
    return " ".join(name.split())

def smart_geocode(geolocator, stop_name):
    """
    Tries multiple search strategies to find a valid location in Qatar.
    """
    strategies = [
        f"{stop_name}, Qatar",
        f"{stop_name}, Doha, Qatar",
        f"{clean_stop_name(stop_name)}, Qatar"
    ]
    
    for query in strategies:
        try:
            # Enforce Qatar country code
            location = geolocator.geocode(query, country_codes="qa")
            
            if location and is_in_qatar(location.latitude, location.longitude):
                return location.latitude, location.longitude
            
            time.sleep(1) # Respect rate limits
        except Exception as e:
            print(f"   ⚠️ Error searching '{query}': {e}")
            time.sleep(1)
            
    return 0.0, 0.0

def run_geocoding():
    print("🌍 Starting Strict Geocoding for Qatar...")
    
    # Paths
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_file = os.path.join(script_dir, 'data', 'raw_stops.csv')
    output_file = os.path.join(script_dir, 'data', 'geocoded_stops.csv')
    
    if not os.path.exists(input_file):
        print(f"❌ Error: {input_file} not found. Run Step 1 first.")
        return

    df = pd.read_csv(input_file)
    print(f"Total Stops to Process: {len(df)}")
    
    # Identify unique stop names to avoid re-geocoding the same text
    unique_names = df['StopName'].unique()
    print(f"Unique Stop Names: {len(unique_names)}")
    
    geolocator = Nominatim(user_agent=USER_AGENT)
    geocode_cache = {}
    
    # Process
    success_count = 0
    fail_count = 0
    
    for i, name in enumerate(unique_names):
        if pd.isna(name): continue
        
        print(f"[{i+1}/{len(unique_names)}] Searching: {name}...")
        
        lat, lon = smart_geocode(geolocator, name)
        
        if lat != 0:
            print(f"   ✅ Found: ({lat:.5f}, {lon:.5f})")
            success_count += 1
        else:
            print(f"   ❌ Not Found")
            fail_count += 1
            
        geocode_cache[name] = (lat, lon)
        
    print("\nApplying coordinates to dataset...")
    
    # Apply results back to main dataframe
    def get_lat(name): return geocode_cache.get(name, (0,0))[0]
    def get_lon(name): return geocode_cache.get(name, (0,0))[1]
    
    df['geocode_lat'] = df['StopName'].apply(get_lat)
    df['geocode_lon'] = df['StopName'].apply(get_lon)
    
    # Fill DB Lat/Lon if empty, otherwise keep DB value?
    # User said "most are missing", so we likely overwrite or fillna.
    # Let's prioritize our new geocode since we validated it is in Qatar.
    
    df['final_lat'] = df.apply(lambda r: r['geocode_lat'] if r['geocode_lat'] != 0 else r['Latitude'], axis=1)
    df['final_lon'] = df.apply(lambda r: r['geocode_lon'] if r['geocode_lon'] != 0 else r['Longitude'], axis=1)
    
    # Save
    df.to_csv(output_file, index=False)
    print(f"💾 Saved results to {output_file}")
    print(f"📊 Stats: {success_count} Found, {fail_count} Failed.")

if __name__ == "__main__":
    run_geocoding()
