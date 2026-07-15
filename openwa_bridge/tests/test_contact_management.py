"""Tests for openwa_bridge.whatsapp_account — contact management & typing API."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
import pytest
import frappe


@pytest.fixture(autouse=True)
def set_test_site():
    if not frappe.db:
        frappe.init(site="manaa-soft")
        frappe.connect()


def _mock_account(openwa_enabled=1):
    acc = MagicMock()
    acc.openwa_enabled = openwa_enabled
    acc.openwa_session_id = "test-session-id"
    acc.openwa_base_url = "http://test:2785"
    return acc


class TestCheckWhatsappNumber:
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_number_exists(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {"isRegistered": True}

        from openwa_bridge.whatsapp_account import check_whatsapp_number
        result = check_whatsapp_number("Test Account", "1234567890")

        assert result["exists"] is True
        assert "@c.us" in result["jid"]
        mock_api.assert_called_once_with(mock_get.return_value, "GET", "/contacts/check/+1234567890")

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_number_not_found(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {"isRegistered": False}

        from openwa_bridge.whatsapp_account import check_whatsapp_number
        result = check_whatsapp_number("Test Account", "9999999999")

        assert result["exists"] is False

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_api_error(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.side_effect = Exception("Connection refused")

        from openwa_bridge.whatsapp_account import check_whatsapp_number
        result = check_whatsapp_number("Test Account", "1234567890")

        assert result["exists"] is False
        assert "Connection refused" in result["error"]


class TestBlockUnblockContact:
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_block_contact(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {}

        from openwa_bridge.whatsapp_account import block_contact
        result = block_contact("Test Account", "12345@c.us")

        assert result["status"] == "blocked"
        mock_api.assert_called_once_with(mock_get.return_value, "POST", "/contacts/12345@c.us/block")

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_unblock_contact(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {}

        from openwa_bridge.whatsapp_account import unblock_contact
        result = unblock_contact("Test Account", "12345@c.us")

        assert result["status"] == "unblocked"
        mock_api.assert_called_once_with(mock_get.return_value, "DELETE", "/contacts/12345@c.us/block")

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_block_contact_error(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.side_effect = Exception("Timeout")

        from openwa_bridge.whatsapp_account import block_contact
        result = block_contact("Test Account", "12345@c.us")

        assert result["status"] == "error"
        assert "Timeout" in result["error"]


class TestTypingIndicator:
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_send_typing(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {}

        from openwa_bridge.whatsapp_account import send_typing_indicator
        result = send_typing_indicator("Test Account", "12345@c.us", "typing")

        assert result["status"] == "ok"
        mock_api.assert_called_once_with(
            mock_get.return_value, "POST", "/chats/typing",
            json_data={"chatId": "12345@c.us", "state": "typing"},
        )

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_send_recording(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {}

        from openwa_bridge.whatsapp_account import send_typing_indicator
        result = send_typing_indicator("Test Account", "12345@c.us", "recording")

        assert result["status"] == "ok"

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_invalid_state(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()

        from openwa_bridge.whatsapp_account import send_typing_indicator
        with pytest.raises(frappe.ValidationError):
            send_typing_indicator("Test Account", "12345@c.us", "invalid")

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_api_failure(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.side_effect = Exception("Server error")

        from openwa_bridge.whatsapp_account import send_typing_indicator
        result = send_typing_indicator("Test Account", "12345@c.us", "typing")

        assert result["status"] == "error"
        assert "Server error" in result["error"]
