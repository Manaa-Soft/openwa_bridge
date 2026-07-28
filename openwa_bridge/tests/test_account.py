"""Unit tests for WhatsApp Account helpers — QR, session, webhooks."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.tests.conftest import mock_openwa_api


class TestSanitizeSessionName(IntegrationTestCase):
    """Test _sanitize_session_name helper."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.whatsapp_account import _sanitize_session_name
        self.sanitizer = _sanitize_session_name

    def test_normal_name(self):
        """Should lowercase and slugify."""
        self.assertEqual(self.sanitizer("Test Account"), "test-account")

    def test_special_chars(self):
        """Should replace special chars with hyphens."""
        self.assertEqual(self.sanitizer("My Account! @#"), "my-account---")

    def test_short_name_padded(self):
        """Should pad names shorter than 3 chars."""
        result = self.sanitizer("ab")
        self.assertGreaterEqual(len(result), 3)

    def test_long_name_truncated(self):
        """Should truncate names longer than 50 chars."""
        long_name = "a" * 100
        result = self.sanitizer(long_name)
        self.assertLessEqual(len(result), 50)

    def test_leading_trailing_hyphens_stripped(self):
        """Should strip leading/trailing hyphens."""
        result = self.sanitizer("--test--")
        self.assertFalse(result.startswith("-"))
        self.assertFalse(result.endswith("-"))


class TestOnAccountUpdate(IntegrationTestCase):
    """Test on_account_update webhook sync."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.whatsapp_account import on_account_update
        self.handler = on_account_update

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_skips_when_disabled(self, mock_frappe, mock_api):
        """Should skip when openwa_enabled is False."""
        mock_doc = MagicMock()
        mock_doc.openwa_enabled = 0
        self.handler(mock_doc, "on_update")
        mock_api.assert_not_called()

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_skips_without_session_id(self, mock_frappe, mock_api):
        """Should skip when no session_id."""
        mock_doc = MagicMock()
        mock_doc.openwa_enabled = 1
        mock_doc.openwa_session_id = None
        self.handler(mock_doc, "on_update")
        mock_api.assert_not_called()

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_creates_webhook_when_none_exists(self, mock_frappe, mock_api):
        """Should create webhook when none exists for our URL."""
        mock_doc = MagicMock()
        mock_doc.openwa_enabled = 1
        mock_doc.openwa_session_id = "session-001"
        mock_doc.get_password.return_value = "secret-123"
        mock_doc.name = "test-account"

        mock_frappe.utils.get_url.return_value = "https://example.com/api/method/openwa_bridge.inbound.receive_openwa_message"

        # First call: GET /webhooks returns empty list
        # Second call: POST /webhooks creates webhook
        mock_api.side_effect = [
            [],  # GET webhooks
            {},  # POST webhook
        ]

        self.handler(mock_doc, "on_update")

        self.assertEqual(mock_api.call_count, 2)
        post_call = mock_api.call_args_list[1]
        self.assertEqual(post_call[0][1], "POST")

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_updates_existing_webhook(self, mock_frappe, mock_api):
        """Should update secret when webhook already exists."""
        mock_doc = MagicMock()
        mock_doc.openwa_enabled = 1
        mock_doc.openwa_session_id = "session-001"
        mock_doc.get_password.return_value = "new-secret-456"
        mock_doc.name = "test-account"

        mock_frappe.utils.get_url.return_value = "https://example.com/api/method/openwa_bridge.inbound.receive_openwa_message"

        existing_webhook = {
            "id": "wh-001",
            "url": "https://example.com/api/method/openwa_bridge.inbound.receive_openwa_message",
        }

        mock_api.side_effect = [
            [existing_webhook],  # GET webhooks
            {},  # PUT webhook
        ]

        self.handler(mock_doc, "on_update")

        self.assertEqual(mock_api.call_count, 2)
        put_call = mock_api.call_args_list[1]
        self.assertEqual(put_call[0][1], "PUT")
        self.assertIn("wh-001", put_call[0][2])


class TestOnAccountTrash(IntegrationTestCase):
    """Test on_account_trash session cleanup."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.whatsapp_account import on_account_trash
        self.handler = on_account_trash

    @patch("openwa_bridge.whatsapp_account._raw_openwa_call")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_deletes_session(self, mock_frappe, mock_raw_call):
        """Should delete session from OpenWA when account is trashed."""
        mock_doc = MagicMock()
        mock_doc.openwa_enabled = 1
        mock_doc.openwa_session_id = "session-001"

        self.handler(mock_doc, "on_trash")

        mock_raw_call.assert_called_once_with(
            mock_doc, "DELETE", "/api/sessions/session-001"
        )

    @patch("openwa_bridge.whatsapp_account._raw_openwa_call")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_skips_when_disabled(self, mock_frappe, mock_raw_call):
        """Should skip when openwa_enabled is False."""
        mock_doc = MagicMock()
        mock_doc.openwa_enabled = 0

        self.handler(mock_doc, "on_trash")

        mock_raw_call.assert_not_called()

    @patch("openwa_bridge.whatsapp_account._raw_openwa_call")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_error_does_not_crash(self, mock_frappe, mock_raw_call):
        """OpenWA error should not crash the delete."""
        mock_doc = MagicMock()
        mock_doc.openwa_enabled = 1
        mock_doc.openwa_session_id = "session-001"
        mock_raw_call.side_effect = Exception("OpenWA unreachable")

        # Should not raise
        self.handler(mock_doc, "on_trash")

        mock_frappe.log_error.assert_called()


class TestEditMessage(IntegrationTestCase):
    """Test edit_message whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_edit_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should POST to /messages/edit with chatId, messageId, body."""
        from openwa_bridge.whatsapp_account import edit_message

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("POST", 200)

        result = edit_message(
            account_name="test-account",
            chat_id="1234567890@c.us",
            message_id="msg-001",
            body="Updated text",
        )

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "POST")
        self.assertIn("/messages/edit", call_args[0][2])
        self.assertEqual(call_args[1]["json"]["body"], "Updated text")


class TestPostStatusText(IntegrationTestCase):
    """Test post_status_text whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_post_status_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should POST to /status/send-text."""
        from openwa_bridge.whatsapp_account import post_status_text

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("POST", 200)

        result = post_status_text(
            account_name="test-account",
            text="Hello World!",
            background_color="#FF5722",
            font="serif",
        )

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "POST")
        self.assertIn("/status/send-text", call_args[0][2])
        self.assertEqual(call_args[1]["json"]["text"], "Hello World!")


class TestRejectCall(IntegrationTestCase):
    """Test reject_call whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_reject_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should POST to /calls/{callId}/reject."""
        from openwa_bridge.whatsapp_account import reject_call

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("POST", 200)

        result = reject_call(
            account_name="test-account",
            call_id="call-001",
        )

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "POST")
        self.assertIn("/calls/call-001/reject", call_args[0][2])


class TestMarkChatRead(IntegrationTestCase):
    """Test mark_chat_read whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_mark_read_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should POST to /chats/{chatId}/read."""
        from openwa_bridge.whatsapp_account import mark_chat_read

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("POST", 200)

        result = mark_chat_read(
            account_name="test-account",
            chat_id="1234567890@c.us",
        )

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "POST")
        self.assertIn("/chats/1234567890@c.us/read", call_args[0][2])


class TestListGroups(IntegrationTestCase):
    """Test list_groups whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_list_groups_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /groups."""
        from openwa_bridge.whatsapp_account import list_groups

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, [{"id": "group-001"}])

        result = list_groups(account_name="test-account")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/groups", call_args[0][2])


class TestSetProfileName(IntegrationTestCase):
    """Test set_profile_name whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_set_profile_name_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should PUT /profile/name."""
        from openwa_bridge.whatsapp_account import set_profile_name

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("PUT", 200)

        result = set_profile_name(
            account_name="test-account",
            name="My Business",
        )

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "PUT")
        self.assertIn("/profile/name", call_args[0][2])
        self.assertEqual(call_args[1]["json"]["name"], "My Business")


class TestSearchMessages(IntegrationTestCase):
    """Test search_messages whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_search_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /search with query param."""
        from openwa_bridge.whatsapp_account import search_messages

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, [])

        result = search_messages(
            account_name="test-account",
            query="hello",
            limit=50,
        )

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/search", call_args[0][2])


class TestGetSessionStats(IntegrationTestCase):
    """Test get_session_stats whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_stats_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /stats."""
        from openwa_bridge.whatsapp_account import get_session_stats

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, {"sent": 100})

        result = get_session_stats(account_name="test-account")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/stats", call_args[0][2])


class TestGetContactStatuses(IntegrationTestCase):
    """Test get_contact_statuses whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /status/:contactId."""
        from openwa_bridge.whatsapp_account import get_contact_statuses

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, [])

        result = get_contact_statuses(account_name="test-account", contact_id="1234567890@c.us")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/status/1234567890@c.us", call_args[0][2])


class TestGetStatusMedia(IntegrationTestCase):
    """Test get_status_media whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /status/:statusId/media."""
        from openwa_bridge.whatsapp_account import get_status_media

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, {})

        result = get_status_media(account_name="test-account", status_id="status-001")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/status/status-001/media", call_args[0][2])


class TestSubscribeChannel(IntegrationTestCase):
    """Test subscribe_channel whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should POST /channels/subscribe."""
        from openwa_bridge.whatsapp_account import subscribe_channel

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("POST", 201)

        result = subscribe_channel(account_name="test-account", invite_code="ABC123")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "POST")
        self.assertIn("/channels/subscribe", call_args[0][2])
        self.assertEqual(call_args[1]["json_data"]["inviteCode"], "ABC123")


class TestUnsubscribeChannel(IntegrationTestCase):
    """Test unsubscribe_channel whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should DELETE /channels/:channelId."""
        from openwa_bridge.whatsapp_account import unsubscribe_channel

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("DELETE", 200)

        result = unsubscribe_channel(account_name="test-account", channel_id="channel-001")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "DELETE")
        self.assertIn("/channels/channel-001", call_args[0][2])


class TestGetMessageReactions(IntegrationTestCase):
    """Test get_message_reactions whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /messages/:chatId/:messageId/reactions."""
        from openwa_bridge.whatsapp_account import get_message_reactions

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, [])

        result = get_message_reactions(
            account_name="test-account",
            chat_id="1234567890@c.us",
            message_id="msg-001",
        )

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/messages/1234567890@c.us/msg-001/reactions", call_args[0][2])


class TestCancelBatch(IntegrationTestCase):
    """Test cancel_batch whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should POST /messages/batch/:batchId/cancel."""
        from openwa_bridge.whatsapp_account import cancel_batch

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("POST", 200)

        result = cancel_batch(account_name="test-account", batch_id="batch-001")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "POST")
        self.assertIn("/messages/batch/batch-001/cancel", call_args[0][2])


class TestGetOverviewStats(IntegrationTestCase):
    """Test get_overview_stats whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /stats/overview."""
        from openwa_bridge.whatsapp_account import get_overview_stats

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, {})

        result = get_overview_stats(account_name="test-account")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/stats/overview", call_args[0][2])


class TestGetMessageStats(IntegrationTestCase):
    """Test get_message_stats whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint_with_period(self, mock_frappe, mock_api):
        """Should GET /stats/messages with period param."""
        from openwa_bridge.whatsapp_account import get_message_stats

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, {})

        result = get_message_stats(account_name="test-account", period="7d")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/stats/messages?period=7d", call_args[0][2])


# ---------------------------------------------------------------------------
# Group enhancement tests
# ---------------------------------------------------------------------------


class TestGetGroup(IntegrationTestCase):
    """Test get_group whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /groups/:groupId."""
        from openwa_bridge.whatsapp_account import get_group

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, {})

        result = get_group(account_name="test-account", group_id="group-001@g.us")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/groups/group-001@g.us", call_args[0][2])


class TestJoinGroupByCode(IntegrationTestCase):
    """Test join_group_by_code whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should POST /groups/join with inviteCode."""
        from openwa_bridge.whatsapp_account import join_group_by_code

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("POST", 201)

        result = join_group_by_code(account_name="test-account", invite_code="https://chat.whatsapp.com/ABC123")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "POST")
        self.assertIn("/groups/join", call_args[0][2])
        self.assertEqual(call_args[1]["json_data"]["inviteCode"], "https://chat.whatsapp.com/ABC123")


class TestGetGroupSettings(IntegrationTestCase):
    """Test get_group_settings whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /groups/:groupId/settings."""
        from openwa_bridge.whatsapp_account import get_group_settings

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, {})

        result = get_group_settings(account_name="test-account", group_id="group-001@g.us")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/groups/group-001@g.us/settings", call_args[0][2])


class TestSetGroupSettings(IntegrationTestCase):
    """Test set_group_settings whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should PUT /groups/:groupId/settings."""
        from openwa_bridge.whatsapp_account import set_group_settings

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("PUT", 200)

        result = set_group_settings(
            account_name="test-account",
            group_id="group-001@g.us",
            settings={"announce": True},
        )

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "PUT")
        self.assertIn("/groups/group-001@g.us/settings", call_args[0][2])
        self.assertEqual(call_args[1]["json_data"]["announce"], True)


class TestSetGroupDescription(IntegrationTestCase):
    """Test set_group_description whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should PUT /groups/:groupId/description."""
        from openwa_bridge.whatsapp_account import set_group_description

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("PUT", 200)

        result = set_group_description(
            account_name="test-account",
            group_id="group-001@g.us",
            description="Welcome to our group!",
        )

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "PUT")
        self.assertIn("/groups/group-001@g.us/description", call_args[0][2])
        self.assertEqual(call_args[1]["json_data"]["description"], "Welcome to our group!")


class TestGetGroupInviteCode(IntegrationTestCase):
    """Test get_group_invite_code whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /groups/:groupId/invite-code."""
        from openwa_bridge.whatsapp_account import get_group_invite_code

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, {})

        result = get_group_invite_code(account_name="test-account", group_id="group-001@g.us")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/groups/group-001@g.us/invite-code", call_args[0][2])


class TestRevokeGroupInviteCode(IntegrationTestCase):
    """Test revoke_group_invite_code whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should POST /groups/:groupId/invite-code/revoke."""
        from openwa_bridge.whatsapp_account import revoke_group_invite_code

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("POST", 200)

        result = revoke_group_invite_code(account_name="test-account", group_id="group-001@g.us")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "POST")
        self.assertIn("/groups/group-001@g.us/invite-code/revoke", call_args[0][2])


# ---------------------------------------------------------------------------
# Contact enhancement tests
# ---------------------------------------------------------------------------


class TestListContacts(IntegrationTestCase):
    """Test list_contacts whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /contacts."""
        from openwa_bridge.whatsapp_account import list_contacts

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, [])

        result = list_contacts(account_name="test-account")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/contacts", call_args[0][2])


class TestGetContact(IntegrationTestCase):
    """Test get_contact whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /contacts/:contactId."""
        from openwa_bridge.whatsapp_account import get_contact

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, {})

        result = get_contact(account_name="test-account", contact_id="1234567890@c.us")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/contacts/1234567890@c.us", call_args[0][2])


class TestGetContactProfilePicture(IntegrationTestCase):
    """Test get_contact_profile_picture whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /contacts/:contactId/profile-picture."""
        from openwa_bridge.whatsapp_account import get_contact_profile_picture

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, {})

        result = get_contact_profile_picture(
            account_name="test-account",
            contact_id="1234567890@c.us",
        )

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/contacts/1234567890@c.us/profile-picture", call_args[0][2])


class TestGetContactPhone(IntegrationTestCase):
    """Test get_contact_phone whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /contacts/:contactId/phone."""
        from openwa_bridge.whatsapp_account import get_contact_phone

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, {})

        result = get_contact_phone(
            account_name="test-account",
            contact_id="1234567890@c.us",
        )

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/contacts/1234567890@c.us/phone", call_args[0][2])


class TestListProfilePictures(IntegrationTestCase):
    """Test list_profile_pictures whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /contacts/profile-pictures."""
        from openwa_bridge.whatsapp_account import list_profile_pictures

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, {})

        result = list_profile_pictures(account_name="test-account")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/contacts/profile-pictures", call_args[0][2])


# ---------------------------------------------------------------------------
# Chat deletion test
# ---------------------------------------------------------------------------


class TestDeleteChat(IntegrationTestCase):
    """Test delete_chat whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should POST /chats/delete."""
        from openwa_bridge.whatsapp_account import delete_chat

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("POST", 200)

        result = delete_chat(account_name="test-account", chat_id="1234567890@c.us")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "POST")
        self.assertIn("/chats/delete", call_args[0][2])
        self.assertEqual(call_args[1]["json_data"]["chatId"], "1234567890@c.us")


# ---------------------------------------------------------------------------
# Status deletion test
# ---------------------------------------------------------------------------


class TestDeleteStatus(IntegrationTestCase):
    """Test delete_status whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should DELETE /status/:statusId."""
        from openwa_bridge.whatsapp_account import delete_status

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("DELETE", 200)

        result = delete_status(account_name="test-account", status_id="status-001")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "DELETE")
        self.assertIn("/status/status-001", call_args[0][2])


# ---------------------------------------------------------------------------
# Label enhancement tests
# ---------------------------------------------------------------------------


class TestGetLabel(IntegrationTestCase):
    """Test get_label whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /labels/:labelId."""
        from openwa_bridge.whatsapp_account import get_label

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, {})

        result = get_label(account_name="test-account", label_id="label-001")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/labels/label-001", call_args[0][2])


class TestGetChatLabels(IntegrationTestCase):
    """Test get_chat_labels whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /labels/chat/:chatId."""
        from openwa_bridge.whatsapp_account import get_chat_labels

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, [])

        result = get_chat_labels(account_name="test-account", chat_id="1234567890@c.us")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/labels/chat/1234567890@c.us", call_args[0][2])


# ---------------------------------------------------------------------------
# Batch status test
# ---------------------------------------------------------------------------


class TestGetBatchStatus(IntegrationTestCase):
    """Test get_batch_status whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /messages/batch/:batchId."""
        from openwa_bridge.whatsapp_account import get_batch_status

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 200, {})

        result = get_batch_status(account_name="test-account", batch_id="batch-001")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/messages/batch/batch-001", call_args[0][2])


# ---------------------------------------------------------------------------
# Webhook test
# ---------------------------------------------------------------------------


class TestTestWebhook(IntegrationTestCase):
    """Test test_webhook whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should POST /webhooks/:webhookId/test."""
        from openwa_bridge.whatsapp_account import test_webhook

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("POST", 200)

        result = test_webhook(account_name="test-account", webhook_id="wh-001")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "POST")
        self.assertIn("/webhooks/wh-001/test", call_args[0][2])


# ---------------------------------------------------------------------------
# Catalog tests (stub — 501 in current OpenWA)
# ---------------------------------------------------------------------------


class TestGetCatalog(IntegrationTestCase):
    """Test get_catalog whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /catalog."""
        from openwa_bridge.whatsapp_account import get_catalog

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 501)

        result = get_catalog(account_name="test-account")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/catalog", call_args[0][2])


class TestGetCatalogProducts(IntegrationTestCase):
    """Test get_catalog_products whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /catalog/products."""
        from openwa_bridge.whatsapp_account import get_catalog_products

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 501)

        result = get_catalog_products(account_name="test-account")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/catalog/products", call_args[0][2])


class TestGetCatalogProduct(IntegrationTestCase):
    """Test get_catalog_product whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should GET /catalog/products/:productId."""
        from openwa_bridge.whatsapp_account import get_catalog_product

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("GET", 501)

        result = get_catalog_product(account_name="test-account", product_id="prod-001")

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "GET")
        self.assertIn("/catalog/products/prod-001", call_args[0][2])


class TestSendProductMessage(IntegrationTestCase):
    """Test send_product_message whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should POST /messages/send-product."""
        from openwa_bridge.whatsapp_account import send_product_message

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("POST", 501)

        result = send_product_message(
            account_name="test-account",
            chat_id="1234567890@c.us",
            product_id="prod-001",
        )

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "POST")
        self.assertIn("/messages/send-product", call_args[0][2])
        self.assertEqual(call_args[1]["json_data"]["chatId"], "1234567890@c.us")
        self.assertEqual(call_args[1]["json_data"]["productId"], "prod-001")


class TestSendCatalogMessage(IntegrationTestCase):
    """Test send_catalog_message whitelisted method."""

    @patch("openwa_bridge.whatsapp_account.openwa_api")
    @patch("openwa_bridge.whatsapp_account.frappe")
    def test_calls_correct_endpoint(self, mock_frappe, mock_api):
        """Should POST /messages/send-catalog."""
        from openwa_bridge.whatsapp_account import send_catalog_message

        mock_frappe.get_doc.return_value = MagicMock(
            openwa_base_url="http://localhost:2785",
            openwa_session_id="session-001",
            get_password.return_value="api-key",
        )
        mock_frappe.has_permission.return_value = True
        mock_api.return_value = mock_openwa_api("POST", 501)

        result = send_catalog_message(
            account_name="test-account",
            chat_id="1234567890@c.us",
            catalog_id="catalog-001",
        )

        mock_api.assert_called_once()
        call_args = mock_api.call_args
        self.assertEqual(call_args[0][1], "POST")
        self.assertIn("/messages/send-catalog", call_args[0][2])
        self.assertEqual(call_args[1]["json_data"]["chatId"], "1234567890@c.us")
        self.assertEqual(call_args[1]["json_data"]["catalogId"], "catalog-001")
