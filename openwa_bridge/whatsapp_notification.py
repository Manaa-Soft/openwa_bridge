"""Override WhatsAppNotification — Jinja code OR OpenWA template per notification."""
import base64
import json
import frappe
from frappe import _
from frappe.utils.safe_exec import get_safe_globals
from frappe.model.document import Document
from frappe.utils import datetime
from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_notification.whatsapp_notification import (
    WhatsAppNotification,
)
from frappe_whatsapp.utils import get_whatsapp_account, format_number
from openwa_bridge.utils import openwa_api, render_doc_as_image


def _is_openwa_account(account_name: str | None) -> bool:
    if not account_name:
        return False
    return bool(frappe.db.get_value("WhatsApp Account", account_name, "openwa_enabled"))


class OverrideWhatsAppNotification(WhatsAppNotification):
    """Routes notification sends through OpenWA when the account is enabled.

    Use ``openwa_send_type`` to control the send path:
    - ``Template`` → send via OpenWA send-template endpoint (real doc values).
    - ``Jinja``    → render ``code`` as Jinja and send as free text.
    - _(empty)_    → fallback: ``template`` → OpenWA, otherwise Meta API.
    """

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def send_template_message(self, doc, phone_no=None, default_template=None, ignore_condition=False):
        """Override to skip parent attachment/header logic for OpenWA templates."""
        if not _is_openwa_account(self.whatsapp_account):
            return super().send_template_message(doc, phone_no, default_template, ignore_condition)

        send_type = self.openwa_send_type or ""
        if send_type != "Template":
            return super().send_template_message(doc, phone_no, default_template, ignore_condition)

        # ── OpenWA Template path (skip parent's header/attachment logic) ──
        if self.disabled:
            return

        doc_data = doc.as_dict()
        if self.condition and not ignore_condition:
            if not frappe.safe_eval(
                self.condition, get_safe_globals(), dict(doc=doc_data)
            ):
                return

        template = default_template or frappe.get_doc("WhatsApp Templates", self.template)
        if not template:
            return

        if self.field_name:
            phone_number = phone_no or doc_data[self.field_name]
        else:
            phone_number = phone_no

        data = {
            "messaging_product": "whatsapp",
            "to": self.format_number(phone_number),
            "type": "template",
            "template": {
                "name": template.actual_name,
                "language": {"code": template.language_code},
                "components": [],
            },
        }

        if self.fields:
            parameters = []
            for field in self.fields:
                if isinstance(doc, Document):
                    value = doc.get_formatted(field.field_name)
                else:
                    value = doc_data.get(field.field_name, "")
                    if isinstance(value, (datetime.date, datetime.datetime)):
                        value = str(value)
                parameters.append({"type": "text", "text": value})
            data["template"]["components"] = [{"type": "body", "parameters": parameters}]

        # ── Step 1: Dynamic image header ──
        if getattr(template, "openwa_dynamic_header", False) and getattr(template, "openwa_print_format", None):
            self._send_dynamic_header_image(doc, template, data)

        self.notify(data, doc_data)

    def notify(self, data: dict, doc_data=None) -> None:  # noqa: ANN001
        if self.whatsapp_account:
            whatsapp_account = frappe.get_doc("WhatsApp Account", self.whatsapp_account)
        else:
            whatsapp_account = get_whatsapp_account(account_type="outgoing")

        if not whatsapp_account:
            frappe.throw(_("Please set a default outgoing WhatsApp Account"))

        if not _is_openwa_account(whatsapp_account.name):
            return super().notify(data, doc_data)

        # ── OpenWA path ──
        send_type = self.openwa_send_type or ""

        # 1) Explicit "Template" → OpenWA send-template with real doc values
        if send_type == "Template" and self.template:
            self._send_openwa_template(whatsapp_account, data, doc_data)
            return

        # 2) Explicit "Jinja" → render code and send as free text
        if send_type == "Jinja" and self.code and doc_data:
            doc = self._resolve_document(doc_data)

            # Dynamic image header (same as Template path)
            if self.template:
                tmpl = frappe.get_doc("WhatsApp Templates", self.template)
                if getattr(tmpl, "openwa_dynamic_header", False) and getattr(tmpl, "openwa_print_format", None):
                    self._send_dynamic_header_image(doc, tmpl, data)

            rendered_message = frappe.render_template(self.code, {"doc": doc}).strip()
            if not rendered_message:
                return
            self._send_openwa_text(whatsapp_account, data, rendered_message, doc_data)
            return

        # 3) Fallback: if template is set, treat as OpenWA template
        if self.template:
            self._send_openwa_template(whatsapp_account, data, doc_data)
            return

    # ------------------------------------------------------------------
    # Dynamic image header
    # ------------------------------------------------------------------

    def _send_dynamic_header_image(self, doc, template, data):
        """Render doc as image via print format and send before template text."""
        print_format = template.openwa_print_format
        letterhead = None
        if getattr(template, "openwa_include_letterhead", False):
            letterhead = getattr(template, "openwa_letterhead", None) or None
        doctype = doc.doctype if hasattr(doc, "doctype") else data.get("template", {}).get("name", "")
        name = doc.name if hasattr(doc, "name") else ""

        if not doctype or not name:
            return

        image_bytes = render_doc_as_image(doctype, name, print_format, letterhead=letterhead)
        if not image_bytes:
            return

        account = self._get_account()
        if not account:
            return

        phone_number = data.get("to", "")
        raw_number = format_number(phone_number)
        chat_id = f"{raw_number}@c.us" if "@c.us" not in raw_number else raw_number

        import requests as _req

        base_url = account.get("openwa_base_url").strip("/")
        session_id = account.get("openwa_session_id")
        api_key = account.get_password("openwa_api_key") if hasattr(account, "get_password") else account.get("openwa_api_key")
        url = f"{base_url}/api/sessions/{session_id}/messages/send-image"
        payload = {
            "chatId": chat_id,
            "base64": base64.b64encode(image_bytes).decode("utf-8"),
            "mimetype": "image/png",
        }
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
                        f"Template {template.name}, Doc {doctype} {name}\n"
                        f"POST {url}\n"
                        f"Status: {img_resp.status_code}\n"
                        f"Response: {img_resp.text[:2000]}"
                    ),
                )
        except Exception as e:
            frappe.log_error(
                title="OpenWA: Dynamic header image failed",
                message=f"Template {template.name}, Doc {doctype} {name}: {e}",
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_account(self):
        """Return the WhatsApp Account doc."""
        if self.whatsapp_account:
            return frappe.get_doc("WhatsApp Account", self.whatsapp_account)
        return None

    def _resolve_document(self, doc_data):
        """Load the actual Frappe Document so Jinja can resolve child tables."""
        source = doc_data if isinstance(doc_data, dict) else doc_data
        doctype = source.get("doctype") if isinstance(source, dict) else getattr(source, "doctype", None)
        name = source.get("name") if isinstance(source, dict) else getattr(source, "name", None)
        if doctype and name:
            return frappe.get_doc(doctype, name)
        return source

    def _send_openwa_text(self, account, data, message: str, doc_data=None) -> None:
        """Create a WhatsApp Message doc that will route through OpenWA as plain text."""
        new_doc = {
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "message": message,
            "to": data.get("to"),
            "content_type": "text",
            "whatsapp_account": account.name,
        }
        if doc_data:
            new_doc["reference_doctype"] = doc_data.get("doctype") if isinstance(doc_data, dict) else getattr(doc_data, "doctype", None)
            new_doc["reference_name"] = doc_data.get("name") if isinstance(doc_data, dict) else getattr(doc_data, "name", None)

        try:
            frappe.get_doc(new_doc).insert(ignore_permissions=True)
            frappe.msgprint("WhatsApp Message Triggered (OpenWA)", indicator="green", alert=True)
        except Exception as e:
            frappe.msgprint(
                f"Failed to trigger WhatsApp message via OpenWA: {e}",
                indicator="red",
                alert=True,
            )

    def _send_openwa_template(self, account, data, doc_data=None) -> None:
        """Create a WhatsApp Message doc that will route through OpenWA as a template."""
        parameters = self._extract_template_parameters(data, doc_data)

        new_doc = {
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "message": str(data.get("template", "")),
            "to": data.get("to"),
            "message_type": "Template",
            "content_type": "text",
            "use_template": 1,
            "template": self.template,
            "template_parameters": parameters,
            "whatsapp_account": account.name,
        }

        if doc_data:
            new_doc["reference_doctype"] = doc_data.get("doctype") if isinstance(doc_data, dict) else getattr(doc_data, "doctype", None)
            new_doc["reference_name"] = doc_data.get("name") if isinstance(doc_data, dict) else getattr(doc_data, "name", None)

        try:
            frappe.get_doc(new_doc).insert(ignore_permissions=True)
            frappe.msgprint("WhatsApp Template Triggered (OpenWA)", indicator="green", alert=True)
        except Exception as e:
            frappe.msgprint(
                f"Failed to trigger WhatsApp template via OpenWA: {e}",
                indicator="red",
                alert=True,
            )

        # Set property after alert if configured
        if doc_data and self.set_property_after_alert and self.property_value:
            try:
                source = doc_data if isinstance(doc_data, dict) else doc_data
                doctype = source.get("doctype") if isinstance(source, dict) else getattr(source, "doctype", None)
                name = source.get("name") if isinstance(source, dict) else getattr(source, "name", None)
                if doctype and name:
                    fieldname = self.set_property_after_alert
                    value = self.property_value
                    meta = frappe.get_meta(doctype)
                    df = meta.get_field(fieldname)
                    if df:
                        if df.fieldtype in frappe.model.numeric_fieldtypes:
                            value = frappe.utils.cint(value)
                        frappe.db.set_value(doctype, name, fieldname, value)
            except Exception:
                pass

    def _extract_template_parameters(self, data, doc_data=None) -> str | None:
        """Extract variable values from the actual document for OpenWA send-template.

        Priority:
        1. ``self.fields`` child table — map each field_name to the doc value
        2. Fallback to pre-built ``data["template"]["components"]`` from parent
        """
        doc = None
        if doc_data:
            doc = self._resolve_document(doc_data)

        # --- Build from notification's fields child table (reads live doc) ---
        if self.fields and doc:
            values = []
            for field_row in self.fields:
                field_name = field_row.field_name
                try:
                    if hasattr(doc, "get_formatted"):
                        value = doc.get_formatted(field_name)
                    else:
                        value = doc.get(field_name)
                except Exception:
                    value = None
                values.append(str(value) if value is not None else "")
            return json.dumps(values, default=str)

        # --- Fallback: extract from parent-built data dict ---
        if data.get("template", {}).get("components"):
            try:
                values = [
                    param["text"]
                    for param in data["template"]["components"][0]["parameters"]
                ]
                return json.dumps(values, default=str)
            except (KeyError, IndexError, TypeError):
                pass

        return None
