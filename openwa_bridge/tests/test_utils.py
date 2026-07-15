"""Tests for OpenWA Bridge utility functions."""
import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.utils import (
    strip_jid_suffix,
    openwa_type_to_frappe,
    frappe_to_openwa_vars,
    openwa_to_frappe_vars,
    verify_openwa_signature,
)


class TestStripJIDSuffix(IntegrationTestCase):
    """Test JID suffix stripping."""

    def test_strip_c_us(self):
        self.assertEqual(strip_jid_suffix("1234567890@c.us"), "1234567890")

    def test_strip_g_us(self):
        self.assertEqual(strip_jid_suffix("120363001@g.us"), "120363001")

    def test_strip_lid(self):
        self.assertEqual(strip_jid_suffix("abc123@lid"), "abc123")

    def test_strip_whatsapp_net(self):
        self.assertEqual(strip_jid_suffix("1234567890@s.whatsapp.net"), "1234567890")

    def test_no_suffix(self):
        self.assertEqual(strip_jid_suffix("1234567890"), "1234567890")


class TestOpenwaTypeToFrappe(IntegrationTestCase):
    """Test OpenWA to Frappe content type mapping."""

    def test_text(self):
        self.assertEqual(openwa_type_to_frappe("text"), "text")

    def test_image(self):
        self.assertEqual(openwa_type_to_frappe("image"), "image")

    def test_sticker_maps_to_image(self):
        self.assertEqual(openwa_type_to_frappe("sticker"), "image")

    def test_voice_maps_to_audio(self):
        self.assertEqual(openwa_type_to_frappe("voice"), "audio")

    def test_unknown_defaults_to_text(self):
        self.assertEqual(openwa_type_to_frappe("unknown_type"), "text")


class TestTemplateVarConversion(IntegrationTestCase):
    """Test bidirectional template variable conversion."""

    def test_frappe_to_openwa(self):
        self.assertEqual(frappe_to_openwa_vars("Hello {{1}}, order {{2}}"), "Hello {{param1}}, order {{param2}}")

    def test_openwa_to_frappe(self):
        self.assertEqual(openwa_to_frappe_vars("Hello {{param1}}, order {{2}}"), "Hello {{1}}, order {{2}}")

    def test_roundtrip(self):
        original = "Hello {{1}}, your order {{2}} is {{3}}"
        converted = frappe_to_openwa_vars(original)
        back = openwa_to_frappe_vars(converted)
        self.assertEqual(back, original)


class TestHMACVerification(IntegrationTestCase):
    """Test HMAC-SHA256 signature verification."""

    def test_valid_signature(self):
        import hmac as hmac_mod
        import hashlib
        secret = "test-secret"
        payload = b'{"event":"test"}'
        sig = hmac_mod.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        sig_header = f"sha256={sig}"
        self.assertTrue(verify_openwa_signature(payload, secret, sig_header))

    def test_invalid_signature(self):
        self.assertFalse(verify_openwa_signature(b"test", "secret", "sha256=wrong"))

    def test_empty_secret(self):
        self.assertFalse(verify_openwa_signature(b"test", "", "sha256=abc"))

    def test_empty_header(self):
        self.assertFalse(verify_openwa_signature(b"test", "secret", ""))
