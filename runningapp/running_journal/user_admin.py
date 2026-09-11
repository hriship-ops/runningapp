"""
user_admin.py
-------------
Invite a new person onto the site. No SMTP is configured, so Frappe's
normal "reset password" flow can't actually email anyone — this
creates the User record and hands back the same self-service
reset-password link Frappe would have emailed, for the site owner to
forward manually (WhatsApp, Slack, whatever).

Restricted to the site owner: this creates a real login, so it isn't
something any logged-in visitor should be able to trigger.
"""

import frappe

from runningapp.running_journal.doctype.run_settings.run_settings import ORIGINAL_OWNER

ALLOWED_INVITERS = ("Administrator", ORIGINAL_OWNER)


def _require_owner():
    if frappe.session.user not in ALLOWED_INVITERS:
        frappe.throw("Not permitted", frappe.PermissionError)


@frappe.whitelist()
def invite_user(email, full_name=None):
    _require_owner()
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        frappe.throw("Enter a valid email address")

    newly_created = not frappe.db.exists("User", email)
    if newly_created:
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": full_name or email.split("@")[0],
            "send_welcome_email": 0,
            "user_type": "Website User",
            "roles": [{"role": "All"}],
        })
        user.insert(ignore_permissions=True)
    else:
        user = frappe.get_doc("User", email)

    # send_email=False: generates & saves reset_password_key without
    # trying (and failing) to send it through unconfigured SMTP.
    user.reset_password(send_email=False)
    frappe.db.commit()

    reset_link = f"{frappe.utils.get_url()}/update-password?key={user.reset_password_key}"
    return {"email": email, "reset_link": reset_link, "created": newly_created}
