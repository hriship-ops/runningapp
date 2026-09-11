# Copyright (c) 2026, hrishi and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password


class RunSettings(Document):
	pass


# ── One-off: Single → per-user migration ────────────────────────────────────
# Run Settings used to be a Frappe Single (one shared record for the whole
# site) and became a regular per-user doctype (autoname: field:user) when
# multi-user support was added. A Single's field values live in `tabSingles`,
# not in a real `tabRun Settings` row, so converting the doctype does not by
# itself carry the old owner's data into the new per-user table — it has to
# be copied over explicitly, once, after `bench migrate` has created the new
# table. Safe to call more than once: no-ops once the target user's record
# already exists. Never returns/prints the decrypted Garmin password itself —
# only whether one was found and carried over — since this runs via
# `bench execute` and its return value is easy to end up in a log or terminal
# scrollback.
ORIGINAL_OWNER = "hrishi.p@azimpremjifoundation.org"

_SINGLE_COPY_FIELDS = [
	"weight_kg", "age", "gender", "resting_heart_rate", "units",
	"default_activity", "pace_format", "calories_formula",
	"resting_hr", "max_hr_override", "date_of_birth",
	"garmin_email", "garmin_last_sync", "garmin_sync_cursor",
	"strava_last_sync",
]


def migrate_single_to_multiuser():
	if frappe.db.exists("Run Settings", ORIGINAL_OWNER):
		return {"status": "already_migrated", "user": ORIGINAL_OWNER}

	rows = frappe.db.sql(
		"select field, value from tabSingles where doctype = %s",
		("Run Settings",), as_dict=True,
	)
	old_values = {r.field: r.value for r in rows}
	if not old_values:
		return {"status": "no_old_single_data_found"}

	doc_data = {"doctype": "Run Settings", "user": ORIGINAL_OWNER}
	copied = []
	for f in _SINGLE_COPY_FIELDS:
		v = old_values.get(f)
		if v not in (None, ""):
			doc_data[f] = v
			copied.append(f)

	doc = frappe.get_doc(doc_data)
	doc.insert(ignore_permissions=True)

	garmin_pw = get_decrypted_password("Run Settings", "Run Settings", "garmin_password", raise_exception=False)
	garmin_pw_migrated = bool(garmin_pw)
	if garmin_pw:
		doc.garmin_password = garmin_pw
		doc.save(ignore_permissions=True)

	frappe.db.commit()
	return {
		"status": "migrated",
		"user": ORIGINAL_OWNER,
		"fields_copied": copied,
		"garmin_password_migrated": garmin_pw_migrated,
	}


def backfill_run_owner():
	"""Every Run record predates the `user` field — without this, the new
	user-scoped get_all_runs()/get_run() would show the original owner zero
	runs. Idempotent: only touches rows still missing a user."""
	frappe.db.sql(
		"update `tabRun` set `user` = %s where `user` is null or `user` = ''",
		(ORIGINAL_OWNER,),
	)
	frappe.db.commit()
	remaining = frappe.db.count("Run", filters={"user": ["in", ["", None]]})
	total = frappe.db.count("Run", filters={"user": ORIGINAL_OWNER})
	return {"backfilled_to": ORIGINAL_OWNER, "total_now_owned": total, "still_unowned": remaining}
