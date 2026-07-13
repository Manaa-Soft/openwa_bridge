"""WhatsApp Account helpers for OpenWA QR code and session management."""
from __future__ import annotations

import re
import time

import frappe
import requests

from openwa_bridge.utils import openwa_api


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_account(account_name: str, require_session: bool = True) -> dict:
    """Return a WhatsApp Account doc with required OpenWA fields validated.

    If *require_session* is ``False`` the ``openwa_session_id`` check is
    skipped — used during the initial setup flow.
    """
    doc = frappe.get_doc("WhatsApp Account", account_name)
    if not doc.openwa_enabled:
        frappe.throw("OpenWA is not enabled on this account.")
    if require_session and not doc.openwa_session_id:
        frappe.throw("OpenWA Session ID is not set.")
    return doc


def _raw_openwa_call(account: dict, method: str, url_path: str,
                     json_data: dict | None = None, timeout: int = 30) -> dict:
    """Call an OpenWA endpoint that is NOT scoped under ``/api/sessions/:id``.

    Used for session listing and creation where the session ID is not yet
    known.
    """
    base_url = account.get("openwa_base_url").strip("/")
    api_key = (account.get_password("openwa_api_key")
               if hasattr(account, "get_password")
               else account.get("openwa_api_key"))

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }
    url = f"{base_url}{url_path}"
    resp = requests.request(method, url, json=json_data, headers=headers,
                            timeout=timeout)
    resp.raise_for_status()
    if resp.status_code == 204:
        return {}
    return resp.json()


def _sanitize_session_name(name: str) -> str:
    """Sanitise an account name into an OpenWA session name.

    Rules (from OpenWA ``CreateSessionDto``):
    - 3-50 characters
    - Only letters, numbers, and hyphens
    """
    slug = name.strip().lower()
    slug = re.sub(r"[^a-z0-9-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    if len(slug) < 3:
        slug = (slug + "---")[:3]
    if len(slug) > 50:
        slug = slug[:50]
    return slug


def _safe_get_session(account: dict) -> dict | None:
    """GET the session by its ID and return the data, or ``None`` on error."""
    try:
        return openwa_api(account, "GET", "")
    except Exception:
        return None


def _start_session(account: dict) -> None:
    """Start an OpenWA session, ignoring 'already started' errors."""
    try:
        openwa_api(account, "POST", "/start", timeout=60)
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 400:
            return  # already started — fine
        raise
    time.sleep(5)


def _fetch_qr(account: dict) -> dict:
    """Fetch the QR code, retrying once if it is not ready yet.

    Returns ``{ qr_code, status }`` on success.
    """
    try:
        qr = openwa_api(account, "GET", "/qr")
        return {"qr_code": qr.get("qrCode"), "status": qr.get("status", "qr_ready")}
    except requests.exceptions.HTTPError as exc:
        error_msg = _extract_error(exc)

        if "not started" in error_msg.lower():
            time.sleep(3)
            qr = openwa_api(account, "GET", "/qr")
            return {"qr_code": qr.get("qrCode"), "status": qr.get("status", "qr_ready")}

        if "already authenticated" in error_msg.lower():
            return {"status": "ready"}

        raise


def _extract_error(exc: requests.exceptions.HTTPError) -> str:
    """Best-effort extraction of an error message from an HTTP response."""
    if exc.response is None:
        return str(exc)
    try:
        return exc.response.json().get("message", exc.response.text)
    except Exception:
        return exc.response.text


# ---------------------------------------------------------------------------
# Whitelisted methods (called from the client JS)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def setup_openwa_session(account_name: str) -> dict:
    """One-click setup: create (or find) an OpenWA session, start it, and
    return the QR code.  Saves the ``openwa_session_id`` on the doc.

    Returns::

        { qr_code: "data:image/png;base64,...", status: "qr_ready",
          session_id: "uuid" }

    or on error::

        { status: "error", error: "..." }
    """
    account = _get_account(account_name, require_session=False)
    base_url = account.get("openwa_base_url")
    if not base_url:
        frappe.throw("OpenWA Base URL is not set.")

    # Warn if using HTTP in production (not localhost)
    if base_url.startswith("http://"):
        from urllib.parse import urlparse
        host = urlparse(base_url).hostname or ""
        if host not in ("localhost", "127.0.0.1", "::1"):
            frappe.msgprint(
                "Warning: OpenWA Base URL uses HTTP instead of HTTPS. "
                "API keys and messages are transmitted in plaintext. "
                "Use HTTPS in production environments.",
                indicator="orange",
                alert=True,
            )

    # Quick connectivity check — fail fast with a clear message.
    try:
        requests.get(base_url.rstrip("/") + "/api/sessions",
                     headers={"X-API-Key": (account.get_password("openwa_api_key")
                               if hasattr(account, "get_password")
                               else account.get("openwa_api_key") or "")},
                     timeout=10)
    except requests.exceptions.ConnectionError:
        return {"status": "error",
                "error": f"Cannot connect to OpenWA at {base_url}. "
                         "Make sure OpenWA is running and reachable."}
    except requests.exceptions.Timeout:
        return {"status": "error",
                "error": f"OpenWA at {base_url} did not respond in time. "
                         "It may be starting up — try again in a few seconds."}

    session_name = _sanitize_session_name(account.account_name)

    # --- 1. Look for an existing session with the same name ----------------
    session_id: str | None = None
    try:
        sessions = _raw_openwa_call(account, "GET", "/api/sessions")
        for s in sessions:
            if s.get("name") == session_name:
                session_id = s.get("id")
                break
    except Exception:
        pass  # will create below

    # --- 2. Create the session if it does not exist ------------------------
    if not session_id:
        try:
            created = _raw_openwa_call(
                account, "POST", "/api/sessions",
                json_data={"name": session_name},
            )
            session_id = created.get("id")
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 409:
                # 409 = name already exists — re-fetch list to find it
                sessions = _raw_openwa_call(account, "GET", "/api/sessions")
                for s in sessions:
                    if s.get("name") == session_name:
                        session_id = s.get("id")
                        break
            if not session_id:
                return {"status": "error", "error": _extract_error(exc)}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    if not session_id:
        return {"status": "error", "error": "Could not determine session ID."}

    # --- 3. Persist the session ID on the WhatsApp Account doc -------------
    frappe.db.set_value("WhatsApp Account", account_name,
                        "openwa_session_id", session_id)
    frappe.db.commit()

    # Re-read the doc so subsequent calls see the new session_id.
    account = _get_account(account_name, require_session=True)

    # --- 4. Start the session and fetch the QR code ------------------------
    status = "unknown"
    try:
        # Check current status first
        session_data = _safe_get_session(account)
        if session_data:
            status = session_data.get("status", "unknown")
    except Exception:
        pass

    if status == "ready":
        return {
            "status": "ready",
            "session_id": session_id,
            "phone": session_data.get("phone"),
            "push_name": session_data.get("pushName"),
        }

    if status in ("disconnected", "created", "failed", "unknown"):
        try:
            _start_session(account)
        except Exception as exc:
            return {"status": "error", "error": str(exc), "session_id": session_id}

    try:
        qr = _fetch_qr(account)
        qr["session_id"] = session_id
        return qr
    except Exception as exc:
        return {"status": "error", "error": str(exc), "session_id": session_id}


@frappe.whitelist()
def get_openwa_session_status(account_name: str) -> dict:
    """Return the current OpenWA session status for the given WhatsApp Account.

    Returns:
        dict: { status, phone, push_name, connected_at, last_active } or
              { status: "error", error: "..." }
    """
    account = _get_account(account_name)
    session = _safe_get_session(account)
    if session is None:
        return {"status": "error", "error": "Could not reach OpenWA server."}

    return {
        "status": session.get("status", "unknown"),
        "phone": session.get("phone"),
        "push_name": session.get("pushName"),
        "connected_at": session.get("connectedAt"),
        "last_active": session.get("lastActive"),
    }


@frappe.whitelist()
def get_openwa_qr(account_name: str) -> dict:
    """Fetch QR code from OpenWA for the given WhatsApp Account.

    Automatically starts the session if it is not running.  Returns a
    PNG data-URL suitable for rendering as ``<img src="...">``.

    Returns:
        dict: { qr_code: "data:image/png;base64,...", status: "qr_ready" } or
              { status: "ready", phone, push_name } or
              { status: "error", error: "..." }
    """
    account = _get_account(account_name)

    # --- 1. Check current session status -----------------------------------
    session = _safe_get_session(account)
    if session is None:
        return {"status": "error", "error": "Could not reach OpenWA server."}

    status = session.get("status", "unknown")

    # Already connected — nothing to show.
    if status == "ready":
        return {
            "status": "ready",
            "phone": session.get("phone"),
            "push_name": session.get("pushName"),
        }

    # --- 2. Start the session if it is not running -------------------------
    if status in ("disconnected", "created", "failed"):
        try:
            _start_session(account)
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # --- 3. Fetch the QR code ----------------------------------------------
    try:
        return _fetch_qr(account)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def stop_openwa_session(account_name: str) -> dict:
    """Stop / disconnect the OpenWA session for the given WhatsApp Account.

    Returns:
        dict: { status: "disconnected" } or { status: "error", error: "..." }
    """
    account = _get_account(account_name)
    try:
        openwa_api(account, "POST", "/stop")
        return {"status": "disconnected"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Doc event hook — called by Frappe when a WhatsApp Account is deleted
# ---------------------------------------------------------------------------


def on_account_trash(doc, method):  # noqa: ANN001
    """Delete the OpenWA session when a WhatsApp Account is deleted from Frappe.

    This is registered via ``doc_events`` in hooks.py.  It runs on the
    ``on_trash`` event so the session is cleaned up automatically.
    """
    if not getattr(doc, "openwa_enabled", 0):
        return
    session_id = getattr(doc, "openwa_session_id", None)
    if not session_id:
        return

    try:
        _raw_openwa_call(doc, "DELETE", f"/api/sessions/{session_id}")
    except Exception:
        # Best-effort: don't block the Frappe delete if OpenWA is unreachable.
        frappe.log_error(
            title="OpenWA: Failed to delete session on account trash",
            message=f"Session {session_id} could not be deleted from OpenWA "
                    f"when WhatsApp Account '{doc.account_name}' was removed.",
        )
