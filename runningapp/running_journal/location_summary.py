"""
location_summary.py
--------------------
Country/state/district breakdown of everywhere the athlete has run, for the
dashboard's drill-down cards. Country/state/district come from Nominatim's
own structured address fields via strava_sync.get_location_details().
"""

import json
import time

import frappe

from runningapp.running_journal.strava_sync import get_location_details, first_latlon

SETTINGS = "Run Settings"

# Nominatim's usage policy caps free reverse-geocoding at ~1 request/sec,
# so this backfill is bounded and paced, and must be called repeatedly
# (like sync_garmin) until more_pending is false.
GEOCODE_BACKFILL_MAX = 40
GEOCODE_TIME_BUDGET_SEC = 60


def _pending_geo_count():
    # Only genuinely un-attempted rows (country IS NULL). A row that was
    # attempted but had no usable point data gets country set to '' (not
    # left NULL) specifically so it's not picked up again forever.
    return frappe.db.sql(
        """SELECT COUNT(*) FROM `tabRun`
           WHERE country IS NULL
             AND route_points IS NOT NULL AND route_points != ''""",
    )[0][0]


def backfill_geo_fields():
    """One-off: populate country/state/district on runs that predate this
    feature, using each run's first route point."""
    t_start = time.monotonic()
    updated = 0

    runs = frappe.db.sql(
        """SELECT name, route_points FROM `tabRun`
           WHERE country IS NULL
             AND route_points IS NOT NULL AND route_points != ''
           LIMIT %s""",
        (GEOCODE_BACKFILL_MAX * 3,),
        as_dict=True,
    )

    for run in runs:
        if updated >= GEOCODE_BACKFILL_MAX or (time.monotonic() - t_start) > GEOCODE_TIME_BUDGET_SEC:
            break

        try:
            points = json.loads(run.route_points)
            ll = first_latlon(points)
        except Exception:
            ll = None

        if ll is None:
            # No point in the whole array has GPS coordinates (not just
            # the first one) — permanently unfixable, mark with the ''
            # sentinel (not NULL) so it's skipped on future calls instead
            # of failing forever.
            frappe.db.set_value(
                "Run", run.name, {"country": "", "state": "", "district": ""}, update_modified=False,
            )
            updated += 1
            frappe.db.commit()
            continue
        lat, lon = ll

        try:
            details = get_location_details(lat, lon)
        except Exception:
            # Network/HTTP failure — transient, leave country as NULL so
            # this row is retried on a later call instead of being
            # permanently mislabeled as having no location data.
            continue
        finally:
            time.sleep(1)  # Nominatim usage policy: max 1 req/sec

        frappe.db.set_value(
            "Run", run.name,
            {"country": details["country"] or "", "state": details["state"], "district": details["district"]},
            update_modified=False,
        )
        updated += 1
        frappe.db.commit()

    remaining = _pending_geo_count()
    return {"updated": updated, "more_pending": remaining > 0, "remaining": remaining}


def _distinct_geo():
    rows = frappe.db.sql(
        """SELECT DISTINCT country, state, district FROM `tabRun`
           WHERE country IS NOT NULL AND country != ''""",
        as_dict=True,
    )
    countries = sorted({r.country for r in rows if r.country})
    states = sorted({r.state for r in rows if r.state})
    districts = sorted({r.district for r in rows if r.district})
    return countries, states, districts


@frappe.whitelist(allow_guest=True)
def get_location_summary():
    """Countries/states/districts run in, with the actual lists for the
    dashboard's drill-down cards."""
    countries, states, districts = _distinct_geo()
    return {
        "countries": countries,
        "states": states,
        "districts": districts,
        "country_count": len(countries),
        "state_count": len(states),
        "district_count": len(districts),
    }


def reset_bad_geo_data():
    """One-off: clear country/state/district back to NULL for rows that
    were affected by two now-fixed bugs, so backfill_geo_fields() re-does
    them with the corrected code:
      1. Nominatim requests that failed (timeout/HTTP error/rate limit)
         were caught by a bare except and mislabeled with the same ''
         sentinel as a genuine "no address data" result — indistinguishable
         from the outside, so every '' row is suspect, not just some.
      2. No accept-language=en was sent, so non-Latin-script countries
         (Nepal, Bhutan, ...) got stored in the local script.
    Safe to run once; does not touch rows with a clean ASCII value that
    was never ''."""
    result = frappe.db.sql(
        """UPDATE `tabRun`
           SET country = NULL, state = NULL, district = NULL
           WHERE country = ''
              OR country REGEXP '[^ -~]'
              OR state REGEXP '[^ -~]'
              OR district REGEXP '[^ -~]'"""
    )
    frappe.db.commit()
    remaining = _pending_geo_count()
    return {"reset": frappe.db.sql("SELECT ROW_COUNT()")[0][0], "now_pending": remaining}


def reset_recoverable_geo_data():
    """One-off: clear country/state/district back to NULL for runs marked
    '' (no location) where route_points actually does contain GPS
    coordinates somewhere in the array — just not in the very first
    sample, which the old code assumed always had a lat/lon. A GPS fix
    frequently hasn't locked yet at the first recorded point (a
    {"t":.., "hr":..}-only sample), while every point after it does have
    coordinates. Only rows with genuinely no GPS anywhere (pool swims,
    treadmill) are left alone. No network calls — pure DB read/parse/write,
    completes in one call."""
    rows = frappe.db.sql(
        """SELECT name, route_points FROM `tabRun`
           WHERE country = '' AND route_points IS NOT NULL AND route_points != ''""",
        as_dict=True,
    )
    reset = 0
    for row in rows:
        try:
            points = json.loads(row.route_points)
        except Exception:
            continue
        if first_latlon(points) is not None:
            frappe.db.set_value(
                "Run", row.name, {"country": None, "state": None, "district": None}, update_modified=False,
            )
            reset += 1
    frappe.db.commit()
    remaining = _pending_geo_count()
    return {"reset": reset, "checked": len(rows), "now_pending": remaining}
