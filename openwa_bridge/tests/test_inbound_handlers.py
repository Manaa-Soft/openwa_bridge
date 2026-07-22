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


class TestHandleMessageRevoked(IntegrationTestCase):
    """Test _handle_message_revoked handler."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _handle_message_revoked
        self.handler = _handle_message_revoked

    @patch("openwa_bridge.inbound.frappe")
    def test_sets_revoked_status(self, mock_frappe):
        """Should set message status to Revoked."""
        mock_frappe.db.get_value.return_value = "MSG-001"
        self.handler({"messageId": "msg-001"})
        mock_frappe.db.set_value.assert_called_once_with(
            "WhatsApp Message", "MSG-001", "status", "Revoked"
        )

    @patch("openwa_bridge.inbound.frappe")
    def test_missing_message_id_noop(self, mock_frappe):
        """Missing message_id should not crash."""
        self.handler({})
        mock_frappe.db.get_value.assert_not_called()

    @patch("openwa_bridge.inbound.frappe")
    def test_unknown_message_noop(self, mock_frappe):
        """Unknown message_id should not crash."""
        mock_frappe.db.get_value.return_value = None
        self.handler({"messageId": "unknown"})
        mock_frappe.db.set_value.assert_not_called()


class TestHandleMessageReaction(IntegrationTestCase):
    """Test _handle_message_reaction handler."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _handle_message_reaction
        self.handler = _handle_message_reaction

    @patch("openwa_bridge.inbound.frappe")
    def test_logs_reaction(self, mock_frappe):
        """Should log the reaction."""
        mock_frappe.db.get_value.return_value = "MSG-001"
        self.handler({"messageId": "msg-001", "emoji": "👍"})
        mock_frappe.logger.return_value.info.assert_called_once()

    @patch("openwa_bridge.inbound.frappe")
    def test_missing_message_id_noop(self, mock_frappe):
        """Missing message_id should not crash."""
        self.handler({"emoji": "👍"})
        mock_frappe.db.get_value.assert_not_called()

    @patch("openwa_bridge.inbound.frappe")
    def test_missing_emoji_noop(self, mock_frappe):
        """Missing emoji should not crash."""
        self.handler({"messageId": "msg-001"})
        mock_frappe.db.get_value.assert_not_called()


class TestHandleSessionQr(IntegrationTestCase):
    """Test _handle_session_qr handler."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _handle_session_qr
        self.handler = _handle_session_qr

    @patch("openwa_bridge.inbound.frappe")
    def test_sets_inactive_status(self, mock_frappe):
        """Should set account status to Inactive when QR is ready."""
        mock_frappe.db.get_value.return_value = "Test Account"
        self.handler({}, "session-001")
        mock_frappe.db.set_value.assert_called_once_with(
            "WhatsApp Account", "Test Account", "status", "Inactive"
        )

    @patch("openwa_bridge.inbound.frappe")
    def test_unknown_session_noop(self, mock_frappe):
        """Unknown session_id should not crash."""
        mock_frappe.db.get_value.return_value = None
        self.handler({}, "unknown-session")
        mock_frappe.db.set_value.assert_not_called()


class TestHandleSessionAuthenticated(IntegrationTestCase):
    """Test _handle_session_authenticated handler."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _handle_session_authenticated
        self.handler = _handle_session_authenticated

    @patch("openwa_bridge.inbound.frappe")
    def test_sets_active_status(self, mock_frappe):
        """Should set account status to Active when authenticated."""
        mock_frappe.db.get_value.return_value = "Test Account"
        self.handler("session-001")
        mock_frappe.db.set_value.assert_called_once_with(
            "WhatsApp Account", "Test Account", "status", "Active"
        )

    @patch("openwa_bridge.inbound.frappe")
    def test_unknown_session_noop(self, mock_frappe):
        """Unknown session_id should not crash."""
        mock_frappe.db.get_value.return_value = None
        self.handler("unknown-session")
        mock_frappe.db.set_value.assert_not_called()


class TestHandleMessageEdited(IntegrationTestCase):
    """Test _handle_message_edited handler."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _handle_message_edited
        self.handler = _handle_message_edited

    @patch("openwa_bridge.inbound.frappe")
    def test_updates_message_body(self, mock_frappe):
        """Should update WhatsApp Message body when message is edited."""
        mock_frappe.db.get_value.side_effect = [
            "MSG-001",  # message lookup by message_id
            "Original text",  # current message body
        ]
        self.handler({"messageId": "msg-001", "body": "Edited text"})
        mock_frappe.db.set_value.assert_called_once_with(
            "WhatsApp Message", "MSG-001", "message", "Edited text"
        )

    @patch("openwa_bridge.inbound.frappe")
    def test_same_body_noop(self, mock_frappe):
        """Should not update when body hasn't changed."""
        mock_frappe.db.get_value.side_effect = [
            "MSG-001",
            "Same text",
        ]
        self.handler({"messageId": "msg-001", "body": "Same text"})
        mock_frappe.db.set_value.assert_not_called()

    @patch("openwa_bridge.inbound.frappe")
    def test_missing_message_id_noop(self, mock_frappe):
        """Missing messageId should not crash."""
        self.handler({"body": "Edited text"})
        mock_frappe.db.get_value.assert_not_called()

    @patch("openwa_bridge.inbound.frappe")
    def test_missing_body_noop(self, mock_frappe):
        """Missing body should not crash."""
        self.handler({"messageId": "msg-001"})
        mock_frappe.db.get_value.assert_not_called()

    @patch("openwa_bridge.inbound.frappe")
    def test_unknown_message_noop(self, mock_frappe):
        """Unknown message_id should not crash."""
        mock_frappe.db.get_value.return_value = None
        self.handler({"messageId": "unknown", "body": "text"})
        mock_frappe.db.set_value.assert_not_called()


class TestHandleSessionReconnectLoop(IntegrationTestCase):
    """Test _handle_session_reconnect_loop handler."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _handle_session_reconnect_loop
        self.handler = _handle_session_reconnect_loop

    @patch("openwa_bridge.inbound.frappe")
    def test_logs_error_for_known_account(self, mock_frappe):
        """Should log error when session is stuck in reconnect loop."""
        mock_frappe.db.get_value.return_value = "Test Account"
        self.handler({}, "session-001")
        mock_frappe.log_error.assert_called_once()
        self.assertIn("Reconnect Loop", mock_frappe.log_error.call_args.kwargs.get("title", ""))

    @patch("openwa_bridge.inbound.frappe")
    def test_unknown_session_noop(self, mock_frappe):
        """Unknown session_id should not crash."""
        mock_frappe.db.get_value.return_value = None
        self.handler({}, "unknown-session")
        mock_frappe.log_error.assert_not_called()


class TestHandleGroupMembership(IntegrationTestCase):
    """Test _handle_group_membership handler."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _handle_group_membership
        self.handler = _handle_group_membership

    @patch("openwa_bridge.inbound.frappe")
    def test_logs_join_event(self, mock_frappe):
        """Should log group.join event."""
        self.handler(
            {"groupId": "group-001", "participantIds": ["user-001"], "actorId": "user-001"},
            "session-001",
            "group.join",
        )
        mock_frappe.logger.return_value.info.assert_called_once()
        self.assertIn("group.join", mock_frappe.logger.return_value.info.call_args[0][0])

    @patch("openwa_bridge.inbound.frappe")
    def test_logs_leave_event(self, mock_frappe):
        """Should log group.leave event."""
        self.handler(
            {"groupId": "group-001", "participantIds": ["user-002"]},
            "session-001",
            "group.leave",
        )
        mock_frappe.logger.return_value.info.assert_called_once()
        self.assertIn("group.leave", mock_frappe.logger.return_value.info.call_args[0][0])

    @patch("openwa_bridge.inbound.frappe")
    def test_missing_group_id_noop(self, mock_frappe):
        """Missing groupId should not crash."""
        self.handler({"participantIds": ["user-001"]}, "session-001", "group.join")
        mock_frappe.logger.return_value.info.assert_not_called()


class TestHandleGroupUpdate(IntegrationTestCase):
    """Test _handle_group_update handler."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _handle_group_update
        self.handler = _handle_group_update

    @patch("openwa_bridge.inbound.frappe")
    def test_logs_changes(self, mock_frappe):
        """Should log group metadata changes."""
        self.handler(
            {"groupId": "group-001", "changes": {"subject": "New Name"}},
            "session-001",
        )
        mock_frappe.logger.return_value.info.assert_called_once()
        self.assertIn("group-001", mock_frappe.logger.return_value.info.call_args[0][0])

    @patch("openwa_bridge.inbound.frappe")
    def test_missing_group_id_noop(self, mock_frappe):
        """Missing groupId should not crash."""
        self.handler({"changes": {"subject": "New Name"}}, "session-001")
        mock_frappe.logger.return_value.info.assert_not_called()


class TestHandleCallReceived(IntegrationTestCase):
    """Test _handle_call_received handler."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _handle_call_received
        self.handler = _handle_call_received

    @patch("openwa_bridge.inbound.frappe")
    def test_logs_voice_call(self, mock_frappe):
        """Should log incoming voice call."""
        self.handler(
            {"callId": "call-001", "from": "1234567890@c.us", "isVideo": False, "isGroup": False},
            "session-001",
        )
        mock_frappe.logger.return_value.info.assert_called_once()
        info_msg = mock_frappe.logger.return_value.info.call_args[0][0]
        self.assertIn("call-001", info_msg)
        self.assertIn("video=False", info_msg)

    @patch("openwa_bridge.inbound.frappe")
    def test_logs_video_call(self, mock_frappe):
        """Should log incoming video call."""
        self.handler(
            {"callId": "call-002", "from": "0987654321@c.us", "isVideo": True, "isGroup": True},
            "session-001",
        )
        mock_frappe.logger.return_value.info.assert_called_once()
        info_msg = mock_frappe.logger.return_value.info.call_args[0][0]
        self.assertIn("video=True", info_msg)
        self.assertIn("group=True", info_msg)

    @patch("openwa_bridge.inbound.frappe")
    def test_empty_data_logs_anyway(self, mock_frappe):
        """Should still log even with empty event data."""
        self.handler({}, "session-001")
        mock_frappe.logger.return_value.info.assert_called_once()


class TestCreateCommunication(IntegrationTestCase):
    """Test _create_communication helper."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.inbound import _create_communication
        self.handler = _create_communication

    @patch("openwa_bridge.inbound.frappe")
    @patch("openwa_bridge.inbound.format_number")
    def test_creates_communication_for_existing_contact(self, mock_format, mock_frappe):
        """Should create Communication when Contact exists."""
        mock_format.return_value = "1234567890"
        mock_frappe.db.get_value.return_value = "Contact-001"

        msg_doc = MagicMock()
        msg_doc.name = "MSG-001"
        msg_doc.message = "Hello"

        mock_comm = MagicMock()
        mock_frappe.get_doc.return_value = mock_comm

        self.handler(msg_doc, "1234567890", "Test User")

        mock_frappe.get_doc.assert_called_once()
        call_args = mock_frappe.get_doc.call_args[0][0]
        self.assertEqual(call_args["doctype"], "Communication")
        self.assertEqual(call_args["party_type"], "Contact")
        self.assertEqual(call_args["party"], "Contact-001")
        mock_comm.insert.assert_called_once_with(ignore_permissions=True)

    @patch("openwa_bridge.inbound.frappe")
    @patch("openwa_bridge.inbound.format_number")
    def test_creates_lead_and_contact_when_none_exists(self, mock_format, mock_frappe):
        """Should create Lead + Contact when no Contact found."""
        mock_format.return_value = "1234567890"
        # First call: db.get_value for Contact → None
        # Lead and Contact creation will use get_doc
        mock_frappe.db.get_value.return_value = None

        msg_doc = MagicMock()
        msg_doc.name = "MSG-001"
        msg_doc.message = "Hello"

        lead_doc = MagicMock()
        lead_doc.name = "LEAD-001"
        contact_doc = MagicMock()
        contact_doc.name = "CONTACT-001"
        comm_doc = MagicMock()

        def get_doc_side_effect(args):
            if args.get("doctype") == "Lead":
                return lead_doc
            elif args.get("doctype") == "Contact":
                return contact_doc
            elif args.get("doctype") == "Communication":
                return comm_doc
            return MagicMock()

        mock_frappe.get_doc.side_effect = get_doc_side_effect

        self.handler(msg_doc, "1234567890", "Test User")

        lead_doc.insert.assert_called_once_with(ignore_permissions=True)
        contact_doc.insert.assert_called_once_with(ignore_permissions=True)
        comm_doc.insert.assert_called_once_with(ignore_permissions=True)
