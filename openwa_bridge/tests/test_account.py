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
