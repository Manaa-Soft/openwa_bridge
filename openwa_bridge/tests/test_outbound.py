"""Unit tests for outbound message routing and outbox processing."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock, PropertyMock

import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.tests.conftest import mock_openwa_api


class TestSendViaOpenwa(IntegrationTestCase):
    """Test OverrideWhatsAppMessage._send_via_openwa."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.whatsapp_message import OverrideWhatsAppMessage
        self.msg_class = OverrideWhatsAppMessage

    @patch("openwa_bridge.whatsapp_message._http_session")
    def test_send_text_message(self, mock_session):
        """Text message should POST to send-text endpoint."""
        mock_session.post.return_value = mock_openwa_api("POST", 200, {"key": {"id": "msg-123"}})

        mock_account = MagicMock()
        mock_account.get.return_value = "http://localhost:2785"
        mock_account.openwa_session_id = "session-001"
        mock_account.get_password.return_value = "api-key-123"

        instance = self.msg_class.__new__(self.msg_class)
        instance.name = "MSG-001"
        instance.to = "1234567890"
        instance.template = None
        instance.body_param = None
        instance.template_parameters = None

        instance._send_via_openwa(mock_account, {})

        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        self.assertIn("send-text", call_args[0][0])

    @patch("openwa_bridge.whatsapp_message._http_session")
    def test_send_template_message(self, mock_session):
        """Template message should POST to send-template endpoint."""
        mock_session.post.return_value = mock_openwa_api("POST", 200, {"key": {"id": "msg-456"}})

        mock_account = MagicMock()
        mock_account.get.return_value = "http://localhost:2785"
        mock_account.openwa_session_id = "session-001"
        mock_account.get_password.return_value = "api-key-123"

        instance = self.msg_class.__new__(self.msg_class)
        instance.name = "MSG-002"
        instance.to = "1234567890"
        instance.template = "welcome-template"
        instance.body_param = json.dumps(["John"])
        instance.template_parameters = None

        with patch("openwa_bridge.whatsapp_message.frappe") as mock_frappe:
            mock_frappe.db.get_value.return_value = "tmpl-uuid-123"
            instance._send_via_openwa(mock_account, {})

        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        self.assertIn("send-template", call_args[0][0])


class TestOutboxProcessing(IntegrationTestCase):
    """Test outbox processing logic."""

    def test_backoff_calculation(self):
        """Verify exponential backoff math."""
        test_cases = [
            (1, 30),     # 30 * 2^0 = 30
            (2, 60),     # 30 * 2^1 = 60
            (3, 120),    # 30 * 2^2 = 120
            (4, 240),    # 30 * 2^3 = 240
            (5, 480),    # 30 * 2^4 = 480
            (10, 3600),  # capped at 3600
        ]
        for attempts, expected in test_cases:
            backoff = min(30 * (2 ** (attempts - 1)), 3600)
            self.assertEqual(
                backoff, expected,
                f"Attempt {attempts}: expected {expected}s, got {backoff}s",
            )

    @patch("openwa_bridge.tasks.frappe")
    def test_fail_outbox_with_account(self, mock_frappe):
        """_fail_outbox should use account setting for max_attempts."""
        from openwa_bridge.tasks import _fail_outbox

        mock_outbox = MagicMock()
        mock_outbox.attempts = 3
        mock_outbox.max_attempts = 5
        mock_frappe.get_doc.return_value = mock_outbox

        mock_account = MagicMock()
        mock_account.openwa_max_outbox_attempts = 10

        _fail_outbox("OUTBOX-001", "Test error", account=mock_account)

        # Should set Pending since 3 < 10
        mock_frappe.db.set_value.assert_called_once()
        call_args = mock_frappe.db.set_value.call_args
        self.assertEqual(call_args[0][2]["status"], "Pending")

    @patch("openwa_bridge.tasks.frappe")
    def test_fail_outbox_exhausted(self, mock_frappe):
        """_fail_outbox should mark Failed when max_attempts reached."""
        from openwa_bridge.tasks import _fail_outbox

        mock_outbox = MagicMock()
        mock_outbox.attempts = 5
        mock_outbox.max_attempts = 5
        mock_frappe.get_doc.return_value = mock_outbox

        mock_account = MagicMock()
        mock_account.openwa_max_outbox_attempts = 5

        _fail_outbox("OUTBOX-002", "Test error", account=mock_account)

        call_args = mock_frappe.db.set_value.call_args
        self.assertEqual(call_args[0][2]["status"], "Failed")


class TestDynamicHeaderOutbox(IntegrationTestCase):
    """Test _send_dynamic_header_for_outbox."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.tasks import _send_dynamic_header_for_outbox
        self.handler = _send_dynamic_header_for_outbox

    @patch("openwa_bridge.tasks.frappe")
    def test_no_template_returns_false(self, mock_frappe):
        """Should return False when msg has no template."""
        msg = MagicMock()
        msg.template = None
        account = MagicMock()

        result = self.handler(msg, account)
        self.assertFalse(result)

    @patch("openwa_bridge.tasks.frappe")
    def test_no_dynamic_header_returns_false(self, mock_frappe):
        """Should return False when template has no dynamic header."""
        msg = MagicMock()
        msg.template = "test-template"
        account = MagicMock()

        mock_tmpl = MagicMock()
        mock_tmpl.openwa_dynamic_header = False
        mock_frappe.get_doc.return_value = mock_tmpl

        result = self.handler(msg, account)
        self.assertFalse(result)

    @patch("openwa_bridge.tasks.frappe")
    def test_no_reference_doc_returns_false(self, mock_frappe):
        """Should return False when message has no reference doc."""
        msg = MagicMock()
        msg.template = "test-template"
        msg.reference_doctype = None
        msg.reference_name = None
        account = MagicMock()

        mock_tmpl = MagicMock()
        mock_tmpl.openwa_dynamic_header = True
        mock_tmpl.openwa_print_format = "Standard"
        mock_frappe.get_doc.return_value = mock_tmpl

        result = self.handler(msg, account)
        self.assertFalse(result)
