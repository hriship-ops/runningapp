"""
strava_oauth.py
----------------
The actual "Connect with Strava" flow — the standard OAuth2
authorize -> callback -> token-exchange pattern used by Runalyze,
Tapiriik, etc: ONE app-level Strava API application (Strava App
Settings, registered once by the site owner) that every user
authorizes against to get their OWN access/refresh tokens. No user
ever sees or enters a client secret.

  1. GET  authorize_url  -> frontend redirects the browser here
  2. Strava shows its consent screen, then redirects to `callback`
  3. callback exchanges the code for this user's tokens, saves them
     on their own Run Settings record, and bounces back to the
     settings page.
"""

import frappe
import requests
from datetime import datetime
from urllib.parse import urlencode
from frappe.utils.password import set_encrypted_password

from runningapp.running_journal.strava_sync import (
    SETTINGS,
    strava_app_credentials,
    ensure_settings_doc,
    current_user,
)

SCOPE = "read,activity:read_all"


def _redirect_uri():
    return f"{frappe.utils.get_url()}/api/method/runningapp.running_journal.strava_oauth.callback"


@frappe.whitelist()
def authorize_url():
    """Called by the settings page to build the link behind its
    "Connect with Strava" button."""
    client_id, _ = strava_app_credentials()
    if not client_id:
        frappe.throw("Strava isn't set up on this site yet — ask the site admin to fill in Strava App Settings.")
    params = {
        "client_id": client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPE,
        # Carries the logged-in user through the redirect round-trip so the
        # callback (a plain GET from Strava, no session state of its own
        # beyond the cookie) knows whose Run Settings to save tokens on.
        "state": current_user(),
    }
    return f"https://www.strava.com/oauth/authorize?{urlencode(params)}"


@frappe.whitelist(allow_guest=True)
def callback():
    """Strava redirects here after the athlete approves (or denies) access."""
    args = frappe.local.form_dict
    error = args.get("error")
    user = args.get("state") or current_user()
    settings_url = "/run-settings"

    if error:
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{settings_url}?strava=denied"
        return

    code = args.get("code")
    if not code:
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{settings_url}?strava=error"
        return

    client_id, client_secret = strava_app_credentials()
    r = requests.post("https://www.strava.com/oauth/token", data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    })

    if r.status_code != 200:
        frappe.log_error(title="Strava OAuth token exchange failed", message=r.text[:2000])
        frappe.local.response["type"] = "redirect"
        frappe.local.response["location"] = f"{settings_url}?strava=error"
        return

    tokens = r.json()
    athlete = tokens.get("athlete") or {}

    ensure_settings_doc(user)
    set_encrypted_password(SETTINGS, user, tokens["access_token"], "strava_access_token")
    set_encrypted_password(SETTINGS, user, tokens["refresh_token"], "strava_refresh_token")
    frappe.db.set_value(SETTINGS, user, "strava_athlete_id", str(athlete.get("id", "")))
    if tokens.get("expires_at"):
        frappe.db.set_value(
            SETTINGS, user, "strava_token_expires",
            datetime.fromtimestamp(tokens["expires_at"]),
        )
    frappe.db.commit()

    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = f"{settings_url}?strava=connected"


@frappe.whitelist()
def disconnect():
    user = current_user()
    frappe.db.set_value(SETTINGS, user, {
        "strava_access_token": "",
        "strava_refresh_token": "",
        "strava_athlete_id": "",
        "strava_token_expires": None,
    })
    frappe.db.commit()
    return {"status": "disconnected"}
