"""OpenWA Outbox -- persistent message queue for outbound WhatsApp messages.

Messages are inserted here by ``OverrideWhatsAppMessage.notify()`` and processed
by a background worker via ``frappe.enqueue``.  The MariaDB-backed DocType
survives server restarts; a scheduler safety-net re-enqueues orphaned entries
every few minutes.
"""
from __future__ import annotations

import frappe
from frappe.model.document import Document


class OpenWAOutbox(Document):
    """Controller for the OpenWA Outbox DocType."""

    def validate(self) -> None:
        """Enforce valid status transitions and cache derived fields."""
        self._set_content_type_from_message()
        self._set_chat_id_from_message()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _set_content_type_from_message(self) -> None:
        """Cache the content_type from the linked WhatsApp Message."""
        if self.content_type:
            return
        if self.whatsapp_message:
            ct = frappe.db.get_value("WhatsApp Message", self.whatsapp_message, "content_type")
            if ct:
                self.content_type = ct

    def _set_chat_id_from_message(self) -> None:
        """Cache the recipient chat_id from the linked WhatsApp Message."""
        if self.chat_id:
            return
        if self.whatsapp_message:
            to_number = frappe.db.get_value("WhatsApp Message", self.whatsapp_message, "to")
            if to_number:
                from frappe_whatsapp.utils import format_number

                raw = format_number(to_number)
                self.chat_id = raw if "@" in raw else f"{raw}@c.us"
