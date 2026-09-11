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
import re
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
    get_location_details,
    first_latlon,
    compute_calories_keytel,
    compute_calories_met,
    compute_vdot,
    compute_trimp,
    current_user,
    ensure_settings_doc,
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
def _token_dir(user):
    # Per-user: two different Garmin accounts must never share a cached
    # session, or one user's sync would silently start acting as the other.
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", user)
    path = frappe.get_site_path("private", "files", "garmin_tokens", safe)
    os.makedirs(path, exist_ok=True)
    return path


def get_garmin_client(user=None):
    from garminconnect import Garmin

    user = user or current_user()
    email = frappe.db.get_value(SETTINGS, user, "garmin_email")
    password = get_decrypted_password(SETTINGS, user, "garmin_password", raise_exception=False)

    client = Garmin(email=email, password=password)
    client.login(tokenstore=_token_dir(user))
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
        # Even sampling across the whole range, always including the last
        # point. `pts[::step][:MAX_POINTS]` looks equivalent but isn't: for
        # any N where N // MAX_POINTS == 1 (e.g. 501-999 points), step=1
        # makes pts[::1] a no-op and [:500] just truncates to the first
        # half of the recording instead of sampling the whole thing.
        n = len(pts) - 1
        pts = [pts[round(i * n / (MAX_POINTS - 1))] for i in range(MAX_POINTS)]
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
def _is_duplicate(activity_date, activity_type, distance_km, duration_sec, user):
    candidates = frappe.get_all(
        "Run",
        filters={"date": activity_date, "activity_type": activity_type, "user": user},
        fields=["name", "distance_km", "duration_sec"],
    )
    for c in candidates:
        dist_diff = abs((c.distance_km or 0) - distance_km)
        # Distance alone is enough when it's a near-exact match: pool
        # swims especially are lap-counted, so distance is essentially
        # identical across sources, while duration commonly isn't — one
        # pipeline counts rest-between-sets, another doesn't. A tight
        # duration tolerance on top of a loose distance one (the original
        # rule) missed same-day swims that matched to 3 decimal places on
        # distance but differed 10-50% on duration.
        if distance_km > 0 and dist_diff <= max(0.02, distance_km * 0.01):
            return True
        dist_ok = dist_diff <= max(0.05, distance_km * 0.03)
        dur_ok = abs((c.duration_sec or 0) - duration_sec) <= max(30, duration_sec * 0.05)
        if dist_ok and dur_ok:
            return True
    return False


# ── Main activity builder ───────────────────────────────────────────────────
def activity_to_run(activity, points, settings, user):
    type_key = (activity.get("activityType") or {}).get("typeKey", "")
    activity_type = GARMIN_TYPE_MAP.get(type_key, "Run")
    distance_km = round((activity.get("distance", 0) or 0) / 1000, 3)
    duration_sec = round(activity.get("duration", 0) or 0)
    elev_gain = round(activity.get("elevationGain", 0) or 0)
    start_date = (activity.get("startTimeLocal", "") or "")[:10]
    run_name = activity.get("activityName") or f"{activity_type} {start_date}"

    avg_hr = round(activity.get("avgHR", 0) or 0)
    max_hr_val = round(activity.get("maxHR", 0) or 0)
    if (not avg_hr or not max_hr_val) and points:
        # Fall back to deriving HR from the route points themselves — the
        # TCX per-point data has proven reliable even when a summary field
        # name turns out to be wrong or missing for a given activity type.
        hr_vals = [p["hr"] for p in points if p.get("hr")]
        if hr_vals:
            if not avg_hr:
                avg_hr = round(sum(hr_vals) / len(hr_vals))
            if not max_hr_val:
                max_hr_val = max(hr_vals)

    location = ""
    # None (not "" defaults) so a transient geocode failure — vs. genuinely
    # no GPS points, or Nominatim successfully returning no country for a
    # real point — leaves country/state/district NULL on insert instead of
    # permanently marking this run as having no location data. NULL rows
    # get picked up and retried by location_summary.backfill_geo_fields().
    geo = None
    ll = first_latlon(points) if points else None
    if ll:
        try:
            details = get_location_details(ll[0], ll[1])
            location = details["display"]
            geo = details
        except Exception:
            pass

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
        "user": user,
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
        # Freshly synced with the corrected even-sampling downsampler —
        # no need for rebackfill_routes() to redo this one.
        "route_points_reflowed": 1,
    }
    if vdot:
        run_doc["vdot"] = vdot
    if trimp:
        run_doc["trimp"] = trimp
    # Left out entirely (not set to "") on a failed geocode, so the field
    # stays NULL and location_summary.backfill_geo_fields() retries it
    # later instead of this run being permanently marked as unknown.
    if geo is not None:
        run_doc["country"] = geo["country"]
        run_doc["state"] = geo["state"]
        run_doc["district"] = geo["district"]
    return run_doc


# ── Sync ─────────────────────────────────────────────────────────────────────
CURSOR_FIELD = "garmin_sync_cursor"


@frappe.whitelist()
def sync_garmin(full_sync=False):
    user = current_user()
    ensure_settings_doc(user)
    client = get_garmin_client(user)
    settings = get_analytics_settings(user)
    imported = 0
    skipped = 0
    # Resume from where the last (possibly time-boxed) call left off, instead
    # of rescanning the whole history from the most recent activity every
    # time — on a large backlog that rescan cost grows every call and starts
    # eating the entire time budget before reaching any new activities.
    start = int(frappe.db.get_value(SETTINGS, user, CURSOR_FIELD) or 0)
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
            if not garmin_id or frappe.db.exists("Run", {"garmin_id": garmin_id, "user": user}):
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

            if _is_duplicate(start_date, activity_type, distance_km, duration_sec, user):
                skipped += 1
                continue

            # No "skip if no GPS" here — pool swims, treadmill, and gym
            # activities never have a route at all, and skipping them
            # meant they were never imported, period, not just missing
            # their map. run-detail already handles empty route_points
            # by simply not showing a map.
            points = fetch_activity_route(client, garmin_id)

            run_data = activity_to_run(activity, points, settings, user)
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
        frappe.db.set_value(SETTINGS, user, CURSOR_FIELD, start)
    else:
        # Reached the end of Garmin's history — reset so the next sync
        # (periodic, catching new activities) starts from the most recent
        # activity again instead of resuming from the tail forever.
        frappe.db.set_value(SETTINGS, user, CURSOR_FIELD, 0)
        frappe.db.set_value(SETTINGS, user, "garmin_last_sync", datetime.now())
    frappe.db.commit()
    return {"imported": imported, "skipped": skipped, "more_pending": more_pending}


@frappe.whitelist(allow_guest=True)
def sync_garmin_public():
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Login required", frappe.AuthenticationError)
    return sync_garmin(full_sync=False)


# ── One-off backfill ─────────────────────────────────────────────────────────
def backfill_hr_from_points():
    """Fix avg_heart_rate/max_heart_rate/trimp for Garmin runs imported before
    the avgHR field-name bug fix — recomputed from each run's own stored
    route_points, no Garmin API access needed. Safe to run via
    `bench execute runningapp.running_journal.garmin_sync.backfill_hr_from_points`.
    """
    settings = get_analytics_settings()
    runs = frappe.get_all(
        "Run",
        filters={"garmin_id": ["!=", ""], "avg_heart_rate": 0},
        fields=["name", "route_points", "activity_type", "distance_km", "duration_sec", "date"],
    )
    updated = 0
    for run in runs:
        if not run.route_points:
            continue
        try:
            points = json.loads(run.route_points)
        except Exception:
            continue
        hr_vals = [p["hr"] for p in points if isinstance(p, dict) and p.get("hr")]
        if not hr_vals:
            continue
        avg_hr = round(sum(hr_vals) / len(hr_vals))
        max_hr_val = max(hr_vals)
        effective_max_hr = settings["max_hr"] or max_hr_val or 0
        trimp = compute_trimp(avg_hr, run.duration_sec, settings["resting_hr"], effective_max_hr, settings["gender"])
        frappe.db.set_value(
            "Run", run.name,
            {"avg_heart_rate": avg_hr, "max_heart_rate": max_hr_val, "trimp": trimp or 0},
            update_modified=False,
        )
        updated += 1
    frappe.db.commit()
    return {"updated": updated, "checked": len(runs)}


def debug_raw_tcx_span(activity_id):
    """Diagnostic: raw trackpoint count/time span before downsampling."""
    from garminconnect import Garmin

    client = get_garmin_client()
    tcx_bytes = client.download_activity(str(activity_id), dl_fmt=Garmin.ActivityDownloadFormat.TCX)
    data = tcx_bytes
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    xml_start = data.find(b"<?xml")
    if xml_start > 0:
        data = data[xml_start:]
    root = ET.fromstring(data)
    trackpoints = root.findall(f".//{TCX_NS}Trackpoint")
    times = []
    for tp in trackpoints:
        time_el = tp.find(f"{TCX_NS}Time")
        if time_el is not None and time_el.text:
            times.append(time_el.text)
    result = {"raw_trackpoint_count": len(trackpoints), "times_found": len(times)}
    if times:
        t0 = datetime.fromisoformat(times[0].replace("Z", "+00:00"))
        t_last = datetime.fromisoformat(times[-1].replace("Z", "+00:00"))
        result["first_time"] = times[0]
        result["last_time"] = times[-1]
        result["raw_span_sec"] = round((t_last - t0).total_seconds())
    return result


# ── Route re-backfill (downsampling bug fix) ────────────────────────────────
REBACKFILL_MAX = 20
REBACKFILL_TIME_BUDGET_SEC = 90


def rebackfill_routes():
    """Re-download + re-parse TCX for Garmin runs affected by the
    pts[::step][:MAX_POINTS] truncation bug (any run whose raw TCX had
    between MAX_POINTS+1 and 2*MAX_POINTS-1 trackpoints lost its second
    half). Bounded per call like sync_garmin; call repeatedly until
    more_pending is false. Marks each run as done via a route_points hash
    stored nowhere — instead we just track via a dedicated flag field,
    avoided here by simply re-processing every garmin_id run once: safe
    to run multiple times since it always re-fetches equally-correct data.
    """
    client = get_garmin_client()
    t_start = time.monotonic()
    updated = 0
    checked = 0
    more_pending = False

    runs = frappe.get_all(
        "Run",
        filters={"garmin_id": ["!=", ""], "route_points_reflowed": 0},
        fields=["name", "garmin_id"],
        limit_page_length=REBACKFILL_MAX * 5,
    )

    for run in runs:
        checked += 1
        if updated >= REBACKFILL_MAX or (time.monotonic() - t_start) > REBACKFILL_TIME_BUDGET_SEC:
            more_pending = True
            break

        points = fetch_activity_route(client, run.garmin_id)
        frappe.db.set_value(
            "Run", run.name,
            {"route_points": json.dumps(points) if points else "", "route_points_reflowed": 1},
            update_modified=False,
        )
        updated += 1
        frappe.db.commit()

    remaining = frappe.db.count("Run", filters={"garmin_id": ["!=", ""], "route_points_reflowed": 0})
    return {"updated": updated, "checked": checked, "more_pending": more_pending or remaining > 0, "remaining": remaining}
