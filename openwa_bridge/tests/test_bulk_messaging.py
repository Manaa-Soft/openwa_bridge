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
        # v0.18: POST /messages/send-bulk returns 202 {batchId, statusUrl}; the
        # batch status is then read via GET /messages/batch/:batchId.
        mock_api.return_value = {
            "batchId": "batch-1",
            "status": "completed",
            "progress": {"total": 2, "sent": 2, "failed": 0, "pending": 0},
        }

        from openwa_bridge.whatsapp_account import send_bulk_openwa
        result = send_bulk_openwa("Test Account", "12345,67890", "Hello everyone")

        assert result["status"] == "ok"
        assert result["sent"] == 2
        assert result["failed"] == 0

        # Verify the v0.18 request body shape
        post_call = mock_api.call_args_list[0]
        assert post_call.args[1] == "POST"
        assert post_call.args[2] == "/messages/send-bulk"
        body = post_call.kwargs["json_data"]
        assert body["messages"] == [
            {"chatId": "12345@c.us", "type": "text", "content": {"text": "Hello everyone"}},
            {"chatId": "67890@c.us", "type": "text", "content": {"text": "Hello everyone"}},
        ]

    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_bulk_send_partial_failure(self, mock_api, mock_get):
        mock_get.return_value = _mock_account()
        mock_api.return_value = {
            "batchId": "batch-1",
            "status": "completed",
            "progress": {"total": 2, "sent": 1, "failed": 1, "pending": 0},
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
    @patch("openwa_bridge.whatsapp_account._resolve_message_chat_id")
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_forward_success(self, mock_api, mock_get, mock_resolve):
        mock_get.return_value = _mock_account()
        mock_resolve.return_value = "12345@c.us"
        mock_api.return_value = {"messageId": "new-msg-id"}

        from openwa_bridge.whatsapp_account import forward_message
        result = forward_message("Test Account", "original-msg-id", "67890@c.us")

        assert result["status"] == "ok"
        mock_api.assert_called_once_with(
            mock_get.return_value, "POST", "/messages/forward",
            json_data={
                "fromChatId": "12345@c.us",
                "toChatId": "67890@c.us",
                "messageId": "original-msg-id",
            },
        )

    @patch("openwa_bridge.whatsapp_account._resolve_message_chat_id")
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_forward_api_error(self, mock_api, mock_get, mock_resolve):
        mock_get.return_value = _mock_account()
        mock_resolve.return_value = "12345@c.us"
        mock_api.side_effect = Exception("Message not found")

        from openwa_bridge.whatsapp_account import forward_message
        result = forward_message("Test Account", "bad-id", "67890@c.us")

        assert result["status"] == "error"

    @patch("openwa_bridge.whatsapp_account._resolve_message_chat_id")
    @patch("openwa_bridge.whatsapp_account._get_account")
    def test_forward_unknown_source_chat(self, mock_get, mock_resolve):
        mock_get.return_value = _mock_account()
        mock_resolve.return_value = None

        from openwa_bridge.whatsapp_account import forward_message
        result = forward_message("Test Account", "orphan-id", "67890@c.us")

        assert result["status"] == "error"


class TestDeleteMessage:
    @patch("openwa_bridge.whatsapp_account._resolve_message_chat_id")
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_delete_self(self, mock_api, mock_get, mock_resolve):
        mock_get.return_value = _mock_account()
        mock_resolve.return_value = "12345@c.us"
        mock_api.return_value = {}

        from openwa_bridge.whatsapp_account import delete_message
        result = delete_message("Test Account", "msg-to-delete", revoke=0)

        assert result["status"] == "deleted"
        assert result["message_id"] == "msg-to-delete"
        mock_api.assert_called_once_with(
            mock_get.return_value, "POST", "/messages/delete",
            json_data={
                "chatId": "12345@c.us",
                "messageId": "msg-to-delete",
                "forEveryone": False,
            },
        )

    @patch("openwa_bridge.whatsapp_account._resolve_message_chat_id")
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_revoke_message(self, mock_api, mock_get, mock_resolve):
        mock_get.return_value = _mock_account()
        mock_resolve.return_value = "12345@c.us"
        mock_api.return_value = {}

        from openwa_bridge.whatsapp_account import delete_message
        result = delete_message("Test Account", "msg-to-revoke", revoke=1)

        assert result["status"] == "deleted"
        mock_api.assert_called_once_with(
            mock_get.return_value, "POST", "/messages/delete",
            json_data={
                "chatId": "12345@c.us",
                "messageId": "msg-to-revoke",
                "forEveryone": True,
            },
        )

    @patch("openwa_bridge.whatsapp_account._resolve_message_chat_id")
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_delete_api_error(self, mock_api, mock_get, mock_resolve):
        mock_get.return_value = _mock_account()
        mock_resolve.return_value = "12345@c.us"
        mock_api.side_effect = Exception("Not found")

        from openwa_bridge.whatsapp_account import delete_message
        result = delete_message("Test Account", "msg-404")

        assert result["status"] == "error"

    @patch("openwa_bridge.whatsapp_account._resolve_message_chat_id")
    @patch("openwa_bridge.whatsapp_account._get_account")
    @patch("openwa_bridge.whatsapp_account.openwa_api")
    def test_delete_unknown_chat(self, mock_api, mock_get, mock_resolve):
        mock_get.return_value = _mock_account()
        mock_resolve.return_value = None

        from openwa_bridge.whatsapp_account import delete_message
        result = delete_message("Test Account", "orphan-msg")

        assert result["status"] == "error"
        mock_api.assert_not_called()
