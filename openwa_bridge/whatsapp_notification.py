"""Override WhatsAppNotification to skip Meta API for OpenWA accounts."""
import json
import frappe
from frappe import _
from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_notification.whatsapp_notification import (
    WhatsAppNotification,
)
from frappe_whatsapp.utils import get_whatsapp_account


def _is_openwa_account(account_name: str | None) -> bool:
    if not account_name:
        return False
    return bool(frappe.db.get_value("WhatsApp Account", account_name, "openwa_enabled"))


class OverrideWhatsAppNotification(WhatsAppNotification):
    """Routes notification sends through OpenWA when the account is enabled."""

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
        # If the notification has Jinja `code`, evaluate it and send as free text.
        # OpenWA doesn't need Meta-approved templates — it can send any text.
        if self.code and doc_data:
            source = doc_data if isinstance(doc_data, dict) else doc_data
            doctype = source.get("doctype") if isinstance(source, dict) else getattr(source, "doctype", None)
            name = source.get("name") if isinstance(source, dict) else getattr(source, "name", None)
            if doctype and name:
                doc = frappe.get_doc(doctype, name)
            else:
                doc = source
            rendered_message = frappe.render_template(self.code, {"doc": doc})
            rendered_message = rendered_message.strip()
            if not rendered_message:
                return

            new_doc = {
                "doctype": "WhatsApp Message",
                "type": "Outgoing",
                "message": rendered_message,
                "to": data.get("to"),
                "content_type": "text",
                "whatsapp_account": whatsapp_account.name,
            }
        else:
            # No Jinja code — fall back to Meta template via OpenWA
            parameters = None
            if data.get("template", {}).get("components"):
                try:
                    parameters = [
                        param["text"]
                        for param in data["template"]["components"][0]["parameters"]
                    ]
                    parameters = json.dumps(parameters, default=str)
                except (KeyError, IndexError, TypeError):
                    pass

            new_doc = {
                "doctype": "WhatsApp Message",
                "type": "Outgoing",
                "message": str(data.get("template", "")),
                "to": data.get("to"),
                "message_type": "Template",
                "content_type": self.content_type or "text",
                "use_template": 1,
                "template": self.template,
                "template_parameters": parameters,
                "whatsapp_account": whatsapp_account.name,
            }

        if doc_data:
            new_doc["reference_doctype"] = doc_data.get("doctype") if isinstance(doc_data, dict) else getattr(doc_data, "doctype", None)
            new_doc["reference_name"] = doc_data.get("name") if isinstance(doc_data, dict) else getattr(doc_data, "name", None)

        try:
            frappe.get_doc(new_doc).insert(ignore_permissions=True)
            frappe.msgprint("WhatsApp Message Triggered (OpenWA)", indicator="green", alert=True)
        except Exception as e:
            frappe.msgprint(
                f"Failed to trigger WhatsApp message via OpenWA: {str(e)}",
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
