"""Shared utilities for OpenWA Bridge."""
import hmac
import hashlib
import re
import frappe
import requests


def verify_openwa_signature(
    payload_bytes: bytes, secret: str, signature_header: str
) -> bool:
    """
    Verify HMAC-SHA256 signature from OpenWA webhook.

    OpenWA signs: HMAC-SHA256(secret, JSON.stringify(payload))
    Format: sha256=<hex-digest>
    """
    if not secret or not signature_header:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    expected_full = f"sha256={expected}"

    return hmac.compare_digest(expected_full, signature_header)


def strip_jid_suffix(jid: str) -> str:
    """
    Strip @c.us / @g.us / @s.whatsapp.net / @lid suffixes from a WhatsApp JID.
    Returns raw phone number or group ID.
    """
    for suffix in ("@c.us", "@s.whatsapp.net", "@g.us", "@lid"):
        if jid.endswith(suffix):
            return jid[: -len(suffix)]
    return jid


def openwa_type_to_frappe(openwa_type: str) -> str:
    """
    Map OpenWA message type strings to frappe_whatsapp content_type values.

    OpenWA types: text, image, video, audio, voice, document, sticker,
                  location, contact, poll, call, revoked, masked, unknown
    frappe_whatsapp content_type: text, document, image, video, audio,
                  flow, reaction, location, contact, button, interactive, order
    """
    type_map = {
        "text": "text",
        "image": "image",
        "video": "video",
        "audio": "audio",
        "voice": "audio",
        "document": "document",
        "sticker": "image",
        "location": "location",
        "contact": "contact",
        "poll": "text",
        "reaction": "reaction",
        "revoked": "text",
    }
    return type_map.get(openwa_type, "text")


# ------------------------------------------------------------------
# OpenWA REST API helper
# ------------------------------------------------------------------


def openwa_api(account, method: str, path: str, json_data=None, timeout: int = 15) -> dict:
    """Call the OpenWA REST API with proper auth headers.

    Parameters
    ----------
    account : WhatsApp Account doc or dict-like object.
    method  : HTTP method (GET, POST, PUT, DELETE).
    path    : Path after /api/sessions/:sessionId  (e.g. "/messages/send-text").
    json_data : Optional JSON body.
    timeout : Request timeout in seconds.
    """
    base_url = account.get("openwa_base_url").strip("/")
    session_id = account.get("openwa_session_id")
    api_key = account.get_password("openwa_api_key") if hasattr(account, "get_password") else account.get("openwa_api_key")

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }

    url = f"{base_url}/api/sessions/{session_id}{path}"
    resp = requests.request(method, url, json=json_data, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ------------------------------------------------------------------
# Template variable helpers  ({{1}} <-> {{paramN}})
# ------------------------------------------------------------------


def frappe_to_openwa_vars(text: str) -> str:
    """Convert Frappe-style ``{{1}}`` placeholders to OpenWA ``{{param1}}``."""
    return re.sub(r"\{\{(\d+)\}\}", lambda m: f"{{{{param{m.group(1)}}}}}", text)


def openwa_to_frappe_vars(text: str) -> str:
    """Convert OpenWA ``{{paramN}}`` placeholders back to Frappe ``{{N}}``."""
    return re.sub(r"\{\{param(\d+)\}\}", lambda m: f"{{{{{m.group(1)}}}}}", text)


# ------------------------------------------------------------------
# Document rendering (PDF → image for dynamic headers)
# ------------------------------------------------------------------


def render_doc_as_image(
    doctype: str, name: str, print_format: str = "Standard", letterhead: bool = True
) -> bytes | None:
    """Render a Frappe document as a PNG image using a print format.

    1. Generate PDF bytes via ``frappe.get_print``.
    2. Convert the first page to PNG via PyMuPDF (fitz).
    3. Return PNG bytes, or ``None`` on failure.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        frappe.log_error(
            title="OpenWA: Missing dependency",
            message="PyMuPDF (fitz) is not installed. Run: bench pip install PyMuPDF",
        )
        return None

    try:
        pdf_bytes = frappe.get_print(
            doctype, name, print_format, as_pdf=True,
            no_letterhead=0 if letterhead else 1,
            pdf_generator="chrome",
        )
        if not pdf_bytes:
            return None

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if not doc.page_count:
            doc.close()
            return None

        zoom = 2  # 2x for reasonable quality
        mat = fitz.Matrix(zoom, zoom)
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes

    except Exception:
        frappe.log_error(
            title="OpenWA: Document render failed",
            message=f"Failed to render {doctype} {name} as image",
        )
        return None
