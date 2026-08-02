"""WhatsApp Account helpers for OpenWA QR code and session management."""
from __future__ import annotations

import re
import time

import frappe
import requests

from openwa_bridge.utils import openwa_api, get_api_key, validate_openwa_url, _http_session, get_account_setting


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
                     json_data: dict | None = None, timeout: int | None = None) -> dict:
    """Call an OpenWA endpoint that is NOT scoped under ``/api/sessions/:id``.

    Used for session listing and creation where the session ID is not yet
    known.
    """
    if timeout is None:
        timeout = get_account_setting(account, "openwa_api_timeout", 30)
    base_url = account.get("openwa_base_url").strip("/")
    api_key = get_api_key(account)

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }
    url = f"{base_url}{url_path}"
    resp = _http_session.request(method, url, json=json_data, headers=headers,
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
    """GET the session by its ID and return the data, or ``None`` on error.

    Returns ``None`` for connection errors, timeouts, and 404 (session deleted).
    """
    try:
        return openwa_api(account, "GET", "")
    except requests.exceptions.HTTPError as exc:
        # 404 means the session was deleted from OpenWA — treat as not found
        if exc.response is not None and exc.response.status_code == 404:
            return None
        return None
    except Exception:
        return None


def _recover_deleted_session(account: dict, account_name: str) -> str | None:
    """When the stored session ID returns 404, search OpenWA for an existing
    session with the same name and return its ID (or ``None``)."""
    try:
        sessions = _raw_openwa_call(account, "GET", "/api/sessions")
    except Exception:
        return None
    session_name = _sanitize_session_name(account_name)
    for s in sessions:
        if s.get("name") == session_name:
            new_id = s.get("id")
            if new_id:
                frappe.logger().info(
                    f"OpenWA: recovered deleted session for '{account_name}' — "
                    f"found existing session '{session_name}' (id={new_id})"
                )
                return new_id
    return None


def _start_session(account: dict) -> None:
    """Start an OpenWA session, ignoring 'already started' errors."""
    timeout = get_account_setting(account, "openwa_session_start_timeout", 60)
    try:
        openwa_api(account, "POST", "/start", timeout=timeout)
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 400:
            return  # already started — fine
        raise
    # Poll for readiness (up to 15s, 1s intervals)
    for _ in range(15):
        time.sleep(1)
        try:
            status = openwa_api(account, "GET", "")
            if status.get("status") in ("ready", "qr_ready"):
                return
        except Exception:
            pass


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
            # Poll for QR readiness (up to 10s, 1s intervals)
            for _ in range(10):
                time.sleep(1)
                try:
                    qr = openwa_api(account, "GET", "/qr")
                    return {"qr_code": qr.get("qrCode"), "status": qr.get("status", "qr_ready")}
                except Exception:
                    pass
            # Final attempt — let it raise
            qr = openwa_api(account, "GET", "/qr")
            return {"qr_code": qr.get("qrCode"), "status": qr.get("status", "qr_ready")}

        if "already authenticated" in error_msg.lower():
            return {"status": "ready"}

        raise


def _delete_and_recreate_session(account: dict) -> str | None:
    """Delete a failed/stuck session and create a fresh one.

    Returns the new session ID, or ``None`` on failure.
    """
    session_id = account.get("openwa_session_id")
    session_name = _sanitize_session_name(account.get("account_name", ""))

    # Delete the old session (best-effort)
    if session_id:
        try:
            _raw_openwa_call(account, "DELETE", f"/api/sessions/{session_id}")
        except Exception:
            pass  # already deleted or unreachable — continue

    # Create a fresh session
    try:
        created = _raw_openwa_call(
            account, "POST", "/api/sessions",
            json_data={"name": session_name},
        )
        return created.get("id")
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 409:
            # 409 = name collision — re-fetch to find it
            try:
                sessions = _raw_openwa_call(account, "GET", "/api/sessions")
                for s in sessions:
                    if s.get("name") == session_name:
                        return s.get("id")
            except Exception:
                pass
        return None
    except Exception:
        return None


def _force_kill_session(account: dict) -> bool:
    """Force-kill a stuck session via OpenWA's /force-kill endpoint.

    This SIGKILLs the crashed Chrome/Puppeteer process but keeps the
    session data (auth tokens, etc.) so no QR re-scan is needed.
    Returns True on success.
    """
    try:
        openwa_api(account, "POST", "/force-kill")
        return True
    except Exception:
        return False


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
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions to manage WhatsApp Account.", frappe.PermissionError)
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
        _http_session.get(base_url.rstrip("/") + "/api/sessions",
                     headers={"X-API-Key": get_api_key(account)},
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
        # Auto-sync webhook even when session is already connected
        sync_webhook(account)
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
        # Auto-sync webhook now that the session exists
        sync_webhook(account)
        return qr
    except Exception as exc:
        return {"status": "error", "error": str(exc), "session_id": session_id}


@frappe.whitelist()
def get_openwa_session_status(account_name: str) -> dict:
    """
    Retrieve the current OpenWA session state for a WhatsApp Account.
    
    Parameters:
        account_name (str): The WhatsApp Account name.
    
    Returns:
        dict: Session status and connection details, or a structured error status
        such as ``not_found``, ``auth_error``, or ``error``.
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions to read WhatsApp Account.", frappe.PermissionError)
    account = _get_account(account_name)

    base_url = account.get("openwa_base_url", "").strip("/")
    session_id = account.get("openwa_session_id")
    api_key = get_api_key(account)
    timeout = get_account_setting(account, "openwa_api_timeout", 30)

    headers = {"Content-Type": "application/json", "X-API-Key": api_key}
    url = f"{base_url}/api/sessions/{session_id}"

    try:
        resp = _http_session.get(url, headers=headers, timeout=timeout)
    except requests.exceptions.ConnectionError as exc:
        frappe.logger().warning(
            f"OpenWA status check failed (connection): {account_name} — {exc}"
        )
        return {"status": "error", "error": "Could not reach OpenWA server."}
    except requests.exceptions.Timeout:
        frappe.logger().warning(
            f"OpenWA status check failed (timeout): {account_name}"
        )
        return {"status": "error", "error": "OpenWA server timed out."}
    except Exception as exc:
        frappe.logger().warning(
            f"OpenWA status check failed: {account_name} — {exc}"
        )
        return {"status": "error", "error": str(exc)}

    if resp.status_code == 200:
        session = resp.json()
        return {
            "status": session.get("status", "unknown"),
            "phone": session.get("phone"),
            "push_name": session.get("pushName"),
            "connected_at": session.get("connectedAt"),
            "last_active": session.get("lastActive"),
            "engine_loaded": session.get("engineLoaded"),
            "last_error": session.get("lastError"),
        }

    if resp.status_code == 404:
        # Session was deleted from OpenWA — try to find existing session
        # by name and auto-recover the stale ID.
        recovered_id = _recover_deleted_session(account, account_name)
        if recovered_id:
            frappe.db.set_value("WhatsApp Account", account_name,
                                "openwa_session_id", recovered_id)
            frappe.db.commit()
            # Re-check status with the new session ID
            url = f"{base_url}/api/sessions/{recovered_id}"
            try:
                resp2 = _http_session.get(url, headers=headers, timeout=timeout)
                if resp2.status_code == 200:
                    session = resp2.json()
                    return {
                        "status": session.get("status", "unknown"),
                        "phone": session.get("phone"),
                        "push_name": session.get("pushName"),
                        "connected_at": session.get("connectedAt"),
                        "last_active": session.get("lastActive"),
                        "engine_loaded": session.get("engineLoaded"),
                        "last_error": session.get("lastError"),
                    }
            except Exception:
                pass
        return {"status": "not_found"}

    if resp.status_code in (401, 403):
        frappe.logger().warning(
            f"OpenWA status check auth error ({resp.status_code}): {account_name}"
        )
        return {"status": "auth_error"}

    if resp.status_code == 429:
        # Rate-limited — don't treat as error.  Fall back to the Frappe
        # doc status so the UI stays accurate during rate-limit bursts.
        frappe_status = frappe.db.get_value(
            "WhatsApp Account", account_name, "status", cache=True
        )
        if frappe_status == "Active":
            return {"status": "ready"}
        return {"status": "error", "error": "OpenWA rate limit — try again shortly."}

    frappe.logger().warning(
        f"OpenWA status check HTTP {resp.status_code}: {account_name}"
    )
    return {"status": "error", "error": f"OpenWA returned {resp.status_code}."}


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
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions to read WhatsApp Account.", frappe.PermissionError)
    account = _get_account(account_name)

    # --- 1. Check current session status -----------------------------------
    session = _safe_get_session(account)
    if session is None:
        # Old session ID may be stale — try to find an existing session
        # by name (e.g. user deleted from OpenWA dashboard, then clicked
        # Reconnect in Frappe).
        session_id = _recover_deleted_session(account, account_name)
        if session_id:
            frappe.db.set_value("WhatsApp Account", account_name,
                                "openwa_session_id", session_id)
            frappe.db.commit()
            account = _get_account(account_name, require_session=True)
            session = _safe_get_session(account)
        if session is None:
            # No session found by ID or by name — create a fresh one.
            frappe.logger().info(
                f"OpenWA QR: no session found for '{account_name}' "
                f"— creating a new one"
            )
            try:
                return setup_openwa_session(account_name)
            except Exception as exc:
                return {"status": "error", "error": str(exc)}

    status = session.get("status", "unknown")

    # Already connected — nothing to show, but ensure webhook is synced.
    if status == "ready":
        sync_webhook(account)
        return {
            "status": "ready",
            "phone": session.get("phone"),
            "push_name": session.get("pushName"),
        }

    # --- 2. Failed / stuck session — force-kill, then restart ----------------
    if status == "failed":
        killed = _force_kill_session(account)
        if killed:
            # Wait for cleanup, then start fresh
            time.sleep(2)
            try:
                _start_session(account)
            except Exception:
                pass
            # Re-check — if still failed, fall through to delete+recreate
            try:
                session = _safe_get_session(account)
                if session and session.get("status") != "failed":
                    # Force-kill worked — sync webhook and return QR
                    sync_webhook(account)
                    try:
                        return _fetch_qr(account)
                    except Exception:
                        pass
            except Exception:
                pass
        # Force-kill didn't help — delete and recreate as last resort
        try:
            new_id = _delete_and_recreate_session(account)
            if new_id:
                frappe.db.set_value("WhatsApp Account", account_name,
                                    "openwa_session_id", new_id)
                frappe.db.commit()
                account = _get_account(account_name, require_session=True)
                _start_session(account)
                sync_webhook(account)
        except Exception as exc:
            return {"status": "error", "error": f"Failed to recover session: {exc}"}

    # --- 3. Start the session if it is not running -------------------------
    elif status in ("disconnected", "created"):
        try:
            _start_session(account)
            sync_webhook(account)
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    # --- 4. Fetch the QR code ----------------------------------------------
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
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions to manage WhatsApp Account.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "POST", "/stop")
        return {"status": "disconnected"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def reset_openwa_session(account_name: str) -> dict:
    """Clear the stale openwa_session_id so a new session can be created.

    Use this when a session was manually deleted from the OpenWA dashboard
    and the Frappe account still holds the old UUID.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions to manage WhatsApp Account.", frappe.PermissionError)
    frappe.db.set_value("WhatsApp Account", account_name, "openwa_session_id", "")
    frappe.db.commit()
    # Clear the cached account so subsequent calls see the empty session_id
    frappe.cache().delete_value(f"openwa_account:{account_name}")
    return {"status": "reset"}


@frappe.whitelist()
def delete_openwa_session(account_name: str) -> dict:
    """
    Permanently delete the OpenWA session and clear its local account state.
    
    The session ID and account status are cleared locally even when the OpenWA session is already missing or unavailable.
    
    Returns:
    	dict: A status dictionary with ``"status": "deleted"`` on success, or an error dictionary when deletion fails.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions to manage WhatsApp Account.", frappe.PermissionError)
    account = _get_account(account_name)
    session_id = account.get("openwa_session_id")
    if not session_id:
        frappe.throw("No session ID set for this account.")

    # Delete from OpenWA (ignore 404 — already gone)
    try:
        _raw_openwa_call(account, "DELETE", f"/api/sessions/{session_id}")
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code != 404:
            return {"status": "error", "error": _extract_error(exc)}
    except Exception:
        pass  # OpenWA may be down — clear the stale ID anyway

    # Clear from Frappe
    frappe.db.set_value("WhatsApp Account", account_name, "openwa_session_id", "")
    frappe.db.set_value("WhatsApp Account", account_name, "status", "Inactive")
    frappe.db.commit()
    frappe.cache().delete_value(f"openwa_account:{account_name}")

    frappe.logger().info(
        f"OpenWA: deleted session '{session_id}' for '{account_name}'"
    )
    return {"status": "deleted"}


@frappe.whitelist()
def recover_openwa_session(account_name: str) -> dict:
    """
    Restart an OpenWA session requiring user action by stopping and starting it with its stored credentials.
    
    Returns:
        dict: A status dictionary with ``{"status": "restarted"}`` on success or
            ``{"status": "error", "error": "..."}`` when stopping or restarting fails.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions to manage WhatsApp Account.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "POST", "/stop")
    except Exception as exc:
        return {"status": "error", "error": f"Failed to stop session: {exc}"}
    time.sleep(2)
    try:
        _start_session(account)
    except Exception as exc:
        return {"status": "error", "error": f"Failed to restart session: {exc}"}
    return {"status": "restarted"}


@frappe.whitelist()
def logout_openwa_session(account_name: str) -> dict:
    """
    Unlink the WhatsApp device and clear its local OpenWA session data.
    
    Returns:
    	dict: A status dictionary indicating whether the device was unlinked, the unlink requires retrying, or an error occurred.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions to manage WhatsApp Account.", frappe.PermissionError)
    account = _get_account(account_name)
    session_id = account.get("openwa_session_id")
    if not session_id:
        frappe.throw("No session ID set for this account.")

    base_url = account.get("openwa_base_url").strip("/")
    api_key = get_api_key(account)
    headers = {"Content-Type": "application/json", "X-API-Key": api_key}
    url = f"{base_url}/api/sessions/{session_id}/logout"

    try:
        resp = _http_session.post(url, headers=headers, timeout=60)
    except requests.exceptions.ConnectionError:
        return {"status": "error", "error": "Could not reach OpenWA server."}
    except requests.exceptions.Timeout:
        return {"status": "error", "error": "OpenWA server timed out."}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    if resp.status_code == 200:
        # Device unlinked — credentials wiped. Clear the doc so the user is
        # taken through a fresh QR setup.
        frappe.db.set_value("WhatsApp Account", account_name, "openwa_session_id", "")
        frappe.db.set_value("WhatsApp Account", account_name, "status", "Inactive")
        frappe.db.commit()
        frappe.cache().delete_value(f"openwa_account:{account_name}")
        frappe.logger().info(
            f"OpenWA: session '{session_id}' unlinked for '{account_name}'"
        )
        return {"status": "unlinked"}

    if resp.status_code == 502:
        try:
            code = resp.json().get("code", "")
        except Exception:
            code = ""
        if code == "SESSION_LOGOUT_INCOMPLETE":
            return {
                "status": "incomplete",
                "error": (
                    "The session was stopped locally, but WhatsApp did not "
                    "confirm the unlink. Start the session again and retry."
                ),
            }
        return {"status": "error", "error": "OpenWA returned 502 on logout."}

    if resp.status_code == 400:
        return {"status": "error", "error": "Session is not started — nothing to unlink."}

    if resp.status_code in (401, 403):
        return {"status": "error", "error": "OpenWA API key invalid or not authorized."}

    return {"status": "error", "error": f"OpenWA returned {resp.status_code} on logout."}


# ---------------------------------------------------------------------------
# Doc event hook — called by Frappe when a WhatsApp Account is validated
# ---------------------------------------------------------------------------


def on_account_validate(doc, method):  # noqa: ANN001
    """Validate the OpenWA base URL on save."""
    if getattr(doc, "openwa_enabled", 0) and doc.openwa_base_url:
        validate_openwa_url(doc.openwa_base_url)


# ---------------------------------------------------------------------------
# Doc event hook — called by Frappe when a WhatsApp Account is saved
# ---------------------------------------------------------------------------

_WEBHOOK_PATH = "/api/method/openwa_bridge.inbound.receive_openwa_message"
_DEFAULT_WEBHOOK_EVENTS = [
    "message.received", "message.sent", "message.ack", "message.failed",
    "message.revoked", "message.reaction", "message.edited",
    "status.received",
    "session.status", "session.qr", "session.authenticated",
    "session.disconnected", "session.reconnect_loop",
    "group.join", "group.leave", "group.update",
    "call.received",
]


def _get_webhook_events(doc):  # noqa: ANN001
    """Parse the webhook events field and return a list of event names."""
    raw = getattr(doc, "openwa_webhook_events", None)
    if not raw:
        return _DEFAULT_WEBHOOK_EVENTS
    try:
        import json
        events = json.loads(raw)
        if isinstance(events, list) and events:
            return events
    except (json.JSONDecodeError, TypeError):
        pass
    return _DEFAULT_WEBHOOK_EVENTS


def sync_webhook(account) -> bool:  # noqa: ANN001
    """Ensure the OpenWA gateway has a webhook pointing to Frappe.

    - On **first setup** (no existing webhook): creates one with the
      configured events from ``_DEFAULT_WEBHOOK_EVENTS`` or the
      ``openwa_webhook_events`` field.
    - On **subsequent calls** (webhook already exists): updates only the
      ``url`` and ``secret``.  The existing ``events`` list on OpenWA is
      **preserved** so manual event selections survive account saves and
      reconnects.

    Returns ``True`` on success, ``False`` on failure (errors are logged).
    """
    if not getattr(account, "openwa_enabled", 0):
        return False
    session_id = getattr(account, "openwa_session_id", None)
    if not session_id:
        return False

    secret = account.get_password("openwa_webhook_secret") if account.get("openwa_webhook_secret") else None
    frappe_url = frappe.utils.get_url(_WEBHOOK_PATH)

    try:
        webhooks = openwa_api(account, "GET", "/webhooks")

        existing = None
        if isinstance(webhooks, list):
            for wh in webhooks:
                if wh.get("url") == frappe_url:
                    existing = wh
                    break

        if existing:
            # UPDATE: only refresh url + secret; keep the events the operator
            # configured on OpenWA (manual event selection is preserved).
            update_payload: dict = {"secret": secret or ""}
            # Only push events if the doc has an explicit override —
            # this means the operator changed events in the Frappe form.
            explicit_events = getattr(account, "openwa_webhook_events", None)
            if explicit_events:
                update_payload["events"] = _get_webhook_events(account)

            openwa_api(
                account, "PUT",
                f"/webhooks/{existing['id']}",
                json_data=update_payload,
            )
            frappe.logger().info(
                f"OpenWA: webhook {existing['id']} updated for session "
                f"{session_id} (url + secret synced, events preserved)"
            )
        else:
            # CREATE: first-time setup — use configured events
            events = _get_webhook_events(account)
            openwa_api(
                account, "POST",
                "/webhooks",
                json_data={
                    "url": frappe_url,
                    "events": events,
                    "secret": secret or "",
                },
            )
            frappe.logger().info(
                f"OpenWA: webhook created for session {session_id} "
                f"with {len(events)} events"
            )
        return True
    except Exception as exc:
        frappe.log_error(
            title="OpenWA: Failed to sync webhook",
            message=f"Account: {account.name}, Session: {session_id}: {exc}",
        )
        return False


def on_account_update(doc, method):  # noqa: ANN001
    """Sync the OpenWA webhook when the account is saved."""
    sync_webhook(doc)


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


# ---------------------------------------------------------------------------
# Contact management API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def check_whatsapp_number(account_name: str, number: str) -> dict:
    """Check if a phone number is registered on WhatsApp via OpenWA.

    Returns:
        dict: { "exists": true, "jid": "12345@c.us" } or { "exists": false }
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    from frappe_whatsapp.utils import format_number

    formatted = format_number(number)
    chat_id = f"{formatted}@c.us" if "@c.us" not in formatted else formatted

    try:
        result = openwa_api(account, "GET", f"/contacts/check/{formatted}")
        return {"exists": result.get("isRegistered", False), "jid": chat_id}
    except Exception as exc:
        return {"exists": False, "error": str(exc)}


@frappe.whitelist()
def block_contact(account_name: str, contact_id: str) -> dict:
    """Block a contact via OpenWA.

    Args:
        contact_id: WhatsApp JID (e.g. ``12345@c.us``).
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "POST", f"/contacts/{contact_id}/block")
        return {"status": "blocked"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def unblock_contact(account_name: str, contact_id: str) -> dict:
    """Unblock a contact via OpenWA."""
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "DELETE", f"/contacts/{contact_id}/block")
        return {"status": "unblocked"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Typing indicator API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def send_typing_indicator(account_name: str, chat_id: str, state: str = "typing") -> dict:
    """Send a typing indicator to a chat via OpenWA.

    Args:
        chat_id: WhatsApp chat ID (e.g. ``12345@c.us``).
        state: One of ``typing``, ``recording``, ``paused``.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    if state not in ("typing", "recording", "paused"):
        frappe.throw("State must be one of: typing, recording, paused")
    account = _get_account(account_name)
    try:
        openwa_api(account, "POST", "/chats/typing", json_data={
            "chatId": chat_id,
            "state": state,
        })
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Bulk messaging API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def send_bulk_openwa(account_name: str, contacts: str, message: str) -> dict:
    """Send a text message to multiple contacts via OpenWA's send-bulk endpoint.

    Args:
        contacts: Comma-separated phone numbers or WhatsApp JIDs.
        message:  The message body text.

    Returns:
        dict: { "status": "ok", "sent": <count>, "failed": <count> }
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)

    from frappe_whatsapp.utils import format_number

    raw_numbers = [n.strip() for n in contacts.split(",") if n.strip()]
    chat_ids = []
    for num in raw_numbers:
        formatted = format_number(num)
        chat_ids.append(f"{formatted}@c.us" if "@c.us" not in formatted else formatted)

    if not chat_ids:
        frappe.throw("No valid contacts provided.")

    try:
        result = openwa_api(account, "POST", "/messages/send-bulk", json_data={
            "chatIds": chat_ids,
            "text": message,
        })
        sent = len(result.get("sent", [])) if isinstance(result, dict) else len(chat_ids)
        failed = len(result.get("failed", [])) if isinstance(result, dict) else 0
        return {"status": "ok", "sent": sent, "failed": failed}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Message forward / delete API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def forward_message(account_name: str, message_id: str, chat_id: str) -> dict:
    """Forward an existing message to another chat via OpenWA.

    Args:
        message_id: The OpenWA message ID to forward.
        chat_id:    Destination WhatsApp chat ID.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "POST", "/messages/forward", json_data={
            "messageId": message_id,
            "chatId": chat_id,
        })
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def delete_message(account_name: str, message_id: str, revoke: int = 0) -> dict:
    """Delete a message via OpenWA.

    Args:
        message_id: The OpenWA message ID to delete.
        revoke:     If ``1``, revoke (delete for everyone). Otherwise delete for self.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    endpoint = f"/messages/{message_id}" + ("?revoke=true" if revoke else "")
    try:
        openwa_api(account, "DELETE", endpoint)
        return {"status": "deleted", "message_id": message_id}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Pairing code authentication (alternative to QR scan)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def request_pairing_code(account_name: str, phone_number: str) -> dict:
    """Request an 8-character pairing code to link via phone number.

    Alternative to QR scanning — user enters the code on their phone.
    The phone number should be in international format without + or spaces.

    Returns:
        dict: { "pairingCode": "ABCD1234", "status": "ok" }
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)

    import re
    digits = re.sub(r"[^0-9]", "", phone_number)
    if len(digits) < 7:
        frappe.throw("Phone number must be at least 7 digits (international format without +).")

    try:
        result = openwa_api(account, "POST", "/pairing-code", json_data={
            "phoneNumber": digits,
        })
        return {"status": "ok", "pairingCode": result.get("pairingCode", "")}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Sticker API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def send_sticker(account_name: str, chat_id: str, url: str = "", base64: str = "") -> dict:
    """
    Send a sticker message to a WhatsApp chat.
    
    Parameters:
        chat_id (str): Identifier of the recipient chat.
        url (str): HTTP or HTTPS URL of the sticker.
        base64 (str): Base64-encoded sticker data.
    
    Returns:
        dict: A success result with the message ID, or an error result with its message.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    if not url and not base64:
        frappe.throw("Provide either 'url' or 'base64' for the sticker.")
    account = _get_account(account_name)

    payload: dict = {"chatId": chat_id}
    if url:
        payload["url"] = url
    if base64:
        payload["base64"] = base64

    try:
        result = openwa_api(account, "POST", "/messages/send-sticker", json_data=payload)
        return {"status": "ok", "messageId": result.get("messageId", "")}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Message edit API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def edit_message(account_name: str, chat_id: str, message_id: str, body: str) -> dict:
    """Edit the text of a sent WhatsApp message.
    
    Args:
        chat_id: WhatsApp chat ID.
        message_id: OpenWA message ID.
        body: Replacement message text.
    
    Returns:
        A dictionary with a success result or an error message.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "POST", "/messages/edit", json_data={
            "chatId": chat_id,
            "messageId": message_id,
            "body": body,
        })
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Status/Stories posting API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def post_status_text(account_name: str, text: str, background_color: str = "", font: str = "") -> dict:
    """
    Publish a text status with optional background color and font styling.
    
    Parameters:
        account_name (str): WhatsApp Account used to publish the status.
        text (str): Status text content.
        background_color (str): Optional background color, such as ``#FF5722``.
        font (str): Optional font style.
    
    Returns:
        dict: A success response containing the OpenWA result, or an error response.
    
    Raises:
        frappe.PermissionError: If the caller lacks write permission for the account.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    payload: dict = {"text": text}
    if background_color:
        payload["backgroundColor"] = background_color
    if font:
        payload["font"] = font
    try:
        result = openwa_api(account, "POST", "/status/send-text", json_data=payload)
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def post_status_image(account_name: str, url: str = "", base64: str = "", caption: str = "") -> dict:
    """
    Post an image status/story through OpenWA.
    
    Args:
        account_name (str): WhatsApp account used to publish the status.
        url (str): URL of the image.
        base64 (str): Base64-encoded image data.
        caption (str): Optional status caption.
    
    Returns:
        dict: A success response containing the OpenWA result, or an error response.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    if not url and not base64:
        frappe.throw("Provide either 'url' or 'base64' for the image.")
    account = _get_account(account_name)
    payload: dict = {}
    if url:
        payload["image"] = {"url": url}
    if base64:
        payload["image"] = {"base64": base64}
    if caption:
        payload["caption"] = caption
    try:
        result = openwa_api(account, "POST", "/status/send-image", json_data=payload)
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def post_status_video(account_name: str, url: str = "", base64: str = "", caption: str = "") -> dict:
    """
    Post a video to the WhatsApp status.
    
    Parameters:
    	url (str): HTTP or HTTPS video URL.
    	base64 (str): Base64-encoded video data.
    	caption (str): Optional status caption.
    
    Returns:
    	dict: A success response containing the OpenWA result, or an error response.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    if not url and not base64:
        frappe.throw("Provide either 'url' or 'base64' for the video.")
    account = _get_account(account_name)
    payload: dict = {}
    if url:
        payload["video"] = {"url": url}
    if base64:
        payload["video"] = {"base64": base64}
    if caption:
        payload["caption"] = caption
    try:
        result = openwa_api(account, "POST", "/status/send-video", json_data=payload)
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def get_statuses(account_name: str) -> dict:
    """
    List all visible status or story updates.
    
    Returns:
        dict: A response containing the statuses or an error message.
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", "/status")
        return {"status": "ok", "statuses": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Call reject API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def reject_call(account_name: str, call_id: str) -> dict:
    """
    Rejects an incoming WhatsApp call.
    
    Args:
        call_id: The call ID from a ``call.received`` webhook event.
    
    Returns:
        A dictionary with ``status`` set to ``"ok"`` on success or ``"error"`` with an error message if the request fails.
    
    Raises:
        frappe.PermissionError: If the caller lacks write permission for the WhatsApp account.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "POST", f"/calls/{call_id}/reject")
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Chat read/unread API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def mark_chat_read(account_name: str, chat_id: str) -> dict:
    """Mark a WhatsApp chat as read.
    
    Args:
        chat_id: WhatsApp chat ID, such as ``12345@c.us``.
    
    Returns:
        A dictionary indicating whether the operation succeeded.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "POST", f"/chats/{chat_id}/read")
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def mark_chat_unread(account_name: str, chat_id: str) -> dict:
    """Mark a WhatsApp chat as unread.
    
    Args:
        chat_id: WhatsApp chat ID, such as ``12345@c.us``.
    
    Returns:
        A dictionary with ``status`` set to ``"ok"`` on success or ``"error"`` with an error message on failure.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "POST", f"/chats/{chat_id}/unread")
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Chat history API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_chat_history(account_name: str, chat_id: str, limit: int = 50) -> dict:
    """
    Retrieve live message history for a WhatsApp chat.
    
    Parameters:
    	chat_id (str): WhatsApp chat identifier, such as ``12345@c.us``.
    	limit (int): Maximum number of messages to retrieve.
    
    Returns:
    	dict: A response containing the messages or an error status.
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/chats/{chat_id}/history?limit={limit}")
        return {"status": "ok", "messages": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Full-text search API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def search_messages(account_name: str, query: str, limit: int = 50) -> dict:
    """
    Search messages across WhatsApp sessions.
    
    Parameters:
        account_name (str): Name of the WhatsApp Account to search.
        query (str): Text to search for.
        limit (int): Maximum number of results.
    
    Returns:
        dict: A status and matching message results, or an error message.
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/search?q={query}&limit={limit}")
        return {"status": "ok", "results": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Group management API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def list_groups(account_name: str) -> dict:
    """List all groups the session belongs to."""
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", "/groups")
        return {"status": "ok", "groups": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def create_group(account_name: str, name: str, participants: str) -> dict:
    """
    Create a new WhatsApp group with the specified participants.
    
    Parameters:
        account_name (str): WhatsApp Account name.
        name (str): Group name.
        participants (str): Comma-separated phone numbers or WhatsApp JIDs.
    
    Returns:
        dict: A success response containing the group result, or an error response.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    from frappe_whatsapp.utils import format_number
    raw = [n.strip() for n in participants.split(",") if n.strip()]
    participant_list = []
    for num in raw:
        formatted = format_number(num)
        participant_list.append(f"{formatted}@c.us" if "@c.us" not in formatted else formatted)
    try:
        result = openwa_api(account, "POST", "/groups", json_data={
            "name": name,
            "participants": participant_list,
        })
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def add_group_participants(account_name: str, group_id: str, participants: str) -> dict:
    """
    Add participants to a WhatsApp group.
    
    Parameters:
        account_name (str): WhatsApp account name.
        group_id (str): WhatsApp group JID.
        participants (str): Comma-separated phone numbers or WhatsApp JIDs.
    
    Returns:
        dict: Operation status and the OpenWA result, or an error message.
    
    Raises:
        frappe.PermissionError: If the caller lacks write permission for the account.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    from frappe_whatsapp.utils import format_number
    raw = [n.strip() for n in participants.split(",") if n.strip()]
    participant_list = []
    for num in raw:
        formatted = format_number(num)
        participant_list.append(f"{formatted}@c.us" if "@c.us" not in formatted else formatted)
    try:
        result = openwa_api(account, "POST", f"/groups/{group_id}/participants/add", json_data={
            "participants": participant_list,
        })
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def remove_group_participants(account_name: str, group_id: str, participants: str) -> dict:
    """
    Remove participants from a WhatsApp group.
    
    Args:
        group_id: WhatsApp group JID.
        participants: Comma-separated phone numbers or WhatsApp JIDs.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    from frappe_whatsapp.utils import format_number
    raw = [n.strip() for n in participants.split(",") if n.strip()]
    participant_list = []
    for num in raw:
        formatted = format_number(num)
        participant_list.append(f"{formatted}@c.us" if "@c.us" not in formatted else formatted)
    try:
        result = openwa_api(account, "POST", f"/groups/{group_id}/participants/remove", json_data={
            "participants": participant_list,
        })
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def promote_group_admins(account_name: str, group_id: str, participants: str) -> dict:
    """
    Promote participants to administrators of a WhatsApp group.
    
    Parameters:
        account_name (str): WhatsApp Account name.
        group_id (str): WhatsApp group JID.
        participants (str): Comma-separated phone numbers or WhatsApp JIDs.
    
    Returns:
        dict: A success response containing the OpenWA result, or an error response.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    from frappe_whatsapp.utils import format_number
    raw = [n.strip() for n in participants.split(",") if n.strip()]
    participant_list = []
    for num in raw:
        formatted = format_number(num)
        participant_list.append(f"{formatted}@c.us" if "@c.us" not in formatted else formatted)
    try:
        result = openwa_api(account, "POST", f"/groups/{group_id}/participants/promote", json_data={
            "participants": participant_list,
        })
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def demote_group_admins(account_name: str, group_id: str, participants: str) -> dict:
    """Demote group administrators to regular participants.
    
    Args:
        group_id: WhatsApp group JID.
        participants: Comma-separated phone numbers or WhatsApp JIDs.
    
    Returns:
        A dictionary containing the operation result or an error message.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    from frappe_whatsapp.utils import format_number
    raw = [n.strip() for n in participants.split(",") if n.strip()]
    participant_list = []
    for num in raw:
        formatted = format_number(num)
        participant_list.append(f"{formatted}@c.us" if "@c.us" not in formatted else formatted)
    try:
        result = openwa_api(account, "POST", f"/groups/{group_id}/participants/demote", json_data={
            "participants": participant_list,
        })
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def set_group_name(account_name: str, group_id: str, name: str) -> dict:
    """Update a WhatsApp group's name.
    
    Args:
        group_id: WhatsApp group JID.
        name: New group name.
    
    Returns:
        A dictionary indicating whether the update succeeded.
    
    Raises:
        frappe.PermissionError: If the caller lacks write permission.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "PUT", f"/groups/{group_id}/subject", json_data={"subject": name})
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def leave_group(account_name: str, group_id: str) -> dict:
    """
    Leave a WhatsApp group.
    
    Args:
        group_id (str): WhatsApp group JID.
    
    Returns:
        dict: A status response indicating whether the group was left successfully.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "POST", f"/groups/{group_id}/leave")
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Label management API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def list_labels(account_name: str) -> dict:
    """List all WhatsApp Business labels."""
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", "/labels")
        return {"status": "ok", "labels": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def add_label_to_chat(account_name: str, label_id: str, chat_id: str) -> dict:
    """
    Add a WhatsApp Business label to a chat.
    
    Args:
        account_name (str): The WhatsApp Account name.
        label_id (str): The WhatsApp Business label ID.
        chat_id (str): The WhatsApp chat ID.
    
    Returns:
        dict: A status dictionary indicating whether the label was added or an error occurred.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "POST", f"/labels/{label_id}/chats/{chat_id}")
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def remove_label_from_chat(account_name: str, label_id: str, chat_id: str) -> dict:
    """
    Remove a label from a WhatsApp chat.
    
    Args:
        account_name: The WhatsApp Account name.
        label_id: The WhatsApp Business label ID.
        chat_id: The WhatsApp chat ID.
    
    Returns:
        A dictionary indicating whether the label was removed successfully.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "DELETE", f"/labels/{label_id}/chats/{chat_id}")
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Batch messaging API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def send_bulk_with_progress(account_name: str, contacts: str, message: str) -> dict:
    """
    Submit a text message to multiple contacts for asynchronous delivery tracking.
    
    Parameters:
        account_name (str): WhatsApp Account used to send the messages.
        contacts (str): Comma-separated phone numbers or WhatsApp JIDs.
        message (str): Text message to send.
    
    Returns:
        dict: A success response containing the batch ID and OpenWA result, or an error response.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    from frappe_whatsapp.utils import format_number
    raw_numbers = [n.strip() for n in contacts.split(",") if n.strip()]
    chat_ids = []
    for num in raw_numbers:
        formatted = format_number(num)
        chat_ids.append(f"{formatted}@c.us" if "@c.us" not in formatted else formatted)
    if not chat_ids:
        frappe.throw("No valid contacts provided.")
    try:
        result = openwa_api(account, "POST", "/messages/send-bulk", json_data={
            "chatIds": chat_ids,
            "text": message,
        })
        return {"status": "ok", "batchId": result.get("batchId", ""), "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Profile management API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def set_profile_name(account_name: str, name: str) -> dict:
    """
    Set the WhatsApp display name.
    
    Parameters:
    	name (str): New display name.
    
    Returns:
    	dict: A status dictionary indicating whether the name was updated successfully.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "PUT", "/profile/name", json_data={"name": name})
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def set_profile_status(account_name: str, status: str) -> dict:
    """Update the WhatsApp profile's about text.
    
    Args:
        account_name: Name of the WhatsApp Account to update.
        status: New about text.
    
    Returns:
        A status dictionary indicating success or containing an error message.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "PUT", "/profile/status", json_data={"status": status})
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def set_profile_picture(account_name: str, url: str = "", base64: str = "") -> dict:
    """
    Set the WhatsApp profile picture from an image URL or encoded image data.
    
    Args:
        url: HTTP or HTTPS URL of the image.
        base64: Base64-encoded image data.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    if not url and not base64:
        frappe.throw("Provide either 'url' or 'base64' for the profile picture.")
    account = _get_account(account_name)
    payload: dict = {}
    if url:
        payload["url"] = url
    if base64:
        payload["base64"] = base64
    try:
        openwa_api(account, "PUT", "/profile/picture", json_data=payload)
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Session stats API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_session_stats(account_name: str) -> dict:
    """Retrieve aggregate statistics for a WhatsApp session.
    
    Returns:
    	dict: A success response containing the session statistics, or an error response.
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", "/sessions/stats/overview")
        return {"status": "ok", "stats": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Channels/Newsletters API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def list_channels(account_name: str) -> dict:
    """List WhatsApp Channels (newsletters) the session is subscribed to."""
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", "/channels")
        return {"status": "ok", "channels": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def get_channel_messages(account_name: str, channel_id: str, limit: int = 50) -> dict:
    """
    Retrieve messages from a WhatsApp Channel.
    
    Args:
        channel_id: WhatsApp Channel or Newsletter ID.
        limit: Maximum number of messages to retrieve.
    
    Returns:
        A dictionary containing the messages or an error response.
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/channels/{channel_id}/messages?limit={limit}")
        return {"status": "ok", "messages": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Status media / contact status API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_contact_statuses(account_name: str, contact_id: str) -> dict:
    """Get status/story updates from a specific contact.

    Args:
        contact_id: Contact JID (e.g. ``1234567890@c.us``).
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/status/{contact_id}")
        return {"status": "ok", "statuses": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def get_status_media(account_name: str, status_id: str) -> dict:
    """
    Retrieve media associated with a stored status.
    
    Args:
        status_id (str): Identifier from the ``status.received`` event.
    
    Returns:
        dict: A success response containing the media, or an error response.
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/status/{status_id}/media")
        return {"status": "ok", "media": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Channel subscribe/unsubscribe API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def subscribe_channel(account_name: str, invite_code: str) -> dict:
    """
    Subscribe to a WhatsApp Channel using an invite code.
    
    Parameters:
    	invite_code (str): Invite code from the channel link.
    
    Returns:
    	dict: A success response containing the subscribed channel, or an error response.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "POST", "/channels/subscribe", json_data={"inviteCode": invite_code})
        return {"status": "ok", "channel": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def unsubscribe_channel(account_name: str, channel_id: str) -> dict:
    """Unsubscribe from a WhatsApp Channel.

    Args:
        channel_id: Channel ID to unsubscribe from.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "DELETE", f"/channels/{channel_id}")
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Message reactions API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_message_reactions(account_name: str, chat_id: str, message_id: str) -> dict:
    """Retrieve reactions associated with a message.
    
    Args:
        chat_id: Chat containing the message.
        message_id: Message whose reactions to retrieve.
    
    Returns:
        A dictionary containing the reaction data or an error status.
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/messages/{chat_id}/{message_id}/reactions")
        return {"status": "ok", "reactions": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Batch cancel API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def cancel_batch(account_name: str, batch_id: str) -> dict:
    """
    Cancel a running bulk message batch.
    
    Parameters:
        batch_id (str): Identifier of the batch to cancel.
    
    Returns:
        dict: A success response containing the batch result, or an error response.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "POST", f"/messages/batch/{batch_id}/cancel")
        return {"status": "ok", "batch": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Global stats API (admin-only)
# ---------------------------------------------------------------------------
# Group: details, join by code, settings, description, invite code
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_group(account_name: str, group_id: str) -> dict:
    """Get metadata for a single WhatsApp group."""
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/groups/{group_id}")
        return {"status": "ok", "group": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def join_group_by_code(account_name: str, invite_code: str) -> dict:
    """Join a WhatsApp group using an invite link or code."""
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "POST", "/groups/join", json_data={"inviteCode": invite_code})
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def get_group_settings(account_name: str, group_id: str) -> dict:
    """
    Retrieve the settings for a WhatsApp group.
    
    Parameters:
    	group_id (str): The WhatsApp group identifier.
    
    Returns:
    	settings (dict): The group's configuration, including options such as announcement-only mode.
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/groups/{group_id}/settings")
        return {"status": "ok", "settings": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def set_group_settings(account_name: str, group_id: str, settings: str | dict) -> dict:
    """
    Update the settings for a WhatsApp group.
    
    Parameters:
        settings (str | dict): Group settings as a JSON string or dictionary.
    
    Returns:
        dict: A status dictionary indicating whether the update succeeded.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    if isinstance(settings, str):
        import json
        settings = json.loads(settings)
    try:
        openwa_api(account, "PUT", f"/groups/{group_id}/settings", json_data=settings)
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def set_group_description(account_name: str, group_id: str, description: str) -> dict:
    """Update a WhatsApp group's description."""
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "PUT", f"/groups/{group_id}/description", json_data={"description": description})
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def get_group_invite_code(account_name: str, group_id: str) -> dict:
    """
    Get the invite code for a WhatsApp group.
    
    Parameters:
    	group_id (str): The WhatsApp group identifier.
    
    Returns:
    	dict: A status dictionary containing the invite code or an error message.
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/groups/{group_id}/invite-code")
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def revoke_group_invite_code(account_name: str, group_id: str) -> dict:
    """Revoke the current invite code/link for a WhatsApp group."""
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "POST", f"/groups/{group_id}/invite-code/revoke")
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Contact: list, get, profile picture, phone lookup
# ---------------------------------------------------------------------------


@frappe.whitelist()
def list_contacts(account_name: str) -> dict:
    """List all contacts for the session."""
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", "/contacts")
        return {"status": "ok", "contacts": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def get_contact(account_name: str, contact_id: str) -> dict:
    """
    Retrieve details for a single WhatsApp contact.
    
    Parameters:
    	contact_id (str): The contact identifier.
    
    Returns:
    	dict: A result containing the contact details or an error message.
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/contacts/{contact_id}")
        return {"status": "ok", "contact": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def get_contact_profile_picture(account_name: str, contact_id: str) -> dict:
    """Get the profile picture URL for a contact."""
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/contacts/{contact_id}/profile-picture")
        return {"status": "ok", "profile_picture": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def get_contact_phone(account_name: str, contact_id: str) -> dict:
    """Resolve the phone number for a contact by their JID."""
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/contacts/{contact_id}/phone")
        return {"status": "ok", "phone": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def list_profile_pictures(account_name: str) -> dict:
    """List profile pictures for all contacts."""
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", "/contacts/profile-pictures")
        return {"status": "ok", "profile_pictures": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Chat: delete
# ---------------------------------------------------------------------------


@frappe.whitelist()
def delete_chat(account_name: str, chat_id: str) -> dict:
    """Delete an entire chat conversation."""
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "POST", "/chats/delete", json_data={"chatId": chat_id})
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Status: delete a posted story
# ---------------------------------------------------------------------------


@frappe.whitelist()
def delete_status(account_name: str, status_id: str) -> dict:
    """
    Delete a posted WhatsApp status or story.
    
    Parameters:
    	status_id (str): Identifier of the status to delete.
    
    Returns:
    	dict: A success response or an error response containing the failure message.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        openwa_api(account, "DELETE", f"/status/{status_id}")
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Labels: get single, get labels for a chat
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_label(account_name: str, label_id: str) -> dict:
    """Get details for a single label."""
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/labels/{label_id}")
        return {"status": "ok", "label": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def get_chat_labels(account_name: str, chat_id: str) -> dict:
    """Get all labels assigned to a specific chat."""
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/labels/chat/{chat_id}")
        return {"status": "ok", "labels": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Batch: get send status
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_batch_status(account_name: str, batch_id: str) -> dict:
    """Get the current status of a batch send operation."""
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/messages/batch/{batch_id}")
        return {"status": "ok", "batch": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Webhook: test delivery
# ---------------------------------------------------------------------------


@frappe.whitelist()
def test_webhook(account_name: str, webhook_id: str) -> dict:
    """Trigger a test webhook delivery from OpenWA to verify configuration.

    OpenWA sends a test payload to the configured webhook URL.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "POST", f"/webhooks/{webhook_id}/test")
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


# ---------------------------------------------------------------------------
# Catalog (Note: these return 501 Not Implemented in current OpenWA)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_catalog(account_name: str) -> dict:
    """Get the WhatsApp Business catalog (stub — returns 501 in OpenWA)."""
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", "/catalog")
        return {"status": "ok", "catalog": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def get_catalog_products(account_name: str) -> dict:
    """
    List products in the WhatsApp catalog.
    
    Returns:
    	dict: A success response containing the catalog products, or an error response.
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", "/catalog/products")
        return {"status": "ok", "products": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def get_catalog_product(account_name: str, product_id: str) -> dict:
    """Retrieve a catalog product by its identifier.
    
    Parameters:
    	product_id (str): Identifier of the catalog product to retrieve.
    
    Returns:
    	dict: A success response containing the product, or an error response when retrieval fails.
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "GET", f"/catalog/products/{product_id}")
        return {"status": "ok", "product": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def send_product_message(account_name: str, chat_id: str, product_id: str) -> dict:
    """
    Send a catalog product message to a WhatsApp chat.
    
    Parameters:
    	chat_id (str): Identifier of the recipient chat.
    	product_id (str): Identifier of the catalog product to send.
    
    Returns:
    	dict: A success response containing the OpenWA result or an error response.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "POST", "/messages/send-product", json_data={
            "chatId": chat_id,
            "productId": product_id,
        })
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@frappe.whitelist()
def send_catalog_message(account_name: str, chat_id: str, catalog_id: str) -> dict:
    """
    Send a catalog listing message to a chat.
    
    Parameters:
    	chat_id (str): The recipient chat identifier.
    	catalog_id (str): The catalog identifier to send.
    
    Returns:
    	dict: A success response containing the OpenWA result, or an error response.
    """
    if not frappe.has_permission("WhatsApp Account", "write", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    account = _get_account(account_name)
    try:
        result = openwa_api(account, "POST", "/messages/send-catalog", json_data={
            "chatId": chat_id,
            "catalogId": catalog_id,
        })
        return {"status": "ok", "result": result}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
