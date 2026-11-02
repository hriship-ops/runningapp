"""
source_match.py
---------------
Garmin auto-uploads to Strava, so the same workout can arrive twice: once via
Garmin sync and once via Strava sync. Rather than skipping (or duplicating),
both syncs LINK the incoming activity to the existing Run by filling in the
missing source ID (garmin_id / strava_id). Next time, the exact-ID check
recognises it instantly.

Matching order:
  1. Exact: Strava records Garmin-originated uploads with an external_id like
     "garmin_push_<garmin activityId>" — match that against Run.garmin_id.
  2. Fuzzy: same user, date and activity type, close distance (and duration),
     restricted to Runs NOT yet linked to the incoming source — so two genuinely
     separate same-day workouts that are both already linked never merge.
"""

import re

import frappe

_GARMIN_EXT_RE = re.compile(r"garmin_(?:push|ping)_(\d+)", re.I)


def garmin_id_from_strava(activity):
	"""Garmin activity id embedded in a Strava activity's external_id, if any."""
	m = _GARMIN_EXT_RE.search(activity.get("external_id") or "")
	return m.group(1) if m else None


def find_existing(user, activity_type, date, distance_km, duration_sec, unlinked_field):
	"""Name of the best-matching Run for this activity, or None.

	unlinked_field: "garmin_id" when importing from Garmin, "strava_id" when
	importing from Strava — only Runs with that field empty are candidates.
	"""
	candidates = frappe.get_all(
		"Run",
		filters={"user": user, "date": date, "activity_type": activity_type},
		fields=["name", "distance_km", "duration_sec", unlinked_field],
	)
	best, best_score = None, None
	for c in candidates:
		if c.get(unlinked_field):
			continue
		cd, ct = c.distance_km or 0, c.duration_sec or 0
		dist_diff = abs(cd - distance_km)
		dur_diff = abs(ct - duration_sec)
		# Near-exact distance is enough on its own: pool swims are lap-counted
		# (identical distance) while sources disagree on duration (rest time).
		near_exact = distance_km > 0 and dist_diff <= max(0.02, distance_km * 0.01)
		close = dist_diff <= max(0.05, distance_km * 0.03) and dur_diff <= max(30, duration_sec * 0.05)
		# No-distance activities (gym, yoga): duration only.
		no_dist = distance_km == 0 and cd == 0 and dur_diff <= max(60, duration_sec * 0.05)
		if not (near_exact or close or no_dist):
			continue
		score = dist_diff / max(distance_km, 0.1) + dur_diff / max(duration_sec, 60)
		if best_score is None or score < best_score:
			best, best_score = c.name, score
	return best


def link(run_name, field, value):
	frappe.db.set_value("Run", run_name, field, value, update_modified=False)
