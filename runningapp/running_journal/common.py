"""
common.py
---------
Shared helpers for the Running Journal app (used by Garmin sync, bulk import,
location summary and the Run API):

  - current user / per-user Run Settings
  - analytics: age at activity, Keytel / MET calories, VDOT, TRIMP
    (formulae + citations in ANALYTICS.md)
  - reverse geocoding (Nominatim) and home-location fallback

Formerly lived in strava_sync.py; the Strava API integration was removed on
2026-11-02 (Strava made API access subscriber-only).
"""

import math
from datetime import datetime

import frappe
import requests

SETTINGS = "Run Settings"

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
