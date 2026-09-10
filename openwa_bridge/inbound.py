"""Inbound webhook receiver for OpenWA Gateway."""
from __future__ import annotations

import frappe
import base64
import hashlib

from frappe_whatsapp.utils import get_whatsapp_account, format_number

from openwa_bridge.utils import (
    verify_openwa_signature,
    strip_jid_suffix,
    openwa_type_to_frappe,
    get_account_setting,
    openwa_api,
)

MAX_MEDIA_SIZE_MB = 10


# ── Public endpoint ──────────────────────────────────────────────────


@frappe.whitelist(allow_guest=True)
def receive_openwa_message() -> dict[str, str]:
    """
    Process an OpenWA webhook payload and route supported events to their handlers.
    
    Invalid JSON, missing event data, rate-limit violations, and failed signature
    checks return an error status. Duplicate events return a duplicate status; other
    accepted events return an OK status.
    
    Returns:
        dict[str, str]: A status response describing the webhook result.
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
    rate_limit = get_account_setting(whatsapp_account, "openwa_rate_limit", 600) if whatsapp_account else 600
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
            hmac_strict = get_account_setting(whatsapp_account, "openwa_hmac_strict", 0)
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
        # OpenWA generates idempotency keys as `msg_{sessionId}_{messageId}`.
        # When the engine provides an empty/null messageId, OpenWA falls back to
        # "unknown", producing identical keys for DIFFERENT rapid-fire messages
        # (e.g. `msg_<sid>_unknown_<webhookId>`).  This causes the second message
        # to be silently deduplicated.  To prevent that, we augment the key with
        # the message body + sender when the key carries the "unknown" sentinel.
        if "_unknown_" in idempotency_key and event_type == "message.received":
            body = event_data.get("body", "")
            sender = event_data.get("from", "")
            content_hash = hashlib.md5(f"{body}:{sender}".encode()).hexdigest()[:12]
            idempotency_key = f"{idempotency_key}_{content_hash}"
        cache_key = f"openwa_idempotent:{idempotency_key}"
        idempotency_ttl = frappe.db.get_single_value("OpenWA Bridge Settings", "idempotency_ttl") or 3600
        existing = frappe.cache().get_value(cache_key)
        if existing is not None:
            return {"status": "duplicate"}
        frappe.cache().set_value(cache_key, 1, expires_in_sec=idempotency_ttl)

    # ── Route to handler ──
    try:
        if event_type == "message.received":
            _handle_inbound_message(event_data, whatsapp_account, session_id)
        elif event_type == "message.sent":
            _handle_message_sent(event_data)
        elif event_type in ("message.ack", "message.failed"):
            _handle_status_update(event_data)
        elif event_type == "message.revoked":
            _handle_message_revoked(event_data)
        elif event_type == "message.reaction":
            _handle_message_reaction(event_data)
        elif event_type == "message.edited":
            _handle_message_edited(event_data)
        elif event_type in ("session.status", "session.disconnected"):
            _handle_session_status(event_data, session_id)
        elif event_type in ("session.qr",):
            _handle_session_qr(event_data, session_id)
        elif event_type in ("session.authenticated",):
            _handle_session_authenticated(session_id)
        elif event_type == "session.reconnect_loop":
            _handle_session_reconnect_loop(event_data, session_id)
        elif event_type in ("group.join", "group.leave"):
            _handle_group_membership(event_data, session_id, event_type)
        elif event_type == "group.update":
            _handle_group_update(event_data, session_id)
        elif event_type == "call.received":
            _handle_call_received(event_data, session_id)
        elif event_type == "status.received":
            _handle_status_received(event_data, session_id)
    except Exception as e:
        frappe.log_error(
            title="OpenWA Inbound Handler Error",
            message=f"Event: {event_type}, Session: {session_id}\n{frappe.get_traceback()}",
        )

    return {"status": "ok"}


# ── Message handler ──────────────────────────────────────────────────


def _handle_inbound_message(
    msg_data: dict,
    whatsapp_account: "WhatsAppAccount | None",  # noqa: F821
    session_id: str,
) -> None:
    """
    Create a WhatsApp Message record from an incoming OpenWA message event.
    
    Parameters:
    	msg_data (dict): OpenWA message event data.
    	whatsapp_account (WhatsAppAccount | None): Account associated with the message, when already resolved.
    	session_id (str): OpenWA session identifier used to resolve the account and sender.
    
    """
    # Skip outgoing echo
    if msg_data.get("fromMe"):
        return

    sender_jid: str = msg_data.get("from", "")
    phone_number = strip_jid_suffix(sender_jid)
    is_unresolved_lid = False

    # Group messages — extract actual author
    if msg_data.get("isGroup", False):
        author_jid: str = msg_data.get("author", "")
        if author_jid and author_jid != sender_jid:
            phone_number = strip_jid_suffix(author_jid)

    # Prefer real phone from OpenWA's LID resolution when available
    sender_phone: str = msg_data.get("senderPhone") or ""
    if sender_phone and sender_phone.strip().isdigit():
        phone_number = sender_phone.strip()
    elif "@lid" in sender_jid or (msg_data.get("isGroup", False) and "@lid" in (msg_data.get("author", "") or "")):
        # Try cache first, then API — but never block the handler
        cached = _get_cached_lid_phone(session_id, sender_jid)
        if cached:
            phone_number = cached
        else:
            is_unresolved_lid = True
            _queue_lid_resolution(session_id, sender_jid)

    # Back-fill LID recipients: if the raw LID digits are in a Recipient List, replace with real phone
    _fix_lid_recipients(sender_jid, phone_number)

    if not whatsapp_account:
        whatsapp_account = _resolve_account_by_session(session_id)
    if not whatsapp_account:
        frappe.log_error(
            title="OpenWA: No matching WhatsApp Account",
            message=f"Session: {session_id}, Sender: {phone_number}",
        )
        frappe.logger().info(
            f"OpenWA: DROPPED - no account for session={session_id} from={phone_number}"
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

    try:
        doc = frappe.get_doc(doc_data)
        doc.insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(
            title="OpenWA: WhatsApp Message insert FAILED",
            message=(
                f"From: {phone_number}, Msg: {message_body[:100]}\n"
                f"Doc data: {doc_data}\n{frappe.get_traceback()}"
            ),
        )
        return

    frappe.logger().info(
        f"OpenWA inbound: created WhatsApp Message {doc.name} from {phone_number} "
        f"kind={msg_data.get('kind', 'unknown')}"
    )

    # These are best-effort — a failure here should NOT prevent the message from being saved
    try:
        media_info = msg_data.get("media")
        if isinstance(media_info, dict) and not media_info.get("omitted"):
            _attach_openwa_media(doc, media_info)
    except Exception:
        frappe.log_error(
            title="OpenWA: Media attach failed",
            message=f"Msg: {doc.name}\n{frappe.get_traceback()}",
        )

    try:
        if not is_unresolved_lid:
            _ensure_whatsapp_profile(phone_number, profile_name, whatsapp_account.name)
    except Exception:
        frappe.log_error(
            title="OpenWA: Profile create failed",
            message=f"Phone: {phone_number}\n{frappe.get_traceback()}",
        )

    try:
        _create_communication(doc, phone_number, profile_name, is_unresolved_lid=is_unresolved_lid)
    except Exception:
        frappe.log_error(
            title="OpenWA: Communication create failed",
            message=f"Msg: {doc.name}, Contact: {phone_number}\n{frappe.get_traceback()}",
        )


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


def _handle_message_sent(event_data: dict) -> None:
    """
    Associate an OpenWA message ID with the most recent matching outgoing WhatsApp message.
    
    Parameters:
    	event_data (dict): OpenWA event data containing the message ID and recipient.
    """
    wa_msg_id: str = event_data.get("id", "")
    if not wa_msg_id:
        return

    # If the message already has this ID stored, nothing to do
    existing = frappe.db.get_value(
        "WhatsApp Message", {"message_id": wa_msg_id}, "name"
    )
    if existing:
        return

    # The sent event has ``to`` as a JID like "12345@c.us".  Extract phone.
    to_jid: str = event_data.get("to") or event_data.get("chatId") or ""
    phone = strip_jid_suffix(to_jid)
    if not phone:
        return

    # Primary: find the most recent outgoing message to this EXACT phone
    # that still has no message_id.  Use an exact match on the last segment
    # of the `to` field (which stores the formatted phone) instead of LIKE.
    name = frappe.db.get_value(
        "WhatsApp Message",
        filters={
            "type": "Outgoing",
            "message_id": ("is", "not set"),
        },
        order_by="creation desc",
        limit_page_length=1,
        pluck="name",
    )

    # Verify the matched message is actually going to this phone.
    # The `to` field may contain the full JID or just the phone.
    if name:
        to_field = frappe.db.get_value("WhatsApp Message", name, "to") or ""
        to_phone = strip_jid_suffix(to_field)
        if to_phone != phone:
            name = None

    if not name:
        # Fallback: try LIKE but with exact phone boundary to reduce false matches
        name = frappe.db.get_value(
            "WhatsApp Message",
            filters={
                "to": ("like", f"%{phone}"),
                "type": "Outgoing",
                "message_id": ("is", "not set"),
            },
            order_by="creation desc",
            limit_page_length=1,
            pluck="name",
        )
    if not name:
        return

    frappe.db.set_value("WhatsApp Message", name, "message_id", wa_msg_id)
    frappe.logger().info(
        f"OpenWA message.sent: stored message_id '{wa_msg_id}' "
        f"on WhatsApp Message {name}"
    )


def _handle_status_update(event_data: dict) -> None:
    """Map OpenWA ``message.ack`` / ``message.failed`` to
    WhatsApp Message status and reconcile the linked OpenWA Outbox entry.

    Status can only ADVANCE (never downgrade) — matching OpenWA's own
    ``ackStatusTransitionFrom`` guard.  Priority:
        pending < Sent < Delivered < Read
    """
    message_id: str = event_data.get("messageId") or event_data.get("id", "")
    status: str = event_data.get("status", "")

    if not message_id or not status:
        return

    name = _find_whatsapp_message(message_id)

    if not name:
        return

    new_status = status.capitalize()
    # OpenWA maps ack 5 (PLAYED) to "read" — normalize
    if new_status in ("Played",):
        new_status = "Read"

    _STATUS_PRIORITY = {
        "Queued": 0,
        "Pending": 0,
        "Sent": 1,
        "Delivered": 2,
        "Read": 3,
        "Failed": -1,
        "Revoked": -1,
    }

    current_status = frappe.db.get_value("WhatsApp Message", name, "status") or ""
    current_priority = _STATUS_PRIORITY.get(current_status, 0)
    new_priority = _STATUS_PRIORITY.get(new_status, 0)

    if new_priority <= current_priority and new_status != current_status:
        frappe.logger().debug(
            f"OpenWA ack: skipping downgrade {current_status} → {new_status} "
            f"for message {name} ({message_id})"
        )
        return

    if new_status != current_status:
        frappe.db.set_value("WhatsApp Message", name, "status", new_status)
        frappe.logger().info(
            f"OpenWA ack: {name} status {current_status} → {new_status} "
            f"(message_id={message_id})"
        )

    # Reconcile the OpenWA Outbox: if the message was delivered/read,
    # any linked outbox entry stuck in Pending or Sending should be
    # marked Sent to prevent duplicate resends by the scheduler.
    if new_status in ("Sent", "Delivered", "Read"):
        _reconcile_outbox_on_ack(name, message_id)


def _find_whatsapp_message(wa_msg_id: str) -> str | None:
    """
    Finds the Frappe document name associated with an OpenWA message ID.
    
    Parameters:
        wa_msg_id (str): The OpenWA message identifier.
    
    Returns:
        str | None: The matching WhatsApp Message document name, or None when no
            matching message is found. Fallback matches are associated with the
            supplied message ID for future lookups.
    """
    name = frappe.db.get_value(
        "WhatsApp Message",
        filters={"message_id": wa_msg_id},
        pluck="name",
    )
    if name:
        return name

    # Fallback: extract phone from JID-style message ID
    phone = strip_jid_suffix(wa_msg_id.split("_")[1]) if "_" in wa_msg_id else ""
    if not phone or not phone.isdigit():
        return None

    # Try exact match first: most recent outgoing with no message_id
    # whose `to` field ends with this phone
    name = frappe.db.get_value(
        "WhatsApp Message",
        filters={
            "to": ("like", f"%{phone}"),
            "type": "Outgoing",
            "message_id": ("is", "not set"),
        },
        order_by="creation desc",
        limit_page_length=1,
        pluck="name",
    )
    if name:
        # Store the message_id now so future lookups are instant
        frappe.db.set_value("WhatsApp Message", name, "message_id", wa_msg_id)
        frappe.logger().info(
            f"OpenWA: resolved message_id '{wa_msg_id}' → WhatsApp Message {name} "
            f"(fallback by phone {phone})"
        )
    return name


def _reconcile_outbox_on_ack(whatsapp_message_name: str, message_id: str) -> None:
    """Mark any stuck OpenWA Outbox entry as Sent when a delivery ack arrives.

    This prevents the scheduler from retrying messages that were already
    delivered.  Called from ``_handle_status_update`` when the ack carries
    a terminal-ish status (sent / delivered / read).
    """
    outbox_entries = frappe.get_all(
        "OpenWA Outbox",
        filters={
            "whatsapp_message": whatsapp_message_name,
            "status": ("in", ["Pending", "Sending"]),
        },
        fields=["name", "status"],
        limit_page_length=0,
    )
    for entry in outbox_entries:
        frappe.db.set_value(
            "OpenWA Outbox",
            entry.name,
            {"status": "Sent"},
        )
        frappe.logger().info(
            f"OpenWA outbox reconciled via ack: {entry.name} "
            f"({entry.status} -> Sent) for message {whatsapp_message_name}"
        )


def _handle_session_status(event_data: dict, session_id: str) -> None:
    """
    Update the WhatsApp Account status for a recognized OpenWA session event.
    
    Parameters:
    	event_data (dict): OpenWA session event data containing the session status.
    	session_id (str): OpenWA session identifier used to locate the account.
    """
    status = event_data.get("status", "")
    if not status:
        return
    status_map = {
        "ready": "Active",
        "disconnected": "Inactive",
        "failed": "Inactive",
        "action_required": "Inactive",
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

        # When session disconnects, log pending outbox entries for visibility
        if status in ("disconnected", "failed"):
            pending_count = frappe.db.count(
                "OpenWA Outbox",
                filters={
                    "whatsapp_account": account_name,
                    "status": ("in", ["Pending", "Sending"]),
                },
            )
            if pending_count:
                frappe.logger().warning(
                    f"OpenWA session '{session_id}' on '{account_name}' "
                    f"is {status} — {pending_count} outbox entries waiting. "
                    f"They will be retried when the session reconnects."
                )
            frappe.log_error(
                title=f"OpenWA: Session {status} on {account_name}",
                message=(
                    f"Session {session_id} status changed to '{status}'. "
                    f"{pending_count} pending outbox entries will be retried "
                    f"automatically when the session reconnects."
                ),
            )

        # action_required (OpenWA 0.12.0+) — the "What's new" onboarding modal
        # needs a human. Sends return 409 until it is acknowledged, so surface
        # lastError and the recovery step instead of treating it like a plain
        # disconnect.
        if status == "action_required":
            last_error = event_data.get("lastError") or ""
            frappe.log_error(
                title=f"OpenWA: Session action_required on {account_name}",
                message=(
                    f"Session {session_id} needs an operator: "
                    f"{last_error or 'WhatsApp onboarding modal not dismissed'}. "
                    "Acknowledge the 'What's new' modal in a browser signed in "
                    "as that account, then stop and start the session — no QR "
                    "rescan is needed."
                ),
            )


def _handle_message_revoked(event_data: dict) -> None:
    """Update WhatsApp Message status when a message is revoked/deleted."""
    message_id: str = event_data.get("messageId") or event_data.get("id", "")
    if not message_id:
        return

    name = frappe.db.get_value(
        "WhatsApp Message",
        filters={"message_id": message_id},
        pluck="name",
    )
    if not name:
        return

    frappe.db.set_value("WhatsApp Message", name, "status", "Revoked")


def _handle_message_reaction(event_data: dict) -> None:
    """Record a message reaction on the WhatsApp Message doc."""
    import json as _json

    message_id: str = event_data.get("messageId") or event_data.get("id", "")
    emoji: str = event_data.get("emoji", "")
    sender: str = event_data.get("sender", "") or event_data.get("from", "")
    if not message_id or not emoji:
        return

    name = frappe.db.get_value(
        "WhatsApp Message",
        filters={"message_id": message_id},
        pluck="name",
    )
    if not name:
        return

    reaction = {
        "emoji": emoji,
        "sender": strip_jid_suffix(sender) if sender else "",
        "timestamp": frappe.utils.now(),
    }

    # Append to the existing reaction list (advance-only on duplicate emoji)
    reactions = []
    existing = frappe.db.get_value("WhatsApp Message", name, "openwa_reactions")
    if existing:
        try:
            reactions = _json.loads(existing)
            if not isinstance(reactions, list):
                reactions = []
        except Exception:
            reactions = []

    deduped = [r for r in reactions if not (
        r.get("emoji") == emoji and r.get("sender") == reaction["sender"]
    )]
    deduped.append(reaction)

    frappe.db.set_value("WhatsApp Message", name, "openwa_reactions", _json.dumps(deduped))
    frappe.logger().info(f"OpenWA reaction on {name}: {emoji}")


def _handle_message_edited(event_data: dict) -> None:
    """Update WhatsApp Message body when a sent message is edited."""
    message_id: str = event_data.get("messageId") or event_data.get("id", "")
    new_body: str = event_data.get("body", "")
    if not message_id or not new_body:
        return

    name = frappe.db.get_value(
        "WhatsApp Message",
        filters={"message_id": message_id},
        pluck="name",
    )
    if not name:
        return

    # Store original message before overwriting
    original = frappe.db.get_value("WhatsApp Message", name, "message") or ""
    if original != new_body:
        frappe.db.set_value("WhatsApp Message", name, "message", new_body)
        frappe.logger().info(
            f"OpenWA: message edited {name}: '{original[:50]}...' → '{new_body[:50]}...'"
        )


def _handle_session_reconnect_loop(event_data: dict, session_id: str) -> None:
    """Alert when session is stuck in a reconnect loop."""
    account_name = frappe.db.get_value(
        "WhatsApp Account",
        {"openwa_session_id": session_id},
        "name",
    )
    if not account_name:
        return

    frappe.log_error(
        title=f"OpenWA: Session Reconnect Loop on {account_name}",
        message=(
            f"Session {session_id} is stuck in a reconnect loop. "
            f"Consider resetting the session or checking network connectivity."
        ),
    )


def _log_event(event_type: str, session_id: str, summary: str,
               payload: dict, group_id: str = "", contact: str = "") -> None:
    """Persist a non-message OpenWA event to the OpenWA Event Log doctype.

    Best-effort: a missing doctype or permission problem must never break the
    webhook receiver, so any failure is swallowed and logged at info level.
    """
    account_name = frappe.db.get_value(
        "WhatsApp Account",
        {"openwa_session_id": session_id},
        "name",
    )
    try:
        import json as _json

        doc = frappe.get_doc({
            "doctype": "OpenWA Event Log",
            "event_type": event_type,
            "whatsapp_account": account_name,
            "session_id": session_id,
            "timestamp": frappe.utils.now(),
            "summary": (summary or "")[:500],
            "payload": _json.dumps(payload, default=str)[:20000],
            "related_group": group_id,
            "related_contact": contact,
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        # Never let a logging failure break webhook processing
        frappe.logger().info(f"OpenWA {event_type}: {summary}")


def _handle_group_membership(event_data: dict, session_id: str, event_type: str) -> None:
    """Persist group join/leave events to the OpenWA Event Log."""
    group_id: str = event_data.get("groupId", "")
    participants: list = event_data.get("participantIds", [])
    actor: str = event_data.get("actorId", "")

    if not group_id:
        return

    _log_event(
        event_type,
        session_id,
        f"{event_type}: {len(participants)} participant(s) via {actor}",
        event_data,
        group_id=group_id,
    )


def _handle_group_update(event_data: dict, session_id: str) -> None:
    """Persist group metadata changes to the OpenWA Event Log."""
    group_id: str = event_data.get("groupId", "")
    changes: dict = event_data.get("changes", {})

    if not group_id:
        return

    _log_event(
        "group.update",
        session_id,
        f"group.update: {list(changes.keys()) if isinstance(changes, dict) else changes}",
        event_data,
        group_id=group_id,
    )


def _handle_call_received(event_data: dict, session_id: str) -> None:
    """Persist incoming call events to the OpenWA Event Log."""
    call_id: str = event_data.get("callId", "")
    caller: str = event_data.get("from", "")
    is_video: bool = event_data.get("isVideo", False)
    is_group: bool = event_data.get("isGroup", False)

    _log_event(
        "call.received",
        session_id,
        f"call.received: {caller} ({'video' if is_video else 'audio'}"
        f"{', group' if is_group else ''})",
        event_data,
        contact=caller,
    )


def _handle_status_received(event_data: dict, session_id: str) -> None:
    """
    Persist a received contact status or story update to the OpenWA Event Log.

    Parameters:
    	event_data (dict): Event payload containing the contact, status type, caption, and media indicator.
    	session_id (str): OpenWA session identifier.
    """
    contact: str = event_data.get("contact", "") or event_data.get("from", "")
    status_type: str = event_data.get("type", "")
    caption: str = event_data.get("caption", "")
    has_media: bool = event_data.get("hasMedia", False)

    _log_event(
        "status.received",
        session_id,
        f"status.received: {contact} ({status_type or 'unknown'})",
        event_data,
        contact=contact,
    )


def _handle_session_qr(event_data: dict, session_id: str) -> None:
    """Update account status when QR code is ready for scanning."""
    account_name = frappe.db.get_value(
        "WhatsApp Account",
        {"openwa_session_id": session_id},
        "name",
    )
    if account_name:
        frappe.db.set_value("WhatsApp Account", account_name, "status", "Inactive")


def _handle_session_authenticated(session_id: str) -> None:
    """Update account status when session is authenticated."""
    account_name = frappe.db.get_value(
        "WhatsApp Account",
        {"openwa_session_id": session_id},
        "name",
    )
    if account_name:
        frappe.db.set_value("WhatsApp Account", account_name, "status", "Active")


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


# ── Communication / Lead helper ──────────────────────────────────────


def _create_communication(
    message_doc: "Document",  # noqa: F821
    phone_number: str,
    profile_name: str | None,
    *,
    is_unresolved_lid: bool = False,
) -> None:
    """Create Communication doc and optionally a Lead/Contact for the sender.

    Looks up an existing Contact by mobile_no. If found, creates a
    Communication linked to that Contact. If not found, creates a
    new Lead + Contact, then the Communication.

    When ``is_unresolved_lid`` is True the phone is still a raw LID
    (WhatsApp privacy ID) so we skip Lead/Contact creation — the
    background LID resolver will back-fill them later.
    """
    formatted = format_number(phone_number)

    contact_name = frappe.db.get_value(
        "Contact", {"mobile_no": formatted}, "name"
    )

    if not contact_name and not is_unresolved_lid:
        lead_name = None
        try:
            lead = frappe.get_doc({
                "doctype": "Lead",
                "lead_name": profile_name or formatted,
                "mobile_no": formatted,
                "source": "WhatsApp",
            })
            lead.insert(ignore_permissions=True)
            lead_name = lead.name

            contact = frappe.get_doc({
                "doctype": "Contact",
                "first_name": profile_name or formatted,
                "mobile_no": formatted,
                "links": [{"link_doctype": "Lead", "link_name": lead_name}],
            })
            contact.insert(ignore_permissions=True)
            contact_name = contact.name
        except Exception:
            frappe.log_error(
                title="OpenWA: Failed to create Lead/Contact",
                message=f"Phone: {formatted}, Name: {profile_name}\n{frappe.get_traceback()}",
            )
            return

    try:
        subject = (message_doc.message or "").strip()[:140] or f"WhatsApp {message_doc.message_type or 'Message'}"
        communication = frappe.get_doc({
            "doctype": "Communication",
            "communication_type": "Communication",
            "communication_medium": "Chat",
            "subject": subject,
            "content": message_doc.message or "",
            "reference_doctype": "WhatsApp Message",
            "reference_name": message_doc.name,
            "party_type": "Contact",
            "party": contact_name,
            "status": "Linked",
        })
        communication.insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(
            title="OpenWA: Failed to create Communication",
            message=f"Msg: {message_doc.name}, Contact: {contact_name}\n{frappe.get_traceback()}",
        )


# ── LID→phone resolution ────────────────────────────────────────────

_LID_CACHE_TTL = 86400  # 24 hours


def _get_cached_lid_phone(session_id: str, lid_jid: str) -> str | None:
    """Check in-memory/Redis cache for a previously resolved LID→phone."""
    cache_key = f"openwa_lid::{session_id}::{lid_jid}"
    return frappe.cache().get_value(cache_key)


def _set_cached_lid_phone(session_id: str, lid_jid: str, phone: str) -> None:
    """Cache a resolved LID→phone mapping."""
    cache_key = f"openwa_lid::{session_id}::{lid_jid}"
    frappe.cache().set_value(cache_key, phone, expires_in_sec=_LID_CACHE_TTL)


def _queue_lid_resolution(session_id: str, lid_jid: str) -> None:
    """Enqueue a background job to resolve LID→phone (non-blocking)."""
    cache_key = f"openwa_lid::{session_id}::{lid_jid}"
    if frappe.cache().get_value(cache_key) is not None:
        return  # already resolved recently
    # Dedup: don't queue the same LID twice within 60 seconds
    dedup_key = f"openwa_lid_queued::{session_id}::{lid_jid}"
    if frappe.cache().get_value(dedup_key):
        return
    frappe.cache().set_value(dedup_key, 1, expires_in_sec=60)
    frappe.enqueue(
        "openwa_bridge.inbound._resolve_lid_phone_background",
        queue="short",
        session_id=session_id,
        lid_jid=lid_jid,
    )


def _resolve_lid_phone_background(session_id: str, lid_jid: str) -> None:
    """Background job: resolve LID→phone and update cached data + recipient lists."""
    phone = _resolve_lid_phone(session_id, lid_jid)
    if phone:
        _set_cached_lid_phone(session_id, lid_jid, phone)
        _fix_lid_recipients(lid_jid, phone)
        _backfill_lid_messages_and_contacts(lid_jid, phone)


def _resolve_lid_phone(session_id: str, lid_jid: str) -> str | None:
    """Resolve a WhatsApp LID (privacy ID) to a real phone number via OpenWA API.

    Calls GET /api/sessions/:sessionId/contacts/:contactId/phone
    Returns the phone digits string, or None on failure.
    """
    if not session_id or not lid_jid:
        return None

    whatsapp_account = _resolve_account_by_session(session_id)
    if not whatsapp_account:
        return None

    try:
        result = openwa_api(
            whatsapp_account,
            "GET",
            f"/contacts/{lid_jid}/phone",
            timeout=5,
        )
        phone = (result or {}).get("phone")
        if phone and phone.strip().isdigit():
            frappe.logger().info(
                f"OpenWA: Resolved LID {lid_jid} → {phone}"
            )
            return phone.strip()
    except Exception:
        frappe.logger().debug(
            f"OpenWA: LID resolution failed for {lid_jid} "
            f"(session {session_id}), using raw JID"
        )

    return None


def _fix_lid_recipients(sender_jid: str, resolved_phone: str) -> None:
    """Replace LID numbers in WhatsApp Recipient List with real phone numbers.

    When a LID sender is resolved to a real phone, scan Recipient Lists for
    entries that still hold the raw LID digits as mobile_number and update them.
    """
    lid_digits = strip_jid_suffix(sender_jid)
    if not resolved_phone or not lid_digits or resolved_phone == lid_digits:
        return
    if "@lid" not in sender_jid:
        return

    try:
        if not frappe.db.exists("DocType", "WhatsApp Recipient"):
            return
        recipients = frappe.get_all(
            "WhatsApp Recipient",
            filters={"mobile_number": lid_digits},
            fields=["name", "parent", "mobile_number"],
        )
        for r in recipients:
            frappe.db.set_value("WhatsApp Recipient", r.name, "mobile_number", resolved_phone)
            frappe.logger().info(
                f"OpenWA: Fixed recipient {r.name} in list {r.parent}: "
                f"{lid_digits} → {resolved_phone}"
            )
    except Exception:
        frappe.logger().debug(
            f"OpenWA: Could not fix recipients for LID {lid_digits}"
        )


def _backfill_lid_messages_and_contacts(lid_jid: str, resolved_phone: str) -> None:
    """After LID→phone resolution, back-fill WhatsApp Messages and create/update Lead+Contact.

    When a message first arrives from a LID, it is saved with the raw LID
    digits as the ``from`` field.  This function updates those messages
    to use the real phone number, and creates the Lead/Contact that were
    skipped during initial processing.
    """
    lid_digits = strip_jid_suffix(lid_jid)
    if not resolved_phone or not lid_digits or resolved_phone == lid_digits:
        return

    formatted_new = format_number(resolved_phone)
    formatted_lid = format_number(lid_digits)

    try:
        # Update WhatsApp Messages that still have the LID digits as sender
        messages = frappe.get_all(
            "WhatsApp Message",
            filters={"from": formatted_lid, "type": "Incoming"},
            fields=["name"],
        )
        for msg in messages:
            frappe.db.set_value("WhatsApp Message", msg.name, "from", formatted_new)
        if messages:
            frappe.logger().info(
                f"OpenWA: Back-filled {len(messages)} WhatsApp Messages: "
                f"{formatted_lid} → {formatted_new}"
            )
    except Exception:
        frappe.logger().debug(
            f"OpenWA: Could not back-fill messages for LID {lid_digits}"
        )

    try:
        # Create Lead + Contact if they don't exist for the resolved phone
        contact_name = frappe.db.get_value("Contact", {"mobile_no": formatted_new}, "name")
        if not contact_name:
            # Find profile name from any existing message
            profile_name = None
            if messages:
                profile_name = frappe.db.get_value(
                    "WhatsApp Message", messages[0].name, "profile_name"
                )

            lead = frappe.get_doc({
                "doctype": "Lead",
                "lead_name": profile_name or formatted_new,
                "mobile_no": formatted_new,
                "source": "WhatsApp",
            })
            lead.insert(ignore_permissions=True)

            contact = frappe.get_doc({
                "doctype": "Contact",
                "first_name": profile_name or formatted_new,
                "mobile_no": formatted_new,
                "links": [{"link_doctype": "Lead", "link_name": lead.name}],
            })
            contact.insert(ignore_permissions=True)
            contact_name = contact.name

            frappe.logger().info(
                f"OpenWA: Created Lead {lead.name} + Contact {contact_name} "
                f"from resolved LID {lid_digits} → {formatted_new}"
            )

            # Link Communications to the new Contact
            for msg in messages:
                if not frappe.db.exists(
                    "Communication",
                    {"reference_doctype": "WhatsApp Message", "reference_name": msg.name},
                ):
                    frappe.get_doc({
                        "doctype": "Communication",
                        "communication_type": "Communication",
                        "communication_medium": "Chat",
                        "subject": f"WhatsApp message from {profile_name or formatted_new}",
                        "reference_doctype": "WhatsApp Message",
                        "reference_name": msg.name,
                        "party_type": "Contact",
                        "party": contact_name,
                        "status": "Linked",
                    }).insert(ignore_permissions=True)

        # Also fix any existing Contact that was created with LID digits
        if contact_name and formatted_lid != formatted_new:
            old_contacts = frappe.get_all(
                "Contact",
                filters={"mobile_no": formatted_lid},
                fields=["name"],
            )
            for old_c in old_contacts:
                frappe.db.set_value("Contact", old_c.name, "mobile_no", formatted_new)
                frappe.logger().info(
                    f"OpenWA: Updated Contact {old_c.name} mobile: "
                    f"{formatted_lid} → {formatted_new}"
                )

    except Exception:
        frappe.log_error(
            title="OpenWA: LID backfill Lead/Contact failed",
            message=f"LID: {lid_digits} → {formatted_new}\n{frappe.get_traceback()}",
        )


# ── Bulk LID fix (one-time) ────────────────────────────────────────


def _is_likely_lid(number: str) -> bool:
    """Heuristic: a number is likely a LID if it doesn't match a phone pattern.

    Real phone numbers (MSISDN) are 7-15 digits. LID numbers tend to be
    15+ digits or start with patterns like 120xxx.
    """
    if not number or not number.isdigit():
        return False
    # LIDs from WhatsApp are typically very long or start with known LID prefixes
    if len(number) > 15:
        return True
    if number.startswith("120") and len(number) > 13:
        return True
    return False


@frappe.whitelist()
def bulk_fix_lid_recipients(session_id: str | None = None) -> dict:
    """One-time fix: resolve all LID numbers in WhatsApp Recipient Lists.

    Call from bench console or API:
        bench --site your-site execute openwa_bridge.inbound.bulk_fix_lid_recipients

    Returns stats: {fixed: int, failed: int, skipped: int}
    """
    if not session_id:
        # Pick the first active OpenWA session
        account = frappe.db.get_value(
            "WhatsApp Account",
            {"openwa_enabled": 1, "status": "Active"},
            ["name", "openwa_session_id"],
            as_dict=True,
        )
        if not account or not account.openwa_session_id:
            return {"fixed": 0, "failed": 0, "skipped": 0, "error": "No active OpenWA session found"}
        session_id = account.openwa_session_id
        whatsapp_account = frappe.get_doc("WhatsApp Account", account.name)
    else:
        whatsapp_account = _resolve_account_by_session(session_id)

    if not whatsapp_account:
        return {"fixed": 0, "failed": 0, "skipped": 0, "error": "Cannot resolve WhatsApp Account"}

    recipients = frappe.get_all(
        "WhatsApp Recipient",
        fields=["name", "mobile_number", "recipient_name"],
    )

    fixed = 0
    failed = 0
    skipped = 0

    for r in recipients:
        num = r.mobile_number or ""
        if not _is_likely_lid(num):
            skipped += 1
            continue

        lid_jid = f"{num}@lid"
        try:
            result = openwa_api(
                whatsapp_account,
                "GET",
                f"/contacts/{lid_jid}/phone",
                timeout=5,
            )
            phone = (result or {}).get("phone")
            if phone and phone.strip().isdigit() and phone.strip() != num:
                frappe.db.set_value("WhatsApp Recipient", r.name, "mobile_number", phone.strip())
                fixed += 1
                frappe.logger().info(
                    f"OpenWA bulk fix: {r.name} ({r.recipient_name}): "
                    f"{num} → {phone.strip()}"
                )
            else:
                skipped += 1
        except Exception:
            failed += 1
            frappe.logger().debug(f"OpenWA bulk fix: failed for {r.name} ({num})")

    frappe.db.commit()
    return {"fixed": fixed, "failed": failed, "skipped": skipped}
