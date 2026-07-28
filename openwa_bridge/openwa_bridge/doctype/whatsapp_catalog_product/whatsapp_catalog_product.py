from __future__ import annotations

import frappe
from frappe.model.document import Document


class WhatsAppCatalogProduct(Document):
    def validate(self) -> None:
        self._auto_fetch_item_fields()

    def _auto_fetch_item_fields(self) -> None:
        if not self.item_code:
            return
        item = frappe.db.get_value(
            "Item",
            self.item_code,
            ["item_name", "description", "image", "valuation_rate"],
            as_dict=True,
        )
        if not item:
            return

        if not self.product_name:
            self.product_name = item.item_name
        if not self.description:
            self.description = item.description
        if not self.image:
            self.image = item.image
        if not self.price:
            self.price = item.valuation_rate or 0
        if not self.currency:
            self.currency = frappe.db.get_single_value("Currency", "default_currency") or "USD"

    @frappe.whitelist()
    def sync_to_catalog(self) -> dict:
        """Sync this product to the WhatsApp catalog via OpenWA.

        Delegates to :func:`openwa_bridge.catalog.sync_single_product`
        with a fresh DB load to avoid concurrency issues.
        """
        from openwa_bridge.catalog import sync_single_product

        return sync_single_product(self.name)

    @frappe.whitelist()
    def send_to_chat(self, chat_id: str) -> dict:
        """Send this product as a message to a WhatsApp chat.

        Delegates to :func:`openwa_bridge.catalog.send_single_product_to_chat`
        with a fresh DB load to avoid concurrency issues.
        """
        from openwa_bridge.catalog import send_single_product_to_chat

        return send_single_product_to_chat(self.name, chat_id)
