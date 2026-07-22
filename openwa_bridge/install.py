"""Pre-install checks and post-install migration for OpenWA Bridge."""
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


def after_install():
    """One-time post-install migration: bump old outbox entries to new max_attempts.

    Pre-fix outbox entries had ``max_attempts=5`` which was too low for
    long outages.  Bump them to 100 so they can still be retried.
    """
    frappe.db.sql(
        """UPDATE `tabOpenWA Outbox`
           SET max_attempts = 100
           WHERE max_attempts = 5"""
    )
    frappe.db.commit()
