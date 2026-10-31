"""
best_efforts.py
---------------
Fastest 1 km / 1 mile / 5 km / 10 km segment inside each run, stored on the
Run as best_1k_sec, best_mile_sec, best_5k_sec, best_10k_sec (seconds).

Ported from the laptop backfill_best_efforts.py, with two changes so it works
on every import path, not just Strava streams:
  - cumulative distance comes from the point's `dst` when present, otherwise
    from GPS (haversine between consecutive lat/lon) — Garmin TCX and
    TCX/GPX bulk imports have no `dst`;
  - each window is interpolated to exactly the target distance inside the
    crossing segment, instead of requiring a point within 5% of it, so
    downsampled tracks (~500 points) still produce results.

GPS glitches: any hop between consecutive points faster than MAX_SPEED_MS
(~2:23/km) adds no distance (time still runs), so a GPS jump can only make
a segment slower, never faster. Segments faster than 2:30/km are dropped too.
"""

import json
import math

import frappe

TARGETS = [
	("best_1k_sec", 1000.0),
	("best_mile_sec", 1609.34),
	("best_5k_sec", 5000.0),
	("best_10k_sec", 10000.0),
]
FIELDS = [f for f, _ in TARGETS]
MIN_SEC_PER_KM = 150  # 2:30/km
MAX_SPEED_MS = 7.0  # faster point-to-point hops are GPS jumps


def _haversine_m(a, b):
	R = 6371000.0
	lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
	dlat = lat2 - lat1
	dlon = math.radians(b["lon"] - a["lon"])
	h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
	return 2 * R * math.asin(math.sqrt(h))


def _series(points):
	"""[(cum_dist_m, t_sec)]: strictly increasing time, distance with GPS jumps removed."""
	if not points:
		return []
	use_dst = sum(1 for p in points if p.get("dst") is not None) >= len(points) * 0.9
	out, cum, prev = [], 0.0, None
	for p in points:
		t = p.get("t")
		if t is None:
			continue
		t = float(t)
		if use_dst:
			if p.get("dst") is None:
				continue
		elif p.get("lat") is None or p.get("lon") is None:
			continue
		if prev is not None:
			if t <= prev[1]:
				continue
			step = (float(p["dst"]) - prev[0]) if use_dst else _haversine_m(prev[2], p)
			if 0 < step <= MAX_SPEED_MS * (t - prev[1]):
				cum += step
		prev = (float(p["dst"]) if use_dst else 0.0, t, p)
		out.append((cum, t))
	return out


def _best_segment(series, target_m):
	"""Fastest time to cover exactly target_m, or None."""
	n = len(series)
	if n < 2 or series[-1][0] - series[0][0] < target_m:
		return None
	best = None
	j = 1
	for i in range(n - 1):
		goal = series[i][0] + target_m
		if j <= i:
			j = i + 1
		while j < n and series[j][0] < goal:
			j += 1
		if j >= n:
			break
		(d0, t0), (d1, t1) = series[j - 1], series[j]
		t_end = t0 + (t1 - t0) * ((goal - d0) / (d1 - d0)) if d1 > d0 else t1
		sec = t_end - series[i][1]
		if sec <= 0 or sec / (target_m / 1000.0) < MIN_SEC_PER_KM:
			continue
		if best is None or sec < best:
			best = sec
	return round(best, 1) if best else None


def compute_best_efforts(points, activity_type="Run"):
	"""Return {field: seconds} for a run's route points (0 where not available)."""
	result = {f: 0 for f in FIELDS}
	if activity_type != "Run" or not points:
		return result
	if isinstance(points, str):
		try:
			points = json.loads(points)
		except ValueError:
			return result
	if not isinstance(points, list):
		return result
	series = _series(points)
	for field, target in TARGETS:
		result[field] = _best_segment(series, target) or 0
	return result


def backfill_best_efforts(only_missing=1):
	"""Compute best efforts for existing runs.

	bench --site frontend execute runningapp.running_journal.best_efforts.backfill_best_efforts
	Pass only_missing=0 to recompute everything.
	"""
	filters = {"activity_type": "Run", "route_points": ["not in", ["", "null", "[]"]]}
	if int(only_missing):
		filters["best_1k_sec"] = ["in", [0, None]]
	names = frappe.get_all("Run", filters=filters, pluck="name")
	updated = 0
	for i, name in enumerate(names, 1):
		pts = frappe.db.get_value("Run", name, "route_points")
		vals = compute_best_efforts(pts)
		if any(vals.values()):
			frappe.db.set_value("Run", name, vals, update_modified=False)
			updated += 1
		if i % 200 == 0:
			frappe.db.commit()
	frappe.db.commit()
	return {"checked": len(names), "updated": updated}
