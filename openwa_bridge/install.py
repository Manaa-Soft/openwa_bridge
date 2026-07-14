"""Pre-install checks for OpenWA Bridge."""
from __future__ import annotations

import frappe


def before_install():
    """Verify that the required ``frappe_whatsapp`` app is installed."""
    if not frappe.db.exists("Module Def", "frappe_whatsapp"):
        frappe.throw(
            "OpenWA Bridge requires the <b>frappe_whatsapp</b> app to be installed first. "
            "Please install frappe_whatsapp before installing OpenWA Bridge.",
            title="Missing Dependency",
        )
