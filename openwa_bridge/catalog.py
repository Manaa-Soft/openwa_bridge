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
    """
    Load a WhatsApp account configured for OpenWA operations.
    
    Parameters:
    	account_name (str): Name of the WhatsApp account to load.
    
    Returns:
    	dict: The configured WhatsApp account document.
    """
    doc = frappe.get_doc("WhatsApp Account", account_name)
    if not doc.openwa_enabled:
        frappe.throw("OpenWA is not enabled on this account.")
    if not doc.openwa_session_id:
        frappe.throw("OpenWA Session ID is not set.")
    return doc


def _send_fallback_product_message(account: dict, chat_id: str,
                                    product) -> dict:
    """Send product details with an image when available, or as text otherwise.
                                    
                                    Parameters:
                                        account (dict): WhatsApp account configuration.
                                        chat_id (str): Recipient's WhatsApp chat identifier.
                                        product: Product record containing its details and optional image.
                                    
                                    Returns:
                                        dict: The send status, fallback method, and OpenWA API result.
                                    """
    import base64
    import os

    from openwa_bridge.utils import openwa_api

    caption_lines = [
        f"*{product.product_name}*",
        "",
        product.description or frappe._("No description available."),
        "",
    ]

    uom = product.get("uom") or ""
    price_list = product.get("price_list") or ""

    price_line = ""
    if product.price:
        price_str = f"{product.currency or ''} {float(product.price):,.2f}"
        if uom:
            price_str += f" /{uom}"
        price_line = f"*{price_list or frappe._('Price')}:* {price_str}"

    if price_line:
        caption_lines.append(price_line)
    caption_lines.append(f"*{frappe._('Available')}:* {frappe._('Yes') if product.is_available else frappe._('No')}")

    caption = "\n".join(caption_lines)

    if product.image:
        try:
            file_path = frappe.get_site_path(product.image.strip("/"))
            if os.path.exists(file_path):
                with open(file_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                import mimetypes
                mimetype = mimetypes.guess_type(file_path)[0] or "image/jpeg"
                result = openwa_api(account, "POST", "/messages/send-image",
                                    json_data={"chatId": chat_id, "base64": b64,
                                               "mimetype": mimetype, "caption": caption})
                return {"status": "ok", "method": "fallback", "result": result}
        except Exception:
            pass

    result = openwa_api(account, "POST", "/messages/send-text",
                        json_data={"chatId": chat_id, "text": caption})
    return {"status": "ok", "method": "fallback", "result": result}


def _send_catalog_summary(account, chat_id: str, account_name: str):
    """
    Send a text summary of the available catalog products.
    
    Parameters:
        chat_id (str): WhatsApp chat identifier.
        account_name (str): WhatsApp account whose available products are summarized.
    
    Returns:
        dict: Result containing the send status, fallback method, and API response.
    """
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
    """Send a catalog product to a WhatsApp chat.
    
    Parameters:
    	product_name (str): The catalog product to send.
    	chat_id (str): The recipient's WhatsApp chat identifier.
    
    Returns:
    	dict: The message response from WhatsApp.
    """
    if not frappe.has_permission("WhatsApp Catalog Product", "read"):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)
    product = frappe.get_doc("WhatsApp Catalog Product", product_name)
    account = _get_account(product.whatsapp_account)
    return _send_fallback_product_message(account, chat_id, product)


@frappe.whitelist()
def get_catalog_products(account_name: str) -> dict:
    """
    List catalog products configured for a WhatsApp account.
    
    Parameters:
        account_name (str): The WhatsApp account whose catalog products to retrieve.
    
    Returns:
        dict: A status and the matching catalog products, ordered by most recent modification.
    """
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
    """
    Retrieve a WhatsApp catalog product for an account.
    
    Parameters:
        account_name (str): WhatsApp account associated with the product.
        product_name (str): Name of the catalog product to retrieve.
    
    Returns:
        dict: A response containing the status and product details.
    
    Raises:
        frappe.PermissionError: If the caller lacks read permission.
        frappe.ValidationError: If the product belongs to a different account.
    """
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
    """
    Send a catalog product to a customer's WhatsApp contact.
    
    Parameters:
        product_name (str): The catalog product to send.
        customer (str): The customer whose contact should receive the product.
    
    Returns:
        dict: The response from sending the product message.
    
    Raises:
        frappe.PermissionError: If the caller lacks read permission for catalog products.
        frappe.ValidationError: If the customer has no contact or phone number.
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
    """
                                 Send a catalog product to a chat using the specified WhatsApp account.
                                 
                                 Parameters:
                                     account_name (str): WhatsApp account that must own the product.
                                     chat_id (str): Identifier of the destination chat.
                                     product_name (str): Name of the catalog product to send.
                                 
                                 Returns:
                                     dict: Result of sending the product message.
                                 
                                 Raises:
                                     frappe.PermissionError: If the caller lacks read permission for catalog products.
                                     frappe.ValidationError: If the product does not belong to the specified account.
                                 """
    if not frappe.has_permission("WhatsApp Catalog Product", "read"):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)

    product = frappe.get_doc("WhatsApp Catalog Product", product_name)
    if product.whatsapp_account != account_name:
        frappe.throw("Product does not belong to this account.")

    return send_product_to_chat(product_name, chat_id)
