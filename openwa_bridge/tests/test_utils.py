"""Tests for OpenWA Bridge utility functions."""
import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.utils import (
    frappe_to_openwa_vars,
    openwa_to_frappe_vars,
    openwa_type_to_frappe,
    strip_jid_suffix,
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

    def test_order_maps_to_order(self):
        self.assertEqual(openwa_type_to_frappe("order"), "order")

    def test_product_maps_to_order(self):
        self.assertEqual(openwa_type_to_frappe("product"), "order")


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
        import hashlib
        import hmac as hmac_mod
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


class TestIdempotencyKey(IntegrationTestCase):
    """Test build_idempotency_key behavior."""

    def test_prefix_and_basic(self):
        from openwa_bridge.utils import build_idempotency_key

        key = build_idempotency_key("MSG-123")
        self.assertTrue(key.startswith("frappe-"))
        self.assertIn("MSG-123", key)

    def test_sanitizes_non_ascii_and_spaces(self):
        from openwa_bridge.utils import build_idempotency_key

        key = build_idempotency_key("MSG 123/测试")
        self.assertNotIn(" ", key)
        self.assertTrue(all(ord(c) >= 0x21 and ord(c) <= 0x7e for c in key))

    def test_truncates_to_255(self):
        from openwa_bridge.utils import build_idempotency_key

        long_seed = "A" * 1000
        key = build_idempotency_key(long_seed)
        self.assertLessEqual(len(key), 255)

    def test_empty_seed_fallback(self):
        from openwa_bridge.utils import build_idempotency_key

        key = build_idempotency_key("")
        self.assertTrue(key.startswith("frappe-"))


class TestOpenWAErrorMessage(IntegrationTestCase):
    """Test openwa_error_message parsing and HTTPError preservation."""

    def test_nested_error_message(self):
        from unittest.mock import MagicMock

        from openwa_bridge.utils import openwa_error_message

        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"error": {"message": "POLL_OPTION_NOT_FOUND"}}
        resp.text = ""

        msg = openwa_error_message(resp, context="POST /messages/send-poll")
        self.assertIn("OpenWA HTTP 400", msg)
        self.assertIn("POLL_OPTION_NOT_FOUND", msg)
        self.assertIn("POST /messages/send-poll", msg)

    def test_nested_error_code_fallback(self):
        from unittest.mock import MagicMock

        from openwa_bridge.utils import openwa_error_message

        resp = MagicMock()
        resp.status_code = 503
        resp.json.return_value = {"error": {"code": "SESSION_NOT_READY"}}
        resp.text = ""

        msg = openwa_error_message(resp, context="")
        self.assertIn("OpenWA HTTP 503", msg)
        self.assertIn("SESSION_NOT_READY", msg)

    def test_top_level_message(self):
        from unittest.mock import MagicMock

        from openwa_bridge.utils import openwa_error_message

        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"message": "unregistered phone"}
        resp.text = ""

        msg = openwa_error_message(resp)
        self.assertIn("unregistered phone", msg)

    def test_top_level_error_string(self):
        from unittest.mock import MagicMock

        from openwa_bridge.utils import openwa_error_message

        resp = MagicMock()
        resp.status_code = 401
        resp.json.return_value = {"error": "unauthorized"}
        resp.text = ""

        msg = openwa_error_message(resp)
        self.assertIn("unauthorized", msg)

    def test_no_body_fallback(self):
        from unittest.mock import MagicMock

        from openwa_bridge.utils import openwa_error_message

        resp = MagicMock()
        resp.status_code = 500
        resp.json.side_effect = ValueError("not json")
        resp.text = ""

        msg = openwa_error_message(resp)
        self.assertIn("(no body)", msg)

    def test_http_error_preserves_response(self):
        from unittest.mock import MagicMock, patch

        import requests

        from openwa_bridge.utils import openwa_api

        account = {"openwa_base_url": "http://localhost:2785", "openwa_session_id": "s1"}
        with patch("openwa_bridge.utils.get_api_key", return_value="k"):
            with patch("openwa_bridge.utils._http_session.request") as mock_req:
                resp = MagicMock()
                resp.status_code = 400
                resp.json.return_value = {"error": {"message": "bad"}}
                resp.text = '{"error":{"message":"bad"}}'
                mock_req.return_value = resp
                try:
                    openwa_api(account, "POST", "/messages/send-text", {"chatId": "x", "text": "y"})
                    self.fail("should have raised")
                except requests.HTTPError as e:
                    self.assertIsNotNone(getattr(e, "response", None))
                    self.assertEqual(e.response.status_code, 400)
                    self.assertIn("bad", str(e))
