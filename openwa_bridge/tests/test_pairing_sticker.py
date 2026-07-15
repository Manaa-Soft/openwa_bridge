"""Tests for pairing code, sticker, and mention features."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest
import frappe


@pytest.fixture(autouse=True)
def set_test_site():
    if not frappe.db:
        frappe.init(site="manaa-soft")
        frappe.connect()


def _mock_account():
    acc = MagicMock()
    acc.openwa_enabled = 1
    acc.openwa_session_id = "test-session-id"
    acc.openwa_base_url = "http://test:2785"
    return acc


class TestPairingCode:
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_request_pairing_code_success(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {"pairingCode": "ABCD1234"}

        from openwa_bridge.whatsapp_account import request_pairing_code
        result = request_pairing_code("Test Account", "1234567890")

        assert result["status"] == "ok"
        assert result["pairingCode"] == "ABCD1234"
        mock_api.assert_called_once_with(
            mock_get.return_value, "POST", "/pairing-code",
            json_data={"phoneNumber": "1234567890"},
        )

    @patch("openwa_bridge.whatsapp_account._get_account")
    def test_request_pairing_code_short_number(self, mock_get):
        mock_get.return_value = _mock_account()

        from openwa_bridge.whatsapp_account import request_pairing_code
        with pytest.raises(frappe.ValidationError):
            request_pairing_code("Test Account", "12345")

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_request_pairing_code_strips_plus(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {"pairingCode": "XYZT9876"}

        from openwa_bridge.whatsapp_account import request_pairing_code
        result = request_pairing_code("Test Account", "+1234567890")

        assert result["pairingCode"] == "XYZT9876"
        call_kwargs = mock_api.call_args
        assert call_kwargs[1]["json_data"]["phoneNumber"] == "1234567890"

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_request_pairing_code_api_error(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.side_effect = Exception("Session not started")

        from openwa_bridge.whatsapp_account import request_pairing_code
        result = request_pairing_code("Test Account", "1234567890")

        assert result["status"] == "error"
        assert "Session not started" in result["error"]


class TestSendSticker:
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_send_sticker_by_url(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {"messageId": "stk-001"}

        from openwa_bridge.whatsapp_account import send_sticker
        result = send_sticker("Test Account", "12345@c.us",
                              url="https://example.com/sticker.webp")

        assert result["status"] == "ok"
        assert result["messageId"] == "stk-001"
        mock_api.assert_called_once_with(
            mock_get.return_value, "POST", "/messages/send-sticker",
            json_data={"chatId": "12345@c.us", "url": "https://example.com/sticker.webp"},
        )

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_send_sticker_by_base64(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {"messageId": "stk-002"}

        from openwa_bridge.whatsapp_account import send_sticker
        result = send_sticker("Test Account", "12345@c.us",
                              base64="UklGRnoGAABXRUJQVlA4WAoAAAA...")

        assert result["status"] == "ok"
        call_kwargs = mock_api.call_args
        assert call_kwargs[1]["json_data"]["base64"] == "UklGRnoGAABXRUJQVlA4WAoAAAA..."

    @patch("openwa_bridge.whatsapp_account._get_account")
    def test_send_sticker_no_data(self, mock_get):
        mock_get.return_value = _mock_account()

        from openwa_bridge.whatsapp_account import send_sticker
        with pytest.raises(frappe.ValidationError):
            send_sticker("Test Account", "12345@c.us")

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_send_sticker_api_error(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.side_effect = Exception("Invalid sticker format")

        from openwa_bridge.whatsapp_account import send_sticker
        result = send_sticker("Test Account", "12345@c.us",
                              url="https://example.com/bad.png")

        assert result["status"] == "error"
        assert "Invalid sticker format" in result["error"]
