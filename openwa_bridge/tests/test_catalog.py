"""Unit tests for WhatsApp Catalog product sync and messaging."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.tests.conftest import mock_openwa_api


class TestBuildProductPayload(IntegrationTestCase):
    """Test _build_product_payload helper."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.catalog import _build_product_payload
        self.builder = _build_product_payload

    def _make_product(self, **overrides):
        product = MagicMock()
        defaults = {
            "product_name": "Test Widget",
            "description": "A fine widget",
            "price": 29.99,
            "currency": "USD",
            "is_available": True,
            "retailer_id": "",
            "image": "",
        }
        for k, v in defaults.items():
            setattr(product, k, overrides.get(k, v))
        return product

    def test_basic_payload(self):
        product = self._make_product()
        payload = self.builder(product, include_image=False)
        self.assertEqual(payload["name"], "Test Widget")
        self.assertEqual(payload["description"], "A fine widget")
        self.assertEqual(payload["price"], 29.99)
        self.assertEqual(payload["currency"], "USD")
        self.assertTrue(payload["isAvailable"])

    def test_retailer_id_included(self):
        product = self._make_product(retailer_id="SKU-001")
        payload = self.builder(product)
        self.assertEqual(payload.get("retailerId"), "SKU-001")

    def test_empty_description(self):
        product = self._make_product(description="")
        payload = self.builder(product)
        self.assertEqual(payload["description"], "")

    def test_zero_price(self):
        product = self._make_product(price=0)
        payload = self.builder(product)
        self.assertEqual(payload["price"], 0)


class TestExtractError(IntegrationTestCase):
    """Test _extract_error helper."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.catalog import _extract_error
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


class TestSyncSingleProduct(IntegrationTestCase):
    """Test _sync_single_product."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.catalog import _sync_single_product
        self.syncer = _sync_single_product

    def _make_product(self, **overrides):
        product = MagicMock()
        defaults = {
            "name": "WCP-0001",
            "product_name": "Test Widget",
            "description": "A fine widget",
            "price": 29.99,
            "currency": "USD",
            "is_available": True,
            "retailer_id": "",
            "image": "",
            "whatsapp_account": "test-account",
        }
        for k, v in defaults.items():
            setattr(product, k, overrides.get(k, v))
        return product

    @patch("openwa_bridge.catalog._get_account")
    @patch("openwa_bridge.catalog._call_openwa")
    @patch("openwa_bridge.catalog.frappe")
    def test_sync_success(self, mock_frappe, mock_call, mock_get_account):
        mock_get_account.return_value = {"openwa_session_id": "sess-001"}
        mock_call.return_value = {"id": "openwa-prod-001"}

        product = self._make_product()
        result = self.syncer(product)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["openwa_product_id"], "openwa-prod-001")
        mock_frappe.db.set_value.assert_called_once()

    @patch("openwa_bridge.catalog._get_account")
    @patch("openwa_bridge.catalog._call_openwa")
    @patch("openwa_bridge.catalog.frappe")
    def test_sync_501_fallback(self, mock_frappe, mock_call, mock_get_account):
        mock_get_account.return_value = {"openwa_session_id": "sess-001"}
        mock_call.return_value = {"statusCode": 501, "error": "Not implemented by engine"}

        product = self._make_product()
        result = self.syncer(product)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["method"], "local")
        mock_frappe.db.set_value.assert_called_once()

    @patch("openwa_bridge.catalog._get_account")
    @patch("openwa_bridge.catalog._call_openwa")
    @patch("openwa_bridge.catalog.frappe")
    def test_sync_failure(self, mock_frappe, mock_call, mock_get_account):
        from requests.exceptions import HTTPError

        mock_get_account.return_value = {"openwa_session_id": "sess-001"}
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"message": "Server error"}
        exc = HTTPError(response=mock_resp)
        mock_call.side_effect = exc

        product = self._make_product()
        result = self.syncer(product)

        self.assertEqual(result["status"], "error")
        mock_frappe.db.set_value.assert_called_once()


class TestSendProductToChat(IntegrationTestCase):
    """Test _send_product_to_chat."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.catalog import _send_product_to_chat
        self.sender = _send_product_to_chat

    def _make_product(self, **overrides):
        product = MagicMock()
        defaults = {
            "name": "WCP-0001",
            "product_name": "Test Widget",
            "description": "A fine widget",
            "price": 29.99,
            "currency": "USD",
            "is_available": True,
            "image": "",
            "openwa_product_id": "openwa-prod-001",
            "whatsapp_account": "test-account",
        }
        for k, v in defaults.items():
            setattr(product, k, overrides.get(k, v))
        return product

    @patch("openwa_bridge.catalog._get_account")
    @patch("openwa_bridge.catalog._call_openwa")
    def test_send_success(self, mock_call, mock_get_account):
        mock_get_account.return_value = {"openwa_session_id": "sess-001"}
        mock_call.return_value = {"messageId": "msg-001"}

        product = self._make_product()
        result = self.sender(product, "12345@c.us")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["method"], "openwa")

    @patch("openwa_bridge.catalog._get_account")
    @patch("openwa_bridge.catalog._call_openwa")
    @patch("openwa_bridge.catalog._send_fallback_product_message")
    def test_send_501_fallback(self, mock_fallback, mock_call, mock_get_account):
        mock_get_account.return_value = {"openwa_session_id": "sess-001"}
        mock_call.return_value = {"statusCode": 501}
        mock_fallback.return_value = {"status": "ok", "method": "fallback"}

        product = self._make_product()
        result = self.sender(product, "12345@c.us")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["method"], "fallback")
        mock_fallback.assert_called_once()

    @patch("openwa_bridge.catalog._get_account")
    @patch("openwa_bridge.catalog._call_openwa")
    def test_send_error(self, mock_call, mock_get_account):
        from requests.exceptions import HTTPError

        mock_get_account.return_value = {"openwa_session_id": "sess-001"}
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"message": "Invalid chat"}
        exc = HTTPError(response=mock_resp)
        mock_call.side_effect = exc

        product = self._make_product()
        result = self.sender(product, "12345@c.us")

        self.assertEqual(result["status"], "error")


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

    @patch("openwa_bridge.catalog.openwa_api")
    @patch("openwa_bridge.catalog.frappe.utils.get_url")
    def test_includes_image_when_present(self, mock_get_url, mock_api):
        mock_get_url.return_value = "http://example.com/files/widget.png"
        mock_api.return_value = {"messageId": "msg-002"}
        account = {"openwa_session_id": "sess-001"}
        product = self._make_product(image="/files/widget.png")

        result = self.sender(account, "12345@c.us", product)

        self.assertEqual(result["status"], "ok")
        payload = mock_api.call_args[1].get("json_data", {})
        self.assertIn("mediaUrl", payload)


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


class TestSyncCatalogProducts(IntegrationTestCase):
    """Test sync_catalog_products whitelisted method."""

    @patch("openwa_bridge.catalog.frappe.has_permission")
    @patch("openwa_bridge.catalog.frappe.get_all")
    @patch("openwa_bridge.catalog.frappe.get_doc")
    @patch("openwa_bridge.catalog._sync_single_product")
    def test_sync_all_pending(self, mock_sync, mock_get_doc, mock_get_all, mock_perm):
        mock_perm.return_value = True
        mock_get_all.return_value = [{"name": "WCP-0001"}, {"name": "WCP-0002"}]
        mock_get_doc.side_effect = lambda dt, name: MagicMock(name=name)
        mock_sync.return_value = {"status": "ok", "method": "local"}

        from openwa_bridge.catalog import sync_catalog_products
        result = sync_catalog_products("test-account")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["synced"], 2)
        self.assertEqual(result["failed"], 0)
