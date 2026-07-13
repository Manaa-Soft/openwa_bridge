"""Scheduled tasks for OpenWA session health and auto-reconnection."""
from __future__ import annotations

import time

import frappe
import requests


def _get_openwa_accounts() -> list[dict]:
    """Return all WhatsApp Accounts that have OpenWA enabled with a session ID."""
    return frappe.get_all(
        "WhatsApp Account",
        filters={"openwa_enabled": 1, "openwa_session_id": ["is", "set"]},
        fields=["name", "openwa_base_url", "openwa_session_id"],
    )


def _check_session_status(base_url: str, session_id: str, api_key: str) -> dict | None:
    """GET /api/sessions/:id and return the session data, or None."""
    try:
        resp = requests.get(
            f"{base_url.rstrip('/')}/api/sessions/{session_id}",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _start_session(base_url: str, session_id: str, api_key: str) -> bool:
    """POST /api/sessions/:id/start and return True on success."""
    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/api/sessions/{session_id}/start",
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            timeout=60,
        )
        return resp.status_code in (200, 201, 400)  # 400 = already started
    except Exception:
        return False


def _set_account_status(account_name: str, openwa_status: str) -> None:
    """Update the WhatsApp Account status field based on OpenWA session status."""
    status_map = {
        "ready": "Active",
        "disconnected": "Inactive",
        "created": "Inactive",
        "failed": "Inactive",
        "initializing": "Inactive",
        "qr_ready": "Inactive",
        "authenticating": "Inactive",
    }
    frappe_status = status_map.get(openwa_status, "Inactive")
    try:
        current = frappe.db.get_value("WhatsApp Account", account_name, "status")
        if current != frappe_status:
            frappe.db.set_value("WhatsApp Account", account_name, "status", frappe_status)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Scheduler hooks
# ---------------------------------------------------------------------------


def daily() -> None:
    """Run session health check once per day.

    Registered via ``scheduler_events`` in ``hooks.py``.
    """
    _run_health_check()


def hourly() -> None:
    """Run session health check every hour for faster recovery."""
    _run_health_check()


def _run_health_check() -> None:
    """Check all OpenWA sessions and restart any that are disconnected."""
    accounts = _get_openwa_accounts()
    if not accounts:
        return

    for acct in accounts:
        account_name = acct.name
        base_url = acct.openwa_base_url
        session_id = acct.openwa_session_id

        if not base_url or not session_id:
            continue

        try:
            doc = frappe.get_doc("WhatsApp Account", account_name)
            api_key = doc.get_password("openwa_api_key") if hasattr(doc, "get_password") else doc.get("openwa_api_key")
        except Exception:
            continue

        if not api_key:
            continue

        # 1. Check current status
        session = _check_session_status(base_url, session_id, api_key)

        if session is None:
            # OpenWA unreachable — mark inactive
            _set_account_status(account_name, "disconnected")
            continue

        status = session.get("status", "unknown")

        # 2. If ready, sync status and move on
        if status == "ready":
            _set_account_status(account_name, "ready")
            continue

        # 3. If disconnected/created/failed, attempt restart
        if status in ("disconnected", "created", "failed"):
            frappe.logger().info(
                f"OpenWA health check: session '{session_id}' on "
                f"'{account_name}' is {status} — attempting restart"
            )
            started = _start_session(base_url, session_id, api_key)
            if started:
                # Give OpenWA a moment to initialize
                time.sleep(3)
                # Re-check status
                session = _check_session_status(base_url, session_id, api_key)
                if session:
                    status = session.get("status", status)
            else:
                frappe.logger().warning(
                    f"OpenWA health check: failed to restart session "
                    f"'{session_id}' on '{account_name}'"
                )

        _set_account_status(account_name, status)

    frappe.db.commit()
