"""Pre-uninstall cleanup for OpenWA Bridge.

Deletes OpenWA sessions and webhooks before the app is removed, so
orphaned resources don't linger on the gateway.
"""
from __future__ import annotations

import frappe


def before_uninstall() -> None:
    """Best-effort cleanup of all OpenWA sessions and webhooks.

    Iterates every WhatsApp Account with OpenWA enabled and issues
    DELETE requests to the gateway.  Errors are logged but never raised
    — we don't want a transient gateway issue to block the uninstall.
    """
    accounts = frappe.get_all(
        "WhatsApp Account",
        filters={"openwa_enabled": 1},
        fields=["name"],
    )
    if not accounts:
        return

    from openwa_bridge.utils import get_api_key, _http_session

    for acct_row in accounts:
        try:
            doc = frappe.get_doc("WhatsApp Account", acct_row.name)
        except Exception:
            continue

        base_url = (doc.openwa_base_url or "").strip("/")
        session_id = doc.openwa_session_id
        if not base_url or not session_id:
            continue

        api_key = ""
        try:
            api_key = get_api_key(doc)
        except Exception:
            pass

        headers = {"X-API-Key": api_key} if api_key else {}

        # 1. Delete webhooks for this session
        try:
            resp = _http_session.get(
                f"{base_url}/api/sessions/{session_id}/webhooks",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                for wh in resp.json():
                    wh_id = wh.get("id")
                    if wh_id:
                        _http_session.delete(
                            f"{base_url}/api/sessions/{session_id}/webhooks/{wh_id}",
                            headers=headers,
                            timeout=10,
                        )
        except Exception:
            frappe.log_error(
                title="OpenWA Uninstall: webhook cleanup failed",
                message=f"Account: {acct_row.name}, Session: {session_id}",
            )

        # 2. Delete the session itself
        try:
            _http_session.delete(
                f"{base_url}/api/sessions/{session_id}",
                headers=headers,
                timeout=10,
            )
        except Exception:
            frappe.log_error(
                title="OpenWA Uninstall: session cleanup failed",
                message=f"Account: {acct_row.name}, Session: {session_id}",
            )

    frappe.logger().info(
        f"OpenWA uninstall cleanup: processed {len(accounts)} account(s)"
    )
