import frappe
import requests
import json
from datetime import datetime
from frappe.utils.password import get_decrypted_password, set_encrypted_password

SETTINGS = "Run Settings"


def get_settings_value(field):
    return frappe.db.get_single_value(SETTINGS, field)


def get_valid_access_token():
    client_id = get_settings_value("strava_client_id")
    access_token = get_decrypted_password(SETTINGS, SETTINGS, "strava_access_token")
    test = requests.get(
        "https://www.strava.com/api/v3/athlete",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if test.status_code == 401:
        client_secret = get_decrypted_password(SETTINGS, SETTINGS, "strava_client_secret")
        refresh_token = get_decrypted_password(SETTINGS, SETTINGS, "strava_refresh_token")
        r = requests.post("https://www.strava.com/oauth/token", data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        })
        tokens = r.json()
        set_encrypted_password(SETTINGS, SETTINGS, tokens["access_token"], "strava_access_token")
        set_encrypted_password(SETTINGS, SETTINGS, tokens["refresh_token"], "strava_refresh_token")
        frappe.db.commit()
        return tokens["access_token"]
    return access_token


def fetch_activities(per_page=50, page=1, after=None):
    token = get_valid_access_token()
    params = {"per_page": per_page, "page": page}
    if after:
        params["after"] = int(after.timestamp()) if isinstance(after, datetime) else after
    r = requests.get(
        "https://www.strava.com/api/v3/activities",
        headers={"Authorization": f"Bearer {token}"},
        params=params
    )
    return r.json()


def fetch_activity_streams(activity_id):
    token = get_valid_access_token()
    r = requests.get(
        f"https://www.strava.com/api/v3/activities/{activity_id}/streams",
        headers={"Authorization": f"Bearer {token}"},
        params={"keys": "latlng,altitude,time", "key_by_type": "true"}
    )
    return r.json()


def get_location(lat, lon):
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": "RunningJournal/1.0"},
            timeout=5
        )
        data = r.json()
        parts = []
        addr = data.get("address", {})
        for key in ["suburb", "neighbourhood", "city", "town", "village"]:
            if addr.get(key):
                parts.append(addr[key])
                break
        if addr.get("city") and addr["city"] not in parts:
            parts.append(addr["city"])
        return ", ".join(parts) if parts else data.get("display_name", "")[:50]
    except:
        return ""


def calculate_calories(distance_km, duration_sec, activity_type):
    try:
        weight = get_settings_value("weight_kg") or 70
        age = get_settings_value("age") or 35
        gender = get_settings_value("gender") or "Male"
        age_factor = 1.0 - (max(0, age - 30) * 0.005)
        gender_factor = 1.0 if gender == "Male" else 0.9
        if activity_type == "Swimming":
            hours = duration_sec / 3600
            calories = 8.0 * weight * hours * age_factor * gender_factor
        else:
            calories = weight * distance_km * 1.036 * age_factor * gender_factor
        return round(calories)
    except:
        return 0


def activity_to_run(activity, streams=None):
    type_map = {
        "Run": "Run", "TrailRun": "Run", "VirtualRun": "Run",
        "Swim": "Swimming", "Ride": "Cycling", "VirtualRide": "Cycling",
        "Walk": "Walk"
    }
    activity_type = type_map.get(activity.get("type", ""), "Run")
    distance_km = round((activity.get("distance", 0) or 0) / 1000, 3)
    duration_sec = activity.get("moving_time", 0) or 0
    elevation_gain = round(activity.get("total_elevation_gain", 0) or 0)
    start_date = activity.get("start_date_local", "")[:10]
    run_name = activity.get("name", f"{activity_type} {start_date}")
    location = ""
    start_latlng = activity.get("start_latlng", [])
    if start_latlng and len(start_latlng) == 2:
        location = get_location(start_latlng[0], start_latlng[1])
    calories = activity.get("calories", 0) or 0
    if not calories:
        calories = calculate_calories(distance_km, duration_sec, activity_type)
    avg_heart_rate = round(activity.get("average_heartrate", 0) or 0)
    max_heart_rate = round(activity.get("max_heartrate", 0) or 0)
       
    route_points = []
    if streams and "latlng" in streams:
        latlng_data = streams["latlng"].get("data", [])
        alt_data = streams.get("altitude", {}).get("data", [])
        step = max(1, len(latlng_data) // 500)
        for i in range(0, len(latlng_data), step):
            pt = {"lat": latlng_data[i][0], "lon": latlng_data[i][1]}
            if i < len(alt_data):
                pt["ele"] = alt_data[i]
            route_points.append(pt)
    return {
        "doctype": "Run",
        "run_name": run_name,
        "date": start_date,
        "activity_type": activity_type,
        "location": location,
        "distance_km": distance_km,
        "duration_sec": duration_sec,
        "elevation_gain": elevation_gain,
        "calories": calories,
        "avg_heart_rate": avg_heart_rate,
        "max_heart_rate": max_heart_rate,
        "route_points": json.dumps(route_points) if route_points else "",
        "strava_id": str(activity.get("id", ""))
    }


@frappe.whitelist()
def sync_strava(full_sync=False):
    last_sync = get_settings_value("strava_last_sync")
    after = None
    if not full_sync and last_sync:
        after = datetime.strptime(str(last_sync)[:19], "%Y-%m-%d %H:%M:%S")
    imported = 0
    skipped = 0
    page = 1
    while True:
        activities = fetch_activities(per_page=50, page=page, after=after)
        if not activities or not isinstance(activities, list):
            break
        for activity in activities:
            strava_id = str(activity.get("id", ""))
            if frappe.db.exists("Run", {"strava_id": strava_id}):
                skipped += 1
                continue
            if activity.get("type") not in ["Run", "TrailRun", "VirtualRun", "Swim", "Ride", "VirtualRide", "Walk"]:
                skipped += 1
                continue
            streams = {}
            if activity.get("start_latlng"):
                streams = fetch_activity_streams(activity["id"])
            run_data = activity_to_run(activity, streams)
            run = frappe.get_doc(run_data)
            run.insert(ignore_permissions=True)
            imported += 1
        if len(activities) < 50:
            break
        page += 1
    frappe.db.set_single_value(SETTINGS, "strava_last_sync", datetime.now())
    frappe.db.commit()
    return {"imported": imported, "skipped": skipped}