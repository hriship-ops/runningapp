import frappe

no_cache = 1
no_breadcrumbs = 1


def get_context(context):
    context.no_cache = 1
    # The page's JS POSTs to whitelisted methods with X-Frappe-CSRF-Token read
    # from <meta name="csrf-token" content="{{ csrf_token }}">. Frappe v16 no
    # longer puts csrf_token into www page contexts, so without this every
    # save on this page failed with HTTP 400 (CSRFTokenError).
    if frappe.session.user != "Guest":
        context.csrf_token = frappe.sessions.get_csrf_token()
