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


def after_migrate():
    """Backfill the OpenWA Bridge desktop tiles for sites on the Desktop Icons page.

    The v17-dev fork's desk has two modes. The Apps page is hook-driven and
    already covers this app via ``add_to_apps_screen``. The Desktop Icons page
    renders ``Desktop Icon`` rows instead, which Frappe only seeds at
    app-install time and only when the site is already on that page. The fork's
    upgrade patch (``v16_0/keep_existing_sites_on_desktop_icons``) forces
    existing sites onto Desktop Icons, so apps installed before the switch end
    up without a tile. This idempotently creates the rows those sites are
    missing, mirroring what a fresh install in Desktop Icons mode would seed.

    No-ops on Frappe releases that lack the ``desktop_page`` setting (pre-fork)
    and on sites on the Apps page (the hook drives those).
    """
    seed_desktop_icons()


def seed_desktop_icons():
    """Create the App tile and its workspace shortcut, if absent."""
    try:
        from frappe.desk.doctype.desktop_settings.desktop_settings import (
            DESKTOP_ICONS,
            get_desktop_page,
        )
    except ImportError:
        return

    if get_desktop_page() != DESKTOP_ICONS:
        return

    app_title = (frappe.get_hooks("app_title", app_name="openwa_bridge") or [None])[0]
    app_details = frappe.get_hooks("add_to_apps_screen", app_name="openwa_bridge")
    if not app_title or not app_details:
        return

    if not frappe.db.exists("Desktop Icon", {"label": app_title, "icon_type": "App"}):
        try:
            icon = frappe.new_doc("Desktop Icon")
            icon.label = app_title
            icon.icon_type = "App"
            icon.app = "openwa_bridge"
            icon.link = app_details[0]["route"]
            icon.link_type = "External"
            icon.logo_url = app_details[0]["logo"]
            icon.idx = 100
            icon.insert(ignore_if_duplicate=True)
        except Exception as e:
            frappe.log_error(title="OpenWA Bridge: failed to create desktop icon", message=e)

    if not frappe.db.exists("Desktop Icon", {"label": "WhatsApp", "icon_type": "Link"}):
        try:
            icon = frappe.new_doc("Desktop Icon")
            icon.label = "WhatsApp"
            icon.icon_type = "Link"
            icon.link_to = "WhatsApp"
            icon.link_type = "Workspace Sidebar"
            icon.app = "openwa_bridge"
            icon.icon = "brand-whatsapp"
            icon.parent_icon = app_title
            icon.idx = 101
            icon.insert(ignore_if_duplicate=True)
        except Exception as e:
            frappe.log_error(title="OpenWA Bridge: failed to create workspace desktop icon", message=e)

    frappe.db.commit()
