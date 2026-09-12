"""
bulk_import.py
---------------
Bulk-import activities from files instead of a live API — for a full
Strava "bulk export" (Settings > My Account > Download or Delete Your
Account > Request Your Archive: a zip containing activities.csv plus an
activities/ folder of .fit.gz/.tcx.gz/.gpx.gz files), or any standalone
TCX/FIT/GPX files (Garmin Connect's per-activity "Export Original"
download, files copied off a watch, etc). Useful for a full history
backfill that would otherwise mean thousands of live API calls, or for
someone who doesn't want to hand over their Garmin/Strava password at all.

Two entry points:
  import_strava_export()  -- the CSV + activities/ folder layout
  import_activity_files()  -- a flat list of loose TCX/FIT/GPX files, no CSV

Both are per-user (same `user` scoping as garmin_sync.py/strava_sync.py)
and share garmin_sync's cross-source dedup (_is_duplicate), so re-running
an import, or importing files for activities already synced live via the
Garmin/Strava API, doesn't create duplicates.
"""

import csv
import gzip
import json
import math
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

import frappe

from runningapp.running_journal.strava_sync import (
    current_user,
    get_analytics_settings,
    compute_age,
    compute_calories_keytel,
    compute_calories_met,
    compute_vdot,
    compute_trimp,
)
from runningapp.running_journal.garmin_sync import _is_duplicate

SEMICIRCLE = 11930465.0  # FIT lat/lon integer -> degrees

TYPE_MAP = {
    "Run": "Run", "TrailRun": "Run", "VirtualRun": "Run",
    "Swim": "Swimming", "Swimming": "Swimming",
    "Ride": "Cycling", "VirtualRide": "Cycling", "Cycling": "Cycling",
    "Walk": "Walk", "Hike": "Walk",
}


# ── File-level parsers ───────────────────────────────────────────────────────
# All three return (summary, route_points, start_time) — summary is whatever
# session-level fields that format actually carries (FIT has real session
# totals; TCX/GPX have none, so summary is always {} for those and totals
# get derived from the points instead). route_points share one shape with
# the rest of the app: {lat, lon, ele, t, hr, spd, dst, cad} where "t" is
# seconds elapsed since start_time, not absolute — so start_time is
# returned alongside the points for anything that needs the real clock
# time (the Run's `date`, mainly).
def parse_fit(filepath):
    try:
        import fitparse
        opener = gzip.open if filepath.endswith(".gz") else open
        with opener(filepath, "rb") as f:
            fit = fitparse.FitFile(f)

            summary = {}
            for msg in fit.get_messages("session"):
                for d in msg:
                    if d.value is not None:
                        summary[d.name] = d.value

            route_points = []
            start_ts = None
            for msg in fit.get_messages("record"):
                fields = {d.name: d.value for d in msg if d.value is not None}
                lat_sc, lon_sc = fields.get("position_lat"), fields.get("position_long")
                ts = fields.get("timestamp")

                pt = {}
                if lat_sc is not None and lon_sc is not None:
                    pt["lat"] = round(lat_sc / SEMICIRCLE, 7)
                    pt["lon"] = round(lon_sc / SEMICIRCLE, 7)
                ele = fields.get("enhanced_altitude") or fields.get("altitude")
                if ele is not None:
                    pt["ele"] = round(float(ele), 1)
                if ts is not None:
                    if start_ts is None:
                        start_ts = ts
                    pt["t"] = int((ts - start_ts).total_seconds())
                hr = fields.get("heart_rate")
                if hr is not None:
                    pt["hr"] = int(hr)
                spd = fields.get("enhanced_speed") or fields.get("speed")
                if spd is not None:
                    pt["spd"] = round(float(spd), 3)
                dst = fields.get("distance")
                if dst is not None:
                    pt["dst"] = round(float(dst), 1)
                if pt:
                    route_points.append(pt)

            return summary, route_points, start_ts
    except Exception:
        return {}, [], None


def _parse_xml_track(filepath, ns_tag, lat_from, lon_from, ele_tag, time_tag, hr_lookup):
    opener = gzip.open if filepath.endswith(".gz") else open
    with opener(filepath, "rb") as f:
        data = f.read()
    xml_start = data.find(b"<?xml")
    if xml_start > 0:
        data = data[xml_start:]
    root = ET.fromstring(data)
    ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""

    route_points, start_ts = [], None
    for tp in root.iter(ns + ns_tag):
        pt = {}
        lat, lon = lat_from(tp, ns), lon_from(tp, ns)
        if lat is not None and lon is not None:
            pt["lat"], pt["lon"] = round(lat, 7), round(lon, 7)

        ele_el = tp.find(ns + ele_tag) if ele_tag else None
        if ele_el is not None and ele_el.text:
            pt["ele"] = round(float(ele_el.text), 1)

        time_el = tp.find(ns + time_tag)
        if time_el is not None and time_el.text:
            try:
                ts = datetime.strptime(time_el.text.strip().replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
                if start_ts is None:
                    start_ts = ts
                pt["t"] = int((ts - start_ts).total_seconds())
            except Exception:
                pass

        if hr_lookup:
            hr_el = tp.find(".//" + ns + hr_lookup)
            if hr_el is not None and hr_el.text:
                try:
                    pt["hr"] = int(float(hr_el.text))
                except Exception:
                    pass

        if pt:
            route_points.append(pt)
    return route_points, start_ts


def parse_tcx(filepath):
    try:
        def lat(tp, ns):
            pos = tp.find(ns + "Position")
            el = pos.find(ns + "LatitudeDegrees") if pos is not None else None
            return float(el.text) if el is not None and el.text else None

        def lon(tp, ns):
            pos = tp.find(ns + "Position")
            el = pos.find(ns + "LongitudeDegrees") if pos is not None else None
            return float(el.text) if el is not None and el.text else None

        points, start_ts = _parse_xml_track(
            filepath, "Trackpoint", lat, lon, "AltitudeMeters", "Time", "Value"
        )
        return {}, points, start_ts
    except Exception:
        return {}, [], None


def parse_gpx(filepath):
    try:
        def lat(tp, ns):
            v = tp.get("lat")
            return float(v) if v else None

        def lon(tp, ns):
            v = tp.get("lon")
            return float(v) if v else None

        points, start_ts = _parse_xml_track(filepath, "trkpt", lat, lon, "ele", "time", None)
        return {}, points, start_ts
    except Exception:
        return {}, [], None


def _parse_activity_file(filepath):
    lower = filepath.lower()
    if lower.endswith((".fit", ".fit.gz")):
        return parse_fit(filepath)
    if lower.endswith((".tcx", ".tcx.gz")):
        return parse_tcx(filepath)
    if lower.endswith((".gpx", ".gpx.gz")):
        return parse_gpx(filepath)
    return {}, [], None


def _haversine_km(p1, p2):
    lat1, lon1, lat2, lon2 = map(math.radians, [p1["lat"], p1["lon"], p2["lat"], p2["lon"]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def _distance_km_from_points(points):
    total = 0.0
    prev = None
    for p in points:
        if "lat" in p and "lon" in p:
            if prev:
                total += _haversine_km(prev, p)
            prev = p
    return round(total, 3)


# ── Shared Run-doc builder ───────────────────────────────────────────────────
def _build_run_doc(user, settings, activity_type, date, run_name, distance_km, duration_sec,
                    elev_gain=0, avg_hr=0, max_hr_val=0, avg_speed=0, max_speed=0,
                    calories=0, calorie_source="", route_points=None, strava_id="",
                    notes="", gear="", extra=None):
    route_points = route_points or []
    if not avg_hr and route_points:
        hr_vals = [p["hr"] for p in route_points if p.get("hr")]
        if hr_vals:
            avg_hr = round(sum(hr_vals) / len(hr_vals))
            max_hr_val = max_hr_val or max(hr_vals)

    if not calories and avg_hr > 0:
        age_at_activity = compute_age(settings["dob"], date)
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

    doc = {
        "doctype": "Run",
        "user": user,
        "run_name": run_name,
        "date": date,
        "activity_type": activity_type,
        "distance_km": distance_km,
        "duration_sec": duration_sec,
        "elevation_gain": elev_gain,
        "calories": calories,
        "calorie_source": calorie_source,
        "avg_heart_rate": avg_hr,
        "max_heart_rate": max_hr_val,
        "avg_speed": avg_speed,
        "max_speed": max_speed,
        "route_points": json.dumps(route_points) if route_points else "",
    }
    if strava_id: doc["strava_id"] = strava_id
    if notes: doc["notes"] = notes
    if gear: doc["gear"] = gear
    if vdot: doc["vdot"] = vdot
    if trimp: doc["trimp"] = trimp
    if extra: doc.update(extra)
    return doc


# ── File-path helpers ────────────────────────────────────────────────────────
def _file_url_to_path(file_url):
    file_doc = frappe.get_doc("File", {"file_url": file_url})
    return file_doc.get_full_path()


def _extract_zip(file_url, user):
    zip_path = _file_url_to_path(file_url)
    safe_user = re.sub(r"[^A-Za-z0-9_.-]", "_", user)
    dest = frappe.get_site_path("private", "files", "bulk_import", safe_user, str(int(time.time())))
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    return dest


def _find_activity_file(activities_dir, csv_filename):
    """csv_filename is like "activities/258679085.fit.gz" — the file stem
    isn't the same as the activity ID, so this matches on the stem from
    the CSV row itself, trying every extension Strava might have used."""
    if not csv_filename:
        return None
    basename = os.path.basename(csv_filename)
    stem = re.sub(r"\.(fit|tcx|gpx)(\.gz)?$", "", basename, flags=re.I)
    base = os.path.join(activities_dir, stem)
    for ext in (".fit.gz", ".fit", ".tcx.gz", ".tcx", ".gpx.gz", ".gpx"):
        path = base + ext
        if os.path.exists(path):
            return path
    return None


# ── Strava bulk-export CSV columns (positional — file has duplicate names) ──
_C_ID, _C_DATE, _C_NAME, _C_DESC = 0, 1, 2, 4
_C_FILENAME, _C_MOVING_TIME, _C_DISTANCE = 12, 16, 17
_C_MAX_SPEED, _C_AVG_SPEED = 18, 19
_C_ELEV_GAIN = 20
_C_MAX_HR, _C_AVG_HR, _C_CALORIES = 30, 31, 34
_C_REL_EFFORT = 37
_C_GEAR, _C_ACTIVITY_GEAR = 69, 11


def _g(row, idx):
    try:
        return row[idx].strip()
    except IndexError:
        return ""


def _sf(val, default=0.0):
    try:
        v = str(val).replace(",", "").strip()
        return float(v) if v else default
    except Exception:
        return default


def _si(val, default=0):
    try:
        v = str(val).replace(",", "").strip()
        return int(float(v)) if v else default
    except Exception:
        return default


def _parse_csv_date(date_str):
    for fmt in ["%b %d, %Y, %I:%M:%S %p", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return date_str.strip()[:10]


@frappe.whitelist()
def import_strava_export(file_url=None, extracted_dir=None, user=None):
    """
    file_url: a Frappe File's file_url for the zip downloaded from Strava
    (Settings > My Account > Download or Delete Your Account > Request
    Your Archive). Extracted once into a private per-user scratch folder.
    extracted_dir: an alternative to file_url — a path on the server that
    already has activities.csv + an activities/ folder, for a bench-execute
    run against files placed there directly instead of through the UI.
    """
    user = user or current_user()
    settings = get_analytics_settings(user)

    if file_url and not extracted_dir:
        extracted_dir = _extract_zip(file_url, user)
    if not extracted_dir:
        frappe.throw("Provide either file_url (an uploaded Strava export zip) or extracted_dir")

    csv_path = os.path.join(extracted_dir, "activities.csv")
    activities_dir = os.path.join(extracted_dir, "activities")
    if not os.path.exists(csv_path):
        frappe.throw("activities.csv not found in that export — is this a genuine Strava bulk-export zip?")

    imported = skipped = errors = no_file = 0
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)

        for row in reader:
            if not row or not row[0].strip():
                continue
            activity_id = _g(row, _C_ID)
            if not activity_id:
                continue
            if frappe.db.exists("Run", {"strava_id": activity_id, "user": user}):
                skipped += 1
                continue

            activity_type = TYPE_MAP.get(_g(row, 3), "")
            if not activity_type:
                skipped += 1
                continue

            try:
                date = _parse_csv_date(_g(row, _C_DATE))
                run_name = _g(row, _C_NAME) or f"{activity_type} {date}"
                notes = _g(row, _C_DESC)
                gear = _g(row, _C_GEAR) or _g(row, _C_ACTIVITY_GEAR)

                csv_distance_km = round(_sf(_g(row, _C_DISTANCE)) / 1000, 3)
                csv_duration = _si(_g(row, _C_MOVING_TIME))
                csv_calories = _si(_g(row, _C_CALORIES))
                csv_avg_hr = _si(_g(row, _C_AVG_HR))
                csv_max_hr = _si(_g(row, _C_MAX_HR))

                filepath = _find_activity_file(activities_dir, _g(row, _C_FILENAME))
                fit_summary, route_points = {}, []
                if filepath:
                    fit_summary, route_points, _ = _parse_activity_file(filepath)
                else:
                    no_file += 1

                def fv(key, csv_val, cast=float):
                    v = fit_summary.get(key)
                    return cast(v) if v is not None else csv_val

                distance_km = round(float(fit_summary["total_distance"]) / 1000, 3) if fit_summary.get("total_distance") else csv_distance_km
                duration_sec = fv("total_moving_time", csv_duration) or fv("total_timer_time", csv_duration)
                elev_gain = fv("total_ascent", _sf(_g(row, _C_ELEV_GAIN)))
                avg_hr = int(fv("avg_heart_rate", csv_avg_hr))
                max_hr_val = int(fv("max_heart_rate", csv_max_hr))
                avg_speed = fv("enhanced_avg_speed", _sf(_g(row, _C_AVG_SPEED))) or fv("avg_speed", _sf(_g(row, _C_AVG_SPEED)))
                max_speed = fv("enhanced_max_speed", _sf(_g(row, _C_MAX_SPEED))) or fv("max_speed", _sf(_g(row, _C_MAX_SPEED)))

                fit_calories = int(fit_summary.get("total_calories", 0) or 0)
                calories = fit_calories or csv_calories
                calorie_source = "fit_session" if fit_calories else ("csv_strava" if csv_calories else "")

                if _is_duplicate(date, activity_type, distance_km, duration_sec, user):
                    skipped += 1
                    continue

                run_doc = _build_run_doc(
                    user, settings, activity_type, date, run_name, distance_km, duration_sec,
                    elev_gain=elev_gain, avg_hr=avg_hr, max_hr_val=max_hr_val,
                    avg_speed=avg_speed, max_speed=max_speed, calories=calories,
                    calorie_source=calorie_source, route_points=route_points,
                    strava_id=activity_id, notes=notes, gear=gear,
                    extra={"relative_effort": _sf(_g(row, _C_REL_EFFORT))},
                )
                run = frappe.get_doc(run_doc)
                run.insert(ignore_permissions=True)
                imported += 1
                if imported % 50 == 0:
                    frappe.db.commit()
            except Exception:
                errors += 1
                continue

    frappe.db.commit()
    return {"imported": imported, "skipped": skipped, "errors": errors, "no_file": no_file}


@frappe.whitelist()
def import_activity_files(file_urls=None, activity_type=None, user=None):
    """
    Loose TCX/FIT/GPX files with no CSV metadata alongside them — e.g.
    Garmin Connect's per-activity "Export Original" download, or files
    copied straight off a watch. One Run per file. activity_type is a
    single value applied to every file in this call, since none of these
    formats reliably say what kind of activity it was; call once per
    activity type if a batch has a mix of runs and swims, say.
    """
    user = user or current_user()
    settings = get_analytics_settings(user)
    if isinstance(file_urls, str):
        file_urls = json.loads(file_urls)
    activity_type = activity_type or "Run"

    imported = skipped = errors = 0
    for file_url in file_urls or []:
        try:
            filepath = _file_url_to_path(file_url)
            summary, route_points, start_ts = _parse_activity_file(filepath)
            if not route_points:
                errors += 1
                continue

            distance_m = float(summary.get("total_distance", 0) or 0)
            distance_km = round(distance_m / 1000, 3) if distance_m else _distance_km_from_points(route_points)
            duration_sec = round(
                float(summary.get("total_moving_time") or summary.get("total_timer_time") or 0)
                or (route_points[-1].get("t", 0) if route_points else 0)
            )
            date = (start_ts or datetime.now()).strftime("%Y-%m-%d")
            run_name = f"{activity_type} {date}"

            avg_hr = int(summary.get("avg_heart_rate", 0) or 0)
            max_hr_val = int(summary.get("max_heart_rate", 0) or 0)
            elev_gain = round(float(summary.get("total_ascent", 0) or 0))

            if _is_duplicate(date, activity_type, distance_km, duration_sec, user):
                skipped += 1
                continue

            run_doc = _build_run_doc(
                user, settings, activity_type, date, run_name, distance_km, duration_sec,
                elev_gain=elev_gain, avg_hr=avg_hr, max_hr_val=max_hr_val,
                route_points=route_points,
            )
            run = frappe.get_doc(run_doc)
            run.insert(ignore_permissions=True)
            imported += 1
            frappe.db.commit()
        except Exception:
            errors += 1
            continue

    return {"imported": imported, "skipped": skipped, "errors": errors}
