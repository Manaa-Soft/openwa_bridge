"""Tests for bulk messaging, forward, and delete APIs."""
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


class TestBulkMessaging:
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_bulk_send_success(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {
            "sent": ["12345@c.us", "67890@c.us"],
            "failed": [],
        }

        from openwa_bridge.whatsapp_account import send_bulk_openwa
        result = send_bulk_openwa("Test Account", "12345,67890", "Hello everyone")

        assert result["status"] == "ok"
        assert result["sent"] == 2
        assert result["failed"] == 0

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_bulk_send_partial_failure(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {
            "sent": ["12345@c.us"],
            "failed": ["99999@c.us"],
        }

        from openwa_bridge.whatsapp_account import send_bulk_openwa
        result = send_bulk_openwa("Test Account", "12345,99999", "Hello")

        assert result["status"] == "ok"
        assert result["sent"] == 1
        assert result["failed"] == 1

    @patch("openwa_bridge.whatsapp_account._get_account")
    def test_bulk_send_empty_contacts(self, mock_get):
        mock_get.return_value = _mock_account()

        from openwa_bridge.whatsapp_account import send_bulk_openwa
        with pytest.raises(frappe.ValidationError):
            send_bulk_openwa("Test Account", "   ,   ", "Hello")

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_bulk_send_api_error(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.side_effect = Exception("Service unavailable")

        from openwa_bridge.whatsapp_account import send_bulk_openwa
        result = send_bulk_openwa("Test Account", "12345", "Hello")

        assert result["status"] == "error"
        assert "Service unavailable" in result["error"]


class TestForwardMessage:
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_forward_success(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {"messageId": "new-msg-id"}

        from openwa_bridge.whatsapp_account import forward_message
        result = forward_message("Test Account", "original-msg-id", "67890@c.us")

        assert result["status"] == "ok"
        mock_api.assert_called_once_with(
            mock_get.return_value, "POST", "/messages/forward",
            json_data={"messageId": "original-msg-id", "chatId": "67890@c.us"},
        )

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_forward_api_error(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.side_effect = Exception("Message not found")

        from openwa_bridge.whatsapp_account import forward_message
        result = forward_message("Test Account", "bad-id", "67890@c.us")

        assert result["status"] == "error"


class TestDeleteMessage:
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_delete_self(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {}

        from openwa_bridge.whatsapp_account import delete_message
        result = delete_message("Test Account", "msg-to-delete", revoke=0)

        assert result["status"] == "deleted"
        assert result["message_id"] == "msg-to-delete"
        mock_api.assert_called_once_with(
            mock_get.return_value, "DELETE", "/messages/msg-to-delete"
        )

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_revoke_message(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {}

        from openwa_bridge.whatsapp_account import delete_message
        result = delete_message("Test Account", "msg-to-revoke", revoke=1)

        assert result["status"] == "deleted"
        mock_api.assert_called_once_with(
            mock_get.return_value, "DELETE", "/messages/msg-to-revoke?revoke=true"
        )

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_delete_api_error(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.side_effect = Exception("Not found")

        from openwa_bridge.whatsapp_account import delete_message
        result = delete_message("Test Account", "msg-404")

        assert result["status"] == "error"
