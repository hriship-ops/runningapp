"""
location_summary.py
--------------------
Country/state/district breakdown of everywhere the athlete has run, for a
dashboard card. Geocoding itself is NOT the LLM's job — country/state/
district come from Nominatim's own structured address fields (accurate,
free) via strava_sync.get_location_details(). The LLM (Anthropic or
OpenRouter, whichever Run Settings is configured for) only turns the
deduplicated list into a short, readable summary sentence.
"""

import json
import time

import frappe
import requests
from frappe.utils.password import get_decrypted_password

from runningapp.running_journal.strava_sync import get_location_details

SETTINGS = "Run Settings"

DEFAULT_MODELS = {
    "Anthropic": "claude-sonnet-4-5-20250929",
    "OpenRouter": "anthropic/claude-sonnet-4.5",
}

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

        details = {"country": "", "state": "", "district": ""}
        try:
            points = json.loads(run.route_points)
            lat = points[0]["lat"]
            lon = points[0]["lon"]
        except Exception:
            # Malformed/unexpected route_points shape — mark with the ''
            # sentinel (not NULL) so this row is skipped on future calls
            # instead of being re-selected and re-failing forever.
            points = None

        if points is not None:
            details = get_location_details(lat, lon)
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


def _call_llm(prompt):
    provider = frappe.db.get_single_value(SETTINGS, "llm_provider") or "Anthropic"
    api_key = get_decrypted_password(SETTINGS, SETTINGS, "llm_api_key", raise_exception=False)
    model = frappe.db.get_single_value(SETTINGS, "llm_model") or DEFAULT_MODELS.get(provider, DEFAULT_MODELS["Anthropic"])

    if not api_key:
        frappe.throw("No LLM API key configured in Run Settings")

    if provider == "OpenRouter":
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 300},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    else:  # Anthropic
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={"model": model, "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"].strip()


@frappe.whitelist()
def refresh_location_summary():
    """Calls the LLM — costs money, so this is login-required (default for
    @frappe.whitelist() without allow_guest=True), triggered only by the
    dashboard's explicit refresh button, never automatically."""
    countries, states, districts = _distinct_geo()
    if not countries:
        text = "No location data yet — sync some runs first."
    else:
        prompt = (
            "Here is everywhere I've gone running, as geocoded place names:\n"
            f"Countries: {', '.join(countries)}\n"
            f"States/regions: {', '.join(states)}\n"
            f"Districts/counties: {', '.join(districts)}\n\n"
            "Write one short, friendly sentence (under 240 characters, no "
            "markdown) for a personal running dashboard card. Mention the "
            f"counts ({len(countries)} countries, {len(states)} states, "
            f"{len(districts)} districts) and name the countries/states."
        )
        text = _call_llm(prompt)

    frappe.db.set_single_value(SETTINGS, "location_summary_text", text)
    frappe.db.set_single_value(SETTINGS, "location_summary_updated", frappe.utils.now())
    frappe.db.commit()
    return {
        "summary": text,
        "countries": countries,
        "states": states,
        "districts": districts,
    }


@frappe.whitelist(allow_guest=True)
def get_location_summary():
    """Cached read only — no LLM call, safe for the public dashboard load.
    Includes the actual lists (not just counts) for the drill-down cards."""
    text = frappe.db.get_single_value(SETTINGS, "location_summary_text") or ""
    updated = frappe.db.get_single_value(SETTINGS, "location_summary_updated")
    countries, states, districts = _distinct_geo()
    return {
        "summary": text,
        "updated": str(updated) if updated else None,
        "countries": countries,
        "states": states,
        "districts": districts,
        "country_count": len(countries),
        "state_count": len(states),
        "district_count": len(districts),
    }
