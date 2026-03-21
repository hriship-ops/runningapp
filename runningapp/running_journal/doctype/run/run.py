import frappe, json
from frappe.model.document import Document

class Run(Document):
    pass

@frappe.whitelist(allow_guest=True)
def get_all_runs(filters=None):
    if filters and isinstance(filters, str):
        filters = json.loads(filters)
    return frappe.db.get_all(
        "Run",
        filters=filters or [],
        fields=["name","run_name","date","activity_type","location","distance_km","duration_sec","elevation_gain","calories","avg_heart_rate","max_heart_rate"],
        order_by="date desc",
        ignore_permissions=True
    )

@frappe.whitelist(allow_guest=True)
def get_run(name):
    doc = frappe.get_doc("Run", name)
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