import frappe, json, os, re, time
from frappe.model.document import Document
from runningapp.running_journal.strava_sync import current_user

class Run(Document):
    pass


@frappe.whitelist()
def backup_and_delete_all_runs(user=None):
    """One-off: dump every Run record owned by `user` to a JSON file under
    private storage, then delete them all. For a from-scratch reimport
    (e.g. a bulk Strava export meant to replace what's already there) —
    the backup means a botched or incomplete import isn't a permanent
    data loss."""
    user = user or current_user()
    runs = frappe.get_all("Run", filters={"user": user}, fields=["*"])

    safe_user = re.sub(r"[^A-Za-z0-9_.-]", "_", user)
    backup_dir = frappe.get_site_path("private", "files", "run_backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(backup_dir, f"{safe_user}_{int(time.time())}.json")
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(runs, f, default=str)

    frappe.db.delete("Run", {"user": user})
    frappe.db.commit()
    return {"backed_up": len(runs), "backup_path": backup_path, "deleted": len(runs)}

@frappe.whitelist(allow_guest=True)
def get_all_runs(filters=None):
    if filters and isinstance(filters, str):
        filters = json.loads(filters)
    filters = list(filters or [])
    filters.append(["user", "=", current_user()])
    return frappe.db.get_all(
        "Run",
        filters=filters,
        fields=["name","run_name","date","activity_type","location","country","state","district","distance_km","duration_sec","elevation_gain","calories","avg_heart_rate","max_heart_rate"],
        order_by="date desc",
        ignore_permissions=True
    )

@frappe.whitelist(allow_guest=True)
def get_run(name):
    doc = frappe.get_doc("Run", name)
    if doc.user and doc.user != current_user():
        frappe.throw("Not found", frappe.DoesNotExistError)
    return {
        "name": doc.name,
        "run_name": doc.run_name,
        "date": str(doc.date),
        "activity_type": doc.activity_type,
        "location": doc.location,
        "distance_km": doc.distance_km,
        "duration_sec": doc.duration_sec,
        "elevation_gain": doc.elevation_gain,
        "calories": doc.calories,
        "avg_heart_rate": doc.avg_heart_rate,
        "max_heart_rate": doc.max_heart_rate,
        "notes": doc.notes,
        "route_points": doc.route_points,
    }
