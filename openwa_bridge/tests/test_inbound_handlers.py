"""Unit tests for inbound webhook handlers."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.tests.conftest import (
    make_message_data,
    make_status_data,
    make_session_status_data,
    make_webhook_payload,
    sign_payload,
    mock_openwa_api,
)


class TestHandleStatusUpdate(IntegrationTestCase):
    """Test _handle_status_update handler."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _handle_status_update
        self.handler = _handle_status_update

    @patch("openwa_bridge.inbound.frappe")
    def test_valid_status_updates_message(self, mock_frappe):
        """Valid status should update the WhatsApp Message doc."""
        mock_frappe.db.get_value.return_value = "MSG-001"
        mock_frappe.db.set_value.return_value = None

        self.handler(make_status_data("msg-001", "DELIVERED"))

        mock_frappe.db.set_value.assert_called_once_with(
            "WhatsApp Message", "MSG-001", "status", "Delivered"
        )

    @patch("openwa_bridge.inbound.frappe")
    def test_missing_message_id_noop(self, mock_frappe):
        """Missing message_id should not crash."""
        self.handler({"status": "DELIVERED"})
        mock_frappe.db.get_value.assert_not_called()

    @patch("openwa_bridge.inbound.frappe")
    def test_missing_status_noop(self, mock_frappe):
        """Missing status should not crash."""
        self.handler({"messageId": "msg-001"})
        mock_frappe.db.get_value.assert_not_called()

    @patch("openwa_bridge.inbound.frappe")
    def test_unknown_message_noop(self, mock_frappe):
        """Unknown message_id should not crash."""
        mock_frappe.db.get_value.return_value = None
        self.handler(make_status_data("unknown-msg", "READ"))
        mock_frappe.db.set_value.assert_not_called()


class TestHandleSessionStatus(IntegrationTestCase):
    """Test _handle_session_status handler."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _handle_session_status
        self.handler = _handle_session_status

    @patch("openwa_bridge.inbound.frappe")
    def test_ready_sets_active(self, mock_frappe):
        """ready status should set Active."""
        mock_frappe.db.get_value.return_value = "Test Account"
        mock_frappe.db.set_value.return_value = None

        self.handler(make_session_status_data("ready"), "session-001")

        mock_frappe.db.set_value.assert_called_once_with(
            "WhatsApp Account", "Test Account", "status", "Active"
        )

    @patch("openwa_bridge.inbound.frappe")
    def test_disconnected_sets_inactive(self, mock_frappe):
        """disconnected status should set Inactive."""
        mock_frappe.db.get_value.return_value = "Test Account"
        self.handler(make_session_status_data("disconnected"), "session-001")
        mock_frappe.db.set_value.assert_called_once_with(
            "WhatsApp Account", "Test Account", "status", "Inactive"
        )

    @patch("openwa_bridge.inbound.frappe")
    def test_unknown_status_noop(self, mock_frappe):
        """Unknown status should not update anything."""
        self.handler(make_session_status_data("connecting"), "session-001")
        mock_frappe.db.get_value.assert_not_called()

    @patch("openwa_bridge.inbound.frappe")
    def test_empty_status_noop(self, mock_frappe):
        """Empty status should not crash."""
        self.handler({}, "session-001")
        mock_frappe.db.get_value.assert_not_called()

    @patch("openwa_bridge.inbound.frappe")
    def test_unknown_account_noop(self, mock_frappe):
        """Unknown session_id should not crash."""
        mock_frappe.db.get_value.return_value = None
        self.handler(make_session_status_data("ready"), "unknown-session")
        mock_frappe.db.set_value.assert_not_called()


class TestResolveAccountBySession(IntegrationTestCase):
    """Test _resolve_account_by_session."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _resolve_account_by_session
        self.resolver = _resolve_account_by_session

    @patch("openwa_bridge.inbound.frappe")
    def test_found_by_session(self, mock_frappe):
        """Should find account by openwa_session_id."""
        mock_frappe.db.get_value.return_value = "Test Account"
        mock_frappe.get_doc.return_value = MagicMock(name="Test Account")

        result = self.resolver("session-001")

        self.assertIsNotNone(result)
        mock_frappe.db.get_value.assert_called_once()

    @patch("openwa_bridge.inbound.frappe")
    def test_not_found_fallback(self, mock_frappe):
        """Should fallback to get_whatsapp_account when not found."""
        mock_frappe.db.get_value.return_value = None
        with patch("openwa_bridge.inbound.get_whatsapp_account") as mock_get:
            mock_get.return_value = MagicMock(name="Fallback Account")
            result = self.resolver("unknown-session")
            self.assertIsNotNone(result)

    @patch("openwa_bridge.inbound.frappe")
    def test_empty_session_returns_none(self, mock_frappe):
        """Empty session_id should return None."""
        result = self.resolver("")
        self.assertIsNone(result)

    def test_none_session_returns_none(self):
        """None session_id should return None."""
        result = self.resolver(None)
        self.assertIsNone(result)


class TestEnsureWhatsAppProfile(IntegrationTestCase):
    """Test _ensure_whatsapp_profile."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _ensure_whatsapp_profile
        self.handler = _ensure_whatsapp_profile

    @patch("openwa_bridge.inbound.frappe")
    @patch("openwa_bridge.inbound.format_number")
    def test_new_profile_created(self, mock_format, mock_frappe):
        """Should create profile when it doesn't exist."""
        mock_format.return_value = "1234567890"
        mock_frappe.db.exists.return_value = False
        mock_frappe.get_doc.return_value = MagicMock()
        mock_frappe.get_doc.return_value.insert.return_value = None

        self.handler("1234567890", "Test User", "test-account")

        mock_frappe.get_doc.assert_called_once()
        mock_frappe.get_doc.return_value.insert.assert_called_once()

    @patch("openwa_bridge.inbound.frappe")
    @patch("openwa_bridge.inbound.format_number")
    def test_existing_profile_updates_name(self, mock_format, mock_frappe):
        """Should update profile_name when profile exists."""
        mock_format.return_value = "1234567890"
        mock_frappe.db.exists.return_value = True

        self.handler("1234567890", "Updated Name", "test-account")

        mock_frappe.db.set_value.assert_called_once()

    @patch("openwa_bridge.inbound.frappe")
    @patch("openwa_bridge.inbound.format_number")
    def test_existing_profile_no_name_noop(self, mock_format, mock_frappe):
        """Should not update when profile exists and no name provided."""
        mock_format.return_value = "1234567890"
        mock_frappe.db.exists.return_value = True

        self.handler("1234567890", None, "test-account")

        mock_frappe.db.set_value.assert_not_called()


class TestAttachOpenwaMedia(IntegrationTestCase):
    """Test _attach_openwa_media handler."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _attach_openwa_media
        self.handler = _attach_openwa_media

    @patch("openwa_bridge.inbound.frappe")
    def test_empty_media_data_noop(self, mock_frappe):
        """Empty media data should log error and return."""
        msg_doc = MagicMock()
        msg_doc.name = "MSG-001"
        self.handler(msg_doc, {"mimetype": "image/png", "data": ""})
        mock_frappe.log_error.assert_called_once()

    @patch("openwa_bridge.inbound.frappe")
    def test_invalid_base64_noop(self, mock_frappe):
        """Invalid base64 should log error and return."""
        msg_doc = MagicMock()
        msg_doc.name = "MSG-001"
        self.handler(msg_doc, {"mimetype": "image/png", "data": "not-valid-base64!!!"})
        mock_frappe.log_error.assert_called_once()

    @patch("openwa_bridge.inbound.frappe")
    def test_oversized_media_rejected(self, mock_frappe):
        """Media exceeding size limit should be rejected."""
        msg_doc = MagicMock()
        msg_doc.name = "MSG-001"
        # Simulate ~11MB of base64 data
        large_data = "A" * (11 * 1024 * 1024)
        self.handler(msg_doc, {"mimetype": "image/png", "data": large_data})
        mock_frappe.log_error.assert_called_once()
        self.assertIn("too large", mock_frappe.log_error.call_args.kwargs.get("title", ""))
