"""
garmin_sync.py
---------------
Garmin Connect sync for Running Journal, via the unofficial `garminconnect`
library — Garmin has no self-serve public API for personal/hobby use, so
this authenticates with the athlete's own Garmin email/password (mobile SSO
flow) and caches session tokens on the shared sites volume so a re-login
(and any MFA prompt) is only needed once, not on every sync.

For each new activity:
  1. Activity list → summary metadata (distance, duration, HR, calories)
  2. TCX download  → per-point GPS/route data
  3. Computed fields → VDOT, TRIMP, Keytel calories fallback (shared with Strava)

Cross-source dedup: an activity is skipped if a Run already exists with the
same garmin_id, OR with the same date + activity_type and distance/duration
within tolerance — this guards against re-importing a run that was already
synced via Strava (or manually backfilled) under a different source ID.
"""

import os
import json
import gzip
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import frappe
from frappe.utils.password import get_decrypted_password

from runningapp.running_journal.strava_sync import (
    get_analytics_settings,
    compute_age,
    get_location,
    compute_calories_keytel,
    compute_calories_met,
    compute_vdot,
    compute_trimp,
)

SETTINGS = "Run Settings"
TCX_NS = "{http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2}"
MAX_POINTS = 500
PAGE_SIZE = 20

# A first-time backfill can have hundreds of activities, each needing a TCX
# download + reverse-geocode call — that can easily blow past gunicorn's
# request timeout (120s) and nginx's proxy_read_timeout (120s) if run in one
# shot. So a single sync call does at most this much work and returns; the
# caller (button click, or a resync) picks up where it left off, since
# already-imported activities are skipped by garmin_id on the next call.
MAX_IMPORTS_PER_CALL = 15
TIME_BUDGET_SEC = 90

GARMIN_TYPE_MAP = {
    "running": "Run", "trail_running": "Run", "track_running": "Run",
    "treadmill_running": "Run", "virtual_run": "Run", "street_running": "Run",
    "swimming": "Swimming", "lap_swimming": "Swimming", "open_water_swimming": "Swimming",
    "cycling": "Cycling", "road_biking": "Cycling", "mountain_biking": "Cycling",
    "indoor_cycling": "Cycling", "virtual_ride": "Cycling", "gravel_cycling": "Cycling",
    "walking": "Walk", "hiking": "Walk", "casual_walking": "Walk",
}


# ── Auth ───────────────────────────────────────────────────────────────────
def _token_dir():
    path = frappe.get_site_path("private", "files", "garmin_tokens")
    os.makedirs(path, exist_ok=True)
    return path


def get_garmin_client():
    from garminconnect import Garmin

    email = frappe.db.get_single_value(SETTINGS, "garmin_email")
    password = get_decrypted_password(SETTINGS, SETTINGS, "garmin_password", raise_exception=False)

    client = Garmin(email=email, password=password)
    client.login(tokenstore=_token_dir())
    return client


# ── TCX route parsing (shared shape with backfill_tcx.py) ───────────────────
def _parse_tcx_points(tcx_bytes):
    try:
        data = tcx_bytes
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        xml_start = data.find(b"<?xml")
        if xml_start > 0:
            data = data[xml_start:]
        root = ET.fromstring(data)
    except Exception:
        return []

    trackpoints = root.findall(f".//{TCX_NS}Trackpoint")
    pts = []
    t0 = None
    for tp in trackpoints:
        time_el = tp.find(f"{TCX_NS}Time")
        pos_el = tp.find(f"{TCX_NS}Position")
        if time_el is None or pos_el is None:
            continue
        lat_el = pos_el.find(f"{TCX_NS}LatitudeDegrees")
        lon_el = pos_el.find(f"{TCX_NS}LongitudeDegrees")
        if lat_el is None or lon_el is None:
            continue
        try:
            t = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
            if t0 is None:
                t0 = t
            elapsed = round((t - t0).total_seconds())
            lat = float(lat_el.text)
            lon = float(lon_el.text)
        except Exception:
            continue

        pt = {"lat": lat, "lon": lon, "t": elapsed}
        alt_el = tp.find(f"{TCX_NS}AltitudeMeters")
        if alt_el is not None:
            try:
                pt["ele"] = round(float(alt_el.text), 1)
            except Exception:
                pass
        hr_el = tp.find(f".//{TCX_NS}Value")  # HeartRateBpm/Value
        if hr_el is not None:
            try:
                pt["hr"] = int(hr_el.text)
            except Exception:
                pass
        pts.append(pt)

    if len(pts) > MAX_POINTS:
        step = len(pts) // MAX_POINTS
        pts = pts[::step][:MAX_POINTS]
    return pts


def fetch_activity_route(client, activity_id):
    from garminconnect import Garmin

    try:
        tcx_bytes = client.download_activity(
            activity_id, dl_fmt=Garmin.ActivityDownloadFormat.TCX
        )
    except Exception:
        return []
    return _parse_tcx_points(tcx_bytes)


# ── Cross-source dedup ───────────────────────────────────────────────────────
def _is_duplicate(activity_date, activity_type, distance_km, duration_sec):
    candidates = frappe.get_all(
        "Run",
        filters={"date": activity_date, "activity_type": activity_type},
        fields=["name", "distance_km", "duration_sec"],
    )
    for c in candidates:
        dist_ok = abs((c.distance_km or 0) - distance_km) <= max(0.05, distance_km * 0.03)
        dur_ok = abs((c.duration_sec or 0) - duration_sec) <= max(30, duration_sec * 0.05)
        if dist_ok and dur_ok:
            return True
    return False


# ── Main activity builder ───────────────────────────────────────────────────
def activity_to_run(activity, points, settings):
    type_key = (activity.get("activityType") or {}).get("typeKey", "")
    activity_type = GARMIN_TYPE_MAP.get(type_key, "Run")
    distance_km = round((activity.get("distance", 0) or 0) / 1000, 3)
    duration_sec = round(activity.get("duration", 0) or 0)
    elev_gain = round(activity.get("elevationGain", 0) or 0)
    start_date = (activity.get("startTimeLocal", "") or "")[:10]
    run_name = activity.get("activityName") or f"{activity_type} {start_date}"

    avg_hr = round(activity.get("averageHR", 0) or 0)
    max_hr_val = round(activity.get("maxHR", 0) or 0)

    location = ""
    if points:
        location = get_location(points[0]["lat"], points[0]["lon"])

    calories = int(activity.get("calories", 0) or 0)
    calorie_source = "garmin" if calories > 0 else ""
    if not calories and avg_hr > 0:
        age_at_activity = compute_age(settings["dob"], start_date)
        calories, calorie_source = compute_calories_keytel(
            avg_hr, duration_sec, settings["weight"], age_at_activity, settings["gender"]
        )
    if not calories:
        calories, calorie_source = compute_calories_met(
            distance_km, duration_sec, 0, settings["weight"], activity_type
        )

    vdot = compute_vdot(distance_km, duration_sec) if activity_type == "Run" else None
    effective_max_hr = settings["max_hr"] or max_hr_val or 0
    trimp = compute_trimp(avg_hr, duration_sec, settings["resting_hr"], effective_max_hr, settings["gender"])

    run_doc = {
        "doctype": "Run",
        "run_name": run_name,
        "date": start_date,
        "activity_type": activity_type,
        "location": location,
        "distance_km": distance_km,
        "duration_sec": duration_sec,
        "elevation_gain": elev_gain,
        "calories": calories,
        "calorie_source": calorie_source,
        "avg_heart_rate": avg_hr,
        "max_heart_rate": max_hr_val,
        "garmin_id": str(activity.get("activityId", "")),
        "route_points": json.dumps(points) if points else "",
    }
    if vdot:
        run_doc["vdot"] = vdot
    if trimp:
        run_doc["trimp"] = trimp
    return run_doc


# ── Sync ─────────────────────────────────────────────────────────────────────
CURSOR_FIELD = "garmin_sync_cursor"


@frappe.whitelist()
def sync_garmin(full_sync=False):
    client = get_garmin_client()
    settings = get_analytics_settings()
    imported = 0
    skipped = 0
    # Resume from where the last (possibly time-boxed) call left off, instead
    # of rescanning the whole history from the most recent activity every
    # time — on a large backlog that rescan cost grows every call and starts
    # eating the entire time budget before reaching any new activities.
    start = int(frappe.db.get_single_value(SETTINGS, CURSOR_FIELD) or 0)
    t_start = time.monotonic()
    more_pending = False

    while True:
        if imported >= MAX_IMPORTS_PER_CALL or (time.monotonic() - t_start) > TIME_BUDGET_SEC:
            more_pending = True
            break

        activities = client.get_activities(start, PAGE_SIZE)
        if not activities or not isinstance(activities, list):
            break

        for activity in activities:
            if imported >= MAX_IMPORTS_PER_CALL or (time.monotonic() - t_start) > TIME_BUDGET_SEC:
                more_pending = True
                break

            garmin_id = str(activity.get("activityId", ""))
            if not garmin_id or frappe.db.exists("Run", {"garmin_id": garmin_id}):
                skipped += 1
                continue

            type_key = (activity.get("activityType") or {}).get("typeKey", "")
            if type_key not in GARMIN_TYPE_MAP:
                skipped += 1
                continue

            activity_type = GARMIN_TYPE_MAP[type_key]
            distance_km = round((activity.get("distance", 0) or 0) / 1000, 3)
            duration_sec = round(activity.get("duration", 0) or 0)
            start_date = (activity.get("startTimeLocal", "") or "")[:10]

            if _is_duplicate(start_date, activity_type, distance_km, duration_sec):
                skipped += 1
                continue

            points = fetch_activity_route(client, garmin_id)
            if not points:
                # No route data available for this activity — skip rather
                # than insert a run with a blank map.
                skipped += 1
                continue

            run_data = activity_to_run(activity, points, settings)
            run = frappe.get_doc(run_data)
            run.insert(ignore_permissions=True)
            imported += 1
            # Commit after every insert: a single sync call can be cut off
            # by a request timeout on a large backlog, and this ensures
            # nothing already imported is lost when that happens.
            frappe.db.commit()

        if more_pending or len(activities) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    if more_pending:
        # Save the resume point for the next call.
        frappe.db.set_single_value(SETTINGS, CURSOR_FIELD, start)
    else:
        # Reached the end of Garmin's history — reset so the next sync
        # (periodic, catching new activities) starts from the most recent
        # activity again instead of resuming from the tail forever.
        frappe.db.set_single_value(SETTINGS, CURSOR_FIELD, 0)
        frappe.db.set_single_value(SETTINGS, "garmin_last_sync", datetime.now())
    frappe.db.commit()
    return {"imported": imported, "skipped": skipped, "more_pending": more_pending}


@frappe.whitelist(allow_guest=True)
def sync_garmin_public():
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Login required", frappe.AuthenticationError)
    return sync_garmin(full_sync=False)
