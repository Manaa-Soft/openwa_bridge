"""WhatsApp Catalog — product messaging for ERPNext Items.

Provides whitelisted methods to manage catalog products and send them
to WhatsApp chats as richly formatted text+image fallback messages.

OpenWA does not implement any catalog endpoints (neither engine supports
create/read/update of catalog products), so all product data lives in
Frappe's ``WhatsApp Catalog Product`` DocType and is sent as fallback
messages.
"""
from __future__ import annotations

import frappe


def _get_account(account_name: str) -> dict:
    doc = frappe.get_doc("WhatsApp Account", account_name)
    if not doc.openwa_enabled:
        frappe.throw("OpenWA is not enabled on this account.")
    if not doc.openwa_session_id:
        frappe.throw("OpenWA Session ID is not set.")
    return doc


def _send_fallback_product_message(account: dict, chat_id: str,
                                    product) -> dict:
    """Send product details as an image with caption, falling back to text-only."""
    from openwa_bridge.utils import openwa_api

    caption_lines = [
        f"*{product.product_name}*",
        "",
        product.description or frappe._("No description available."),
        "",
    ]
    if product.price:
        caption_lines.append(f"*{frappe._('Price')}:* {product.currency or ''} {float(product.price):.2f}")
    caption_lines.append(f"*{frappe._('Available')}:* {frappe._('Yes') if product.is_available else frappe._('No')}")

    caption = "\n".join(caption_lines)

    if product.image:
        try:
            image_url = frappe.utils.get_url(product.image)
            result = openwa_api(account, "POST", "/messages/send-image",
                                json_data={"chatId": chat_id, "url": image_url,
                                           "caption": caption})
            return {"status": "ok", "method": "fallback", "result": result}
        except Exception:
            pass

    result = openwa_api(account, "POST", "/messages/send-text",
                        json_data={"chatId": chat_id, "text": caption})
    return {"status": "ok", "method": "fallback", "result": result}


def _send_catalog_summary(account, chat_id: str, account_name: str):
    """Send a summary of available products as a text message."""
    from openwa_bridge.utils import openwa_api

    products = frappe.get_all(
        "WhatsApp Catalog Product",
        filters={"whatsapp_account": account_name, "is_available": 1},
        fields=["product_name", "price", "currency"],
        order_by="modified desc",
    )

    if not products:
        lines = [f"*{frappe._('Catalog')}*", "", frappe._("No products available at the moment.")]
    else:
        lines = [f"*{frappe._('Available Products')}*", ""]
        for p in products:
            price_str = f"{p.currency or ''} {float(p.price or 0):.2f}" if p.price else frappe._("Price on request")
            lines.append(f"- *{p.product_name}* — {price_str}")

    text = "\n".join(lines)
    result = openwa_api(account, "POST", "/messages/send-text",
                        json_data={"chatId": chat_id, "text": text})
    return {"status": "ok", "method": "fallback", "result": result}


# ---------------------------------------------------------------------------
# Whitelisted methods (top-level API)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def send_product_to_chat(product_name: str, chat_id: str) -> dict:
    """Send a WhatsApp Catalog Product to a specific chat as a fallback text+image message."""
    if not frappe.has_permission("WhatsApp Catalog Product", "read"):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    product = frappe.get_doc("WhatsApp Catalog Product", product_name)
    account = _get_account(product.whatsapp_account)
    return _send_fallback_product_message(account, chat_id, product)


@frappe.whitelist()
def get_catalog_products(account_name: str) -> dict:
    """List all WhatsApp Catalog Products for a given account."""
    if not frappe.has_permission("WhatsApp Catalog Product", "read"):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)

    products = frappe.get_all(
        "WhatsApp Catalog Product",
        filters={"whatsapp_account": account_name},
        fields=["name", "product_name", "item_code", "price", "currency",
                "sync_status", "is_available", "image", "modified"],
        order_by="modified desc",
    )
    return {"status": "ok", "products": products}


@frappe.whitelist()
def get_catalog_product(account_name: str, product_name: str) -> dict:
    """Get a single WhatsApp Catalog Product by name."""
    if not frappe.has_permission("WhatsApp Catalog Product", "read"):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)

    product = frappe.get_doc("WhatsApp Catalog Product", product_name)
    if product.whatsapp_account != account_name:
        frappe.throw("Product does not belong to this account.")

    return {"status": "ok", "product": product.as_dict()}


@frappe.whitelist()
def send_catalog_to_chat(account_name: str, chat_id: str) -> dict:
    """Send the full catalog summary to a WhatsApp chat."""
    if not frappe.has_permission("WhatsApp Catalog Product", "read"):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)

    account = _get_account(account_name)
    return _send_catalog_summary(account, chat_id, account_name)


@frappe.whitelist()
def send_product_to_customer(product_name: str, customer: str) -> dict:
    """Send a WhatsApp Catalog Product to a Customer's WhatsApp number.

    Looks up the Customer's primary Contact, resolves the phone number
    to a WhatsApp chat ID, and sends the product as a fallback text+image
    message.
    """
    if not frappe.has_permission("WhatsApp Catalog Product", "read"):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)

    product = frappe.get_doc("WhatsApp Catalog Product", product_name)
    account = _get_account(product.whatsapp_account)

    contact = frappe.get_all(
        "Contact",
        filters=[
            ["Dynamic Link", "link_doctype", "=", "Customer"],
            ["Dynamic Link", "link_name", "=", customer],
        ],
        fields=["name", "mobile_no", "phone"],
        limit=1,
    )
    if not contact:
        frappe.throw(f"No Contact found for Customer {customer}.")

    phone = contact[0].mobile_no or contact[0].phone
    if not phone:
        frappe.throw(f"Contact {contact[0].name} has no phone number set.")

    from frappe_whatsapp.utils import format_number
    raw = format_number(phone)
    chat_id = f"{raw}@c.us" if "@" not in raw else raw

    return _send_fallback_product_message(account, chat_id, product)


@frappe.whitelist()
def send_product_to_chat_direct(account_name: str, chat_id: str,
                                 product_name: str) -> dict:
    """Send a specific catalog product to a chat by product name."""
    if not frappe.has_permission("WhatsApp Catalog Product", "read"):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)

    product = frappe.get_doc("WhatsApp Catalog Product", product_name)
    if product.whatsapp_account != account_name:
        frappe.throw("Product does not belong to this account.")

    return send_product_to_chat(product_name, chat_id)
