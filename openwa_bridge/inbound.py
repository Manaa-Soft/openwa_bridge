"""Inbound webhook receiver for OpenWA Gateway."""
from __future__ import annotations

import frappe
import base64

from frappe_whatsapp.utils import get_whatsapp_account, format_number

from openwa_bridge.utils import (
    verify_openwa_signature,
    strip_jid_suffix,
    openwa_type_to_frappe,
    get_account_setting,
)

MAX_MEDIA_SIZE_MB = 10


# ── Public endpoint ──────────────────────────────────────────────────


@frappe.whitelist(allow_guest=True)
def receive_openwa_message() -> dict[str, str]:
    """
    Inbound webhook endpoint for OpenWA Gateway.

    Configure in OpenWA dashboard:
      URL: https://your-domain/api/method/openwa_bridge.inbound.receive_openwa_message
      Events: message.received, message.ack, message.failed
      Secret: (set same value in WhatsApp Account -> openwa_webhook_secret)

    Always returns HTTP 200 to prevent OpenWA retry loops.
    """
    raw_body = frappe.request.get_data()
    try:
        payload = frappe.request.get_json(force=True)
    except Exception:
        frappe.log_error(title="OpenWA: Failed to parse webhook body")
        return {"status": "error", "message": "Invalid JSON"}

    if not payload or "event" not in payload:
        frappe.log_error(
            title="OpenWA: Missing event field",
            message=f"Payload keys: {list(payload.keys()) if payload else 'None'}",
        )
        return {"status": "error", "message": "Missing 'event' field"}

    session_id: str = payload.get("sessionId", "")
    event_type: str = payload.get("event", "")
    event_data: dict = payload.get("data", {})

    whatsapp_account = _resolve_account_by_session(session_id)

    # ── Rate limiting (configurable per account) ──
    rate_limit = get_account_setting(whatsapp_account, "openwa_rate_limit", 60) if whatsapp_account else 60
    forwarded_for = frappe.request.headers.get("X-Forwarded-For", "")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else (
        frappe.request.remote_addr or "unknown"
    )
    rate_key = f"openwa_rate::{client_ip}"
    count = frappe.cache().get_value(rate_key) or 0
    if count >= rate_limit:
        frappe.log_error(
            title="OpenWA: Rate limit exceeded",
            message=f"IP: {client_ip}, Count: {count}, Limit: {rate_limit}",
        )
        return {"status": "error", "message": "Rate limit exceeded"}
    frappe.cache().set_value(rate_key, count + 1, expires_in_sec=60)

    # ── HMAC verification ──
    if whatsapp_account:
        secret = whatsapp_account.get_password("openwa_webhook_secret")
        if secret:
            signature = frappe.request.headers.get("X-OpenWA-Signature", "")
            hmac_strict = getattr(whatsapp_account, "openwa_hmac_strict", 0)
            if not signature and hmac_strict:
                frappe.log_error(
                    title="OpenWA HMAC Missing (strict mode)",
                    message=f"Session: {session_id}, strict=True",
                )
                return {"status": "error", "message": "HMAC signature required (strict mode)"}
            if signature and not verify_openwa_signature(raw_body, secret, signature):
                frappe.log_error(
                    title="OpenWA HMAC Verification Failed",
                    message=f"Session: {session_id}, Sig: '{signature}'",
                )
                return {"status": "error", "message": "Signature verification failed"}
            elif not signature:
                frappe.logger().info(
                    f"OpenWA: No HMAC signature from session {session_id} "
                    "— messages processed without verification"
                )

    # ── Idempotency check ──
    idempotency_key = frappe.request.headers.get("X-OpenWA-Idempotency-Key", "")
    if idempotency_key:
        cache_key = f"openwa_idempotent:{idempotency_key}"
        if frappe.cache().get_value(cache_key):
            return {"status": "duplicate"}
        idempotency_ttl = frappe.db.get_single_value("OpenWA Bridge Settings", "idempotency_ttl") or 3600
        frappe.cache().set_value(cache_key, 1, expires_in_sec=idempotency_ttl)

    # ── Route to handler ──
    try:
        if event_type == "message.received":
            _handle_inbound_message(event_data, whatsapp_account, session_id)
        elif event_type in ("message.ack", "message.failed"):
            _handle_status_update(event_data)
        elif event_type == "session.status":
            _handle_session_status(event_data, session_id)
    except Exception as e:
        frappe.log_error(
            title="OpenWA Inbound Handler Error",
            message=f"Event: {event_type}, Session: {session_id}\n{frappe.get_traceback()}",
        )

    frappe.db.commit()
    return {"status": "ok"}


# ── Message handler ──────────────────────────────────────────────────


def _handle_inbound_message(
    msg_data: dict,
    whatsapp_account: "WhatsAppAccount | None",  # noqa: F821
    session_id: str,
) -> None:
    """Map OpenWA ``message.received`` to WhatsApp Message DocType."""
    # Skip outgoing echo
    if msg_data.get("fromMe"):
        return

    sender_jid: str = msg_data.get("from", "")
    phone_number = strip_jid_suffix(sender_jid)

    # Group messages — extract actual author
    if msg_data.get("isGroup", False):
        author_jid: str = msg_data.get("author", "")
        if author_jid and author_jid != sender_jid:
            phone_number = strip_jid_suffix(author_jid)

    if not whatsapp_account:
        whatsapp_account = _resolve_account_by_session(session_id)
    if not whatsapp_account:
        frappe.log_error(
            title="OpenWA: No matching WhatsApp Account",
            message=f"Session: {session_id}, Sender: {phone_number}",
        )
        return

    # Profile name
    profile_name: str | None = None
    contact_info = msg_data.get("contact")
    if isinstance(contact_info, dict):
        profile_name = contact_info.get("pushName") or contact_info.get("name")

    # Content type
    openwa_type: str = msg_data.get("type", "text")
    content_type = openwa_type_to_frappe(openwa_type)

    # Reply detection
    quoted = msg_data.get("quotedMessage")
    is_reply = bool(quoted and quoted.get("id"))
    reply_to_message_id = quoted.get("id") if is_reply else None

    message_body: str = msg_data.get("body", "")

    doc_data: dict = {
        "doctype": "WhatsApp Message",
        "type": "Incoming",
        "from": phone_number,
        "message": message_body,
        "message_id": msg_data.get("id"),
        "content_type": content_type,
        "is_reply": is_reply,
        "reply_to_message_id": reply_to_message_id,
        "profile_name": profile_name,
        "whatsapp_account": whatsapp_account.name,
    }

    # Location formatting
    if openwa_type == "location":
        location = msg_data.get("location", {})
        doc_data["message"] = (
            f"Location: {location.get('description', '')} "
            f"({location.get('latitude')}, {location.get('longitude')})"
        )

    doc = frappe.get_doc(doc_data)
    doc.insert(ignore_permissions=True)

    frappe.logger().info(
        f"OpenWA inbound: created WhatsApp Message {doc.name} from {phone_number}"
    )

    # Attach media if present
    media_info = msg_data.get("media")
    if isinstance(media_info, dict) and not media_info.get("omitted"):
        _attach_openwa_media(doc, media_info)

    # Create / update WhatsApp Profile
    _ensure_whatsapp_profile(phone_number, profile_name, whatsapp_account.name)


# ── Media handler ────────────────────────────────────────────────────


def _attach_openwa_media(message_doc: "Document", media_info: dict) -> None:  # noqa: F821
    """Decode OpenWA base64 media and create File DocType attachment."""
    mime_type: str = media_info.get("mimetype", "application/octet-stream")
    file_extension = mime_type.split("/")[-1] if "/" in mime_type else "bin"
    raw_data: str = media_info.get("data", "")

    if not raw_data:
        frappe.log_error(
            title="OpenWA: Empty media data",
            message=(
                f"Message {message_doc.name}: mimetype={mime_type}, "
                "omitted=False but no data"
            ),
        )
        return

    # Check size before base64 decode to avoid memory issues
    max_media_size_mb = frappe.db.get_single_value("OpenWA Bridge Settings", "max_media_size_mb") or 10
    estimated_bytes = len(raw_data) * 3 // 4  # rough base64→bytes estimate
    if estimated_bytes > max_media_size_mb * 1024 * 1024:
        frappe.log_error(
            title="OpenWA: Media too large",
            message=(
                f"Message {message_doc.name}: ~{estimated_bytes // (1024 * 1024)}MB "
                f"(limit {max_media_size_mb}MB), mimetype={mime_type}"
            ),
        )
        return

    try:
        file_data = base64.b64decode(raw_data)
    except Exception:
        frappe.log_error(
            title="OpenWA: Invalid base64 media",
            message=f"Message {message_doc.name}: failed to decode base64",
        )
        return

    file_name: str = media_info.get("filename") or (
        f"{frappe.generate_hash(length=10)}.{file_extension}"
    )

    file = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": file_name,
            "attached_to_doctype": "WhatsApp Message",
            "attached_to_name": message_doc.name,
            "content": file_data,
            "attached_to_field": "attach",
        }
    ).save(ignore_permissions=True)

    message_doc.attach = file.file_url
    message_doc.save(ignore_permissions=True)


# ── Status handler ───────────────────────────────────────────────────


def _handle_status_update(event_data: dict) -> None:
    """Map OpenWA ``message.ack`` / ``message.failed`` to WhatsApp Message status."""
    message_id: str = event_data.get("messageId") or event_data.get("id", "")
    status: str = event_data.get("status", "")

    if not message_id or not status:
        return

    name = frappe.db.get_value(
        "WhatsApp Message",
        filters={"message_id": message_id},
        pluck="name",
    )
    if not name:
        return

    frappe.db.set_value("WhatsApp Message", name, "status", status.capitalize())


def _handle_session_status(event_data: dict, session_id: str) -> None:
    """Update WhatsApp Account status from OpenWA session.status events."""
    status = event_data.get("status", "")
    if not status:
        return
    status_map = {
        "ready": "Active",
        "disconnected": "Inactive",
        "failed": "Inactive",
    }
    frappe_status = status_map.get(status)
    if not frappe_status:
        return
    account_name = frappe.db.get_value(
        "WhatsApp Account",
        {"openwa_session_id": session_id},
        "name",
    )
    if account_name:
        frappe.db.set_value("WhatsApp Account", account_name, "status", frappe_status)


# ── Account resolution ───────────────────────────────────────────────


def _resolve_account_by_session(session_id: str):
    """Find WhatsApp Account by OpenWA session ID custom field."""
    if not session_id:
        return None

    account_name = frappe.db.get_value(
        "WhatsApp Account",
        filters={"openwa_session_id": session_id},
        pluck="name",
    )
    if account_name:
        return frappe.get_doc("WhatsApp Account", account_name)

    return get_whatsapp_account(account_type="incoming")


# ── Profile helper ───────────────────────────────────────────────────


def _ensure_whatsapp_profile(
    phone_number: str,
    profile_name: str | None,
    whatsapp_account_name: str,
) -> None:
    """Create or update WhatsApp Profile record."""
    formatted = format_number(phone_number)

    if not frappe.db.exists("WhatsApp Profiles", {"number": formatted}):
        frappe.get_doc(
            {
                "doctype": "WhatsApp Profiles",
                "profile_name": profile_name or phone_number,
                "number": formatted,
                "whatsapp_account": whatsapp_account_name,
            }
        ).insert(ignore_permissions=True)
    elif profile_name:
        frappe.db.set_value(
            "WhatsApp Profiles",
            {"number": formatted},
            "profile_name",
            profile_name,
        )
