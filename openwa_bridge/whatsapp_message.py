"""Outbound message router — overrides WhatsAppMessage.notify() to route via OpenWA."""
import frappe
import requests
import json

from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import (
    WhatsAppMessage,
)
from frappe_whatsapp.utils import format_number
from openwa_bridge.utils import openwa_api, frappe_to_openwa_vars


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

            try:
                return self._send_via_openwa(account, data)
            except Exception as e:
                frappe.log_error(
                    title="OpenWA Transmission Failure",
                    message=f"Failed sending message {self.name}: {str(e)}",
                )
                frappe.throw(f"OpenWA Routing Failed: {str(e)}")

        return super().notify(data)

    # ------------------------------------------------------------------
    # OpenWA dispatchers
    # ------------------------------------------------------------------

    def _send_via_openwa(self, account: "WhatsAppAccount", meta_payload: dict) -> None:  # noqa: F821
        """Translate and dispatch the payload to the OpenWA Gateway REST API."""
        base_url = account.get("openwa_base_url").strip("/")
        session_id = account.get("openwa_session_id")
        api_key = account.get_password("openwa_api_key")

        raw_number = format_number(self.to)
        chat_id = f"{raw_number}@c.us" if "@c.us" not in raw_number else raw_number

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": api_key,
        }

        # --- template via OpenWA send-template endpoint ---
        if self.template:
            openwa_tid = frappe.db.get_value("WhatsApp Templates", self.template, "openwa_template_id")
            if openwa_tid:
                params: dict[str, str] = {}
                if self.body_param:
                    try:
                        params = {f"param{k}": v for k, v in json.loads(self.body_param).items()}
                    except (json.JSONDecodeError, TypeError):
                        pass
                elif self.template_parameters:
                    try:
                        raw = json.loads(self.template_parameters)
                        params = {f"param{i + 1}": v for i, v in enumerate(raw)}
                    except (json.JSONDecodeError, TypeError):
                        pass

                send_payload = {"chatId": chat_id, "templateId": openwa_tid, "vars": params}
                frappe.logger().info(f"OpenWA send-template payload: {json.dumps(send_payload, default=str)}")
                resp = requests.post(
                    f"{base_url}/api/sessions/{session_id}/messages/send-template",
                    json=send_payload,
                    headers=headers,
                    timeout=15,
                )
            else:
                message_body = self._translate_template_payload()
                resp = requests.post(
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
            resp = requests.post(
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
            resp = requests.post(
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
            resp = requests.post(
                f"{base_url}/api/sessions/{session_id}/messages/{endpoint}",
                json=payload,
                headers=headers,
                timeout=30,
            )

        elif self.content_type == "reaction":
            resp = requests.post(
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
            resp = requests.post(
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
            resp = requests.post(
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
            resp = requests.post(
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
