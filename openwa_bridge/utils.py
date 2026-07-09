"""Shared utilities for OpenWA Bridge."""
import hmac
import hashlib


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
    Strip @c.us / @g.us / @s.whatsapp.net suffixes from a WhatsApp JID.
    Returns raw phone number or group ID.
    """
    for suffix in ("@c.us", "@s.whatsapp.net", "@g.us"):
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
