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
    parser.add_argument('--per-km-splits', action='store_true', help="Print pace, HR, and cadence per 1km split from Strava")
    parser.add_argument('--pace-zones', action='store_true', help="Print pace zone distribution from Intervals.icu")
    parser.add_argument('--strava-token', help="Strava API access token (required for --per-km-splits)")
    parser.add_argument('--zone-times', action='store_true', help="Print HR zone time distribution from Intervals.icu")
    parser.add_argument('--laps', action='store_true', help="Print workout intervals from Intervals.icu")

    args = parser.parse_args()

    athlete_id = args.athlete_id or os.environ.get('INTERVALS_ATHLETE_ID')
    api_key = args.api_key or os.environ.get('INTERVALS_API_KEY')

    if not athlete_id or not api_key:
        print("ERROR: Missing credentials!")
        print("Set environment variables INTERVALS_ATHLETE_ID and INTERVALS_API_KEY")
        print("or use parameters: python script.py --athlete-id i12345 --api-key YOUR_KEY")
        sys.exit(1)

    strava_token = args.strava_token or os.environ.get('STRAVA_ACCESS_TOKEN')

    if strava_token:
        strava_token = strava_token.strip().strip('"\'')

    if args.per_km_splits and not strava_token:
        print("ERROR: --per-km-splits requires Strava API access token!")
        print("Set environment variable STRAVA_ACCESS_TOKEN or use --strava-token")
        sys.exit(1)

    return athlete_id, api_key, args.per_km_splits, strava_token, args.zone_times, args.laps


def update_env_file(key, value):
    """Update a key in .env file"""
    env_path = '.env'
    with open(env_path, 'r') as f:
        lines = f.readlines()

    updated = False
    with open(env_path, 'w') as f:
        for line in lines:
            if line.startswith(f'{key}='):
                f.write(f'{key}={value}\n')
                updated = True
            else:
                f.write(line)

    if not updated:
        f.write(f'{key}={value}\n')


def refresh_strava_token():
    """Automatically refresh Strava access token using refresh token."""
    try:
        refresh_token = os.getenv('STRAVA_REFRESH_TOKEN')
        client_id = os.getenv('STRAVA_CLIENT_ID')
        client_secret = os.getenv('STRAVA_CLIENT_SECRET')

        if not all([refresh_token, client_id, client_secret]):
            return None

        print("Access token expired. Refreshing using refresh token...")
        response = requests.post(
            'https://www.strava.com/oauth/token',
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'refresh_token': refresh_token,
                'grant_type': 'refresh_token'
            }
        )

        if response.status_code == 200:
            data = response.json()
            access_token = data['access_token']
            refresh_token_new = data['refresh_token']

            # Update .env file
            update_env_file('STRAVA_ACCESS_TOKEN', access_token)
            update_env_file('STRAVA_REFRESH_TOKEN', refresh_token_new)

            # Reload environment
            load_dotenv(override=True)

            print("Token refreshed successfully!")
            return access_token
        return None
    except Exception as e:
        print(f"Failed to refresh token: {e}")
        return None


def get_strava_activities(strava_token, after_date, before_date):
    """Fetch activities from Strava API."""
    url = "https://www.strava.com/api/v3/athlete/activities"
    params = {
        'after': int(after_date.timestamp()),
        'before': int(before_date.timestamp()),
        'per_page': 200
    }
    headers = {'Authorization': f'Bearer {strava_token}'}

    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 401:
        new_token = refresh_strava_token()
        if new_token:
            headers['Authorization'] = f'Bearer {new_token}'
            response = requests.get(url, headers=headers, params=params)
        else:
            raise requests.exceptions.HTTPError("Failed to refresh token", response=response)

    response.raise_for_status()
    return response.json()


def find_matching_strava_activity(intervals_activity, strava_activities):
    """Find matching Strava activity based on date and distance similarity."""
    intervals_date = intervals_activity.get('start_date_local', '')[:10]
    intervals_distance = intervals_activity.get('distance', 0)

    candidates = []
    for activity in strava_activities:
        if activity.get('type') == 'Run':
            strava_date = activity.get('start_date_local', '')[:10]
            strava_distance = activity.get('distance', 0)
            if strava_date == intervals_date:
                if intervals_distance > 0:
                    diff_pct = abs(strava_distance - intervals_distance) / intervals_distance * 100
                    if diff_pct < 10:
                        candidates.append((activity, diff_pct))

    if candidates:
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]

    return None


def get_strava_streams(activity_id, strava_token):
    """Fetch stream data for a Strava activity."""
    url = f"https://www.strava.com/api/v3/activities/{activity_id}/streams"
    params = {
        'keys': 'distance,time,latlng,heartrate,cadence',
        'key_by_type': 'true'
    }
    headers = {'Authorization': f'Bearer {strava_token}'}
    response = requests.get(url, headers=headers, params=params)

    if response.status_code == 401:
        print(" Strava access token expired (401 Unauthorized)")
        new_token = refresh_strava_token()
        if new_token:
            headers['Authorization'] = f'Bearer {new_token}'
            response = requests.get(url, headers=headers, params=params)
        else:
            raise requests.exceptions.HTTPError("Failed to refresh token", response=response)

    response.raise_for_status()
    return response.json()


def calculate_per_km_splits(act):
    """Calculate pace, HR, and cadence per 1km splits from activity streams."""
    streams = act.get('streams', {})

    time_data = streams.get('time', {}).get('data', [])
    distance_data = streams.get('distance', {}).get('data', [])
    hr_data = streams.get('heartrate', {}).get('data', [])
    cadence_data = streams.get('cadence', {}).get('data', [])

    if not time_data or not distance_data:
        return []

    splits = []
    current_km = 1
    start_index = 0
    start_distance = distance_data[0] if distance_data else 0

    for i in range(len(distance_data)):
        distance = distance_data[i] - start_distance

        if distance >= current_km * 1000 or i == len(distance_data) - 1:
            end_index = i

            split_time = time_data[end_index] - time_data[start_index]
            split_distance = distance_data[end_index] - distance_data[start_index]

            if split_time > 0 and split_distance > 0:
                pace_ms = split_distance / split_time

                hr_segment = hr_data[start_index:end_index+1] if hr_data else []
                avg_hr = sum(hr_segment) / len(hr_segment) if hr_segment else 0

                cadence_segment = cadence_data[start_index:end_index+1] if cadence_data else []
                avg_cadence = (sum(cadence_segment) / len(cadence_segment) * 2) if cadence_segment else 0

                splits.append({
                    'km': current_km,
                    'time': split_time,
                    'distance': split_distance / 1000,
                    'pace_ms': pace_ms,
                    'avg_hr': avg_hr,
                    'avg_cadence': avg_cadence
                })

            current_km += 1
            start_index = end_index

    return splits


def format_pace(meters_per_second):
    if meters_per_second <= 0: return "0:00"
    minutes_per_km = 16.666666666667 / meters_per_second
    mins = int(minutes_per_km)
    secs = int((minutes_per_km - mins) * 60)
    return f"{mins}:{secs:02d}"


def format_time(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))


def get_latest_run(athlete_id, api_key, per_km_splits=False, strava_token=None, zone_times=False, show_laps=False):
    url_activities = f"https://intervals.icu/api/v1/athlete/{athlete_id}/activities"

    now = datetime.date.today()
    oldest = (now - datetime.timedelta(days=90)).isoformat()
    newest = (now + datetime.timedelta(days=1)).isoformat()

    params = {
        'oldest': oldest,
        'newest': newest
    }

    response = requests.get(url_activities, auth=('API_KEY', api_key), params=params)

    if response.status_code in (401, 403):
        print("ERROR: Access Denied. Check your API key and Athlete ID.")
        sys.exit(1)

    response.raise_for_status()
    activities = response.json()

    if not activities:
        print(f"No activities found on the account between {oldest} and {newest}.")
        return

    activities.sort(key=lambda x: x.get('start_date_local', ''), reverse=True)
    run_activity = next((act for act in activities if act.get('type') == 'Run'), None)

    if not run_activity:
        print("No run found in the last 90 days.")
        return

    activity_id = run_activity.get('id')
    name = run_activity.get('name', 'No name')
    date_str = run_activity.get('start_date_local', '')[:10]
    distance_km = run_activity.get('distance', 0) / 1000
    moving_time_s = run_activity.get('moving_time', 0)
    elevation_gain = run_activity.get('total_elevation_gain', 0)
    avg_pace_ms = run_activity.get('average_speed', 0)

    avg_hr = run_activity.get('average_heartrate', 0)
    max_hr = run_activity.get('max_heartrate', 0)
    avg_cadence = run_activity.get('average_cadence', 0) * 2
    rpe = run_activity.get('rpe', 'None')
    description = run_activity.get('description', 'No comment')

    print(f"* **Workout:** {name} ({date_str})")
    print(f"* **Time & Distance:** ({format_time(moving_time_s)}, {distance_km:.2f} km)")
    print(f"* **Elevation Gain:** {elevation_gain:.0f} m")
    print(f"* **Average Pace:** ({format_pace(avg_pace_ms)}/km)")
    print(f"* **Average HR / Max:** {avg_hr:.0f}/{max_hr:.0f}")
    print(f"* **Average Cadence:** {avg_cadence:.0f} spm")
    print(f"* **RPE (Fatigue 1-10):** {rpe}")
    print(f"* **Comment:** {description}")
    print("")

    if zone_times:
        hr_zone_times = run_activity.get('icu_hr_zone_times') or run_activity.get('hr_zone_times') or []
        print(f"--- Heart Rate Zones ---")
        if hr_zone_times:
            total_hr_time = sum(hr_zone_times)
            for i, zt in enumerate(hr_zone_times):
                if zt > 0:
                    pct = (zt / total_hr_time) * 100 if total_hr_time else 0
                    print(f"Zone {i+1:<15} {format_time(zt):>8} {pct:>5.1f}%")
        else:
            print("No Heart Rate zone data found.")
        print("")
        print("")

    api_suffix = "?intervals=true"
    url_activity_details = f"https://intervals.icu/api/v1/activity/{activity_id}{api_suffix}"
    details_response = requests.get(url_activity_details, auth=('API_KEY', api_key))

    if details_response.status_code == 200:
        details = details_response.json()
        laps = details.get('laps',[])

        # Show 1km splits from laps if available (these are from the device)
        if laps:
            print("--- Laps (from device) ---")
            for i, lap in enumerate(laps):
                lap_dist = lap.get('distance', 0) / 1000
                lap_time = lap.get('moving_time', 0)
                lap_pace = format_pace(lap.get('average_speed', 0))
                lap_hr = lap.get('average_heartrate', 0)

                print(f"Lap {i+1:>2}: {lap_dist:>5.2f} km | {format_time(lap_time):>7} | {lap_pace:>5}/km | HR: {lap_hr:>3.0f}")
            print("")

        # Show workout intervals from Intervals.icu (Coros-style)
        if show_laps:
            icu_intervals = details.get('icu_intervals', [])
            if icu_intervals:
                print("--- Workout Intervals (from Intervals.icu) ---")
                print(f"{'Lap':>3} {'Type':<8} {'Distance':>8} {'Time':>8} {'Total':>8} {'Pace':>7} {'HR':>5} {'Cad':>5}")
                cumulative_time = 0
                lap_num = 1
                for interval in icu_intervals:
                    int_type = interval.get('type', 'WORK')
                    if int_type == 'RECOVERY':
                        int_type = 'Rest'
                    else:
                        int_type = 'Run'

                    dist = interval.get('distance', 0) or 0
                    dist_km = dist / 1000 if dist else 0
                    time_s = interval.get('moving_time', 0) or 0
                    cumulative_time += time_s

                    pace_ms = interval.get('average_speed', 0) or 0
                    pace_str = format_pace(pace_ms) if pace_ms > 0 else '--:--'

                    hr = interval.get('average_heartrate', 0) or 0
                    cad = (interval.get('average_cadence', 0) or 0) * 2

                    print(f"{lap_num:>3} {int_type:<8} {dist_km:>7.2f}km {format_time(time_s):>8} {format_time(cumulative_time):>8} {pace_str:>7} {hr:>5.0f} {cad:>5.0f}")
                    lap_num += 1
                print("")
            else:
                print("No workout intervals found in Intervals.icu data.")
                print("")

    if per_km_splits and strava_token:
        try:
            now = datetime.date.today()
            after = now - datetime.timedelta(days=7)
            before = now + datetime.timedelta(days=1)

            strava_acts = get_strava_activities(strava_token, datetime.datetime.combine(after, datetime.datetime.min.time()), datetime.datetime.combine(before, datetime.datetime.min.time()))

            matching_act = find_matching_strava_activity(run_activity, strava_acts)

            if not matching_act:
                print("No matching Strava activity found for splits")
            else:
                strava_id = matching_act.get('id')
                strava_id = matching_act.get('id')
                streams = get_strava_streams(strava_id, strava_token)
                if not streams:
                    print("No stream data available from Strava")
                else:
                    act_with_streams = {'streams': streams}
                    print("--- 1km Splits (from Strava) ---")
                    per_km = calculate_per_km_splits(act_with_streams)
                    if per_km:
                        for split in per_km:
                            print(f"KM {split['km']:<2} | {split['distance']:.2f} km | {format_time(split['time']):>7} | {format_pace(split['pace_ms'])}/km | HR: {split['avg_hr']:>3.0f} | Cad: {split['avg_cadence']:>3.0f}")
                    else:
                        print("Could not calculate splits from Strava data")

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                print("Strava authentication failed! Please check your access token.")
                print(" - Token may be expired (Strava tokens expire after 6 hours)")
                print(" - Token may be invalid")
                print(" - Get a new token from: https://www.strava.com/settings/api")
            else:
                print(f"HTTP Error from Strava: {e}")
        print("")


if __name__ == "__main__":
    ATHLETE_ID, API_KEY, PER_KM_SPLITS, STRAVA_TOKEN, zone_times, show_laps = get_credentials()
    get_latest_run(ATHLETE_ID, API_KEY, PER_KM_SPLITS, STRAVA_TOKEN, zone_times, show_laps)