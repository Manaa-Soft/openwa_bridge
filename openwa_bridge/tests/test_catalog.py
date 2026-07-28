"""Unit tests for WhatsApp Catalog product messaging."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.tests.conftest import mock_openwa_api


class TestExtractError(IntegrationTestCase):
    """Test _extract_error helper (shared from whatsapp_account.py)."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.whatsapp_account import _extract_error
        self.extractor = _extract_error

    def test_extracts_json_message(self):
        exc = MagicMock()
        exc.response.json.return_value = {"message": "Something went wrong"}
        exc.response.text = "raw text"
        exc.response is not None
        result = self.extractor(exc)
        self.assertEqual(result, "Something went wrong")

    def test_falls_back_to_text(self):
        exc = MagicMock()
        exc.response.json.side_effect = ValueError("invalid json")
        exc.response.text = "raw error text"
        result = self.extractor(exc)
        self.assertEqual(result, "raw error text")

    def test_no_response(self):
        exc = MagicMock()
        exc.response = None
        exc.__str__.return_value = "connection error"
        result = self.extractor(exc)
        self.assertEqual(result, "connection error")


class TestSendProductToChat(IntegrationTestCase):
    """Test send_product_to_chat — always uses fallback text+image."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.catalog import send_product_to_chat
        self.sender = send_product_to_chat

    def _make_product_mock(self, **overrides):
        product = MagicMock()
        defaults = {
            "name": "WCP-0001",
            "product_name": "Test Widget",
            "description": "A fine widget",
            "price": 29.99,
            "currency": "USD",
            "is_available": True,
            "image": "",
            "whatsapp_account": "test-account",
        }
        for k, v in defaults.items():
            setattr(product, k, overrides.get(k, v))
        return product

    @patch("openwa_bridge.catalog._get_account")
    @patch("openwa_bridge.catalog.frappe.get_doc")
    @patch("openwa_bridge.catalog.frappe.has_permission")
    @patch("openwa_bridge.catalog._send_fallback_product_message")
    def test_send_fallback(self, mock_fallback, mock_perm, mock_get_doc,
                            mock_get_account):
        mock_perm.return_value = True
        mock_get_account.return_value = {"openwa_session_id": "sess-001"}
        mock_get_doc.return_value = self._make_product_mock()
        mock_fallback.return_value = {"status": "ok", "method": "fallback"}

        result = self.sender("WCP-0001", "12345@c.us")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["method"], "fallback")
        mock_fallback.assert_called_once()


class TestSendFallbackProductMessage(IntegrationTestCase):
    """Test _send_fallback_product_message."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.catalog import _send_fallback_product_message
        self.sender = _send_fallback_product_message

    def _make_product(self, **overrides):
        product = MagicMock()
        defaults = {
            "product_name": "Test Widget",
            "description": "A fine widget",
            "price": 29.99,
            "currency": "USD",
            "is_available": True,
            "image": "",
            "item_code": "",
            "uom": "",
            "price_list": "",
        }
        for k, v in defaults.items():
            setattr(product, k, overrides.get(k, v))
        return product

    @patch("openwa_bridge.catalog.openwa_api")
    def test_sends_formatted_text(self, mock_api):
        mock_api.return_value = {"messageId": "msg-001"}
        account = {"openwa_session_id": "sess-001"}
        product = self._make_product()

        result = self.sender(account, "12345@c.us", product)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["method"], "fallback")

        call_args = mock_api.call_args[0]
        self.assertIn("/messages/send-text", call_args[2])
        payload = mock_api.call_args[1].get("json_data", {})
        self.assertIn("Test Widget", payload.get("text", ""))
        self.assertEqual(payload["chatId"], "12345@c.us")

    @patch("builtins.open", new_callable=MagicMock)
    @patch("os.path.exists")
    @patch("openwa_bridge.catalog.frappe.get_site_path")
    @patch("openwa_bridge.catalog.openwa_api")
    def test_sends_image_with_caption_when_product_has_image(self, mock_api, mock_get_path,
                                                              mock_exists, mock_open):
        mock_get_path.return_value = "/site/private/files/widget.png"
        mock_exists.return_value = True
        mock_file = MagicMock()
        mock_file.read.return_value = b"fake-image-data"
        mock_open.return_value.__enter__.return_value = mock_file
        mock_api.return_value = {"messageId": "msg-002"}
        account = {"openwa_session_id": "sess-001"}
        product = self._make_product(image="/private/files/widget.png")

        result = self.sender(account, "12345@c.us", product)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["method"], "fallback")
        call_args = mock_api.call_args[0]
        self.assertIn("/messages/send-image", call_args[2])
        payload = mock_api.call_args[1].get("json_data", {})
        self.assertIn("base64", payload)
        self.assertEqual(payload["mimetype"], "image/png")
        self.assertIn("Test Widget", payload.get("caption", ""))

    @patch("builtins.open", new_callable=MagicMock)
    @patch("os.path.exists")
    @patch("openwa_bridge.catalog.frappe.get_site_path")
    @patch("openwa_bridge.catalog.openwa_api")
    def test_falls_back_to_text_when_image_file_missing(self, mock_api, mock_get_path,
                                                         mock_exists, mock_open):
        mock_get_path.return_value = "/site/private/files/widget.png"
        mock_exists.return_value = False
        mock_api.return_value = {"messageId": "msg-003"}
        account = {"openwa_session_id": "sess-001"}
        product = self._make_product(image="/private/files/widget.png")

        result = self.sender(account, "12345@c.us", product)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["method"], "fallback")
        self.assertIn("/messages/send-text", mock_api.call_args[0][2])

    @patch("openwa_bridge.catalog.openwa_api")
    def test_enriches_caption_with_stored_fields(self, mock_api):
        mock_api.return_value = {"messageId": "msg-004"}
        account = {"openwa_session_id": "sess-001"}
        product = self._make_product(
            price=35.0, uom="Box", price_list="Standard Selling",
        )

        result = self.sender(account, "12345@c.us", product)

        self.assertEqual(result["status"], "ok")
        payload = mock_api.call_args[1].get("json_data", {})
        caption = payload.get("text") or payload.get("caption", "")
        self.assertIn("Standard Selling", caption)
        self.assertIn("35.00", caption)
        self.assertIn("/Box", caption)


class TestSendCatalogSummary(IntegrationTestCase):
    """Test _send_catalog_summary."""

    @patch("openwa_bridge.catalog.openwa_api")
    @patch("openwa_bridge.catalog.frappe.get_all")
    def test_summary_with_products(self, mock_get_all, mock_api):
        mock_api.return_value = {"messageId": "msg-001"}
        mock_get_all.return_value = [
            {"product_name": "Widget A", "price": 10.0, "currency": "USD"},
            {"product_name": "Widget B", "price": 20.0, "currency": "USD"},
        ]

        from openwa_bridge.catalog import _send_catalog_summary
        account = {"openwa_session_id": "sess-001"}
        result = _send_catalog_summary(account, "12345@c.us", "test-account")

        self.assertEqual(result["status"], "ok")
        payload = mock_api.call_args[1].get("json_data", {})
        self.assertIn("Available Products", payload.get("text", ""))
        self.assertIn("Widget A", payload["text"])

    @patch("openwa_bridge.catalog.openwa_api")
    @patch("openwa_bridge.catalog.frappe.get_all")
    def test_summary_no_products(self, mock_get_all, mock_api):
        mock_api.return_value = {"messageId": "msg-001"}
        mock_get_all.return_value = []

        from openwa_bridge.catalog import _send_catalog_summary
        account = {"openwa_session_id": "sess-001"}
        result = _send_catalog_summary(account, "12345@c.us", "test-account")

        self.assertEqual(result["status"], "ok")
        payload = mock_api.call_args[1].get("json_data", {})
        self.assertIn("No products available", payload.get("text", ""))


class TestSendProductToCustomer(IntegrationTestCase):
    """Test send_product_to_customer — resolves Customer Contact → phone → chat."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.catalog import send_product_to_customer
        self.sender = send_product_to_customer

    def _make_product_mock(self, **overrides):
        product = MagicMock()
        defaults = {
            "name": "WCP-0001",
            "product_name": "Test Widget",
            "description": "A fine widget",
            "price": 29.99,
            "currency": "USD",
            "is_available": True,
            "image": "",
            "whatsapp_account": "test-account",
        }
        for k, v in defaults.items():
            setattr(product, k, overrides.get(k, v))
        return product

    @patch("openwa_bridge.catalog._get_account")
    @patch("openwa_bridge.catalog.frappe.get_doc")
    @patch("openwa_bridge.catalog.frappe.has_permission")
    @patch("openwa_bridge.catalog._send_fallback_product_message")
    def test_sends_to_customer_contact(self, mock_fallback, mock_perm,
                                        mock_get_doc, mock_get_account):
        mock_perm.return_value = True
        mock_get_doc.return_value = self._make_product_mock()
        mock_get_account.return_value = {"openwa_session_id": "sess-001"}
        mock_fallback.return_value = {"status": "ok", "method": "fallback"}

        with patch("openwa_bridge.catalog.frappe.get_all") as mock_get_all:
            mock_get_all.side_effect = [
                [{"name": "CON-001", "mobile_no": "+967712345678", "phone": ""}],
            ]

            result = self.sender("WCP-0001", "Acme Corp")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["method"], "fallback")
        args = mock_fallback.call_args[0]
        self.assertIn("@c.us", args[1])

    @patch("openwa_bridge.catalog._get_account")
    @patch("openwa_bridge.catalog.frappe.get_doc")
    @patch("openwa_bridge.catalog.frappe.has_permission")
    def test_raises_if_no_contact(self, mock_perm, mock_get_doc, mock_get_account):
        mock_perm.return_value = True
        mock_get_doc.return_value = self._make_product_mock()
        mock_get_account.return_value = {"openwa_session_id": "sess-001"}

        with patch("openwa_bridge.catalog.frappe.get_all") as mock_get_all:
            mock_get_all.return_value = []

            with self.assertRaises(frappe.ValidationError):
                self.sender("WCP-0001", "No Contact Customer")


class TestGetCatalogProducts(IntegrationTestCase):
    """Test get_catalog_products whitelisted method."""

    @patch("openwa_bridge.catalog.frappe.has_permission")
    @patch("openwa_bridge.catalog.frappe.get_all")
    def test_returns_product_list(self, mock_get_all, mock_perm):
        mock_perm.return_value = True
        mock_get_all.return_value = [
            {"name": "WCP-0001", "product_name": "Widget", "price": 10.0},
        ]

        from openwa_bridge.catalog import get_catalog_products
        result = get_catalog_products("test-account")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["products"]), 1)
