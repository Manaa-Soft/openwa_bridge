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
              { status: "not_found" } if session was deleted from OpenWA or
              { status: "error", error: "..." } if OpenWA is unreachable
    """
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions to read WhatsApp Account.", frappe.PermissionError)
    account = _get_account(account_name)
    session = _safe_get_session(account)
    if session is None:
        # Distinguish between "session deleted" (404) and "server unreachable"
        try:
            # If we can reach the server but session is gone, it's deleted
            _raw_openwa_call(account, "GET", "/api/sessions")
            return {"status": "not_found"}
        except Exception:
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
    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions to read WhatsApp Account.", frappe.PermissionError)
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
                    # Force-kill worked — save and return QR
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
        except Exception as exc:
            return {"status": "error", "error": f"Failed to recover session: {exc}"}

    # --- 3. Start the session if it is not running -------------------------
    elif status in ("disconnected", "created"):
        try:
            _start_session(account)
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
    "message.revoked", "message.reaction", "session.status", "session.qr",
    "session.authenticated", "session.disconnected",
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


def on_account_update(doc, method):  # noqa: ANN001
    """Sync the OpenWA webhook secret to the gateway when the account is saved.

    If a webhook for our Frappe URL already exists on the session the secret
    is updated.  Otherwise a new webhook is created with the configured events.
    """
    if not getattr(doc, "openwa_enabled", 0):
        return
    session_id = getattr(doc, "openwa_session_id", None)
    if not session_id:
        return

    secret = doc.get_password("openwa_webhook_secret") if doc.get("openwa_webhook_secret") else None
    frappe_url = frappe.utils.get_url(_WEBHOOK_PATH)
    events = _get_webhook_events(doc)

    try:
        webhooks = openwa_api(doc, "GET", "/webhooks")

        existing = None
        if isinstance(webhooks, list):
            for wh in webhooks:
                if wh.get("url") == frappe_url:
                    existing = wh
                    break

        if existing:
            openwa_api(
                doc, "PUT",
                f"/webhooks/{existing['id']}",
                json_data={"secret": secret or "", "events": events},
            )
        else:
            openwa_api(
                doc, "POST",
                "/webhooks",
                json_data={
                    "url": frappe_url,
                    "events": events,
                    "secret": secret or "",
                },
            )
    except Exception as exc:
        frappe.log_error(
            title="OpenWA: Failed to sync webhook secret",
            message=f"Account: {doc.name}, Session: {session_id}: {exc}",
        )


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
    """Send a sticker message via OpenWA.

    Provide either ``url`` (http/https link to sticker image) or ``base64``
    (base64-encoded sticker data).

    Returns:
        dict: { "status": "ok", "messageId": "..." }
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
