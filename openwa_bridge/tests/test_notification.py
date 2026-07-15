"""Unit tests for WhatsApp Notification override."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.utils import is_openwa_account


class TestIsOpenwaAccount(IntegrationTestCase):
    """Test the is_openwa_account helper."""

    @patch("openwa_bridge.utils.frappe")
    def test_returns_true_when_enabled(self, mock_frappe):
        mock_frappe.db.get_value.return_value = 1
        self.assertTrue(is_openwa_account("test-account"))

    def test_returns_false_when_none(self):
        self.assertFalse(is_openwa_account(None))

    @patch("openwa_bridge.utils.frappe")
    def test_returns_false_when_disabled(self, mock_frappe):
        mock_frappe.db.get_value.return_value = 0
        self.assertFalse(is_openwa_account("test-account"))


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
        mock_notif.whatsapp_account = None
        mock_notif.template = None

        self.notif_class.send_template_message(mock_notif, {})


class TestOpenwaTextFlow(IntegrationTestCase):
    """Test _send_openwa_text creates a WhatsApp Message doc."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.whatsapp_notification import OverrideWhatsAppNotification
        self.notif_class = OverrideWhatsAppNotification

    @patch("openwa_bridge.whatsapp_notification.frappe")
    def test_creates_whatsapp_message_doc(self, mock_frappe):
        """Should create a WhatsApp Message doc via frappe.get_doc().insert()."""
        mock_insert = MagicMock()
        mock_doc = MagicMock()
        mock_doc.insert = mock_insert
        mock_frappe.get_doc.return_value = mock_doc

        account = MagicMock()
        account.name = "test-account"

        data = {"to": "1234567890"}

        instance = self.notif_class.__new__(self.notif_class)
        instance.template = "test-template"

        instance._send_openwa_text(account, data, "Hello World")

        mock_frappe.get_doc.assert_called_once()
        call_args = mock_frappe.get_doc.call_args[0][0]
        self.assertEqual(call_args["doctype"], "WhatsApp Message")
        self.assertEqual(call_args["type"], "Outgoing")
        self.assertEqual(call_args["message"], "Hello World")
        self.assertEqual(call_args["to"], "1234567890")
        self.assertEqual(call_args["content_type"], "text")
        self.assertEqual(call_args["whatsapp_account"], "test-account")
        mock_insert.assert_called_once_with(ignore_permissions=True)

    @patch("openwa_bridge.whatsapp_notification.frappe")
    def test_includes_reference_when_doc_data(self, mock_frappe):
        """Should include reference_doctype/name when doc_data provided."""
        mock_insert = MagicMock()
        mock_doc = MagicMock()
        mock_doc.insert = mock_insert
        mock_frappe.get_doc.return_value = mock_doc

        account = MagicMock()
        account.name = "test-account"

        data = {"to": "1234567890"}
        doc_data = {"doctype": "Sales Invoice", "name": "SI-001"}

        instance = self.notif_class.__new__(self.notif_class)
        instance.template = None

        instance._send_openwa_text(account, data, "Hello", doc_data)

        call_args = mock_frappe.get_doc.call_args[0][0]
        self.assertEqual(call_args["reference_doctype"], "Sales Invoice")
        self.assertEqual(call_args["reference_name"], "SI-001")

    @patch("openwa_bridge.whatsapp_notification.frappe")
    def test_logs_error_on_failure(self, mock_frappe):
        """Insert failure should log error, not raise."""
        mock_frappe.get_doc.side_effect = Exception("DB error")

        account = MagicMock()
        account.name = "test-account"
        data = {"to": "1234567890"}

        instance = self.notif_class.__new__(self.notif_class)
        instance.template = None

        # Should not raise
        instance._send_openwa_text(account, data, "Hello")

        mock_frappe.log_error.assert_called_once()


class TestOpenwaTemplateFlow(IntegrationTestCase):
    """Test _send_openwa_template creates a WhatsApp Message doc."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.whatsapp_notification import OverrideWhatsAppNotification
        self.notif_class = OverrideWhatsAppNotification

    @patch("openwa_bridge.whatsapp_notification.frappe")
    def test_creates_whatsapp_message_doc(self, mock_frappe):
        """Should create a WhatsApp Message doc with use_template=1."""
        mock_insert = MagicMock()
        mock_doc = MagicMock()
        mock_doc.insert = mock_insert
        mock_frappe.get_doc.return_value = mock_doc

        account = MagicMock()
        account.name = "test-account"
        data = {"to": "1234567890"}

        instance = self.notif_class.__new__(self.notif_class)
        instance.template = "welcome-template"
        instance.code = None
        instance.fields = None
        instance.set_property_after_alert = None
        instance.property_value = None

        instance._send_openwa_template(account, data)

        call_args = mock_frappe.get_doc.call_args[0][0]
        self.assertEqual(call_args["doctype"], "WhatsApp Message")
        self.assertEqual(call_args["use_template"], 1)
        self.assertEqual(call_args["template"], "welcome-template")
        self.assertEqual(call_args["whatsapp_account"], "test-account")
        mock_insert.assert_called_once_with(ignore_permissions=True)

    @patch("openwa_bridge.whatsapp_notification.frappe")
    def test_logs_error_on_failure(self, mock_frappe):
        """Insert failure should log error, not raise."""
        mock_frappe.get_doc.side_effect = Exception("DB error")

        account = MagicMock()
        account.name = "test-account"
        data = {"to": "1234567890"}

        instance = self.notif_class.__new__(self.notif_class)
        instance.template = "welcome-template"
        instance.code = None
        instance.fields = None

        # Should not raise
        instance._send_openwa_template(account, data)

        mock_frappe.log_error.assert_called_once()
