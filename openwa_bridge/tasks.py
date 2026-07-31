"""Scheduled tasks for OpenWA session health, auto-reconnection, and outbox processing."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import frappe
import requests

from openwa_bridge.utils import get_cached_account, get_account_setting, get_api_key, _http_session


def _get_openwa_accounts() -> list[dict]:
    """Return all WhatsApp Accounts that have OpenWA enabled with a session ID."""
    return frappe.get_all(
        "WhatsApp Account",
        filters={"openwa_enabled": 1, "openwa_session_id": ["is", "set"]},
        fields=["name", "openwa_base_url", "openwa_session_id"],
    )


def _check_session_status(base_url: str, session_id: str, api_key: str) -> dict | None:
    """GET /api/sessions/:id.

    Returns:
        dict with session data if found (HTTP 200).
        dict with ``{"_deleted": True}`` if the session was removed from
        OpenWA (HTTP 404) — callers must check for this.
        dict with ``{"_rate_limited": True}`` on HTTP 429 — callers should
        skip this check and keep the current status.
        None if the server is unreachable or returned an unexpected status.
    """
    try:
        resp = _http_session.get(
            f"{base_url.rstrip('/')}/api/sessions/{session_id}",
            headers={"X-API-Key": api_key},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            return {"_deleted": True}
        if resp.status_code == 429:
            return {"_rate_limited": True}
    except Exception:
        pass
    return None


def _start_session(base_url: str, session_id: str, api_key: str) -> bool:
    """POST /api/sessions/:id/start and return True on success.

    A 409 ``SESSION_NAME_TEARDOWN_PENDING`` means a prior logout still owns
    destructive cleanup for the session name — it is retryable, so we wait
    briefly and retry up to three times before giving up.
    """
    for _ in range(4):
        try:
            resp = _http_session.post(
                f"{base_url.rstrip('/')}/api/sessions/{session_id}/start",
                headers={"X-API-Key": api_key},
                timeout=60,
            )
            if resp.status_code == 409:
                time.sleep(2)
                continue
            return resp.status_code in (200, 201, 400)  # 400 = already started
        except Exception:
            return False
    return False


def _stop_session(base_url: str, session_id: str, api_key: str) -> bool:
    """POST /api/sessions/:id/stop and return True on success."""
    try:
        resp = _http_session.post(
            f"{base_url.rstrip('/')}/api/sessions/{session_id}/stop",
            headers={"X-API-Key": api_key},
            timeout=60,
        )
        return resp.status_code in (200, 204, 400)  # 400 = already stopped
    except Exception:
        return False


def _delete_session(base_url: str, session_id: str, api_key: str) -> None:
    """DELETE /api/sessions/:id.

    A 409 ``SESSION_NAME_TEARDOWN_PENDING`` means a prior logout still owns
    destructive cleanup for the name — retry briefly before giving up.
    """
    for attempt in range(3):
        try:
            resp = _http_session.delete(
                f"{base_url.rstrip('/')}/api/sessions/{session_id}",
                headers={"X-API-Key": api_key},
                timeout=15,
            )
            if resp.status_code == 409:
                time.sleep(3)
                continue
            return
        except Exception:
            return


def _poll_status(
    base_url: str,
    session_id: str,
    api_key: str,
    targets: tuple[str, ...],
    fallback: str,
    timeout_s: int = 15,
) -> str:
    """Poll session status once per second until a target status or timeout.

    Returns the last observed status.
    """
    for _ in range(timeout_s):
        time.sleep(1)
        session = _check_session_status(base_url, session_id, api_key)
        if session and not session.get("_deleted"):
            fallback = session.get("status", fallback)
            if fallback in targets:
                break
    return fallback


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
        "action_required": "Inactive",
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
    reconcile_stale_outbox()


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
            doc = get_cached_account(account_name)
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

        if session.get("_deleted"):
            # Session was deleted from OpenWA (manually or by server).
            # Clear the stale session ID so the user can re-setup.
            frappe.logger().info(
                f"OpenWA health check: session '{session_id}' on "
                f"'{account_name}' no longer exists — clearing stale ID"
            )
            frappe.db.set_value(
                "WhatsApp Account", account_name,
                "openwa_session_id", None,
            )
            frappe.db.set_value(
                "WhatsApp Account", account_name,
                "openwa_status", "Inactive",
            )
            continue

        if session.get("_rate_limited"):
            # Rate-limited — don't change anything, just move on.
            frappe.logger().info(
                f"OpenWA health check: session '{session_id}' on "
                f"'{account_name}' rate-limited — skipping"
            )
            continue

        status = session.get("status", "unknown")
        phone = session.get("phone")
        # OpenWA 0.12.1+: whether the gateway holds a live engine. It
        # disambiguates "disconnected" — an engine still registered while a
        # reconnect backs off (start answers 400) vs a genuinely stopped
        # session that needs a start. Falls back to the old behaviour when the
        # field is absent (gateway < 0.12.1).
        engine_loaded = session.get("engineLoaded")

        # 2. If ready AND connected to WhatsApp (phone set), sync status
        if status == "ready" and phone:
            _set_account_status(account_name, "ready")
            # Auto-sync webhook on health check — ensures the webhook exists
            # after session reconnect or OpenWA restart.
            try:
                from openwa_bridge.whatsapp_account import sync_webhook
                sync_webhook(doc)
            except Exception:
                pass
            continue

        # 3. If ready but no phone — engine alive but not connected to WhatsApp
        if status == "ready" and not phone:
            frappe.logger().info(
                f"OpenWA health check: session '{session_id}' on "
                f"'{account_name}' is ready but not linked to WhatsApp — restarting"
            )
            if _start_session(base_url, session_id, api_key):
                status = _poll_status(base_url, session_id, api_key, ("ready",), status)
            else:
                frappe.logger().warning(
                    f"OpenWA health check: failed to restart session "
                    f"'{session_id}' on '{account_name}'"
                )

        # 4. Disconnected with NO live engine — stopped, needs a start.
        #    A disconnected session that still has a live engine is in
        #    automatic reconnect backoff and must be left alone.
        elif status == "disconnected" and not engine_loaded:
            frappe.logger().info(
                f"OpenWA health check: session '{session_id}' on "
                f"'{account_name}' is disconnected (no live engine) — restarting"
            )
            if _start_session(base_url, session_id, api_key):
                status = _poll_status(base_url, session_id, api_key, ("ready",), status)
            else:
                frappe.logger().warning(
                    f"OpenWA health check: failed to restart session "
                    f"'{session_id}' on '{account_name}'"
                )

        # 5. Freshly created session — start it
        elif status == "created":
            frappe.logger().info(
                f"OpenWA health check: session '{session_id}' on "
                f"'{account_name}' is created — starting"
            )
            if _start_session(base_url, session_id, api_key):
                status = _poll_status(base_url, session_id, api_key, ("ready",), status)
            else:
                frappe.logger().warning(
                    f"OpenWA health check: failed to start session "
                    f"'{session_id}' on '{account_name}'"
                )

        # 6. action_required — the "What's new" onboarding modal needs a human.
        #    Auto stop→start once to re-drive the engine from stored
        #    credentials (no QR rescan). If it is still action_required, surface
        #    lastError so an operator can acknowledge the modal manually.
        elif status == "action_required":
            frappe.logger().info(
                f"OpenWA health check: session '{session_id}' on "
                f"'{account_name}' is action_required — attempting stop→start"
            )
            _stop_session(base_url, session_id, api_key)
            time.sleep(2)
            if _start_session(base_url, session_id, api_key):
                status = _poll_status(base_url, session_id, api_key, ("ready",), status)
            if status == "action_required":
                last_error = session.get("lastError") or ""
                frappe.logger().warning(
                    f"OpenWA health check: session '{session_id}' on "
                    f"'{account_name}' still action_required after stop→start — "
                    f"an operator must acknowledge the WhatsApp onboarding modal"
                )
                frappe.log_error(
                    title=f"OpenWA: Session action_required on {account_name}",
                    message=(
                        f"Session {session_id} needs an operator: "
                        f"{last_error or 'WhatsApp onboarding modal not dismissed'}. "
                        "Acknowledge the 'What's new' modal in a browser signed in "
                        "as that account, then the session will be restarted."
                    ),
                )

        # 7. Failed session — the engine is evicted by design in OpenWA 0.12.0+
        #    (force-kill answers 400 with no live engine), so delete and
        #    recreate directly.
        elif status == "failed":
            frappe.logger().info(
                f"OpenWA health check: session '{session_id}' on "
                f"'{account_name}' is failed — recreating session"
            )
            _delete_session(base_url, session_id, api_key)

            # Create new session with same name
            session_name = account_name.strip().lower().replace(" ", "-")
            import re as _re
            session_name = _re.sub(r"[^a-z0-9-]", "-", session_name)
            session_name = _re.sub(r"-+", "-", session_name).strip("-")
            if len(session_name) < 3:
                session_name = (session_name + "---")[:3]

            try:
                resp = _http_session.post(
                    f"{base_url.rstrip('/')}/api/sessions",
                    json={"name": session_name},
                    headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                    timeout=15,
                )
                if resp.status_code in (200, 201):
                    new_data = resp.json()
                    new_id = new_data.get("id")
                    if new_id:
                        frappe.db.set_value(
                            "WhatsApp Account", account_name,
                            "openwa_session_id", new_id,
                        )
                        session_id = new_id
                        _start_session(base_url, session_id, api_key)
                        status = _poll_status(base_url, session_id, api_key, ("ready",), status)
            except Exception as exc:
                frappe.logger().warning(
                    f"OpenWA health check: failed to recreate session "
                    f"for '{account_name}': {exc}"
                )

        _set_account_status(account_name, status)

    frappe.db.commit()


# ---------------------------------------------------------------------------
# Outbox processing
# ---------------------------------------------------------------------------


@frappe.whitelist()
def process_outbox_entry(outbox_name: str) -> None:  # noqa: C901
    """Process a single OpenWA Outbox entry.

    Called by ``frappe.enqueue`` from ``OverrideWhatsAppMessage.notify()`` and
    also by the scheduler safety-net ``process_pending_outbox``.
    """
    # Distributed lock — prevent duplicate processing when scheduler safety-net
    # and frappe.enqueue overlap on the same entry.
    # Use a longer TTL (5 minutes) to cover the full processing window including
    # session restart attempts. The lock is extended during processing.
    lock_key = f"openwa_outbox_lock::{outbox_name}"
    lock_ttl = 300  # 5 minutes
    existing = frappe.cache().get_value(lock_key)
    if existing is not None:
        return  # another worker is already processing this entry
    frappe.cache().set_value(lock_key, 1, expires_in_sec=lock_ttl)

    try:
        _process_outbox_entry_inner(outbox_name)
    finally:
        frappe.cache().delete_value(lock_key)


def _process_outbox_entry_inner(outbox_name: str) -> None:  # noqa: C901
    """Inner processing logic — called under the distributed lock."""
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

    # Load linked docs (account must be loaded first for settings checks)
    try:
        msg = frappe.get_doc("WhatsApp Message", outbox.whatsapp_message)
        account = get_cached_account(outbox.whatsapp_account)
    except Exception as exc:
        frappe.log_error(
            title=f"OpenWA Outbox: failed to load docs for {outbox_name}",
            message=str(exc),
        )
        frappe.db.set_value(
            "OpenWA Outbox",
            outbox_name,
            {"status": "Failed", "last_error": str(exc)[:65000]},
        )
        frappe.db.commit()
        return

    if not account.get("openwa_enabled"):
        frappe.db.set_value(
            "OpenWA Outbox",
            outbox_name,
            {"status": "Failed", "last_error": "WhatsApp Account no longer has OpenWA enabled"},
        )
        frappe.db.commit()
        return

    # Guard: exhausted retries
    max_attempts = get_account_setting(account, "openwa_max_outbox_attempts", 5)
    if outbox.attempts >= max_attempts:
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

    # Idempotency: if the WhatsApp Message already has a message_id, it was
    # sent successfully on a prior attempt (or by a webhook callback).
    # Mark the outbox as Sent and bail out — do NOT resend.
    if msg.message_id:
        frappe.db.set_value(
            "OpenWA Outbox",
            outbox_name,
            {"status": "Sent"},
        )
        frappe.db.commit()
        frappe.logger().info(
            f"OpenWA outbox {outbox_name}: skipping send — "
            f"message {msg.name} already has message_id '{msg.message_id}'"
        )
        return

    # Pre-send session check: verify the session is TRULY connected to
    # WhatsApp (status=ready AND phone set) BEFORE attempting to send.
    # Without this, _ensure_session_ready would restart a disconnected
    # session, the engine accepts the message (201+messageId), but it
    # can never be delivered because WhatsApp is not linked.
    base_url = account.get("openwa_base_url", "").strip("/")
    session_id = account.get("openwa_session_id")
    api_key = get_api_key(account)
    try:
        from openwa_bridge.utils import _http_session
        check_resp = _http_session.get(
            f"{base_url}/api/sessions/{session_id}",
            headers={"X-API-Key": api_key},
            timeout=10,
        )
        if check_resp.status_code == 200:
            sess = check_resp.json()
            if sess.get("status") != "ready" or not sess.get("phone"):
                _fail_outbox(
                    outbox_name,
                    f"Session not connected to WhatsApp "
                    f"(status={sess.get('status')}, phone={sess.get('phone')}). "
                    f"Will retry when session reconnects.",
                    account=account,
                )
                return
        elif check_resp.status_code == 404:
            _fail_outbox(
                outbox_name,
                "Session no longer exists on OpenWA. Re-setup required.",
                account=account,
            )
            return
        # On 429 or other errors, proceed — _ensure_session_ready will handle
    except Exception:
        # Can't reach OpenWA — _ensure_session_ready will handle the error
        pass

    # Check circuit breaker
    from openwa_bridge.utils import OpenWACircuitBreaker

    cb_threshold = get_account_setting(account, "openwa_cb_threshold", 5)
    cb_cooldown = get_account_setting(account, "openwa_cb_cooldown", 300)
    breaker = OpenWACircuitBreaker(account.name, threshold=cb_threshold, cooldown_seconds=cb_cooldown)
    if breaker.is_open():
        _fail_outbox(
            outbox_name,
            f"Circuit breaker open — {breaker.remaining_cooldown()}s cooldown remaining",
            account=account,
        )
        return

    # Build and send payload
    try:
        _send_outbox_message(msg, account, outbox)
    except Exception as exc:
        breaker.record_failure()
        _fail_outbox(outbox_name, str(exc), account=account)
        return

    # Message was sent successfully.  Mark outbox as Sent even if
    # circuit-breaker or DB commit fails — we must NOT set it back
    # to Pending which would cause a duplicate resend.
    try:
        breaker.record_success()
    except Exception:
        pass

    frappe.db.set_value(
        "OpenWA Outbox",
        outbox_name,
        {"status": "Sent"},
    )
    try:
        frappe.db.commit()
    except Exception:
        pass


def _send_dynamic_header_for_outbox(msg, account, caption=None) -> bool:
    """Send dynamic header image before the main message, if configured.

    Checks the linked WhatsApp Templates doc for ``openwa_dynamic_header``
    and ``openwa_print_format``.  Renders the reference doc as an image
    and sends it via OpenWA send-image endpoint with the text as caption.

    Returns True if image was sent successfully (or delivered despite a
    non-2xx response — verified via message_id), False otherwise.
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

    from openwa_bridge.utils import render_doc_as_image, get_api_key, _http_session
    from frappe_whatsapp.utils import format_number

    image_bytes = render_doc_as_image(ref_doctype, ref_name, print_format, letterhead=letterhead)
    if not image_bytes:
        return False

    import base64

    base_url = account.get("openwa_base_url").strip("/")
    session_id = account.get("openwa_session_id")
    api_key = get_api_key(account)

    raw_number = format_number(msg.to)
    chat_id = f"{raw_number}@c.us" if "@c.us" not in raw_number else raw_number

    url = f"{base_url}/api/sessions/{session_id}/messages/send-image"
    mimetype = "image/jpeg" if image_bytes[:3] == b'\xff\xd8\xff' else "image/png"
    payload = {
        "chatId": chat_id,
        "base64": base64.b64encode(image_bytes).decode("utf-8"),
        "mimetype": mimetype,
    }
    if caption:
        payload["caption"] = caption

    img_timeout = get_account_setting(account, "openwa_image_timeout", 90)
    try:
        img_resp = _http_session.post(
            url,
            json=payload,
            headers={"X-API-Key": api_key},
            timeout=img_timeout,
        )
    except Exception as e:
        err_str = str(e)
        is_timeout = "timed out" in err_str.lower() or "timeout" in err_str.lower()
        if is_timeout:
            # OpenWA engines deliver messages BEFORE the REST response is built.
            # A timeout means OpenWA likely received the request and may have
            # already queued/sent the image.  Falling back to text would cause
            # a duplicate.  Raise so the outbox retry handles it — the
            # idempotency check (message_id) will detect if it was sent.
            frappe.log_error(
                title="OpenWA: Dynamic header image timed out (will retry)",
                message=(
                    f"Template {tmpl.name}, Doc {ref_doctype} {ref_name}: {e}\n"
                    "Not falling back to text — message may be queued in OpenWA. "
                    "Outbox will retry with backoff; idempotency check will reconcile."
                ),
            )
            raise
        frappe.log_error(
            title="OpenWA: Dynamic header image failed",
            message=f"Template {tmpl.name}, Doc {ref_doctype} {ref_name}: {e}",
        )
        return False

    # --- Extract and store message_id from response ---
    resp_data = {}
    try:
        resp_data = img_resp.json()
    except Exception:
        pass

    msg_id = resp_data.get("messageId") or (
        resp_data.get("key", {}).get("id") if isinstance(resp_data.get("key"), dict) else None
    )

    if msg_id:
        frappe.db.set_value("WhatsApp Message", msg.name, "message_id", msg_id)

    # --- 2xx = success ---
    if img_resp.status_code < 400:
        if msg_id:
            frappe.db.set_value("WhatsApp Message", msg.name, "status", "Sent")
        return True

    # --- Non-2xx but message may have been delivered anyway ---
    # OpenWA engines (whatsapp-web.js/Baileys) deliver the message
    # BEFORE the REST response is built.  A 500 after delivery is
    # extremely common when the engine succeeds but post-send
    # persistence (saveOutgoingMessage / persistSentState) fails
    # on the OpenWA server side.
    #
    # We do NOT wait for the ack webhook here — it can take 10+
    # seconds to arrive, which would cause a text fallback and
    # duplicate delivery.  Instead, assume the image was delivered
    # on any 500 and return True to prevent the text fallback.
    # The ack webhook handler (_handle_status_update / _handle_message_sent)
    # will reconcile the message_id when it arrives.
    frappe.log_error(
        title="OpenWA: Dynamic header image returned error (assuming delivered)",
        message=(
            f"Template {tmpl.name}, Doc {ref_doctype} {ref_name}\n"
            f"POST {url}\n"
            f"Status: {img_resp.status_code}\n"
            f"Image size: {len(image_bytes)} bytes ({mimetype})\n"
            f"Response: {img_resp.text[:2000]}\n"
            f"Returning True to prevent text fallback duplicate."
        ),
    )

    if msg_id:
        frappe.db.set_value("WhatsApp Message", msg.name, "status", "Sent")

    return True


def _send_outbox_message(msg, account, outbox) -> None:  # noqa: C901
    """Actually send the message via OpenWA. Reuses the dispatcher from whatsapp_message."""

    has_dynamic_header = False
    if msg.template:
        try:
            tmpl = frappe.get_doc("WhatsApp Templates", msg.template)
            has_dynamic_header = bool(
                getattr(tmpl, "openwa_dynamic_header", False)
                and getattr(tmpl, "openwa_print_format", None)
            )
        except Exception:
            pass

    if has_dynamic_header:
        # Try sending image+caption.  If the image fails (e.g. OpenWA 500),
        # check whether the ack webhook already confirmed delivery before
        # falling back to text — otherwise we'd send a duplicate.
        image_sent = _send_dynamic_header_for_outbox(msg, account, caption=msg.message)
        if image_sent:
            return

        # The image returned an error, but OpenWA engines often deliver
        # the message before the REST response is built.  The ack webhook
        # may have already stored the message_id.  Reload and check.
        frappe.db.commit()
        msg.reload()
        if msg.message_id:
            frappe.logger().info(
                f"OpenWA: Dynamic header image returned error but message "
                f"was delivered (message_id={msg.message_id}). Skipping text fallback."
            )
            return

        frappe.log_error(
            title="OpenWA: Dynamic header failed, falling back to text",
            message=(
                f"Msg {msg.name}, Template {msg.template}: "
                "image send failed, falling back to text/template delivery."
            ),
        )
        # Fall through to text/template send below

    # No dynamic header — send text/template message directly
    from frappe_whatsapp.utils import format_number

    # msg is already an OverrideWhatsAppMessage instance via override_doctype_class.
    # Build meta payload for media types
    meta_payload: dict = {}
    if msg.content_type in ("image", "video", "audio", "document") and msg.attach:
        link = msg.attach if msg.attach.startswith("http") else frappe.utils.get_url() + "/" + msg.attach
        meta_payload = {msg.content_type: {"link": link, "caption": msg.message}}
    elif msg.content_type == "reaction":
        meta_payload = {"reaction": {"message_id": msg.reply_to_message_id, "emoji": msg.message}}

    msg._ensure_session_ready(account)
    msg._send_via_openwa(account, meta_payload)


def _fail_outbox(outbox_name: str, error: str, account=None) -> None:
    """Mark an outbox entry as Failed or schedule a retry."""
    try:
        outbox = frappe.get_doc("OpenWA Outbox", outbox_name)
    except Exception:
        return

    # attempts was already incremented by process_outbox_entry() before calling us
    attempts = outbox.attempts or 0
    if account:
        max_attempts = get_account_setting(account, "openwa_max_outbox_attempts", 5)
    else:
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
    """Scheduler safety-net: re-enqueue orphaned Pending entries and recover
    stuck Sending entries.

    Runs every ~4 minutes via the ``all`` scheduler event.  Picks up entries
    that were never processed (e.g. worker crash, Redis restart) and
    re-enqueues them into the ``long`` queue.

    Also recovers entries stuck in ``Sending`` for more than 5 minutes —
    these are typically caused by worker crashes or Redis restarts between
    the status update and the final Sent/Failed mark.
    """
    now = datetime.now()
    # Use a default max attempts filter. The real guard is per-entry in
    # process_outbox_entry(), so this is just a query optimization.
    max_attempts = 5
    batch_size = frappe.db.get_single_value("OpenWA Bridge Settings", "outbox_batch_size") or 25

    entries = frappe.get_all(
        "OpenWA Outbox",
        filters=[
            ["status", "=", "Pending"],
            ["attempts", "<", max_attempts],
            ["next_retry_at", "is", "not set"],
        ],
        fields=["name"],
        order_by="priority DESC, creation ASC",
        limit=batch_size,
    )

    # Also pick up entries where next_retry_at <= now
    retry_entries = frappe.get_all(
        "OpenWA Outbox",
        filters=[
            ["status", "=", "Pending"],
            ["attempts", "<", max_attempts],
            ["next_retry_at", "<=", now],
        ],
        fields=["name"],
        order_by="priority DESC, next_retry_at ASC",
        limit=batch_size,
    )

    # Recover orphaned Sending entries — worker crashed before marking Sent/Failed.
    # Give a 5-minute grace period to avoid resetting entries still being processed.
    sending_cutoff = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    stuck_sending = frappe.get_all(
        "OpenWA Outbox",
        filters=[
            ["status", "=", "Sending"],
            ["modified", "<", sending_cutoff],
            ["attempts", "<", max_attempts],
        ],
        fields=["name"],
        order_by="creation ASC",
        limit=batch_size,
    )
    for entry in stuck_sending:
        frappe.db.set_value(
            "OpenWA Outbox",
            entry.name,
            {"status": "Pending"},
        )
        frappe.logger().warning(
            f"OpenWA outbox recovery: reset stuck Sending entry {entry.name} to Pending"
        )
    if stuck_sending:
        frappe.db.commit()

    candidates = list({e.name for e in entries} | {e.name for e in retry_entries})

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


# ---------------------------------------------------------------------------
# Proactive outbox reconciliation
# ---------------------------------------------------------------------------


def reconcile_stale_outbox() -> None:
    """Proactive reconciliation: fix outbox entries that should be Sent but aren't.

    This handles the case where:
    1. The message WAS delivered (webhook confirmed via WhatsApp Message status)
       but the outbox entry was never updated.
    2. The outbox entry is stuck in Sending/Pending but the WhatsApp Message
       already has a message_id (meaning it was accepted by OpenWA).

    Runs every ~4 minutes via the ``all`` scheduler event and also from the
    hourly health check.
    """
    now = datetime.now()
    stale_cutoff = (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

    # Find outbox entries stuck in Pending or Sending where the linked
    # WhatsApp Message already has a message_id or status=Sent
    stale_entries = frappe.get_all(
        "OpenWA Outbox",
        filters=[
            ["status", "in", ["Pending", "Sending"]],
            ["modified", "<", stale_cutoff],
        ],
        fields=["name", "whatsapp_message", "whatsapp_account"],
        limit_page_length=50,
    )

    reconciled = 0
    for entry in stale_entries:
        if not entry.whatsapp_message:
            continue
        try:
            msg_status = frappe.db.get_value(
                "WhatsApp Message", entry.whatsapp_message, "status"
            )
            msg_id = frappe.db.get_value(
                "WhatsApp Message", entry.whatsapp_message, "message_id"
            )
        except Exception:
            continue

        # Case 1: WhatsApp Message already confirmed sent — mark outbox Sent
        if msg_status and msg_status.lower() in ("sent", "delivered", "read"):
            frappe.db.set_value("OpenWA Outbox", entry.name, {"status": "Sent"})
            reconciled += 1
            continue

        # Case 2: WhatsApp Message has a message_id but status not yet updated
        if msg_id:
            frappe.db.set_value(
                "WhatsApp Message",
                entry.whatsapp_message,
                {"status": "Sent"},
            )
            frappe.db.set_value("OpenWA Outbox", entry.name, {"status": "Sent"})
            reconciled += 1
            continue

        # Case 3: Outbox attempted >= max but still Pending/Sending
        attempts = frappe.db.get_value("OpenWA Outbox", entry.name, "attempts") or 0
        max_attempts = frappe.db.get_value("OpenWA Outbox", entry.name, "max_attempts") or 100
        if attempts >= max_attempts:
            frappe.db.set_value(
                "OpenWA Outbox",
                entry.name,
                {"status": "Failed", "last_error": "Max attempts exhausted (reconciliation)"},
            )
            reconciled += 1

    if reconciled:
        frappe.db.commit()
        frappe.logger().info(
            f"OpenWA outbox reconciliation: fixed {reconciled} stale entries"
        )


def cleanup_old_outbox() -> None:
    """Scheduler task: delete old outbox entries to prevent table bloat.

    - Deletes Sent entries older than 7 days
    - Deletes Failed entries older than 30 days
    Runs weekly via the ``daily`` scheduler event.
    """
    now = datetime.now()
    sent_cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    failed_cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

    deleted_sent = frappe.db.delete(
        "OpenWA Outbox",
        filters=[
            ["status", "=", "Sent"],
            ["modified", "<", sent_cutoff],
        ],
        run_commit=False,
    )

    deleted_failed = frappe.db.delete(
        "OpenWA Outbox",
        filters=[
            ["status", "=", "Failed"],
            ["modified", "<", failed_cutoff],
        ],
        run_commit=False,
    )

    if deleted_sent or deleted_failed:
        frappe.db.commit()
        frappe.logger().info(
            f"OpenWA outbox cleanup: deleted {deleted_sent} Sent, "
            f"{deleted_failed} Failed entries"
        )


# ---------------------------------------------------------------------------
# Outbox Dashboard API
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_outbox_dashboard(account_name: str | None = None) -> dict:
    """Return outbox statistics for the dashboard.

    If *account_name* is provided, stats are scoped to that account.
    Otherwise, returns aggregate stats across all OpenWA-enabled accounts.

    Returns::

        {
          "queue_depth": {"Pending": 3, "Sending": 1, "Sent": 150, "Failed": 2},
          "circuit_breakers": {"manaa2": {"open": false, "failures": 0}},
          "success_rate_24h": 98.5,
          "success_rate_7d": 97.2,
          "avg_send_time_ms": 1200,
          "recent_failures": [{"name": "...", "error": "...", "modified": "..."}]
        }
    """
    from datetime import datetime, timedelta

    filters: list = []
    if account_name:
        filters.append(["whatsapp_account", "=", account_name])

    # Queue depth
    status_counts = frappe.get_all(
        "OpenWA Outbox",
        filters=filters + [["status", "in", ["Pending", "Sending", "Sent", "Failed"]]],
        fields=["status"],
        limit_page_length=0,
    )
    queue_depth: dict[str, int] = {"Pending": 0, "Sending": 0, "Sent": 0, "Failed": 0}
    for row in status_counts:
        queue_depth[row.status] = queue_depth.get(row.status, 0) + 1

    # Circuit breaker status per account
    from openwa_bridge.utils import OpenWACircuitBreaker

    accounts_to_check = (
        [account_name]
        if account_name
        else [
            a.name
            for a in frappe.get_all(
                "WhatsApp Account",
                filters={"openwa_enabled": 1},
                fields=["name"],
            )
        ]
    )
    circuit_breakers: dict[str, dict] = {}
    for acct in accounts_to_check:
        cb = OpenWACircuitBreaker(acct)
        failures_key = f"openwa_cb::{acct}::failures"
        failures = frappe.cache().get_value(failures_key) or 0
        circuit_breakers[acct] = {
            "open": cb.is_open(),
            "failures": failures,
            "remaining_cooldown": cb.remaining_cooldown() if cb.is_open() else 0,
        }

    # Success rates (last 24h and 7d)
    now = datetime.now()
    success_rate_24h = _calc_success_rate(filters, now - timedelta(hours=24), now)
    success_rate_7d = _calc_success_rate(filters, now - timedelta(days=7), now)

    # Average send time (last 24h) — from WhatsApp Message docs
    msg_filters: list = [["status", "in", ["sent", "delivered", "read"]]]
    if account_name:
        msg_filters.append(["whatsapp_account", "=", account_name])
    recent_msgs = frappe.get_all(
        "WhatsApp Message",
        filters=msg_filters + [
            ["modified", ">", (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")],
        ],
        fields=["creation", "modified"],
        limit_page_length=0,
    )
    avg_send_time_ms = 0
    if recent_msgs:
        deltas = [(m.modified - m.creation).total_seconds() * 1000 for m in recent_msgs]
        avg_send_time_ms = int(sum(deltas) / len(deltas)) if deltas else 0

    # Recent failures (last 10)
    fail_filters = [["status", "=", "Failed"]]
    if account_name:
        fail_filters.append(["whatsapp_account", "=", account_name])
    recent_failures = frappe.get_all(
        "OpenWA Outbox",
        filters=fail_filters,
        fields=["name", "last_error", "modified"],
        order_by="modified desc",
        limit_page_length=10,
    )

    return {
        "queue_depth": queue_depth,
        "circuit_breakers": circuit_breakers,
        "success_rate_24h": success_rate_24h,
        "success_rate_7d": success_rate_7d,
        "avg_send_time_ms": avg_send_time_ms,
        "recent_failures": recent_failures,
    }


def _calc_success_rate(base_filters: list, since: datetime, until: datetime) -> float:
    """Calculate success rate (Sent / (Sent + Failed)) for a time range."""
    filters = base_filters + [
        ["status", "in", ["Sent", "Failed"]],
        ["modified", ">", since.strftime("%Y-%m-%d %H:%M:%S")],
        ["modified", "<=", until.strftime("%Y-%m-%d %H:%M:%S")],
    ]
    rows = frappe.get_all("OpenWA Outbox", filters=filters, fields=["status"], limit_page_length=0)
    sent = sum(1 for r in rows if r.status == "Sent")
    failed = sum(1 for r in rows if r.status == "Failed")
    total = sent + failed
    if total == 0:
        return 100.0
    return round((sent / total) * 100, 1)


# ---------------------------------------------------------------------------
# Webhook Replay / Backfill
# ---------------------------------------------------------------------------


@frappe.whitelist()
def replay_webhooks(account_name: str, since: str | None = None, chat_id: str | None = None) -> dict:
    """Fetch messages from OpenWA and create WhatsApp Message docs for any
    that are missing from Frappe.

    Useful for backfilling messages that arrived while the webhook was down.

    Args:
        account_name: WhatsApp Account name.
        since: ISO datetime string — only fetch messages after this time.
                Defaults to 24 hours ago.
        chat_id: Optional chat ID filter (e.g. ``12345@c.us``).

    Returns::

        {
          "fetched": 42,
          "created": 5,
          "skipped": 37,
          "errors": ["..."]
        }
    """
    from datetime import datetime, timedelta

    if not frappe.has_permission("WhatsApp Account", "read", account_name):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)

    doc = frappe.get_doc("WhatsApp Account", account_name)
    if not doc.get("openwa_enabled"):
        frappe.throw("OpenWA is not enabled on this account.")

    base_url = (doc.openwa_base_url or "").strip("/")
    session_id = doc.openwa_session_id
    if not base_url or not session_id:
        frappe.throw("OpenWA Base URL or Session ID is not set.")

    api_key = get_account_password(doc)

    # Default: last 24 hours
    if since:
        since_dt = datetime.fromisoformat(since)
    else:
        since_dt = datetime.now() - timedelta(hours=24)

    # Fetch messages from OpenWA
    params: dict = {"limit": 200}
    if chat_id:
        params["chatId"] = chat_id

    try:
        resp = _http_session.get(
            f"{base_url}/api/sessions/{session_id}/messages",
            params=params,
            headers={"X-API-Key": api_key},
            timeout=30,
        )
        resp.raise_for_status()
        messages = resp.json()
    except Exception as exc:
        frappe.throw(f"Failed to fetch messages from OpenWA: {exc}")

    if isinstance(messages, dict):
        messages = messages.get("messages", [])

    fetched = len(messages)
    created = 0
    skipped = 0
    errors: list[str] = []

    for msg_data in messages:
        try:
            # Filter by time
            msg_time = msg_data.get("timestamp") or msg_data.get("created_at")
            if msg_time:
                if isinstance(msg_time, str):
                    msg_dt = datetime.fromisoformat(msg_time.replace("Z", "+00:00"))
                else:
                    msg_dt = datetime.fromtimestamp(msg_time / 1000)
                if msg_dt.replace(tzinfo=None) < since_dt:
                    skipped += 1
                    continue

            # Only process incoming messages (we don't replay our own sends)
            direction = msg_data.get("direction") or msg_data.get("from")
            if not direction:
                skipped += 1
                continue

            # Determine phone number
            phone = msg_data.get("from") or msg_data.get("to") or ""
            phone = phone.replace("@c.us", "").replace("@g.us", "").replace("@lid", "")

            # Determine message content
            text = msg_data.get("body") or msg_data.get("text") or ""
            msg_type = msg_data.get("type") or "text"
            msg_id = msg_data.get("id") or msg_data.get("key") or ""

            # Check for duplicates by message_id or body hash
            if msg_id:
                exists = frappe.db.exists(
                    "WhatsApp Message",
                    {"message_id": msg_id},
                )
                if exists:
                    skipped += 1
                    continue

            # Create the WhatsApp Message doc
            new_msg = frappe.get_doc({
                "doctype": "WhatsApp Message",
                "type": "Incoming",
                "from": phone,
                "message": text,
                "message_type": msg_type.capitalize() if msg_type else "Text",
                "content_type": "text",
                "message_id": msg_id,
                "status": "Received",
                "whatsapp_account": account_name,
            })
            new_msg.insert(ignore_permissions=True)
            created += 1

        except Exception as exc:
            errors.append(str(exc)[:200])

    frappe.db.commit()

    return {
        "fetched": fetched,
        "created": created,
        "skipped": skipped,
        "errors": errors[:10],
    }


def get_account_password(doc: "WhatsAppAccount") -> str:  # noqa: F821
    """Get the API key password from the account doc."""
    return doc.get_password("openwa_api_key")
