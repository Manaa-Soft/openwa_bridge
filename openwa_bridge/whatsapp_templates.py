"""Override WhatsAppTemplates — skip Meta API, sync to OpenWA."""
import frappe
from frappe import _
from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_templates.whatsapp_templates import (
    WhatsAppTemplates,
)
from openwa_bridge.utils import openwa_api, frappe_to_openwa_vars, is_openwa_account


def _is_openwa_account(account_name: str | None) -> bool:
    """Return True if the WhatsApp Account has OpenWA enabled. Wrapper for utils.is_openwa_account."""
    return is_openwa_account(account_name)


class OverrideWhatsAppTemplates(WhatsAppTemplates):
    """Skips Meta Cloud API calls and syncs templates to OpenWA."""

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def validate(self):
        self.set_whatsapp_account()
        if not self.language_code or self.has_value_changed("language"):
            lang_code = frappe.db.get_value("Language", self.language) or "en"
            self.language_code = lang_code.replace("-", "_")

        if _is_openwa_account(self.whatsapp_account):
            if not self.actual_name and self.template_name:
                self.actual_name = self.template_name.lower().replace(" ", "_")
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
            self.status = "APPROVED"
            self.db_update()
            self._sync_to_openwa()
            return

        super().after_insert()

    def on_update(self):
        if _is_openwa_account(self.whatsapp_account):
            self._sync_to_openwa()
            return

        super().on_update()

    def on_trash(self):
        if _is_openwa_account(self.whatsapp_account):
            self._delete_from_openwa()
            return

        super().on_trash()

    # ------------------------------------------------------------------
    # OpenWA sync
    # ------------------------------------------------------------------

    def _get_account(self):
        """Return the WhatsApp Account doc for this template."""
        if self.whatsapp_account:
            return frappe.get_doc("WhatsApp Account", self.whatsapp_account)
        return None

    def _build_openwa_payload(self) -> dict:
        """Build the OpenWA template payload from this doc."""
        body = frappe_to_openwa_vars(self.template or "")
        header = frappe_to_openwa_vars(self.header) if self.header else None
        return {
            "name": self.actual_name or self.template_name.lower().replace(" ", "_"),
            "body": body,
            "header": header,
            "footer": self.footer or None,
        }

    def _sync_to_openwa(self):
        """Create or update the template on OpenWA."""
        account = self._get_account()
        if not account:
            return

        payload = self._build_openwa_payload()
        existing_id = None
        result = None

        try:
            if self.openwa_template_id:
                try:
                    result = openwa_api(
                        account, "PUT",
                        f"/templates/{self.openwa_template_id}",
                        json_data=payload,
                    )
                except Exception as put_err:
                    if "404" in str(put_err):
                        existing_id = self._find_openwa_by_name(account, payload["name"])
                    else:
                        raise

            if result is None:
                if existing_id:
                    result = openwa_api(
                        account, "PUT",
                        f"/templates/{existing_id}",
                        json_data=payload,
                    )
                else:
                    result = openwa_api(account, "POST", "/templates", json_data=payload)

            template_id = result.get("id") or existing_id or ""
            if not template_id:
                self.openwa_synced = 0
            frappe.db.set_value(
                "WhatsApp Templates",
                self.name,
                {"openwa_template_id": template_id, "openwa_synced": 1},
                update_modified=False,
            )
            frappe.db.commit()
        except Exception as e:
            frappe.log_error(
                title="OpenWA Template Sync Failed",
                message=f"Template {self.name}: {e}",
            )

    def _find_openwa_by_name(self, account, name: str) -> str | None:
        """Look up an existing OpenWA template by name. Returns its ID or None."""
        try:
            templates = openwa_api(account, "GET", "/templates")
            for t in templates:
                if t.get("name") == name:
                    return t.get("id")
        except Exception:
            pass
        return None

    def _delete_from_openwa(self):
        """Delete the template from OpenWA."""
        account = self._get_account()
        if not account or not self.openwa_template_id:
            return

        try:
            openwa_api(account, "DELETE", f"/templates/{self.openwa_template_id}")
        except Exception:
            pass
