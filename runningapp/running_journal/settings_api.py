"""
settings_api.py
----------------
Backs the /run-settings page: the logged-in user's own profile fields,
Garmin credentials, and Strava connection status. Every call is scoped
to frappe.session.user (never current_user()'s Guest->default-owner
fallback — a settings page only ever makes sense for a real logged-in
person).
"""

import frappe
from frappe.utils.password import get_decrypted_password

from runningapp.running_journal.strava_sync import SETTINGS, ensure_settings_doc

PROFILE_FIELDS = [
    "weight_kg", "age", "gender", "resting_heart_rate", "units",
    "default_activity", "pace_format", "calories_formula",
    "resting_hr", "max_hr_override", "date_of_birth",
]


def _require_login():
    if frappe.session.user == "Guest":
        frappe.throw("Login required", frappe.AuthenticationError)
    return frappe.session.user


@frappe.whitelist()
def get_my_settings():
    user = _require_login()
    doc = ensure_settings_doc(user)

    data = {f: doc.get(f) for f in PROFILE_FIELDS}
    data["garmin_email"] = doc.garmin_email or ""
    data["garmin_connected"] = bool(
        doc.garmin_email and get_decrypted_password(SETTINGS, user, "garmin_password", raise_exception=False)
    )
    data["garmin_last_sync"] = doc.garmin_last_sync

    data["strava_connected"] = bool(
        get_decrypted_password(SETTINGS, user, "strava_access_token", raise_exception=False)
    )
    data["strava_athlete_id"] = doc.strava_athlete_id or ""
    data["strava_last_sync"] = doc.strava_last_sync

    return data


@frappe.whitelist()
def save_my_settings(**kwargs):
    user = _require_login()
    doc = ensure_settings_doc(user)

    for f in PROFILE_FIELDS:
        if f in kwargs and kwargs[f] not in (None, ""):
            doc.set(f, kwargs[f])

    if kwargs.get("garmin_email"):
        doc.garmin_email = kwargs["garmin_email"]
    # Only overwrite the stored password if a new one was actually typed —
    # the settings form never round-trips the existing password back to the
    # browser, so an empty submission here means "leave it as is", not
    # "clear it".
    if kwargs.get("garmin_password"):
        doc.garmin_password = kwargs["garmin_password"]

    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"status": "saved"}


@frappe.whitelist()
def disconnect_garmin():
    user = _require_login()
    doc = ensure_settings_doc(user)
    doc.garmin_email = ""
    doc.garmin_password = ""
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"status": "disconnected"}
