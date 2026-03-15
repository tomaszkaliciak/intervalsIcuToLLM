import os
import sys
import argparse
import requests
import datetime
from dotenv import load_dotenv

def get_credentials():
    load_dotenv()
    
    parser = argparse.ArgumentParser(description="Fetch the latest run from Intervals.icu for LLM format")
    parser.add_argument('--athlete-id', help="Your Intervals.icu ID (e.g., i12345)")
    parser.add_argument('--api-key', help="Your Intervals.icu API Key")
    
    args = parser.parse_args()

    athlete_id = args.athlete_id or os.environ.get('INTERVALS_ATHLETE_ID')
    api_key = args.api_key or os.environ.get('INTERVALS_API_KEY')

    if not athlete_id or not api_key:
        print("❌ ERROR: Missing credentials!")
        print("Set environment variables INTERVALS_ATHLETE_ID and INTERVALS_API_KEY")
        print("or use parameters: python script.py --athlete-id i12345 --api-key YOUR_KEY")
        sys.exit(1)

    return athlete_id, api_key

def format_pace(meters_per_second):
    if meters_per_second <= 0: return "0:00"
    minutes_per_km = 16.666666666667 / meters_per_second
    mins = int(minutes_per_km)
    secs = int((minutes_per_km - mins) * 60)
    return f"{mins}:{secs:02d}"

def format_time(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))

def get_latest_run(athlete_id, api_key):
    url = f"https://intervals.icu/api/v1/athlete/{athlete_id}/activities"
    
    # Intervals API strictly requires 'oldest' and 'newest' date parameters
    now = datetime.date.today()
    oldest = (now - datetime.timedelta(days=90)).isoformat()
    newest = (now + datetime.timedelta(days=1)).isoformat()
    
    params = {
        'oldest': oldest,
        'newest': newest
    } 
    
    response = requests.get(url, auth=('API_KEY', api_key), params=params)
    
    # Friendly error if authentication fails
    if response.status_code in (401, 403):
        print("❌ ERROR: Access Denied. Check your API key and Athlete ID.")
        sys.exit(1)
        
    response.raise_for_status()
        
    activities = response.json()
    
    if not activities:
        print(f"No activities found on the account between {oldest} and {newest}.")
        return

    # Sort activities by date descending (newest first) to be absolutely sure
    activities.sort(key=lambda x: x.get('start_date_local', ''), reverse=True)
    
    # Find the first activity that is a run
    run_activity = next((act for act in activities if act.get('type') == 'Run'), None)

    if not run_activity:
        print("❌ No run found in the last 90 days.")
        return
    
    name = run_activity.get('name', 'No name')
    date_str = run_activity.get('start_date_local', '')[:10]
    distance_km = run_activity.get('distance', 0) / 1000
    moving_time_s = run_activity.get('moving_time', 0)
    avg_pace_ms = run_activity.get('average_speed', 0)
    
    avg_hr = run_activity.get('average_heartrate', 0)
    max_hr = run_activity.get('max_heartrate', 0)
    avg_cadence = run_activity.get('average_cadence', 0) * 2 
    rpe = run_activity.get('rpe', 'None')
    description = run_activity.get('description', 'No comment')

    print(f"*   **Workout:** {name} ({date_str})")
    print(f"*   **Time & Distance:** ({format_time(moving_time_s)}, {distance_km:.2f} km)")
    print(f"*   **Average Pace:** ({format_pace(avg_pace_ms)}/km)")
    print(f"*   **Average HR / Max:** {avg_hr:.0f}/{max_hr:.0f}")
    print(f"*   **Average Cadence:** {avg_cadence:.0f} spm")
    print(f"*   **RPE (Fatigue 1-10):** {rpe}")
    print(f"*   **Comment:** {description}")
    print("")

    zones = run_activity.get('icu_pace_zones',[])
    if zones:
        total_zone_time = sum(zones)
        zone_names =[
            "Recovery", 
            "Aerobic Endurance", 
            "Aerobic Power", 
            "Threshold", 
            "Anaerobic Endurance", 
            "Anaerobic Power", 
            "Max Power"
        ]
        
        for i, time_in_zone in enumerate(zones):
            if i < len(zone_names):
                percent = (time_in_zone / total_zone_time) * 100 if total_zone_time else 0
                time_str = format_time(time_in_zone)
                print(f"{zone_names[i]:<20} {time_str:>8} {percent:>3.0f}%")

if __name__ == "__main__":
    ATHLETE_ID, API_KEY = get_credentials()
    get_latest_run(ATHLETE_ID, API_KEY)