from __future__ import annotations

import frappe
from frappe.model.document import Document


class WhatsAppCatalogProduct(Document):
    def validate(self) -> None:
        """Populate missing product fields from the linked item before validation completes."""
        self._auto_fetch_item_fields()

    def _auto_fetch_item_fields(self) -> None:
        """
        Populate missing product fields from the linked Item and applicable selling settings.
        
        Fields populated may include the product name, description, image, price, currency, unit of measure, and selling price list.
        """
        if not self.item_code:
            return
        item = frappe.db.get_value(
            "Item",
            self.item_code,
            ["item_name", "description", "image", "valuation_rate", "stock_uom"],
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
        if not self.uom:
            self.uom = item.stock_uom or ""
        if not self.price_list:
            selling = frappe.get_single_value("Selling Settings", "selling_price_list")
            if selling:
                ip = frappe.db.get_value(
                    "Item Price",
                    {"item_code": self.item_code, "price_list": selling, "selling": 1},
                    "price_list",
                )
                if ip:
                    self.price_list = ip

    @frappe.whitelist()
    def send_to_chat(self, chat_id: str) -> dict:
        """Send this product as a fallback text+image message to a WhatsApp chat."""
        from openwa_bridge.catalog import send_product_to_chat

        return send_product_to_chat(self.name, chat_id)
