"""
strava_sync.py
--------------
Strava sync for Running Journal. Multi-user: every user has their own
Run Settings record (holding their own Strava tokens, Garmin
credentials, and profile fields) and their own Strava OAuth connection
via the shared app-level Strava App Settings (client_id/secret) —
see strava_oauth.py for the actual "Connect with Strava" flow.

For each new activity:
  1. List endpoint    → activity metadata
  2. Detail endpoint  → real calories, gear, suffer_score
  3. Streams endpoint → full per-point data (all 11 keys, no cap)
  4. Computed fields  → VDOT, TRIMP, Keytel calories fallback

Analytics formulae:
  - Calories: Keytel et al. (2005) Heart-rate based if HR available
  - Calories fallback: MET-based (Pandolf et al. 1977 adapted)
  - VDOT: Jack Daniels & Gilbert (1979) performance-based VO2max proxy
  - TRIMP: Banister et al. (1991) training impulse
"""

import frappe
import requests
import json
import math
from datetime import datetime
from frappe.utils.password import get_decrypted_password, set_encrypted_password

SETTINGS = "Run Settings"
APP_SETTINGS = "Strava App Settings"

# The original site owner's data, shown to anyone visiting the public
# /run-journal page without logging in — preserves the pre-multi-user
# behaviour for the primary account. Any *other* logged-in user only
# ever sees their own data.
DEFAULT_PUBLIC_USER = "hrishi.p@azimpremjifoundation.org"


def current_user():
    """The user whose data should be read/written for this request —
    the logged-in user, or DEFAULT_PUBLIC_USER for anonymous viewers."""
    u = frappe.session.user
    return DEFAULT_PUBLIC_USER if u == "Guest" else u


def ensure_settings_doc(user=None):
    """Get-or-create this user's Run Settings record."""
    user = user or current_user()
    if frappe.db.exists(SETTINGS, user):
        return frappe.get_doc(SETTINGS, user)
    doc = frappe.get_doc({"doctype": SETTINGS, "user": user})
    doc.insert(ignore_permissions=True)
    return doc


# ── Settings helpers ──────────────────────────────────────────────────────────
def get_settings_value(field, user=None):
    user = user or current_user()
    return frappe.db.get_value(SETTINGS, user, field)


def compute_age(dob, activity_date):
    """Compute age at time of activity — not today's age."""
    if not dob:
        return 35
    if isinstance(activity_date, str):
        activity_date = datetime.strptime(activity_date[:10], "%Y-%m-%d").date()
    if hasattr(dob, 'year'):
        pass  # already a date
    else:
        dob = datetime.strptime(str(dob)[:10], "%Y-%m-%d").date()
    age = activity_date.year - dob.year
    if (activity_date.month, activity_date.day) < (dob.month, dob.day):
        age -= 1
    return max(1, age)


def get_analytics_settings(user=None):
    user = user or current_user()
    try:
        dob = get_settings_value("date_of_birth", user)
        return {
            "weight":     float(get_settings_value("weight_kg", user) or 70),
            "dob":        dob or "1975-04-20",
            "gender":     get_settings_value("gender", user) or "Male",
            "resting_hr": int(get_settings_value("resting_hr", user) or 50),
            "max_hr":     int(get_settings_value("max_hr_override", user) or 0),
        }
    except Exception:
        return {"weight": 70, "dob": "1975-04-20", "gender": "Male", "resting_hr": 50, "max_hr": 0}


# ── App-level Strava OAuth credentials (shared by every user) ────────────────
def strava_app_credentials():
    client_id = frappe.db.get_single_value(APP_SETTINGS, "client_id")
    client_secret = get_decrypted_password(APP_SETTINGS, APP_SETTINGS, "client_secret", raise_exception=False)
    return client_id, client_secret


# ── Auth ──────────────────────────────────────────────────────────────────────
def get_valid_access_token(user=None):
    user = user or current_user()
    client_id, client_secret = strava_app_credentials()
    access_token = get_decrypted_password(SETTINGS, user, "strava_access_token", raise_exception=False)
    test = requests.get(
        "https://www.strava.com/api/v3/athlete",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if test.status_code == 401:
        refresh_token = get_decrypted_password(SETTINGS, user, "strava_refresh_token", raise_exception=False)
        r = requests.post("https://www.strava.com/oauth/token", data={
            "client_id":     client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type":    "refresh_token"
        })
        tokens = r.json()
        set_encrypted_password(SETTINGS, user, tokens["access_token"], "strava_access_token")
        set_encrypted_password(SETTINGS, user, tokens["refresh_token"], "strava_refresh_token")
        frappe.db.commit()
        return tokens["access_token"]
    return access_token


# ── API fetchers ──────────────────────────────────────────────────────────────
def fetch_activities(per_page=50, page=1, user=None):
    token = get_valid_access_token(user)
    r = requests.get(
        "https://www.strava.com/api/v3/activities",
        headers={"Authorization": f"Bearer {token}"},
        params={"per_page": per_page, "page": page}
    )
    return r.json()

def fetch_activity_detail(activity_id, user=None):
    """Detail endpoint — returns real calories, gear, suffer_score, description."""
    token = get_valid_access_token(user)
    r = requests.get(
        f"https://www.strava.com/api/v3/activities/{activity_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    if r.status_code == 200:
        return r.json()
    return {}

def fetch_activity_streams(activity_id, user=None):
    """
    Fetch all available stream keys — no artificial point cap.
    Returns per-point data for the full activity.
    """
    token = get_valid_access_token(user)
    keys = "latlng,altitude,time,heartrate,cadence,watts,temp,grade_smooth,velocity_smooth,moving,distance"
    r = requests.get(
        f"https://www.strava.com/api/v3/activities/{activity_id}/streams",
        headers={"Authorization": f"Bearer {token}"},
        params={"keys": keys, "key_by_type": "true"}
    )
    return r.json() if r.status_code == 200 else {}


# ── Geocoding ─────────────────────────────────────────────────────────────────
def get_location_details(lat, lon):
    """Reverse-geocode to both a short display string and the structured
    country/state/district, in one Nominatim call. Raises on network/HTTP
    failure so callers can tell "the request failed, try again later" apart
    from "Nominatim genuinely has no address data for this point" — those
    aren't the same thing, and conflating them was silently mislabeling
    transient failures as permanent unknown-location runs."""
    r = requests.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={"lat": lat, "lon": lon, "format": "json", "accept-language": "en"},
        headers={"User-Agent": "RunningJournal/1.0"},
        timeout=5
    )
    r.raise_for_status()
    data = r.json()
    addr = data.get("address", {})
    parts = []
    for key in ["suburb", "neighbourhood", "city", "town", "village"]:
        if addr.get(key):
            parts.append(addr[key])
            break
    if addr.get("city") and addr["city"] not in parts:
        parts.append(addr["city"])
    display = ", ".join(parts) if parts else data.get("display_name", "")[:50]
    return {
        "display": display,
        "country": addr.get("country", ""),
        "state": addr.get("state", ""),
        "district": addr.get("state_district") or addr.get("county") or "",
    }


def get_location(lat, lon):
    try:
        return get_location_details(lat, lon)["display"]
    except Exception:
        return ""


def first_latlon(points):
    """First point in a route_points list that actually has GPS coordinates.
    The very first sample is often a GPS-not-locked-yet reading (just
    {"t":.., "hr":..}, no lat/lon) while later points do have a fix —
    blindly using points[0] silently treated plenty of real outdoor runs
    as having no location at all."""
    for p in points:
        if isinstance(p, dict) and p.get("lat") is not None and p.get("lon") is not None:
            return p["lat"], p["lon"]
    return None


def home_location(user):
    """Best-guess "home" for an activity with no GPS at all — a pool swim,
    treadmill run, or gym session. These aren't "nowhere", they're
    wherever the athlete actually lives; leaving them out of country/
    state/district meant they silently vanished from the location
    drill-down cards forever instead of counting toward the athlete's
    own country/state/district like every GPS-tagged run does. The most
    common already-geocoded district (not the most recent run) is the
    better guess — a one-off trip shouldn't get treadmill sessions
    wrongly tagged to it just because it happened to be the last GPS fix
    on file."""
    row = frappe.db.sql(
        """SELECT country, state, district, COUNT(*) AS c
           FROM `tabRun`
           WHERE user = %s AND country IS NOT NULL AND country != ''
           GROUP BY country, state, district
           ORDER BY c DESC LIMIT 1""",
        (user,), as_dict=True,
    )
    if not row:
        return None
    return {"country": row[0].country, "state": row[0].state, "district": row[0].district}


# ── Analytics formulae ────────────────────────────────────────────────────────
def compute_calories_keytel(avg_hr, duration_sec, weight, age, gender):
    """
    Keytel et al. (2005) J Sports Sci 23(3):289-97.
    HR-based calorie estimation — accounts for effort intensity.
    """
    if not avg_hr or avg_hr <= 0:
        return 0, "formula_no_hr"
    mins = duration_sec / 60.0
    if gender == "Male":
        kcal_per_min = (-55.0969 + 0.6309 * avg_hr + 0.1988 * weight + 0.2017 * age) / 4.184
    else:
        kcal_per_min = (-20.4022 + 0.4472 * avg_hr + 0.1263 * weight - 0.0740 * age) / 4.184
    return max(0, round(kcal_per_min * mins)), "keytel_2005"

def compute_calories_met(distance_km, duration_sec, avg_grade, weight, activity_type):
    """
    MET-based fallback. Pandolf et al. (1977) adapted.
    Used when HR is unavailable.
    """
    if duration_sec <= 0:
        return 0, "formula_zero_duration"
    hours = duration_sec / 3600.0
    speed_ms = (distance_km * 1000 / duration_sec) if duration_sec > 0 else 0
    if activity_type == "Swimming":
        met = 8.0
    elif activity_type == "Cycling":
        met = max(1.0, 6.0 + speed_ms * 0.5)
    else:
        grade_frac = (avg_grade or 0) / 100.0
        met = max(1.0, 1.0 + speed_ms * 0.2 + grade_frac * speed_ms * 0.9)
    return max(0, round(met * weight * hours)), "met_pandolf"

def compute_vdot(distance_km, duration_sec):
    """
    Jack Daniels & Gilbert (1979) VDOT formula.
    Performance-based aerobic capacity proxy.
    Only computed for runs >= 1km, >= 4 minutes.
    """
    if distance_km < 1.0 or duration_sec < 240:
        return None
    velocity  = (distance_km * 1000) / (duration_sec / 60.0)
    t         = duration_sec / 60.0
    vo2       = -4.60 + 0.182258 * velocity + 0.000104 * velocity ** 2
    pct_vo2max = 0.8 + 0.1894393 * math.exp(-0.012778 * t) + 0.2989558 * math.exp(-0.1932605 * t)
    if pct_vo2max <= 0:
        return None
    vdot = vo2 / pct_vo2max
    return round(vdot, 1) if vdot > 0 else None

def compute_trimp(avg_hr, duration_sec, resting_hr, max_hr, gender):
    """
    Banister et al. (1991) TRIMP — Training Impulse.
    Weights duration by HR intensity.
    """
    if not avg_hr or not resting_hr or not max_hr:
        return None
    if max_hr <= resting_hr:
        return None
    hr_ratio = (avg_hr - resting_hr) / (max_hr - resting_hr)
    if hr_ratio <= 0 or hr_ratio > 1:
        return None
    mins = duration_sec / 60.0
    if gender == "Male":
        trimp = mins * hr_ratio * 0.64 * math.exp(1.92 * hr_ratio)
    else:
        trimp = mins * hr_ratio * 0.86 * math.exp(1.67 * hr_ratio)
    return round(trimp, 1)


# ── Route points builder ──────────────────────────────────────────────────────
def build_route_points(streams):
    """
    Build route_points list from Strava streams.
    No point cap — store all points Strava returns.
    Each point: {lat, lon, ele, t, hr, spd, dst, cad, pwr, tmp, grd, mov}
    """
    if not streams or "latlng" not in streams:
        return []

    latlng   = streams.get("latlng",          {}).get("data", [])
    alt      = streams.get("altitude",         {}).get("data", [])
    time_s   = streams.get("time",             {}).get("data", [])
    hr       = streams.get("heartrate",        {}).get("data", [])
    cadence  = streams.get("cadence",          {}).get("data", [])
    watts    = streams.get("watts",            {}).get("data", [])
    temp     = streams.get("temp",             {}).get("data", [])
    grade    = streams.get("grade_smooth",     {}).get("data", [])
    velocity = streams.get("velocity_smooth",  {}).get("data", [])
    moving   = streams.get("moving",           {}).get("data", [])
    distance = streams.get("distance",         {}).get("data", [])

    route_points = []
    for i in range(len(latlng)):
        pt = {
            "lat": latlng[i][0],
            "lon": latlng[i][1],
        }
        if i < len(alt)      and alt[i]      is not None: pt["ele"] = round(alt[i], 1)
        if i < len(time_s)   and time_s[i]   is not None: pt["t"]   = time_s[i]
        if i < len(hr)       and hr[i]        is not None: pt["hr"]  = int(hr[i])
        if i < len(velocity) and velocity[i]  is not None: pt["spd"] = round(velocity[i], 3)
        if i < len(distance) and distance[i]  is not None: pt["dst"] = round(distance[i], 1)
        if i < len(cadence)  and cadence[i]   is not None: pt["cad"] = int(cadence[i])
        if i < len(watts)    and watts[i]     is not None: pt["pwr"] = int(watts[i])
        if i < len(temp)     and temp[i]      is not None: pt["tmp"] = temp[i]
        if i < len(grade)    and grade[i]     is not None: pt["grd"] = round(grade[i], 2)
        if i < len(moving)   and moving[i]    is not None: pt["mov"] = 1 if moving[i] else 0
        route_points.append(pt)

    return route_points


# ── Activity type map ─────────────────────────────────────────────────────────
TYPE_MAP = {
    "Run": "Run", "TrailRun": "Run", "VirtualRun": "Run",
    "Swim": "Swimming", "Swimming": "Swimming",
    "Ride": "Cycling", "VirtualRide": "Cycling",
    "Walk": "Walk", "Hike": "Walk",
}


# ── Main activity builder ─────────────────────────────────────────────────────
def activity_to_run(activity, detail, streams, settings, user=None):
    """
    Build a Run doc dict from list + detail + streams data.
    detail overrides list for calories.
    FIT session data not available here — streams is our best source.
    """
    user = user or current_user()
    activity_type = TYPE_MAP.get(activity.get("type", ""), "Run")
    distance_km   = round((activity.get("distance", 0) or 0) / 1000, 3)
    duration_sec  = activity.get("moving_time", 0) or 0
    elev_gain     = round(activity.get("total_elevation_gain", 0) or 0)
    start_date    = activity.get("start_date_local", "")[:10]
    run_name      = activity.get("name", f"{activity_type} {start_date}")

    avg_hr    = round(activity.get("average_heartrate", 0) or 0)
    max_hr_val= round(activity.get("max_heartrate", 0) or 0)
    avg_speed = activity.get("average_speed", 0) or 0
    max_speed = activity.get("max_speed", 0) or 0

    # Location
    location = ""
    # None (not "" defaults) so a transient geocode failure leaves
    # country/state/district NULL on insert — picked up and retried by
    # location_summary.backfill_geo_fields() — instead of permanently
    # marking this run as having no location data.
    geo = None
    start_latlng = activity.get("start_latlng", [])
    if start_latlng and len(start_latlng) == 2:
        try:
            details = get_location_details(start_latlng[0], start_latlng[1])
            location = details["display"]
            geo = details
        except Exception:
            pass
    else:
        # No GPS at all (pool swim, treadmill, gym) — tag with the
        # athlete's own most-common location instead of leaving this run
        # out of the country/state/district drill-down entirely.
        geo = home_location(user)

    # Calories — detail endpoint first, then Keytel, then MET
    calories       = int(detail.get("calories", 0) or 0)
    calorie_source = "strava_detail" if calories > 0 else ""
    if not calories and avg_hr > 0:
        age_at_activity = compute_age(settings['dob'], start_date)
        calories, calorie_source = compute_calories_keytel(
            avg_hr, duration_sec, settings['weight'], age_at_activity, settings['gender']
        )
    if not calories:
        calories, calorie_source = compute_calories_met(
            distance_km, duration_sec, 0, settings['weight'], activity_type
        )

    # Additional detail fields
    description  = detail.get("description", "") or ""
    suffer_score = detail.get("suffer_score", 0) or 0
    gear_name    = (detail.get("gear") or {}).get("name", "") or ""

    # Computed analytics
    vdot = compute_vdot(distance_km, duration_sec) if activity_type == "Run" else None
    effective_max_hr = settings['max_hr'] or max_hr_val or 0
    trimp = compute_trimp(avg_hr, duration_sec, settings['resting_hr'], effective_max_hr, settings['gender'])

    # Route points from streams
    route_points = build_route_points(streams)

    run_doc = {
        "doctype":            "Run",
        "user":               user,
        "run_name":           run_name,
        "date":               start_date,
        "activity_type":      activity_type,
        "location":           location,
        "distance_km":        distance_km,
        "duration_sec":       duration_sec,
        "elevation_gain":     elev_gain,
        "calories":           calories,
        "calorie_source":     calorie_source,
        "avg_heart_rate":     avg_hr,
        "max_heart_rate":     max_hr_val,
        "avg_speed":          avg_speed,
        "max_speed":          max_speed,
        "relative_effort":    suffer_score,
        "strava_id":          str(activity.get("id", "")),
        "route_points":       json.dumps(route_points) if route_points else "",
    }

    if description:  run_doc["notes"] = description
    if gear_name:    run_doc["gear"]  = gear_name
    if vdot:         run_doc["vdot"]  = vdot
    if trimp:        run_doc["trimp"] = trimp
    # Left out entirely (not set to "") on a failed geocode, so the field
    # stays NULL and location_summary.backfill_geo_fields() retries it
    # later instead of this run being permanently marked as unknown.
    if geo is not None:
        run_doc["country"] = geo["country"]
        run_doc["state"] = geo["state"]
        run_doc["district"] = geo["district"]

    return run_doc


# ── Sync ──────────────────────────────────────────────────────────────────────
@frappe.whitelist()
def sync_strava(full_sync=False):
    user = current_user()
    settings = get_analytics_settings(user)
    imported = 0
    skipped  = 0
    page     = 1

    while True:
        activities = fetch_activities(per_page=50, page=page, user=user)
        if not activities or not isinstance(activities, list):
            break

        for activity in activities:
            strava_id = str(activity.get("id", ""))

            if frappe.db.exists("Run", {"strava_id": strava_id, "user": user}):
                skipped += 1
                continue

            if activity.get("type") not in TYPE_MAP:
                skipped += 1
                continue

            # Fetch detail and streams for every new activity
            detail  = fetch_activity_detail(activity["id"], user=user)
            streams = {}
            if activity.get("start_latlng"):
                streams = fetch_activity_streams(activity["id"], user=user)

            run_data = activity_to_run(activity, detail, streams, settings, user=user)
            run = frappe.get_doc(run_data)
            run.insert(ignore_permissions=True)
            imported += 1

        if len(activities) < 50:
            break
        page += 1

    frappe.db.set_value(SETTINGS, user, "strava_last_sync", datetime.now())
    frappe.db.commit()
    return {"imported": imported, "skipped": skipped}


@frappe.whitelist(allow_guest=True)
def sync_strava_public():
    user = frappe.session.user
    if user == 'Guest':
        frappe.throw('Login required', frappe.AuthenticationError)
    return sync_strava(full_sync=False)
