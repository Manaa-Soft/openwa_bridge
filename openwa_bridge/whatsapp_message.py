"""Outbound message router — overrides WhatsAppMessage.notify() to route via OpenWA."""
from __future__ import annotations

import frappe
import json
import time

from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import (
    WhatsAppMessage,
)
from frappe_whatsapp.utils import format_number
from openwa_bridge.utils import openwa_api, frappe_to_openwa_vars, get_api_key, _http_session


def _send_typing_indicator(base_url: str, session_id: str, api_key: str, chat_id: str, state: str = "typing") -> None:
    """Send a typing/recording/paused indicator to a chat. Non-blocking — failures are logged and swallowed."""
    try:
        headers = {"Content-Type": "application/json", "X-API-Key": api_key}
        _http_session.post(
            f"{base_url}/api/sessions/{session_id}/chats/typing",
            json={"chatId": chat_id, "state": state},
            headers=headers,
            timeout=5,
        )
    except Exception:
        pass  # Typing indicators are cosmetic — never block the send flow


class OverrideWhatsAppMessage(WhatsAppMessage):
    """Intercepts all outbound messages and routes OpenWA-enabled accounts through the gateway."""

    def notify(self, data: dict) -> None:
        """Intercept before Meta API call. ``data`` is the fully-built Meta payload."""
        account = frappe.get_doc("WhatsApp Account", self.whatsapp_account)

        if account and account.get("openwa_enabled"):
            # Interactive / flow have no OpenWA equivalent — fall back to Meta
            if self.content_type in ("interactive", "flow"):
                frappe.log_error(
                    title="OpenWA Bridge: Fallback to Meta",
                    message=(
                        f"Msg {self.name}: '{self.content_type}' unsupported by OpenWA. "
                        "Routing via Meta API."
                    ),
                )
                return super().notify(data)

            # Do NOT create the outbox entry here — we are inside before_insert()
            # where self.name is still None (db_insert hasn't run yet).  Instead,
            # set a flag so after_insert() can create the entry with the real name.
            self._openwa_outbox_needed = True
            return

        return super().notify(data)

    def after_insert(self):
        """Create the outbox entry now that self.name is assigned.

        ``notify()`` sets ``_openwa_outbox_needed`` during ``before_insert()``
        when ``self.name`` is still ``None``.  We create the outbox entry here
        so the background worker can find the linked WhatsApp Message.
        """
        if not getattr(self, "_openwa_outbox_needed", False):
            return

        outbox = frappe.get_doc({
            "doctype": "OpenWA Outbox",
            "whatsapp_message": self.name,
            "whatsapp_account": self.whatsapp_account,
            "content_type": self.content_type,
            "status": "Pending",
            "max_attempts": 5,
        })
        outbox.insert(ignore_permissions=True)

        try:
            frappe.enqueue(
                "openwa_bridge.tasks.process_outbox_entry",
                queue="long",
                timeout=300,
                job_id=f"openwa_outbox::{outbox.name}",
                deduplicate=True,
                outbox_name=outbox.name,
            )
        except Exception:
            frappe.log_error(
                title="OpenWA: Failed to enqueue outbox entry",
                message=f"Outbox {outbox.name} will be picked up by scheduler safety-net.",
            )

    # ------------------------------------------------------------------
    # OpenWA dispatchers
    # ------------------------------------------------------------------

    def _ensure_session_ready(self, account: "WhatsAppAccount") -> None:  # noqa: F821
        """Verify the OpenWA session is truly connected to WhatsApp.

        A session can be ``ready`` (engine alive) without being connected
        to WhatsApp (no phone number).  This method verifies the phone
        field is set — only then is the session actually usable.

        Raises ``frappe.ValidationError`` if the session cannot be recovered.
        """
        base_url = account.get("openwa_base_url").strip("/")
        session_id = account.get("openwa_session_id")
        api_key = get_api_key(account)

        headers = {"Content-Type": "application/json", "X-API-Key": api_key}

        # Check current status
        session_data = None
        try:
            resp = _http_session.get(
                f"{base_url}/api/sessions/{session_id}",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                session_data = resp.json()
                status = session_data.get("status", "unknown")
                phone = session_data.get("phone")
                if status == "ready" and phone:
                    return  # truly connected — engine alive AND WhatsApp linked
                # Engine alive but not connected to WhatsApp — need restart
            elif resp.status_code == 404:
                frappe.throw(
                    f"OpenWA session '{session_id}' no longer exists on the server. "
                    "Please re-setup the WhatsApp Account from the form.",
                    title="Session Deleted",
                )
            elif resp.status_code == 429:
                # Rate-limited — assume session is fine, don't restart.
                frappe.logger().info(
                    f"OpenWA pre-send: session '{session_id}' rate-limited "
                    f"— assuming connected, proceeding with send"
                )
                return
            else:
                status = "disconnected"
        except Exception:
            status = "disconnected"

        # Not ready — attempt restart
        frappe.logger().info(
            f"OpenWA pre-send: session '{session_id}' status is "
            f"'{getattr(session_data, 'get', lambda k, d=None: d)('status', 'unknown')}' "
            f"(phone={getattr(session_data, 'get', lambda k, d=None: d)('phone', None)}) "
            f"— attempting restart before sending message {self.name}"
        )
        try:
            start_resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/start",
                headers=headers,
                timeout=60,
            )
            if start_resp.status_code not in (200, 201, 400):
                frappe.throw(
                    f"OpenWA session restart failed: HTTP {start_resp.status_code}"
                )
        except Exception as exc:
            frappe.throw(
                f"OpenWA session is {status} and restart failed: {exc}"
            )

        # Poll for readiness (up to 15s, 1s intervals) — wait longer
        # because the engine needs time to reconnect to WhatsApp after
        # restart.  We require BOTH status=ready AND phone set.
        for i in range(15):
            time.sleep(1)
            try:
                verify = _http_session.get(
                    f"{base_url}/api/sessions/{session_id}",
                    headers=headers,
                    timeout=5,
                )
                if verify.status_code == 200:
                    vdata = verify.json()
                    new_status = vdata.get("status", "unknown")
                    phone = vdata.get("phone")
                    if new_status == "ready" and phone:
                        frappe.logger().info(
                            f"OpenWA pre-send: session '{session_id}' restarted "
                            f"— connected as {phone}"
                        )
                        return
                    if new_status == "qr_ready":
                        frappe.throw(
                            "OpenWA session restarted but requires QR re-scan. "
                            "Please open the WhatsApp Account form and scan the QR code."
                        )
            except Exception:
                pass

        # Not ready after wait — let outbox retry handle it
        frappe.throw(
            f"OpenWA session is {status} and could not be recovered automatically. "
            "The message will be retried. You can also click 'Reconnect' on the "
            "WhatsApp Account form."
        )

    def _send_via_openwa(self, account: "WhatsAppAccount", meta_payload: dict) -> None:  # noqa: F821
        """Translate and dispatch the payload to the OpenWA Gateway REST API."""
        # Idempotency guard: if the message already has a message_id, it was
        # already sent (possibly by a previous attempt or webhook reconciliation).
        # Do NOT send again — just return.
        if self.message_id:
            frappe.logger().info(
                f"OpenWA: skipping send for {self.name} — "
                f"message_id '{self.message_id}' already set"
            )
            return

        # Ensure session is ready before attempting to send
        self._ensure_session_ready(account)

        base_url = account.get("openwa_base_url").strip("/")
        session_id = account.get("openwa_session_id")
        api_key = get_api_key(account)

        raw_number = format_number(self.to)
        chat_id = f"{raw_number}@c.us" if "@c.us" not in raw_number else raw_number

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        }

        # Send typing indicator before non-bulk, non-template, non-reaction sends
        if self.content_type in ("text", "image", "video", "audio", "document", "location", "contact", "sticker"):
            _send_typing_indicator(base_url, session_id, api_key, chat_id, "typing")

        # --- template via OpenWA send-template endpoint ---
        if self.use_template and self.template:
            openwa_tid = frappe.db.get_value("WhatsApp Templates", self.template, "openwa_template_id")
            if openwa_tid:
                params: dict[str, str] = {}
                if self.body_param:
                    try:
                        bp = json.loads(self.body_param)
                        if isinstance(bp, dict):
                            params = {f"param{k}": v for k, v in bp.items()}
                        elif isinstance(bp, list):
                            params = {f"param{i + 1}": v for i, v in enumerate(bp)}
                    except (json.JSONDecodeError, TypeError):
                        pass
                elif self.template_parameters:
                    try:
                        raw = json.loads(self.template_parameters)
                        params = {f"param{i + 1}": v for i, v in enumerate(raw)}
                    except (json.JSONDecodeError, TypeError):
                        pass

                if not params:
                    # Check if the template body actually has placeholders
                    tmpl_body = frappe.db.get_value("WhatsApp Templates", self.template, "template") or ""
                    has_placeholders = bool(re.search(r"\{\{.*?\}\}", tmpl_body))
                    if has_placeholders:
                        frappe.throw(
                            f"Template '{self.template}' requires variables but none were provided. "
                            "Fill in 'Template Variables' on the Bulk WhatsApp Message, "
                            "or configure the notification's 'Fields' child table."
                        )

                send_payload = {"chatId": chat_id, "templateId": openwa_tid, "vars": params}
                frappe.logger().info(f"OpenWA send-template payload: {json.dumps(send_payload, default=str)}")
                resp = _http_session.post(
                    f"{base_url}/api/sessions/{session_id}/messages/send-template",
                    json=send_payload,
                    headers=headers,
                    timeout=30,
                )
                # Stale template ID recovery: if 404, look up by name and retry
                if resp.status_code == 404:
                    new_tid = self._recover_template_id(account, base_url, session_id, api_key, headers)
                    if new_tid and new_tid != openwa_tid:
                        send_payload["templateId"] = new_tid
                        resp = _http_session.post(
                            f"{base_url}/api/sessions/{session_id}/messages/send-template",
                            json=send_payload,
                            headers=headers,
                            timeout=30,
                        )
            else:
                message_body = self._translate_template_payload()
                resp = _http_session.post(
                    f"{base_url}/api/sessions/{session_id}/messages/send-text",
                    json={"chatId": chat_id, "text": message_body},
                    headers=headers,
                    timeout=30,
                )

        elif self.is_reply and self.reply_to_message_id:
            if self.content_type != "text":
                frappe.throw(
                    "OpenWA bridge does not support media replies. "
                    "Send the media and reply separately, or use a Meta account."
                )
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/reply",
                json={
                    "chatId": chat_id,
                    "quotedMessageId": self.reply_to_message_id,
                    "text": self.message,
                },
                headers=headers,
                timeout=30,
            )

        elif self.content_type == "text":
            text_payload: dict = {"chatId": chat_id, "text": self.message}
            # Support @mentions — extract JIDs from message body (e.g. @12345@c.us)
            import re as _re
            mention_matches = _re.findall(r"(\d{5,15})@c\.us", self.message)
            if mention_matches:
                text_payload["mentions"] = [f"{m}@c.us" for m in mention_matches]
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/send-text",
                json=text_payload,
                headers=headers,
                timeout=30,
            )

        elif self.content_type in ("image", "video", "audio", "document"):
            link = meta_payload.get(self.content_type, {}).get("link", "")
            payload: dict = {"chatId": chat_id, "url": link}
            if self.content_type != "audio":
                payload["caption"] = self.message
            endpoint = f"send-{self.content_type}"
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/{endpoint}",
                json=payload,
                headers=headers,
                timeout=60,
            )

        elif self.content_type == "reaction":
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/react",
                json={
                    "chatId": chat_id,
                    "messageId": meta_payload.get("reaction", {}).get("message_id"),
                    "emoji": self.message,
                },
                headers=headers,
                timeout=30,
            )

        elif self.content_type == "location":
            location_data = json.loads(self.message) if self.message else {}
            lat = location_data.get("latitude")
            lng = location_data.get("longitude")
            if lat is None or lng is None:
                frappe.throw(
                    "Location messages require JSON in the message field: "
                    '{"latitude": -6.2088, "longitude": 106.8456, "description": "...", "address": "..."}'
                )
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/send-location",
                json={
                    "chatId": chat_id,
                    "latitude": float(lat),
                    "longitude": float(lng),
                    "description": location_data.get("description", ""),
                    "address": location_data.get("address", ""),
                },
                headers=headers,
                timeout=30,
            )

        elif self.content_type == "contact":
            contact_data = json.loads(self.message) if self.message else {}
            contact_name = contact_data.get("contact_name", "")
            contact_number = contact_data.get("contact_number", "")
            if not contact_name or not contact_number:
                frappe.throw(
                    "Contact messages require JSON in the message field: "
                    '{"contact_name": "John Doe", "contact_number": "+1234567890"}'
                )
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/send-contact",
                json={
                    "chatId": chat_id,
                    "contactName": contact_name,
                    "contactNumber": contact_number,
                },
                headers=headers,
                timeout=30,
            )

        elif self.content_type == "sticker":
            sticker_data = json.loads(self.message) if self.message else {}
            sticker_url = sticker_data.get("url", "")
            sticker_b64 = sticker_data.get("base64", "")
            if not sticker_url and not sticker_b64:
                frappe.throw(
                    "Sticker messages require JSON in the message field: "
                    '{"url": "https://example.com/sticker.webp"} or '
                    '{"base64": "<base64-encoded data>"}'
                )
            sticker_payload: dict = {"chatId": chat_id}
            if sticker_url:
                sticker_payload["url"] = sticker_url
            if sticker_b64:
                sticker_payload["base64"] = sticker_b64
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/send-sticker",
                json=sticker_payload,
                headers=headers,
                timeout=60,
            )

        elif self.content_type == "order":
            poll_data = json.loads(self.message) if self.message else {}
            poll_name = poll_data.get("name", "")
            poll_options = poll_data.get("options", [])
            if not poll_name or len(poll_options) < 2:
                frappe.throw(
                    "Poll messages require JSON in the message field: "
                    '{"name": "Question?", "options": ["Option 1", "Option 2"], "allowMultipleAnswers": false}'
                )
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/send-poll",
                json={
                    "chatId": chat_id,
                    "name": poll_name,
                    "options": poll_options,
                    "allowMultipleAnswers": poll_data.get("allowMultipleAnswers", False),
                },
                headers=headers,
                timeout=30,
            )

        elif self.content_type == "edit":
            edit_data = json.loads(self.message) if self.message else {}
            chat_id_to_edit = edit_data.get("chat_id", chat_id)
            message_id_to_edit = edit_data.get("message_id", "")
            new_body = edit_data.get("body", self.message)
            if not message_id_to_edit:
                frappe.throw(
                    "Edit messages require JSON in the message field: "
                    '{"chat_id": "12345@c.us", "message_id": "ABC123", "body": "new text"}'
                )
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/edit",
                json={
                    "chatId": chat_id_to_edit,
                    "messageId": message_id_to_edit,
                    "body": new_body,
                },
                headers=headers,
                timeout=30,
            )

        else:
            frappe.throw(
                f"Content type '{self.content_type}' cannot be routed via OpenWA."
            )

        if resp.status_code >= 400:
            try:
                err_body = resp.text
            except Exception:
                err_body = "(no body)"

            # OpenWA guarantee: persistSentState always returns 201+messageId
            # on success (swallows DB errors). failSend throws → 500 with NO
            # messageId. So ANY 4xx/5xx means the message was NOT delivered.
            # Log for diagnostics and let the caller (_fail_outbox via outbox
            # processor) handle retry with backoff.
            frappe.log_error(
                title=f"OpenWA API Error ({resp.status_code})",
                message=(
                    f"POST {resp.url} returned {resp.status_code}\n"
                    f"Request body: {json.dumps(resp.request.body.decode() if resp.request.body else '', default=str)}\n"
                    f"Response: {err_body}"
                ),
            )
            resp.raise_for_status()

        res_data = resp.json()

        # Extract message ID — support both top-level messageId and nested key.id
        msg_id = res_data.get("messageId") or (
            res_data.get("key", {}).get("id") if isinstance(res_data.get("key"), dict) else None
        )

        if msg_id:
            frappe.db.set_value(
                "WhatsApp Message",
                self.name,
                {"message_id": msg_id, "status": "Sent"},
            )
        else:
            # Message was accepted by OpenWA (HTTP 200+) but no ID returned.
            # Mark as Sent anyway to prevent infinite retries — the ack
            # webhook will set the real message_id when it arrives.
            frappe.db.set_value(
                "WhatsApp Message",
                self.name,
                {"status": "Sent"},
            )
            frappe.log_error(
                title="OpenWA: No messageId in response",
                message=(
                    f"Msg {self.name}: POST succeeded (HTTP {resp.status_code}) "
                    f"but response had no messageId.\n"
                    f"Response: {json.dumps(res_data, default=str)[:2000]}"
                ),
            )

    # ------------------------------------------------------------------
    # Template ID recovery (stale ID after session recreate)
    # ------------------------------------------------------------------

    def _recover_template_id(self, account, base_url, session_id, api_key, headers):
        """If template is missing on OpenWA, create or re-sync it, then return the ID."""
        try:
            frappe_name = frappe.db.get_value("WhatsApp Templates", self.template, "actual_name")
            if not frappe_name:
                frappe_name = self.template.lower().replace(" ", "_")

            # Try to find by name on OpenWA
            try:
                frappe_body = frappe.db.get_value("WhatsApp Templates", self.template, "template") or ""
                converted_body = frappe_to_openwa_vars(frappe_body)

                templates = openwa_api(account, "GET", "/templates")
                for t in templates:
                    if t.get("name") == frappe_name:
                        # Found by name — check if content matches
                        if t.get("body") == converted_body:
                            new_id = t.get("id")
                            frappe.db.set_value(
                                "WhatsApp Templates", self.template,
                                "openwa_template_id", new_id, update_modified=False,
                            )
                            frappe.db.commit()
                            frappe.log_error(
                                title="OpenWA: Template ID recovered",
                                message=f"Template '{self.template}' found on OpenWA → ID {new_id}",
                            )
                            return new_id
                        else:
                            # Content differs — update the OpenWA template
                            openwa_api(account, "PUT", f"/templates/{t['id']}", json_data={
                                "name": frappe_name,
                                "body": converted_body,
                                "header": frappe_to_openwa_vars(
                                    frappe.db.get_value("WhatsApp Templates", self.template, "header") or ""
                                ) or None,
                                "footer": frappe.db.get_value("WhatsApp Templates", self.template, "footer") or None,
                            })
                            frappe.db.set_value(
                                "WhatsApp Templates", self.template,
                                "openwa_template_id", t["id"], update_modified=False,
                            )
                            frappe.db.commit()
                            frappe.log_error(
                                title="OpenWA: Template content updated",
                                message=f"Template '{self.template}' content was stale on OpenWA, updated.",
                            )
                            return t["id"]
            except Exception:
                pass

            # Not found on OpenWA — create it there from Frappe doc
            frappe.log_error(
                title="OpenWA: Template missing, re-syncing",
                message=f"Template '{self.template}' not found on OpenWA. Creating it now.",
            )
            tmpl_doc = frappe.get_doc("WhatsApp Templates", self.template)
            old_id = tmpl_doc.openwa_template_id
            tmpl_doc._sync_to_openwa()
            frappe.db.commit()
            # Re-read from DB — _sync_to_openwa uses db.set_value which
            # doesn't update the in-memory doc attribute
            new_id = frappe.db.get_value("WhatsApp Templates", self.template, "openwa_template_id")
            if new_id and new_id != old_id:
                frappe.log_error(
                    title="OpenWA: Template re-synced",
                    message=f"Template '{self.template}' created on OpenWA → ID {new_id}",
                )
                return new_id
            else:
                frappe.log_error(
                    title="OpenWA: Template re-sync failed",
                    message=(
                        f"Template '{self.template}' — _sync_to_openwa did not produce a new ID. "
                        f"Old ID was {old_id}. The session may be dead."
                    ),
                )
                return None
        except Exception as e:
            frappe.log_error(
                title="OpenWA: Template ID recovery failed",
                message=str(e),
            )
        return None

    # ------------------------------------------------------------------
    # Template translation
    # ------------------------------------------------------------------

    def _translate_template_payload(self) -> str:
        """Convert structured Meta templates into substituted OpenWA strings."""
        try:
            template_doc = frappe.get_doc("WhatsApp Templates", self.template)
            body_text = template_doc.template
        except Exception:
            body_text = frappe.db.get_value(
                "WhatsApp Templates", self.template, "template"
            ) or ""

        params: list[str] = []
        if self.body_param:
            try:
                bp = json.loads(self.body_param)
                if isinstance(bp, dict):
                    params = list(bp.values())
                elif isinstance(bp, list):
                    params = bp
            except (json.JSONDecodeError, TypeError):
                pass
        elif self.template_parameters:
            params = json.loads(self.template_parameters)

        for i, val in enumerate(params, 1):
            body_text = body_text.replace(f"{{{{{i}}}}}", str(val))

        return body_text
