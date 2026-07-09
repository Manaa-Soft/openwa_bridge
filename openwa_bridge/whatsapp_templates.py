"""Override WhatsAppTemplates to skip Meta API calls for OpenWA accounts."""
import json
import frappe
from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_templates.whatsapp_templates import (
    WhatsAppTemplates,
)


def _is_openwa_account(account_name: str | None) -> bool:
    """Return True if the WhatsApp Account has OpenWA enabled."""
    if not account_name:
        return False
    return bool(frappe.db.get_value("WhatsApp Account", account_name, "openwa_enabled"))


class OverrideWhatsAppTemplates(WhatsAppTemplates):
    """Skips all Meta Cloud API round-trips for OpenWA-enabled accounts."""

    def validate(self):
        self.set_whatsapp_account()
        if not self.language_code or self.has_value_changed("language"):
            lang_code = frappe.db.get_value("Language", self.language) or "en"
            self.language_code = lang_code.replace("-", "_")

        if _is_openwa_account(self.whatsapp_account):
            if not self.actual_name and self.template_name:
                self.actual_name = self.template_name.lower().replace(" ", "_")
            if not self.status:
                self.status = "APPROVED"
            return

        if self.header_type in ["IMAGE", "DOCUMENT"] and self.sample:
            self.get_session_id(self.sample)
            self.get_media_id(self.sample)

        if not self.is_new():
            self.update_template()

    def after_insert(self):
        if _is_openwa_account(self.whatsapp_account):
            if self.template_name:
                self.actual_name = self.template_name.lower().replace(" ", "_")
            if not self.status:
                self.status = "APPROVED"
            self.db_update()
            return

        super().after_insert()

    def on_trash(self):
        if _is_openwa_account(self.whatsapp_account):
            return

        super().on_trash()
