"""Unit tests for WhatsApp Templates override — sync to OpenWA."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.tests.conftest import mock_openwa_api


class TestIsOpenwaAccountTemplates(IntegrationTestCase):
    """Test the is_openwa_account helper used in templates module."""

    @patch("openwa_bridge.utils.frappe")
    def test_returns_true_when_enabled(self, mock_frappe):
        from openwa_bridge.utils import is_openwa_account
        mock_frappe.db.get_value.return_value = 1
        self.assertTrue(is_openwa_account("test-account"))

    @patch("openwa_bridge.utils.frappe")
    def test_returns_false_when_disabled(self, mock_frappe):
        from openwa_bridge.utils import is_openwa_account
        mock_frappe.db.get_value.return_value = 0
        self.assertFalse(is_openwa_account("test-account"))


class TestOverrideWhatsAppTemplates(IntegrationTestCase):
    """Test OverrideWhatsAppTemplates hooks."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.whatsapp_templates import OverrideWhatsAppTemplates
        self.tmpl_class = OverrideWhatsAppTemplates

    @patch("openwa_bridge.whatsapp_templates.frappe")
    def test_validate_sets_actual_name(self, mock_frappe):
        """validate() should set actual_name from template_name for OpenWA accounts."""
        mock_frappe.db.get_value.return_value = 1  # is_openwa_account = True

        instance = self.tmpl_class.__new__(self.tmpl_class)
        instance.whatsapp_account = "test-account"
        instance.language_code = "en"
        instance.language = "English"
        instance.has_value_changed.return_value = False
        instance.actual_name = None
        instance.template_name = "Welcome Message"

        with patch.object(self.tmpl_class, "set_whatsapp_account"):
            instance.validate()

        self.assertEqual(instance.actual_name, "welcome_message")
        self.assertEqual(instance.status, "APPROVED")

    @patch("openwa_bridge.whatsapp_templates.frappe")
    def test_validate_preserves_existing_actual_name(self, mock_frappe):
        """validate() should not overwrite existing actual_name."""
        mock_frappe.db.get_value.return_value = 1

        instance = self.tmpl_class.__new__(self.tmpl_class)
        instance.whatsapp_account = "test-account"
        instance.language_code = "en"
        instance.language = "English"
        instance.has_value_changed.return_value = False
        instance.actual_name = "existing_name"
        instance.template_name = "New Name"

        with patch.object(self.tmpl_class, "set_whatsapp_account"):
            instance.validate()

        self.assertEqual(instance.actual_name, "existing_name")
