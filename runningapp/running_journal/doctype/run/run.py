# Copyright (c) 2026, hrishi and contributors
import frappe, json
from frappe.model.document import Document

class Run(Document):
    pass

@frappe.whitelist()
def get_all_runs(filters=None):
    if filters and isinstance(filters, str):
        filters = json.loads(filters)
    return frappe.db.get_all(
        "Run",
        filters=filters or [],
        fields=["name","run_name","date","activity_type","location","distance_km","duration_sec","elevation_gain","calories"],
        order_by="date desc",
        ignore_permissions=True
    )
