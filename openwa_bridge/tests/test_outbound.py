"""Unit tests for outbound message routing and outbox processing."""
from __future__ import annotations

import base64
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

    @patch("openwa_bridge.whatsapp_message._send_typing_indicator")
    @patch("openwa_bridge.whatsapp_message.render_doc_as_pdf", return_value=b"%PDF-1.4 fake pdf bytes")
    @patch("openwa_bridge.whatsapp_message._http_session")
    def test_send_document_as_pdf(self, mock_session, mock_render, mock_typing):
        """Document + openwa_send_pdf should POST base64 PDF to send-document."""
        mock_session.post.return_value = mock_openwa_api("POST", 200, {"messageId": "doc-123"})

        mock_account = MagicMock()
        mock_account.get.return_value = "http://localhost:2785"
        mock_account.openwa_session_id = "session-001"
        mock_account.get_password.return_value = "api-key-123"

        instance = self.msg_class.__new__(self.msg_class)
        instance.name = "MSG-003"
        instance.to = "1234567890"
        instance.template = None
        instance.body_param = None
        instance.template_parameters = None
        instance.content_type = "document"
        instance.message = "Invoice attached"
        instance.is_reply = False
        instance.reply_to_message_id = None
        instance.openwa_send_pdf = 1
        instance.reference_doctype = "Sales Invoice"
        instance.reference_name = "ACC-SINV-001"
        instance.openwa_print_format = "Standard"
        instance.openwa_pdf_filename = ""

        with patch.object(self.msg_class, "_ensure_session_ready", return_value=None):
            instance._send_via_openwa(mock_account, {})

        mock_render.assert_called_once_with(
            "Sales Invoice",
            "ACC-SINV-001",
            "Standard",
            letterhead=None,
            language=None,
            settings={},
        )

        document_call = None
        for call in mock_session.post.call_args_list:
            if "send-document" in call[0][0]:
                document_call = call
        self.assertIsNotNone(document_call, "Expected a send-document call")

        payload = document_call[1]["json"]
        self.assertEqual(payload["mimetype"], "application/pdf")
        self.assertEqual(payload["filename"], "ACC-SINV-001.pdf")
        self.assertEqual(
            payload["base64"],
            base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode(),
        )
        self.assertEqual(payload["caption"], "Invoice attached")

    @patch("openwa_bridge.whatsapp_message._send_typing_indicator")
    @patch("openwa_bridge.whatsapp_message.render_doc_as_pdf", return_value=b"%PDF-1.4 fake pdf bytes")
    @patch("openwa_bridge.whatsapp_message._http_session")
    def test_send_document_pdf_forwards_print_options(self, mock_session, mock_render, mock_typing):
        """Letterhead, language and dynamic settings should be forwarded to render_doc_as_pdf."""
        mock_session.post.return_value = mock_openwa_api("POST", 200, {"messageId": "doc-456"})

        mock_account = MagicMock()
        mock_account.get.return_value = "http://localhost:2785"
        mock_account.openwa_session_id = "session-001"
        mock_account.get_password.return_value = "api-key-123"

        instance = self.msg_class.__new__(self.msg_class)
        instance.name = "MSG-005"
        instance.to = "1234567890"
        instance.template = None
        instance.body_param = None
        instance.template_parameters = None
        instance.content_type = "document"
        instance.message = ""
        instance.is_reply = False
        instance.reply_to_message_id = None
        instance.openwa_send_pdf = 1
        instance.reference_doctype = "Sales Invoice"
        instance.reference_name = "ACC-SINV-001"
        instance.openwa_print_format = "Invoice Format"
        instance.openwa_pdf_filename = ""
        instance.openwa_letterhead = "Letter Head - ACME"
        instance.openwa_language = "de"
        instance.openwa_print_settings = '{"compact_item_print": 1}'

        with patch.object(self.msg_class, "_ensure_session_ready", return_value=None):
            instance._send_via_openwa(mock_account, {})

        mock_render.assert_called_once_with(
            "Sales Invoice",
            "ACC-SINV-001",
            "Invoice Format",
            letterhead="Letter Head - ACME",
            language="de",
            settings={"compact_item_print": 1},
        )

    def test_get_pdf_settings_parses_json(self):
        """_get_pdf_settings should parse the stored JSON into a dict."""
        instance = self.msg_class.__new__(self.msg_class)
        instance.openwa_print_settings = '{"compact_item_print": 1}'
        self.assertEqual(instance._get_pdf_settings(), {"compact_item_print": 1})

    def test_get_pdf_settings_handles_missing_or_invalid(self):
        """_get_pdf_settings should degrade gracefully."""
        instance = self.msg_class.__new__(self.msg_class)
        instance.openwa_print_settings = None
        self.assertEqual(instance._get_pdf_settings(), {})
        instance.openwa_print_settings = "not json"
        self.assertEqual(instance._get_pdf_settings(), {})

    @patch("openwa_bridge.whatsapp_message._send_typing_indicator")
    @patch("openwa_bridge.whatsapp_message.render_doc_as_pdf", return_value=None)
    @patch("openwa_bridge.whatsapp_message._http_session")
    def test_send_document_pdf_render_failure(self, mock_session, mock_render, mock_typing):
        """Render failure should raise so the outbox retries."""
        mock_account = MagicMock()
        mock_account.get.return_value = "http://localhost:2785"
        mock_account.openwa_session_id = "session-001"
        mock_account.get_password.return_value = "api-key-123"

        instance = self.msg_class.__new__(self.msg_class)
        instance.name = "MSG-004"
        instance.to = "1234567890"
        instance.template = None
        instance.body_param = None
        instance.template_parameters = None
        instance.content_type = "document"
        instance.message = ""
        instance.is_reply = False
        instance.reply_to_message_id = None
        instance.openwa_send_pdf = 1
        instance.reference_doctype = "Sales Invoice"
        instance.reference_name = "ACC-SINV-001"
        instance.openwa_print_format = "Standard"
        instance.openwa_pdf_filename = ""

        with patch.object(self.msg_class, "_ensure_session_ready", return_value=None):
            with self.assertRaises(frappe.ValidationError):
                instance._send_via_openwa(mock_account, {})


class TestSendDocumentPdf(IntegrationTestCase):
    """Test the send_document_pdf whitelisted method."""

    def test_creates_whatsapp_message_with_pdf_fields(self):
        from openwa_bridge.whatsapp_message import send_document_pdf

        mock_doc = MagicMock()
        mock_doc.name = "MSG-PDF-001"
        doc_spec = {}

        def _fake_get_doc(spec):
            for k, v in spec.items():
                mock_doc.__setattr__(k, v)
            doc_spec.update(spec)
            return mock_doc

        with patch("openwa_bridge.whatsapp_message.frappe") as mock_frappe:
            mock_frappe.get_doc.side_effect = _fake_get_doc
            result = send_document_pdf(
                to="967777713637",
                reference_doctype="Sales Invoice",
                reference_name="ACC-SINV-2026-00047",
                print_format="Invoice Format",
                filename="my-invoice.pdf",
                caption="Please find your invoice",
            )

        self.assertEqual(result, "MSG-PDF-001")
        self.assertEqual(doc_spec["doctype"], "WhatsApp Message")
        self.assertEqual(doc_spec["content_type"], "document")
        self.assertEqual(doc_spec["message"], "Please find your invoice")
        self.assertEqual(doc_spec["openwa_send_pdf"], 1)
        self.assertEqual(doc_spec["openwa_print_format"], "Invoice Format")
        self.assertEqual(doc_spec["openwa_pdf_filename"], "my-invoice.pdf")
        mock_doc.save.assert_called_once()

    def test_defaults_print_format_and_filename(self):
        from openwa_bridge.whatsapp_message import send_document_pdf

        mock_doc = MagicMock()
        mock_doc.name = "MSG-PDF-002"
        doc_spec = {}

        def _fake_get_doc(spec):
            for k, v in spec.items():
                mock_doc.__setattr__(k, v)
            doc_spec.update(spec)
            return mock_doc

        with patch("openwa_bridge.whatsapp_message.frappe") as mock_frappe:
            mock_frappe.get_doc.side_effect = _fake_get_doc
            send_document_pdf(
                to="967777713637",
                reference_doctype="Sales Invoice",
                reference_name="ACC-SINV-2026-00047",
            )

        self.assertEqual(doc_spec["openwa_print_format"], "Standard")
        self.assertEqual(doc_spec["openwa_pdf_filename"], "ACC-SINV-2026-00047.pdf")

    def test_stores_print_options_on_message(self):
        """Letterhead, language and settings should be stored on the WhatsApp Message."""
        from openwa_bridge.whatsapp_message import send_document_pdf

        mock_doc = MagicMock()
        mock_doc.name = "MSG-PDF-003"
        doc_spec = {}

        def _fake_get_doc(spec):
            for k, v in spec.items():
                mock_doc.__setattr__(k, v)
            doc_spec.update(spec)
            return mock_doc

        with patch("openwa_bridge.whatsapp_message.frappe") as mock_frappe:
            mock_frappe.get_doc.side_effect = _fake_get_doc
            send_document_pdf(
                to="967777713637",
                reference_doctype="Sales Invoice",
                reference_name="ACC-SINV-2026-00047",
                print_format="Invoice Format",
                filename="my-invoice.pdf",
                caption="Please find your invoice",
                letterhead="Letter Head - ACME",
                language="de",
                settings='{"compact_item_print": 1}',
            )

        self.assertEqual(doc_spec["openwa_letterhead"], "Letter Head - ACME")
        self.assertEqual(doc_spec["openwa_language"], "de")
        self.assertEqual(doc_spec["openwa_print_settings"], '{"compact_item_print": 1}')

    def test_print_options_default_to_none(self):
        """Without print options they should be stored as None."""
        from openwa_bridge.whatsapp_message import send_document_pdf

        mock_doc = MagicMock()
        mock_doc.name = "MSG-PDF-004"
        doc_spec = {}

        def _fake_get_doc(spec):
            for k, v in spec.items():
                mock_doc.__setattr__(k, v)
            doc_spec.update(spec)
            return mock_doc

        with patch("openwa_bridge.whatsapp_message.frappe") as mock_frappe:
            mock_frappe.get_doc.side_effect = _fake_get_doc
            send_document_pdf(
                to="967777713637",
                reference_doctype="Sales Invoice",
                reference_name="ACC-SINV-2026-00047",
            )

        self.assertIsNone(doc_spec["openwa_letterhead"])
        self.assertIsNone(doc_spec["openwa_language"])
        self.assertIsNone(doc_spec["openwa_print_settings"])


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


class TestTypingIndicator(IntegrationTestCase):
    """Test _send_typing_indicator helper."""

    @patch("openwa_bridge.whatsapp_message._http_session")
    def test_sends_typing_request(self, mock_session):
        """Should POST to typing endpoint with chatId and state."""
        from openwa_bridge.whatsapp_message import _send_typing_indicator

        mock_session.post.return_value = mock_openwa_api("POST", 200)

        _send_typing_indicator(
            "http://localhost:2785", "session-001", "api-key",
            "1234567890@c.us", "typing",
        )

        mock_session.post.assert_called_once()
        call_kwargs = mock_session.post.call_args
        self.assertIn("/chats/typing", call_kwargs[0][0])
        self.assertEqual(call_kwargs[1]["json"]["chatId"], "1234567890@c.us")
        self.assertEqual(call_kwargs[1]["json"]["state"], "typing")

    @patch("openwa_bridge.whatsapp_message._http_session")
    def test_failure_does_not_raise(self, mock_session):
        """Typing indicator failure should be swallowed silently."""
        from openwa_bridge.whatsapp_message import _send_typing_indicator

        mock_session.post.side_effect = Exception("Connection refused")

        # Should NOT raise
        _send_typing_indicator(
            "http://localhost:2785", "session-001", "api-key",
            "1234567890@c.us", "typing",
        )

    @patch("openwa_bridge.whatsapp_message._http_session")
    def test_sends_recording_state(self, mock_session):
        """Should support recording state."""
        from openwa_bridge.whatsapp_message import _send_typing_indicator

        mock_session.post.return_value = mock_openwa_api("POST", 200)

        _send_typing_indicator(
            "http://localhost:2785", "session-001", "api-key",
            "1234567890@c.us", "recording",
        )

        call_kwargs = mock_session.post.call_args
        self.assertEqual(call_kwargs[1]["json"]["state"], "recording")


class TestTypingInSendFlow(IntegrationTestCase):
    """Test that typing indicator is called during _send_via_openwa for text messages."""

    @patch("openwa_bridge.whatsapp_message._send_typing_indicator")
    @patch("openwa_bridge.whatsapp_message._http_session")
    def test_typing_called_for_text(self, mock_session, mock_typing):
        """Typing indicator should be sent before text message."""
        mock_session.post.return_value = mock_openwa_api("POST", 200, {"key": {"id": "msg-123"}})

        from openwa_bridge.whatsapp_message import OverrideWhatsAppMessage

        mock_account = MagicMock()
        mock_account.get.return_value = "http://localhost:2785"
        mock_account.openwa_session_id = "session-001"
        mock_account.get_password.return_value = "api-key-123"

        instance = OverrideWhatsAppMessage.__new__(OverrideWhatsAppMessage)
        instance.name = "MSG-001"
        instance.to = "1234567890"
        instance.template = None
        instance.body_param = None
        instance.template_parameters = None
        instance.content_type = "text"

        instance._send_via_openwa(mock_account, {})

        mock_typing.assert_called_once()
        self.assertIn("/chats/typing", mock_typing.call_args[0][1])

    @patch("openwa_bridge.whatsapp_message._send_typing_indicator")
    @patch("openwa_bridge.whatsapp_message._http_session")
    def test_typing_not_called_for_template(self, mock_session, mock_typing):
        """Typing indicator should NOT be sent for template messages."""
        mock_session.post.return_value = mock_openwa_api("POST", 200, {"key": {"id": "msg-456"}})

        from openwa_bridge.whatsapp_message import OverrideWhatsAppMessage

        mock_account = MagicMock()
        mock_account.get.return_value = "http://localhost:2785"
        mock_account.openwa_session_id = "session-001"
        mock_account.get_password.return_value = "api-key-123"

        instance = OverrideWhatsAppMessage.__new__(OverrideWhatsAppMessage)
        instance.name = "MSG-002"
        instance.to = "1234567890"
        instance.template = "welcome-template"
        instance.body_param = json.dumps(["John"])
        instance.template_parameters = None
        instance.content_type = "template"
        instance.use_template = True

        with patch("openwa_bridge.whatsapp_message.frappe") as mock_frappe:
            mock_frappe.db.get_value.return_value = "tmpl-uuid-123"
            instance._send_via_openwa(mock_account, {})

        mock_typing.assert_not_called()
