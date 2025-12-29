import pyodbc
import pandas as pd
import os
from config import DB_CONFIG

def get_db_connection():
    conn_str = (
        f"DRIVER={DB_CONFIG['driver']};"
        f"SERVER={DB_CONFIG['server']};"
        f"DATABASE={DB_CONFIG['database']};"
        f"UID={DB_CONFIG['username']};"
        f"PWD={DB_CONFIG['password']}"
    )
    return pyodbc.connect(conn_str)

def fetch_raw_data():
    conn = get_db_connection()
    
    # Create data directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, 'data')
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        
    print(f"🚀 Connected to {DB_CONFIG['database']}. Fetching raw data...")

    # 1. School Details (Depot)
    print("   Fetching School Info...")
    sql_school = "select SchoolID,SchoolName,Description,Address1,Place from schools.Schools"
    df_school = pd.read_sql(sql_school, conn)
    df_school.to_csv(os.path.join(output_dir, "raw_school.csv"), index=False)

    # 2. Vehicles
    print("   Fetching Vehicles...")
    sql_vehicles = """
    Select VehicleIID,VehicleTypeID,VehicleOwnershipTypeID,VehicleRegistrationNumber,
    Description,ModelName,YearMade,TransmissionID,Color,AllowSeatingCapacity,
    MaximumSeatingCapacity,IsSecurityEnabled,IsCameraEnabled,SchoolID,
    AcademicYearID,FleetCode 
    from mutual.Vehicles 
    where IsActive=1
    """
    df_vehicles = pd.read_sql(sql_vehicles, conn)
    df_vehicles.to_csv(os.path.join(output_dir, "raw_vehicles.csv"), index=False)

    # 3. All Active Stops (Including those with missing Lat/Lon)
    print("   Fetching Route Stops...")
    # NOTE: I removed 'and Latitude is not null' so we can see ALL demand, not just mapped ones.
    sql_stops = """
    select RouteStopMapIID,RouteID,StopName,Longitude,Latitude 
    from schools.RouteStopMaps 
    where IsActive=1
    """
    df_stops = pd.read_sql(sql_stops, conn)
    df_stops.to_csv(os.path.join(output_dir, "raw_stops.csv"), index=False)

    # 4. Student Demand with Names
    print("   Fetching Student Assignments & Names...")
    sql_students = """
    select 
        m.StudentRouteStopMapIID, m.StudentID, m.PickupStopMapID, m.DropStopMapID,
        m.SchoolID, m.ClassID, m.SectionID,
        (s.FirstName + ' ' + ISNULL(s.LastName, '')) as FullName
    from schools.StudentRouteStopMaps m
    left join registration.StudentMasters s on m.StudentID = s.StudentIID
    where m.IsActive=1
    """
    df_students = pd.read_sql(sql_students, conn)
    df_students.to_csv(os.path.join(output_dir, "raw_students.csv"), index=False)

    # 5. Staff Demand with Names
    print("   Fetching Staff Assignments & Names...")
    sql_staff = """
    select 
        m.StaffRouteStopMapIID, m.StaffID, m.PickupStopMapID, m.DropStopMapID,
        m.SchoolID,
        (s.FirstName + ' ' + ISNULL(s.LastName, '')) as FullName
    from schools.StaffRouteStopMaps m
    left join hr.StaffMasters s on m.StaffID = s.StaffIID
    where m.IsActive=1
    """
    df_staff = pd.read_sql(sql_staff, conn)
    df_staff.to_csv(os.path.join(output_dir, "raw_staff.csv"), index=False)

    conn.close()
    
    # --- Summary ---
    print("\n📊 Data Fetch Summary:")
    print(f"   - School:   {len(df_school)} records")
    print(f"   - Vehicles: {len(df_vehicles)} active buses")
    print(f"   - Stops:    {len(df_stops)} total stops")
    
    # Check Missing Coordinates
    missing_coords = df_stops[ (df_stops['Latitude'].isnull()) | (df_stops['Latitude'] == 0) ]
    print(f"   - ⚠️ Missing Lat/Lon: {len(missing_coords)} stops (need geocoding)")
    
    print(f"   - Students: {len(df_students)} records")
    print(f"   - Staff:    {len(df_staff)} records")
    print(f"\n✅ Raw data saved to: {output_dir}")

if __name__ == "__main__":
    fetch_raw_data()
