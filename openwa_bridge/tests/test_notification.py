"""Unit tests for WhatsApp Notification override."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.tests.conftest import mock_openwa_api


class TestIsOpenwaAccount(IntegrationTestCase):
    """Test the _is_openwa_account wrapper."""

    @patch("openwa_bridge.whatsapp_notification.frappe")
    def test_returns_true_when_enabled(self, mock_frappe):
        from openwa_bridge.whatsapp_notification import _is_openwa_account
        mock_frappe.db.get_value.return_value = 1
        self.assertTrue(_is_openwa_account("test-account"))

    @patch("openwa_bridge.whatsapp_notification.frappe")
    def test_returns_false_when_none(self, mock_frappe):
        from openwa_bridge.whatsapp_notification import _is_openwa_account
        self.assertFalse(_is_openwa_account(None))


class TestSendTemplateMessage(IntegrationTestCase):
    """Test OverrideWhatsAppNotification.send_template_message."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.whatsapp_notification import OverrideWhatsAppNotification
        self.notif_class = OverrideWhatsAppNotification

    @patch("openwa_bridge.whatsapp_notification.frappe")
    def test_openwa_send_type_empty_returns(self, mock_frappe):
        """Empty openwa_send_type should return silently."""
        mock_notif = MagicMock()
        mock_notif.openwa_send_type = ""

        # Should not raise
        self.notif_class.send_template_message(mock_notif, {})


class TestOpenwaTemplateFlow(IntegrationTestCase):
    """Test _send_openwa_template flow."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.whatsapp_notification import OverrideWhatsAppNotification
        self.notif_class = OverrideWhatsAppNotification

    @patch("openwa_bridge.whatsapp_notification.frappe")
    def test_missing_template_logs_error(self, mock_frappe):
        """Missing template should log error."""
        mock_frappe.db.get_value.return_value = None

        instance = self.notif_class.__new__(self.notif_class)
        instance.template = "nonexistent-template"
        instance.openwa_dynamic_header = 0
        instance.openwa_print_format = None

        # Should not raise
        result = instance._send_openwa_template(
            MagicMock(), MagicMock(), "1234567890", {}
        )


class TestOpenwaTextFlow(IntegrationTestCase):
    """Test _send_openwa_text flow."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.whatsapp_notification import OverrideWhatsAppNotification
        self.notif_class = OverrideWhatsAppNotification

    @patch("openwa_bridge.whatsapp_notification._http_session")
    @patch("openwa_bridge.whatsapp_notification.frappe")
    def test_sends_text_message(self, mock_frappe, mock_session):
        """Should POST to send-text endpoint."""
        mock_session.post.return_value = mock_openwa_api("POST", 200, {"key": {"id": "msg-789"}})

        mock_account = MagicMock()
        mock_account.get.return_value = "http://localhost:2785"
        mock_account.openwa_session_id = "session-001"
        mock_account.get_password.return_value = "api-key-123"

        instance = self.notif_class.__new__(self.notif_class)

        instance._send_openwa_text(mock_account, "1234567890", "Hello World")

        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        self.assertIn("send-text", call_args[0][0])

    @patch("openwa_bridge.whatsapp_notification._http_session")
    @patch("openwa_bridge.whatsapp_notification.frappe")
    def test_send_failure_logs_error(self, mock_frappe, mock_session):
        """Send failure should log error."""
        mock_session.post.return_value = mock_openwa_api("POST", 500, {"error": "fail"})

        mock_account = MagicMock()
        mock_account.get.return_value = "http://localhost:2785"
        mock_account.openwa_session_id = "session-001"
        mock_account.get_password.return_value = "api-key-123"

        instance = self.notif_class.__new__(self.notif_class)

        instance._send_openwa_text(mock_account, "1234567890", "Hello")

        mock_frappe.log_error.assert_called()
