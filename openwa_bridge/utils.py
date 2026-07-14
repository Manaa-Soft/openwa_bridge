"""Shared utilities for OpenWA Bridge."""
from __future__ import annotations

import hmac
import hashlib
import re
from datetime import datetime, timedelta

import frappe
import requests

# Module-level Session for HTTP connection pooling (reuses TCP connections)
_http_session = requests.Session()
_http_session.headers.update({"Content-Type": "application/json"})


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
# Shared helpers
# ------------------------------------------------------------------


def get_api_key(account) -> str:
    """Extract the encrypted OpenWA API key from a WhatsApp Account doc.

    Parameters
    ----------
    account : WhatsApp Account doc (must have ``get_password`` method).

    Returns
    -------
    str : The decrypted API key.

    Raises
    ------
    frappe.ValidationError : If the account has no API key set.
    """
    return account.get_password("openwa_api_key")


def is_openwa_account(account_name: str | None) -> bool:
    """Check if a WhatsApp Account has OpenWA enabled.

    Parameters
    ----------
    account_name : Name of the WhatsApp Account doc, or None.

    Returns
    -------
    bool : True if the account exists and has ``openwa_enabled`` checked.
    """
    if not account_name:
        return False
    return bool(frappe.db.get_value("WhatsApp Account", account_name, "openwa_enabled"))


def get_cached_account(account_name: str):
    """Return a cached WhatsApp Account doc (5-minute TTL).

    Parameters
    ----------
    account_name : Name of the WhatsApp Account doc.

    Returns
    -------
    Document : The cached WhatsApp Account doc.
    """
    cache_key = f"openwa_account:{account_name}"
    cached = frappe.cache().get_value(cache_key)
    if cached is not None:
        return cached
    doc = frappe.get_doc("WhatsApp Account", account_name)
    frappe.cache().set_value(cache_key, doc, expires_in_sec=300)
    return doc


def get_account_setting(account, field: str, default=None):
    """Read a configurable value from a WhatsApp Account doc.

    Parameters
    ----------
    account : WhatsApp Account doc or name.
    field   : Field name to read (e.g. 'openwa_cb_threshold').
    default : Fallback value if the field is not set.

    Returns
    -------
    The field value, or *default* if unset/zero.
    """
    if isinstance(account, str):
        account = get_cached_account(account)
    val = getattr(account, field, None)
    return val if val else default


def validate_openwa_url(url: str) -> str:
    """Validate an OpenWA base URL against SSRF and format issues.

    Returns the cleaned URL on success.  Raises ``frappe.ValidationError``
    on problems.
    """
    import ipaddress
    from urllib.parse import urlparse

    if not url:
        frappe.throw("OpenWA Base URL is required.")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        frappe.throw(f"OpenWA Base URL must use http or https (got: {parsed.scheme})")

    hostname = parsed.hostname or ""
    if not hostname:
        frappe.throw("OpenWA Base URL must have a valid hostname.")

    # Warn on private/internal IPs (SSRF risk) — allow but log
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
            frappe.logger().info(
                f"OpenWA URL warning: hostname '{hostname}' resolves to a "
                f"private/internal IP ({ip}). This is OK for local setups "
                f"but should not be used in production."
            )
    except ValueError:
        # hostname is a domain name, not an IP — that's fine
        pass

    return url.strip()


# ------------------------------------------------------------------
# OpenWA REST API helper
# ------------------------------------------------------------------


def openwa_api(account, method: str, path: str, json_data=None, timeout: int = 30) -> dict:
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
    api_key = get_api_key(account)

    headers = {
        "Content-Type": "application/json",
        "X-API-Key": api_key,
    }

    url = f"{base_url}/api/sessions/{session_id}{path}"
    resp = _http_session.request(method, url, json=json_data, headers=headers, timeout=timeout)
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
    doctype: str, name: str, print_format: str = "Standard",
    letterhead: str | None = None,
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
            letterhead=letterhead,
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


# ------------------------------------------------------------------
# Circuit breaker for OpenWA send failures
# ------------------------------------------------------------------


class OpenWACircuitBreaker:
    """Trip after N consecutive failures per account.  Blocks sends during cooldown.

    State is stored in ``frappe.cache()`` (Redis-backed) so it survives process
    restarts but resets on Redis restart -- which is the correct behavior since
    a Redis restart likely means the gateway itself is healthy again.
    """

    CACHE_PREFIX = "openwa_cb::"

    def __init__(
        self,
        account_name: str,
        threshold: int = 5,
        cooldown_seconds: int = 300,
    ) -> None:
        self.account_name = account_name
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self._key_failures = f"{self.CACHE_PREFIX}{account_name}::failures"
        self._key_tripped = f"{self.CACHE_PREFIX}{account_name}::tripped_at"

    # ------------------------------------------------------------------

    def record_success(self) -> None:
        """Reset the failure counter (circuit closes)."""
        frappe.cache().delete_value(self._key_failures)
        frappe.cache().delete_value(self._key_tripped)

    def record_failure(self) -> None:
        """Increment consecutive failure count; trip if threshold reached."""
        count = (frappe.cache().get_value(self._key_failures) or 0) + 1
        frappe.cache().set_value(
            self._key_failures, count, expires_in_sec=self.cooldown_seconds * 2,
        )

        if count >= self.threshold:
            frappe.cache().set_value(
                self._key_tripped,
                datetime.now().isoformat(),
                expires_in_sec=self.cooldown_seconds * 2,
            )
            frappe.logger().warning(
                f"OpenWA circuit breaker TRIPPED for '{self.account_name}' "
                f"after {count} consecutive failures"
            )

    def is_open(self) -> bool:
        """Return True if the circuit is tripped and sends should be blocked."""
        tripped_at_str = frappe.cache().get_value(self._key_tripped)
        if not tripped_at_str:
            return False

        tripped_at = datetime.fromisoformat(tripped_at_str)
        if datetime.now() - tripped_at > timedelta(seconds=self.cooldown_seconds):
            # Cooldown expired -- close the circuit
            frappe.cache().delete_value(self._key_tripped)
            frappe.cache().delete_value(self._key_failures)
            return False

        return True

    def remaining_cooldown(self) -> int:
        """Seconds remaining until the circuit closes.  0 if closed."""
        tripped_at_str = frappe.cache().get_value(self._key_tripped)
        if not tripped_at_str:
            return 0

        tripped_at = datetime.fromisoformat(tripped_at_str)
        elapsed = (datetime.now() - tripped_at).total_seconds()
        remaining = max(0, int(self.cooldown_seconds - elapsed))
        return remaining
