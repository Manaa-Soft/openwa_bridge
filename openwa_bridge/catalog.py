"""WhatsApp Catalog — product sync and messaging for ERPNext Items.

Provides whitelisted methods to manage catalog products and send them
to WhatsApp chats.  All catalog operations try the native OpenWA
endpoints first, falling back to richly formatted text+image messages
when the engine returns 501 (Not Implemented).
"""
from __future__ import annotations

import json

import frappe
import requests


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_account(account_name: str) -> dict:
    doc = frappe.get_doc("WhatsApp Account", account_name)
    if not doc.openwa_enabled:
        frappe.throw("OpenWA is not enabled on this account.")
    if not doc.openwa_session_id:
        frappe.throw("OpenWA Session ID is not set.")
    return doc


def _build_product_payload(product, include_image: bool = True) -> dict:  # noqa: ANN001
    """Build the catalog product payload from a WhatsAppCatalogProduct doc."""
    payload = {
        "name": product.product_name,
        "description": product.description or "",
        "price": float(product.price or 0),
        "currency": product.currency or "USD",
        "isAvailable": bool(product.is_available),
    }
    if product.retailer_id:
        payload["retailerId"] = product.retailer_id
    if include_image and product.image:
        payload["imageUrl"] = frappe.utils.get_url(product.image)
    return payload


def _call_openwa(account: dict, method: str, path: str,
                 json_data: dict | None = None) -> dict:
    """Call an OpenWA API endpoint and return the JSON response.

    Raises ``requests.HTTPError`` on non-2xx responses (except 501
    which is returned as a dict with ``{"statusCode": 501}`` so callers
    can handle the fallback).
    """
    from openwa_bridge.utils import openwa_api

    try:
        result = openwa_api(account, method, path, json_data=json_data)
        return result if isinstance(result, dict) else {"result": result}
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 501:
            return {"statusCode": 501, "error": "Not implemented by engine"}
        raise


# ---------------------------------------------------------------------------
# Fallback: rich text message with product details
# ---------------------------------------------------------------------------

def _send_fallback_product_message(account: dict, chat_id: str,
                                    product) -> dict:  # noqa: ANN001
    """Send a richly formatted text message with product details.

    Used when the native catalog send-product endpoint returns 501.
    """
    from openwa_bridge.utils import openwa_api

    lines = [
        f"*{product.product_name}*",
        "",
        product.description or "No description available.",
        "",
    ]
    if product.price:
        lines.append(f"*Price:* {product.currency or ''} {float(product.price):.2f}")
    lines.append(f"*Available:* {'Yes' if product.is_available else 'No'}")

    text = "\n".join(lines)

    payload: dict = {"chatId": chat_id, "text": text}
    if product.image:
        payload["mediaUrl"] = frappe.utils.get_url(product.image)

    result = openwa_api(account, "POST", "/messages/send-text", json_data=payload)
    return {"status": "ok", "method": "fallback", "result": result}


# ---------------------------------------------------------------------------
# Single product operations (called from DocType controller)
# ---------------------------------------------------------------------------

def _sync_single_product(product) -> dict:  # noqa: ANN001
    """Sync one WhatsApp Catalog Product to OpenWA.

    Can be called from the DocType controller or directly.
    """
    account = _get_account(product.whatsapp_account)
    payload = _build_product_payload(product)

    try:
        result = _call_openwa(account, "POST", "/catalog/products", json_data=payload)
    except requests.exceptions.HTTPError as exc:
        frappe.db.set_value("WhatsApp Catalog Product", product.name,
                            "sync_status", "Failed")
        frappe.db.commit()
        error_msg = _extract_error(exc)
        frappe.log_error(title="WhatsApp Catalog: Sync Failed",
                         message=f"Product {product.name}: {error_msg}")
        return {"status": "error", "error": error_msg}

    if isinstance(result, dict) and result.get("statusCode") == 501:
        # Mark as synced anyway — the product data is stored locally and
        # will be usable via the fallback message path.
        frappe.db.set_value("WhatsApp Catalog Product", product.name, {
            "sync_status": "Synced",
            "last_sync_on": frappe.utils.now(),
        })
        frappe.db.commit()
        return {"status": "ok", "method": "local",
                "message": "Stored locally (OpenWA catalog not yet available)"}

    openwa_id = ""
    if isinstance(result, dict):
        openwa_id = result.get("id") or result.get("productId") or ""

    frappe.db.set_value("WhatsApp Catalog Product", product.name, {
        "sync_status": "Synced",
        "openwa_product_id": openwa_id,
        "last_sync_on": frappe.utils.now(),
    })
    frappe.db.commit()
    return {"status": "ok", "method": "openwa", "openwa_product_id": openwa_id}


def _send_product_to_chat(product, chat_id: str) -> dict:  # noqa: ANN001
    """Send a WhatsApp Catalog Product to a specific chat.

    Tries native send-product first, falls back to formatted text.
    """
    account = _get_account(product.whatsapp_account)

    # Try native catalog send-product
    payload = {"chatId": chat_id, "productId": product.openwa_product_id or product.name}
    try:
        result = _call_openwa(account, "POST", "/messages/send-product",
                              json_data=payload)
    except requests.exceptions.HTTPError as exc:
        return {"status": "error", "error": _extract_error(exc)}

    if isinstance(result, dict) and result.get("statusCode") == 501:
        return _send_fallback_product_message(account, chat_id, product)

    return {"status": "ok", "method": "openwa", "result": result}


def _extract_error(exc: requests.exceptions.HTTPError) -> str:
    if exc.response is None:
        return str(exc)
    try:
        return exc.response.json().get("message", exc.response.text)
    except Exception:
        return exc.response.text


# ---------------------------------------------------------------------------
# Whitelisted methods (top-level API)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_catalog_products(account_name: str) -> dict:
    """List all WhatsApp Catalog Products for a given account.

    Returns:
        dict: { "status": "ok", "products": [...] }
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
    """Get a single WhatsApp Catalog Product by name."""
    if not frappe.has_permission("WhatsApp Catalog Product", "read"):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)

    product = frappe.get_doc("WhatsApp Catalog Product", product_name)
    if product.whatsapp_account != account_name:
        frappe.throw("Product does not belong to this account.")

    return {"status": "ok", "product": product.as_dict()}


@frappe.whitelist()
def sync_catalog_products(account_name: str, product_names: str | None = None) -> dict:
    """Sync products to the WhatsApp catalog.

    Args:
        account_name: WhatsApp Account name.
        product_names: Comma-separated product names to sync.
                       If empty, syncs all non-synced products.

    Returns:
        dict: { "status": "ok", "synced": <count>, "failed": <count>, "results": [...] }
    """
    if not frappe.has_permission("WhatsApp Catalog Product", "write"):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)

    filters: dict = {"whatsapp_account": account_name}
    if product_names:
        names = [n.strip() for n in product_names.split(",") if n.strip()]
        filters["name"] = ["in", names]
    else:
        filters["sync_status"] = ["!=", "Synced"]

    products = frappe.get_all("WhatsApp Catalog Product",
                               filters=filters, fields=["name"])

    synced = 0
    failed = 0
    results = []

    for p in products:
        doc = frappe.get_doc("WhatsApp Catalog Product", p.name)
        try:
            result = _sync_single_product(doc)
            results.append({"name": p.name, "status": "ok", "method": result.get("method")})
            synced += 1
        except Exception as exc:
            results.append({"name": p.name, "status": "error", "error": str(exc)})
            failed += 1

    return {"status": "ok", "synced": synced, "failed": failed, "results": results}


@frappe.whitelist()
def send_catalog_to_chat(account_name: str, chat_id: str) -> dict:
    """Send the full catalog link to a WhatsApp chat.

    Tries the native OpenWA endpoint, falls back to a summary message
    listing available products.
    """
    if not frappe.has_permission("WhatsApp Catalog Product", "read"):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)

    account = _get_account(account_name)

    try:
        result = _call_openwa(account, "POST", "/messages/send-catalog",
                              json_data={"chatId": chat_id})
    except requests.exceptions.HTTPError as exc:
        return {"status": "error", "error": _extract_error(exc)}

    if isinstance(result, dict) and result.get("statusCode") == 501:
        return _send_catalog_summary(account, chat_id, account_name)

    return {"status": "ok", "method": "openwa", "result": result}


def _send_catalog_summary(account, chat_id: str, account_name: str) -> dict:  # noqa: ANN001
    """Fallback: send a summary of available products as a text message."""
    from openwa_bridge.utils import openwa_api

    products = frappe.get_all(
        "WhatsApp Catalog Product",
        filters={"whatsapp_account": account_name, "is_available": 1},
        fields=["product_name", "price", "currency"],
        order_by="modified desc",
    )

    if not products:
        lines = ["*Catalog*", "", "No products available at the moment."]
    else:
        lines = ["*Available Products*", ""]
        for p in products:
            price_str = f"{p.currency or ''} {float(p.price or 0):.2f}" if p.price else "Price on request"
            lines.append(f"- *{p.product_name}* — {price_str}")

    text = "\n".join(lines)
    result = openwa_api(account, "POST", "/messages/send-text",
                        json_data={"chatId": chat_id, "text": text})
    return {"status": "ok", "method": "fallback", "result": result}


@frappe.whitelist()
def get_openwa_catalog_info(account_name: str) -> dict:
    """Get catalog info from OpenWA.

    Returns local product count if OpenWA catalog is not implemented.
    """
    if not frappe.has_permission("WhatsApp Catalog Product", "read"):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)

    account = _get_account(account_name)

    try:
        result = _call_openwa(account, "GET", "/catalog")
    except requests.exceptions.HTTPError as exc:
        return {"status": "error", "error": _extract_error(exc)}

    if isinstance(result, dict) and result.get("statusCode") == 501:
        count = frappe.db.count("WhatsApp Catalog Product",
                                 filters={"whatsapp_account": account_name})
        return {"status": "ok", "method": "local",
                "catalog": {"name": account_name, "productCount": count}}

    return {"status": "ok", "method": "openwa", "catalog": result}


@frappe.whitelist()
def send_product_to_chat_direct(account_name: str, chat_id: str,
                                 product_name: str) -> dict:
    """Send a specific catalog product to a chat by product name.

    Sugar method — looks up the product by name and delegates to the
    DocType controller's ``send_to_chat``.
    """
    if not frappe.has_permission("WhatsApp Catalog Product", "read"):
        frappe.throw("Insufficient permissions.", frappe.PermissionError)

    product = frappe.get_doc("WhatsApp Catalog Product", product_name)
    if product.whatsapp_account != account_name:
        frappe.throw("Product does not belong to this account.")

    return _send_product_to_chat(product, chat_id)
