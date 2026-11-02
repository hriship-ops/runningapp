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
  3. callback checks the one-time `state` nonce, exchanges the code for
     this user's tokens, saves them on their own Run Settings record, and
     bounces back to where the user started (dashboard or settings page).
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


# Where the browser may be sent back to after connecting (no open redirects).
ALLOWED_NEXT = {"/run-journal", "/run-settings"}
STATE_TTL_SEC = 900


def _state_key(nonce):
    return f"strava_oauth_state:{nonce}"


@frappe.whitelist()
def authorize_url(next=None):
    """Build the Strava consent-screen URL behind a "Connect with Strava" button.

    `state` is a random one-time nonce bound (in the cache) to the logged-in
    user, NOT the user's email: the callback is a guest endpoint, so trusting a
    caller-supplied identity would let anyone attach their Strava account to
    someone else's journal with a crafted link.
    """
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Please log in first.", frappe.AuthenticationError)
    client_id, _ = strava_app_credentials()
    if not client_id:
        frappe.throw("Strava isn't set up on this site yet - ask the site admin to fill in Strava App Settings.")
    nonce = frappe.generate_hash(length=32)
    frappe.cache().set_value(
        _state_key(nonce),
        {"user": user, "next": next if next in ALLOWED_NEXT else "/run-settings"},
        expires_in_sec=STATE_TTL_SEC,
    )
    params = {
        "client_id": client_id,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPE,
        "state": nonce,
    }
    return f"https://www.strava.com/oauth/authorize?{urlencode(params)}"


def _back(url, result):
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = f"{url}?strava={result}"


@frappe.whitelist(allow_guest=True)
def callback():
    """Strava redirects here after the athlete approves (or denies) access."""
    args = frappe.local.form_dict
    nonce = args.get("state") or ""
    pending = frappe.cache().get_value(_state_key(nonce)) if nonce else None
    if not pending:
        # Unknown, expired or already-used state: never save tokens.
        return _back("/run-settings", "error")
    frappe.cache().delete_value(_state_key(nonce))   # one-time use
    user, next_url = pending["user"], pending.get("next") or "/run-settings"

    # The same browser that started the flow must finish it.
    if frappe.session.user not in ("Guest", user):
        return _back(next_url, "error")

    if args.get("error"):
        return _back(next_url, "denied")
    code = args.get("code")
    if not code:
        return _back(next_url, "error")

    client_id, client_secret = strava_app_credentials()
    r = requests.post("https://www.strava.com/oauth/token", data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    })
    if r.status_code != 200:
        frappe.log_error(title="Strava OAuth token exchange failed", message=r.text[:2000])
        return _back(next_url, "error")

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
    return _back(next_url, "connected")


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
