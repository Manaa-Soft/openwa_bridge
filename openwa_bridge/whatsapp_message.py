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
        """Verify the OpenWA session is ready; attempt restart if not.

        Raises ``frappe.ValidationError`` if the session cannot be recovered.
        """
        base_url = account.get("openwa_base_url").strip("/")
        session_id = account.get("openwa_session_id")
        api_key = get_api_key(account)

        headers = {"Content-Type": "application/json", "X-API-Key": api_key}

        # Check current status
        try:
            resp = _http_session.get(
                f"{base_url}/api/sessions/{session_id}",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                status = resp.json().get("status", "unknown")
                if status == "ready":
                    return  # all good
            else:
                status = "disconnected"
        except Exception:
            status = "disconnected"

        # Not ready — attempt restart
        frappe.logger().info(
            f"OpenWA pre-send: session '{session_id}' status is '{status}' — "
            f"attempting restart before sending message {self.name}"
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

        # Poll for readiness (up to 20s, 1s intervals)
        for _ in range(20):
            time.sleep(1)
            try:
                verify = _http_session.get(
                    f"{base_url}/api/sessions/{session_id}",
                    headers=headers,
                    timeout=10,
                )
                if verify.status_code == 200:
                    new_status = verify.json().get("status", "unknown")
                    if new_status == "ready":
                        frappe.logger().info(
                            f"OpenWA pre-send: session '{session_id}' restarted "
                            f"successfully — status is now 'ready'"
                        )
                        return
                    if new_status == "qr_ready":
                        frappe.throw(
                            "OpenWA session restarted but requires QR re-scan. "
                            "Please open the WhatsApp Account form and scan the QR code."
                        )
            except Exception:
                pass

        frappe.throw(
            f"OpenWA session is {status} and could not be recovered automatically. "
            "Please open the WhatsApp Account form and click 'Reconnect'."
        )

    def _send_via_openwa(self, account: "WhatsAppAccount", meta_payload: dict) -> None:  # noqa: F821
        """Translate and dispatch the payload to the OpenWA Gateway REST API."""
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
                    timeout=15,
                )
            else:
                message_body = self._translate_template_payload()
                resp = _http_session.post(
                    f"{base_url}/api/sessions/{session_id}/messages/send-text",
                    json={"chatId": chat_id, "text": message_body},
                    headers=headers,
                    timeout=15,
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
                timeout=15,
            )

        elif self.content_type == "text":
            resp = _http_session.post(
                f"{base_url}/api/sessions/{session_id}/messages/send-text",
                json={"chatId": chat_id, "text": self.message},
                headers=headers,
                timeout=15,
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
                timeout=30,
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
                timeout=15,
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
                timeout=15,
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
                timeout=15,
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
                timeout=15,
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
            frappe.log_error(
                title="OpenWA API Error",
                message=(
                    f"POST {resp.url} returned {resp.status_code}\n"
                    f"Request body: {json.dumps(resp.request.body.decode() if resp.request.body else '', default=str)}\n"
                    f"Response: {err_body}"
                ),
            )
            resp.raise_for_status()

        res_data = resp.json()

        if "messageId" in res_data:
            frappe.db.set_value(
                "WhatsApp Message",
                self.name,
                {"message_id": res_data["messageId"], "status": "Sent"},
            )

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
            params = list(json.loads(self.body_param).values())
        elif self.template_parameters:
            params = json.loads(self.template_parameters)

        for i, val in enumerate(params, 1):
            body_text = body_text.replace(f"{{{{{i}}}}}", str(val))

        return body_text
