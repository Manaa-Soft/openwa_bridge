"""Scheduled tasks for OpenWA session health, auto-reconnection, and outbox processing."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

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
            api_key = doc.get_password("openwa_api_key")
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


# ---------------------------------------------------------------------------
# Outbox processing
# ---------------------------------------------------------------------------


def process_outbox_entry(outbox_name: str) -> None:  # noqa: C901
    """Process a single OpenWA Outbox entry.

    Called by ``frappe.enqueue`` from ``OverrideWhatsAppMessage.notify()`` and
    also by the scheduler safety-net ``process_pending_outbox``.
    """
    try:
        outbox = frappe.get_doc("OpenWA Outbox", outbox_name, for_update=True)
    except Exception:
        return

    # Guard: only process Pending entries
    if outbox.status not in ("Pending",):
        return

    # Guard: respect backoff window
    if outbox.next_retry_at and outbox.next_retry_at > datetime.now():
        return

    # Guard: exhausted retries
    if outbox.attempts >= (outbox.max_attempts or 5):
        frappe.db.set_value(
            "OpenWA Outbox",
            outbox_name,
            {"status": "Failed", "last_error": "Maximum retry attempts exhausted"},
        )
        frappe.db.commit()
        return

    # Mark as sending
    frappe.db.set_value(
        "OpenWA Outbox",
        outbox_name,
        {"status": "Sending", "attempts": (outbox.attempts or 0) + 1},
    )
    frappe.db.commit()

    # Load linked docs
    try:
        msg = frappe.get_doc("WhatsApp Message", outbox.whatsapp_message)
        account = frappe.get_doc("WhatsApp Account", outbox.whatsapp_account)
    except Exception as exc:
        _fail_outbox(outbox_name, str(exc))
        return

    if not account.get("openwa_enabled"):
        _fail_outbox(outbox_name, "WhatsApp Account no longer has OpenWA enabled")
        return

    # Check circuit breaker
    from openwa_bridge.utils import OpenWACircuitBreaker

    breaker = OpenWACircuitBreaker(account.name)
    if breaker.is_open():
        _fail_outbox(
            outbox_name,
            f"Circuit breaker open — {breaker.remaining_cooldown()}s cooldown remaining",
        )
        return

    # Build and send payload
    try:
        _send_outbox_message(msg, account, outbox)
        breaker.record_success()
        # Success
        frappe.db.set_value(
            "OpenWA Outbox",
            outbox_name,
            {"status": "Sent"},
        )
        frappe.db.commit()
    except Exception as exc:
        breaker.record_failure()
        _fail_outbox(outbox_name, str(exc))


def _send_dynamic_header_for_outbox(msg, account, caption=None) -> bool:
    """Send dynamic header image before the main message, if configured.

    Checks the linked WhatsApp Templates doc for ``openwa_dynamic_header``
    and ``openwa_print_format``.  Renders the reference doc as an image
    and sends it via OpenWA send-image endpoint with the text as caption.

    Returns True if image was sent successfully, False otherwise.
    """
    if not msg.template:
        return False

    try:
        tmpl = frappe.get_doc("WhatsApp Templates", msg.template)
    except Exception:
        return False

    if not getattr(tmpl, "openwa_dynamic_header", False) or not getattr(tmpl, "openwa_print_format", None):
        return False

    # Need the source document to render
    ref_doctype = msg.reference_doctype
    ref_name = msg.reference_name
    if not ref_doctype or not ref_name:
        return False

    try:
        doc = frappe.get_doc(ref_doctype, ref_name)
    except Exception:
        return False

    print_format = tmpl.openwa_print_format
    letterhead = None
    if getattr(tmpl, "openwa_include_letterhead", False):
        letterhead = getattr(tmpl, "openwa_letterhead", None) or None

    from openwa_bridge.utils import render_doc_as_image, get_api_key
    from frappe_whatsapp.utils import format_number

    image_bytes = render_doc_as_image(ref_doctype, ref_name, print_format, letterhead=letterhead)
    if not image_bytes:
        return False

    import requests as _req
    import base64

    base_url = account.get("openwa_base_url").strip("/")
    session_id = account.get("openwa_session_id")
    api_key = get_api_key(account)

    raw_number = format_number(msg.to)
    chat_id = f"{raw_number}@c.us" if "@c.us" not in raw_number else raw_number

    url = f"{base_url}/api/sessions/{session_id}/messages/send-image"
    payload = {
        "chatId": chat_id,
        "base64": base64.b64encode(image_bytes).decode("utf-8"),
        "mimetype": "image/png",
    }
    if caption:
        payload["caption"] = caption

    try:
        img_resp = _req.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", "X-API-Key": api_key},
            timeout=30,
        )
        if img_resp.status_code >= 400:
            frappe.log_error(
                title="OpenWA: Dynamic header image failed",
                message=(
                    f"Template {tmpl.name}, Doc {ref_doctype} {ref_name}\n"
                    f"POST {url}\n"
                    f"Status: {img_resp.status_code}\n"
                    f"Response: {img_resp.text[:2000]}"
                ),
            )
            return False
        return True
    except Exception as e:
        frappe.log_error(
            title="OpenWA: Dynamic header image failed",
            message=f"Template {tmpl.name}, Doc {ref_doctype} {ref_name}: {e}",
        )
        return False


def _send_outbox_message(msg, account, outbox) -> None:  # noqa: C901
    """Actually send the message via OpenWA. Reuses the dispatcher from whatsapp_message."""

    # Phase 1: Send dynamic header image with text as caption (moved from sync path)
    image_sent = _send_dynamic_header_for_outbox(msg, account, caption=msg.message)

    # Phase 2: Send the text/template message (skip if image was sent with caption)
    if image_sent:
        return

    from openwa_bridge.whatsapp_message import OverrideWhatsAppMessage

    # Create a lightweight instance to reuse _ensure_session_ready and _send_via_openwa
    instance = OverrideWhatsAppMessage.__new__(OverrideWhatsAppMessage)
    instance.name = msg.name
    instance.to = msg.to
    instance.message = msg.message
    instance.content_type = msg.content_type
    instance.template = msg.template
    instance.body_param = msg.body_param
    instance.template_parameters = msg.template_parameters
    instance.is_reply = msg.is_reply
    instance.reply_to_message_id = msg.reply_to_message_id
    instance.whatsapp_account = msg.whatsapp_account

    # Build meta payload for media types
    meta_payload: dict = {}
    if msg.content_type in ("image", "video", "audio", "document") and msg.attach:
        link = msg.attach if msg.attach.startswith("http") else frappe.utils.get_url() + "/" + msg.attach
        meta_payload = {msg.content_type: {"link": link, "caption": msg.message}}
    elif msg.content_type == "reaction":
        meta_payload = {"reaction": {"message_id": msg.reply_to_message_id, "emoji": msg.message}}

    instance._ensure_session_ready(account)
    instance._send_via_openwa(account, meta_payload)


def _fail_outbox(outbox_name: str, error: str) -> None:
    """Mark an outbox entry as Failed or schedule a retry."""
    try:
        outbox = frappe.get_doc("OpenWA Outbox", outbox_name)
    except Exception:
        return

    # attempts was already incremented by process_outbox_entry() before calling us
    attempts = outbox.attempts or 0
    max_attempts = outbox.max_attempts or 5
    error_msg = str(error)[:65000]

    if attempts >= max_attempts:
        frappe.db.set_value(
            "OpenWA Outbox",
            outbox_name,
            {"status": "Failed", "last_error": error_msg},
        )
    else:
        # Exponential backoff: 30s, 60s, 120s, 300s, capped at 1 hour
        backoff_seconds = min(30 * (2 ** (attempts - 1)), 3600)
        next_retry = datetime.now() + timedelta(seconds=backoff_seconds)
        frappe.db.set_value(
            "OpenWA Outbox",
            outbox_name,
            {
                "status": "Pending",
                "last_error": error_msg,
                "next_retry_at": next_retry,
            },
        )

    frappe.db.commit()

    if attempts >= max_attempts:
        frappe.log_error(
            title=f"OpenWA Outbox Failed: {outbox_name}",
            message=f"Attempt {attempts}/{max_attempts}: {error_msg}",
        )
    else:
        frappe.logger().warning(
            f"OpenWA outbox {outbox_name}: attempt {attempts}/{max_attempts} "
            f"failed, retrying in {backoff_seconds}s — {error_msg[:200]}"
        )


def process_pending_outbox() -> None:
    """Scheduler safety-net: re-enqueue orphaned Pending entries.

    Runs every ~4 minutes via the ``all`` scheduler event.  Picks up entries
    that were never processed (e.g. worker crash, Redis restart) and
    re-enqueues them into the ``long`` queue.
    """
    now = datetime.now()

    entries = frappe.get_all(
        "OpenWA Outbox",
        filters={
            "status": "Pending",
            "attempts": ["<", frappe.db.get_single_value("System Settings", "max_auto_retry_count") or 5],
        },
        fields=["name", "next_retry_at"],
        limit=50,
    )

    # Filter: next_retry_at is NULL or <= now
    candidates = [
        e.name for e in entries
        if not e.next_retry_at or e.next_retry_at <= now
    ]

    for name in candidates:
        frappe.enqueue(
            "openwa_bridge.tasks.process_outbox_entry",
            queue="long",
            timeout=300,
            job_id=f"openwa_outbox::{name}",
            deduplicate=True,
            outbox_name=name,
        )

    if candidates:
        frappe.logger().info(f"OpenWA outbox safety-net: re-enqueued {len(candidates)} entry(ies)")
