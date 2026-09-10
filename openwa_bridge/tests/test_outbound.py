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
            mock_frappe.get_installed_apps.return_value = ["frappe_whatsapp"]
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
        self.assertEqual(doc_spec["openwa_render_doctype"], "Sales Invoice")
        self.assertEqual(doc_spec["openwa_render_name"], "ACC-SINV-2026-00047")
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
            mock_frappe.get_installed_apps.return_value = ["frappe_whatsapp"]
            send_document_pdf(
                to="967777713637",
                reference_doctype="Sales Invoice",
                reference_name="ACC-SINV-2026-00047",
            )

        self.assertEqual(doc_spec["openwa_print_format"], "Standard")
        self.assertEqual(doc_spec["openwa_pdf_filename"], "ACC-SINV-2026-00047.pdf")
        self.assertEqual(doc_spec["openwa_render_doctype"], "Sales Invoice")
        self.assertEqual(doc_spec["openwa_render_name"], "ACC-SINV-2026-00047")
        self.assertEqual(mock_doc.reference_doctype, "Sales Invoice")
        self.assertEqual(mock_doc.reference_name, "ACC-SINV-2026-00047")

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
            mock_frappe.get_installed_apps.return_value = ["frappe_whatsapp"]
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
            mock_frappe.get_installed_apps.return_value = ["frappe_whatsapp"]
            send_document_pdf(
                to="967777713637",
                reference_doctype="Sales Invoice",
                reference_name="ACC-SINV-2026-00047",
            )

        self.assertIsNone(doc_spec["openwa_letterhead"])
        self.assertIsNone(doc_spec["openwa_language"])
        self.assertIsNone(doc_spec["openwa_print_settings"])

    def test_links_reference_to_crm_deal_when_number_matches(self):
        """A recipient number matching a CRM Deal should relink the reference.

        The render fields keep the source document so the PDF still renders the
        invoice, while the reference points at the Deal for the CRM thread.
        """
        import sys

        from openwa_bridge.whatsapp_message import send_document_pdf

        mock_doc = MagicMock()
        mock_doc.name = "MSG-PDF-005"
        doc_spec = {}

        def _fake_get_doc(spec):
            for k, v in spec.items():
                mock_doc.__setattr__(k, v)
            doc_spec.update(spec)
            return mock_doc

        fake_crm_api = MagicMock()
        fake_crm_api.get_contact_lead_or_deal_from_number.return_value = (
            "CRM-DEAL-2026-00003",
            "CRM Deal",
        )

        with patch("openwa_bridge.whatsapp_message.frappe") as mock_frappe:
            mock_frappe.get_doc.side_effect = _fake_get_doc
            mock_frappe.get_installed_apps.return_value = ["frappe_whatsapp", "crm"]
            with patch.dict(
                sys.modules,
                {
                    "crm": MagicMock(),
                    "crm.integrations": MagicMock(),
                    "crm.integrations.api": fake_crm_api,
                },
            ):
                send_document_pdf(
                    to="967777713637",
                    reference_doctype="Sales Invoice",
                    reference_name="ACC-SINV-2026-00216",
                )

        fake_crm_api.get_contact_lead_or_deal_from_number.assert_called_once_with(
            "967777713637"
        )
        self.assertEqual(doc_spec["reference_doctype"], "Sales Invoice")
        self.assertEqual(doc_spec["reference_name"], "ACC-SINV-2026-00216")
        self.assertEqual(mock_doc.reference_doctype, "CRM Deal")
        self.assertEqual(mock_doc.reference_name, "CRM-DEAL-2026-00003")
        self.assertEqual(mock_doc.openwa_render_doctype, "Sales Invoice")
        self.assertEqual(mock_doc.openwa_render_name, "ACC-SINV-2026-00216")

    def test_keeps_reference_when_crm_not_installed(self):
        """Without the CRM app the reference should stay the passed document."""
        from openwa_bridge.whatsapp_message import send_document_pdf

        mock_doc = MagicMock()
        mock_doc.name = "MSG-PDF-006"

        def _fake_get_doc(spec):
            for k, v in spec.items():
                mock_doc.__setattr__(k, v)
            return mock_doc

        with patch("openwa_bridge.whatsapp_message.frappe") as mock_frappe:
            mock_frappe.get_doc.side_effect = _fake_get_doc
            mock_frappe.get_installed_apps.return_value = ["frappe_whatsapp"]
            send_document_pdf(
                to="967777713637",
                reference_doctype="Sales Invoice",
                reference_name="ACC-SINV-2026-00216",
            )

        self.assertEqual(mock_doc.reference_doctype, "Sales Invoice")
        self.assertEqual(mock_doc.reference_name, "ACC-SINV-2026-00216")
        self.assertEqual(mock_doc.openwa_render_doctype, "Sales Invoice")
        self.assertEqual(mock_doc.openwa_render_name, "ACC-SINV-2026-00216")

    def test_sending_from_crm_doctype_keeps_reference(self):
        """Sending from a Deal form should keep the Deal as the reference."""
        from openwa_bridge.whatsapp_message import send_document_pdf

        mock_doc = MagicMock()
        mock_doc.name = "MSG-PDF-007"

        def _fake_get_doc(spec):
            for k, v in spec.items():
                mock_doc.__setattr__(k, v)
            return mock_doc

        with patch("openwa_bridge.whatsapp_message.frappe") as mock_frappe:
            mock_frappe.get_doc.side_effect = _fake_get_doc
            mock_frappe.get_installed_apps.return_value = ["frappe_whatsapp", "crm"]
            send_document_pdf(
                to="967777713637",
                reference_doctype="CRM Deal",
                reference_name="CRM-DEAL-2026-00003",
            )

        self.assertEqual(mock_doc.reference_doctype, "CRM Deal")
        self.assertEqual(mock_doc.reference_name, "CRM-DEAL-2026-00003")
        self.assertEqual(mock_doc.openwa_render_doctype, "CRM Deal")
        self.assertEqual(mock_doc.openwa_render_name, "CRM-DEAL-2026-00003")
        mock_frappe.get_installed_apps.assert_not_called()

    def test_denies_send_when_no_read_permission_on_reference(self):
        """Without read permission on the render doc the send must be rejected."""
        from openwa_bridge.whatsapp_message import send_document_pdf

        with patch("openwa_bridge.whatsapp_message.frappe") as mock_frappe:
            mock_frappe.has_permission.return_value = False
            with self.assertRaises(frappe.PermissionError):
                send_document_pdf(
                    to="967777713637",
                    reference_doctype="Sales Invoice",
                    reference_name="ACC-SINV-2026-00047",
                )

        mock_frappe.has_permission.assert_called_once_with(
            "Sales Invoice", "read", "ACC-SINV-2026-00047"
        )
        mock_frappe.get_doc.assert_not_called()


class TestPrintFormatValidation(IntegrationTestCase):
    """Test _validate_print_format_for_doctype guard (doctype-agnostic)."""

    def test_standard_passes(self):
        """Standard/None should resolve to Standard without a DB lookup."""
        from openwa_bridge.utils import _validate_print_format_for_doctype

        self.assertEqual(_validate_print_format_for_doctype("CRM Deal", "Standard"), "Standard")
        self.assertEqual(_validate_print_format_for_doctype("CRM Deal", None), "Standard")
        self.assertEqual(_validate_print_format_for_doctype("CRM Deal", ""), "Standard")
        self.assertEqual(_validate_print_format_for_doctype("CRM Deal", "   "), "Standard")

    @patch("openwa_bridge.utils.frappe")
    def test_matching_doctype_passes(self, mock_frappe):
        """A format whose doc_type matches the doctype should be accepted."""
        from openwa_bridge.utils import _validate_print_format_for_doctype

        mock_frappe.db.get_value.return_value = "Sales Invoice"
        result = _validate_print_format_for_doctype(
            "Sales Invoice", "SALES INVOICE Qualification"
        )
        self.assertEqual(result, "SALES INVOICE Qualification")
        mock_frappe.db.get_value.assert_called_once_with(
            "Print Format", "SALES INVOICE Qualification", "doc_type"
        )

    @patch("openwa_bridge.utils.frappe")
    def test_generic_format_passes(self, mock_frappe):
        """A format with no doc_type is generic and can be used anywhere."""
        from openwa_bridge.utils import _validate_print_format_for_doctype

        mock_frappe.db.get_value.return_value = None
        result = _validate_print_format_for_doctype("CRM Deal", "Generic Quote")
        self.assertEqual(result, "Generic Quote")

    @patch("openwa_bridge.utils.frappe")
    def test_cross_doctype_format_rejected(self, mock_frappe):
        """A Sales Invoice format on a CRM Deal must be refused."""
        from openwa_bridge.utils import _validate_print_format_for_doctype

        mock_frappe.db.get_value.return_value = "Sales Invoice"

        def _throw(msg, exc=None):
            raise exc or Exception(msg)

        mock_frappe.throw.side_effect = _throw
        mock_frappe.ValidationError = ValueError

        with self.assertRaises(ValueError):
            _validate_print_format_for_doctype(
                "CRM Deal", "SALES INVOICE Qualification"
            )
        mock_frappe.throw.assert_called_once()

    @patch("openwa_bridge.utils.frappe")
    def test_any_doctype_pair_rejected(self, mock_frappe):
        """The guard applies to every doctype, not just CRM Deal.

        Uses an unrelated pair (a ToDo doctype printing with a format built
        for Note) to prove the check is fully generic."""
        from openwa_bridge.utils import _validate_print_format_for_doctype

        mock_frappe.db.get_value.return_value = "Note"

        def _throw(msg, exc=None):
            raise exc or Exception(msg)

        mock_frappe.throw.side_effect = _throw
        mock_frappe.ValidationError = ValueError

        with self.assertRaises(ValueError):
            _validate_print_format_for_doctype("ToDo", "Note Header")
        mock_frappe.throw.assert_called_once()
        mock_frappe.db.get_value.assert_called_once_with(
            "Print Format", "Note Header", "doc_type"
        )


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
        msg.openwa_render_doctype = None
        msg.openwa_render_name = None
        account = MagicMock()

        mock_tmpl = MagicMock()
        mock_tmpl.openwa_dynamic_header = True
        mock_tmpl.openwa_print_format = "Standard"
        mock_frappe.get_doc.return_value = mock_tmpl

        result = self.handler(msg, account)
        self.assertFalse(result)

    @patch("openwa_bridge.utils._http_session")
    @patch("openwa_bridge.utils.render_doc_as_image", return_value=b"\xff\xd8\xff fake jpeg")
    @patch("openwa_bridge.tasks.frappe")
    def test_uses_render_doctype_when_reference_relinked(self, mock_frappe, mock_render, mock_session):
        """Should render from openwa_render_doctype/name when the reference is a CRM record."""
        msg = MagicMock()
        msg.template = "test-template"
        msg.reference_doctype = "CRM Deal"
        msg.reference_name = "DEAL-0001"
        msg.openwa_render_doctype = "Sales Invoice"
        msg.openwa_render_name = "ACC-SINV-0001"
        msg.message = "caption text"
        msg.to = "1234567890"
        account = MagicMock()
        account.get.side_effect = lambda key, default=None: {
            "openwa_base_url": "http://localhost:2785",
            "openwa_session_id": "session-001",
        }.get(key, default)

        mock_tmpl = MagicMock()
        mock_tmpl.openwa_dynamic_header = True
        mock_tmpl.openwa_print_format = "Standard"
        mock_frappe.get_doc.return_value = mock_tmpl
        mock_frappe.db.get_value.return_value = None

        mock_session.post.return_value = mock_openwa_api("POST", 200, {"key": {"id": "img-123"}})

        result = self.handler(msg, account)
        self.assertTrue(result)
        mock_render.assert_called_once()
        call_args = mock_render.call_args
        self.assertEqual(call_args[0][0], "Sales Invoice")
        self.assertEqual(call_args[0][1], "ACC-SINV-0001")

    @patch("openwa_bridge.tasks._send_dynamic_header_for_outbox", return_value=True)
    @patch("openwa_bridge.tasks.frappe")
    def test_use_template_sends_dynamic_header_and_skips_template_text(self, mock_frappe, mock_image):
        """Template sends with a dynamic header deliver the image+caption only."""
        from openwa_bridge.tasks import _send_outbox_message

        mock_tmpl = MagicMock()
        mock_tmpl.openwa_dynamic_header = True
        mock_tmpl.openwa_print_format = "Standard"
        mock_tmpl.header = "Manaa Soft Enterprise"
        mock_tmpl.template = "Dear {{1}}, your invoice is due."
        mock_tmpl.footer = "Thank you"
        mock_frappe.get_doc.return_value = mock_tmpl

        msg = MagicMock()
        msg.template = "test-template"
        msg.use_template = 1
        msg.content_type = "text"
        msg.attach = None
        msg.message = "Dear {{1}}, your invoice is due."
        msg.template_parameters = json.dumps(["Faissal"])
        msg.reply_to_message_id = None
        msg._ensure_session_ready = MagicMock()
        msg._send_via_openwa = MagicMock()
        account = MagicMock()
        outbox = MagicMock()

        _send_outbox_message(msg, account, outbox)

        mock_image.assert_called_once()
        call_kwargs = mock_image.call_args.kwargs
        self.assertEqual(
            call_kwargs["caption"],
            "Manaa Soft Enterprise\nDear Faissal, your invoice is due.\nThank you",
        )
        msg._ensure_session_ready.assert_not_called()
        msg._send_via_openwa.assert_not_called()

    @patch("openwa_bridge.tasks._send_dynamic_header_for_outbox", return_value=False)
    @patch("openwa_bridge.tasks.frappe")
    def test_use_template_dynamic_header_fallback_sends_template_text(self, mock_frappe, mock_image):
        """If the header image fails, the template text is still sent."""
        from openwa_bridge.tasks import _send_outbox_message

        mock_tmpl = MagicMock()
        mock_tmpl.openwa_dynamic_header = True
        mock_tmpl.openwa_print_format = "Standard"
        mock_tmpl.header = "Manaa Soft Enterprise"
        mock_tmpl.template = "Dear {{1}}, your invoice is due."
        mock_tmpl.footer = "Thank you"
        mock_frappe.get_doc.return_value = mock_tmpl

        msg = MagicMock()
        msg.template = "test-template"
        msg.use_template = 1
        msg.content_type = "text"
        msg.attach = None
        msg.message = "Dear {{1}}, your invoice is due."
        msg.template_parameters = json.dumps(["Faissal"])
        msg.reply_to_message_id = None
        msg.message_id = None
        msg.reload = MagicMock()
        msg._ensure_session_ready = MagicMock()
        msg._send_via_openwa = MagicMock()
        account = MagicMock()
        outbox = MagicMock()

        _send_outbox_message(msg, account, outbox)

        mock_image.assert_called_once()
        msg._ensure_session_ready.assert_called_once()
        msg._send_via_openwa.assert_called_once()


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


class TestReferencePreservation(IntegrationTestCase):
    """Test that explicitly set references survive CRM's validate doc_events hook."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.whatsapp_message import OverrideWhatsAppMessage
        self.msg_class = OverrideWhatsAppMessage

    def test_explicit_reference_restored_after_clobber(self):
        """Outgoing message with an explicit reference keeps it after validate."""
        instance = self.msg_class.__new__(self.msg_class)
        instance.reference_doctype = "CRM Deal"
        instance.reference_name = "DEAL-0001"

        instance.before_validate()

        # CRM's crm.api.whatsapp.validate clobbers reference during validate
        instance.reference_doctype = "CRM Lead"
        instance.reference_name = "LEAD-0001"

        instance.before_save()

        self.assertEqual(instance.reference_doctype, "CRM Deal")
        self.assertEqual(instance.reference_name, "DEAL-0001")

    def test_empty_reference_allows_crm_auto_link(self):
        """Incoming message with no reference keeps CRM's auto-link."""
        instance = self.msg_class.__new__(self.msg_class)
        instance.reference_doctype = None
        instance.reference_name = None

        instance.before_validate()

        # CRM auto-links incoming messages from the sender number
        instance.reference_doctype = "CRM Lead"
        instance.reference_name = "LEAD-0001"

        instance.before_save()

        self.assertEqual(instance.reference_doctype, "CRM Lead")
        self.assertEqual(instance.reference_name, "LEAD-0001")

    def test_template_send_keeps_crm_relink_when_render_captured(self):
        """A template sent from an invoice stays linked to the CRM record after validate."""
        instance = self.msg_class.__new__(self.msg_class)
        instance.reference_doctype = "Sales Invoice"
        instance.reference_name = "ACC-SINV-0001"
        instance.template = "Sales Invoice-en"

        instance.before_validate()

        # The source doc is preserved for the dynamic header image ...
        self.assertEqual(instance.openwa_render_doctype, "Sales Invoice")
        self.assertEqual(instance.openwa_render_name, "ACC-SINV-0001")

        # ... while CRM's validate hook relinks the message to the Deal
        instance.reference_doctype = "CRM Deal"
        instance.reference_name = "DEAL-0001"

        instance.before_save()

        self.assertEqual(instance.reference_doctype, "CRM Deal")
        self.assertEqual(instance.reference_name, "DEAL-0001")


class TestTemplateMessageRendering(IntegrationTestCase):
    """Test _prepare_template_message (Jinja / placeholder / plain rendering)."""

    def setUp(self):
        super().setUp()
        from openwa_bridge.whatsapp_message import OverrideWhatsAppMessage
        self.msg_class = OverrideWhatsAppMessage

    def _make_instance(self, **kwargs):
        instance = self.msg_class.__new__(self.msg_class)
        defaults = dict(
            type="Outgoing",
            message=None,
            message_type="Template",
            use_template=None,
            template_parameters=None,
            body_param=None,
            reference_doctype="CRM Deal",
            reference_name="DEAL-0001",
            openwa_render_doctype="Sales Invoice",
            openwa_render_name="ACC-SINV-0001",
        )
        defaults.update(kwargs)
        for key, value in defaults.items():
            setattr(instance, key, value)
        return instance

    def _mock_template(self, body, header="Manaa Soft", footer="GOOD BAY", field_names=""):
        tmpl = MagicMock()
        tmpl.get.side_effect = lambda key, default=None: {
            "template": body,
            "header": header,
            "footer": footer,
            "field_names": field_names,
        }.get(key, default)
        return tmpl

    @patch("openwa_bridge.whatsapp_message.frappe")
    def test_jinja_body_renders_header_body_footer(self, mock_frappe):
        """Jinja body should render against the doc and compose all template parts."""
        instance = self._make_instance(template="Sales Invoice-en")
        mock_tmpl = self._mock_template(
            "{% set x = 1 %}\nDear {{ frappe.utils.escape_html(doc.customer_name) }}!"
        )
        mock_invoice = MagicMock()
        mock_frappe.get_doc.side_effect = lambda doctype, name: {
            "WhatsApp Templates": mock_tmpl,
            "Sales Invoice": mock_invoice,
        }[doctype]
        mock_frappe.render_template.return_value = "Dear Acme Corp!"

        instance.before_save()

        self.assertEqual(instance.message, "Manaa Soft\n\nDear Acme Corp!\n\nGOOD BAY")
        self.assertEqual(instance.message_type, "Manual")
        self.assertEqual(instance.use_template, 0)
        call = mock_frappe.render_template.call_args
        self.assertIs(call[0][1]["frappe"], mock_frappe)
        self.assertIs(call[0][1]["doc"], mock_invoice)

    @patch("openwa_bridge.whatsapp_message.frappe")
    def test_placeholder_body_uses_template_parameters(self, mock_frappe):
        """Placeholder body should extract vars from field_names and use send-template."""
        instance = self._make_instance(template="sales-invoice-en-2-en")
        mock_tmpl = self._mock_template(
            "Dear {{1}}, invoice {{2}} is ready. Amount: {{3}} {{4}}.",
            field_names="customer_name, name, grand_total, currency",
        )
        mock_invoice = MagicMock()
        mock_invoice.get_formatted.side_effect = ["Acme Corp", "ACC-SINV-0001", "100.00", "SAR"]
        mock_frappe.get_doc.side_effect = lambda doctype, name: {
            "WhatsApp Templates": mock_tmpl,
            "Sales Invoice": mock_invoice,
        }[doctype]

        instance.before_save()

        self.assertEqual(instance.use_template, 1)
        self.assertEqual(instance.message_type, "Template")
        self.assertEqual(
            json.loads(instance.template_parameters),
            ["Acme Corp", "ACC-SINV-0001", "100.00", "SAR"],
        )

    @patch("openwa_bridge.whatsapp_message.frappe")
    def test_placeholder_uses_fields_from_matching_notification(self, mock_frappe):
        """Vars should be borrowed from a notification's Fields child table."""
        instance = self._make_instance(template="sales-invoice-en-2-en")
        mock_tmpl = self._mock_template(
            "Dear {{1}}, invoice {{2}} is ready. Amount: {{3}} {{4}}. Due: {{5}}",
            field_names="",
        )
        mock_invoice = MagicMock()
        mock_invoice.get_formatted.side_effect = [
            "Acme Corp",
            "ACC-SINV-0001",
            "70.0",
            "YER",
            "2026-08-06",
        ]
        mock_notif = MagicMock()
        mock_notif.get.side_effect = lambda key, default=None: {
            "fields": [
                MagicMock(field_name="customer_name"),
                MagicMock(field_name="name"),
                MagicMock(field_name="grand_total"),
                MagicMock(field_name="currency"),
                MagicMock(field_name="due_date"),
            ],
        }.get(key, default)

        def _get_doc(doctype, name=None):
            return {
                "WhatsApp Templates": mock_tmpl,
                "WhatsApp Notification": mock_notif,
                "Sales Invoice": mock_invoice,
            }[doctype]

        mock_frappe.get_doc.side_effect = _get_doc
        mock_frappe.get_all.return_value = ["Sales"]

        instance.before_save()

        self.assertEqual(instance.use_template, 1)
        self.assertEqual(instance.message_type, "Template")
        self.assertEqual(
            json.loads(instance.template_parameters),
            ["Acme Corp", "ACC-SINV-0001", "70.0", "YER", "2026-08-06"],
        )

    @patch("openwa_bridge.whatsapp_message.frappe")
    def test_placeholder_without_variables_blocks_send(self, mock_frappe):
        """Placeholder body with no variable source should throw, not send literal {{1}}."""
        instance = self._make_instance(template="sales-invoice-en-2-en")
        mock_tmpl = self._mock_template("Dear {{1}}, invoice {{2}} is ready.", field_names="")
        mock_frappe.get_doc.side_effect = lambda doctype, name: {
            "WhatsApp Templates": mock_tmpl,
        }.get(doctype, MagicMock())
        mock_frappe.get_all.return_value = []
        mock_frappe.throw.side_effect = frappe.ValidationError

        with self.assertRaises(frappe.ValidationError):
            instance.before_save()

    @patch("openwa_bridge.whatsapp_message.frappe")
    def test_render_failure_falls_back_to_raw_body(self, mock_frappe):
        """A Jinja render error should keep the raw body and never raise."""
        instance = self._make_instance(template="Sales Invoice-en")
        mock_tmpl = self._mock_template("{% set x = 1 %}\nBroken {{ doc.missing }}")
        mock_frappe.get_doc.side_effect = lambda doctype, name: {
            "WhatsApp Templates": mock_tmpl,
            "Sales Invoice": MagicMock(),
        }[doctype]
        mock_frappe.render_template.side_effect = Exception("Jinja error")
        mock_frappe.log_error = MagicMock()

        instance.before_save()

        self.assertEqual(instance.message_type, "Manual")
        self.assertEqual(instance.use_template, 0)
        self.assertIn("Broken", instance.message)

    @patch("openwa_bridge.whatsapp_message.frappe")
    def test_existing_message_is_not_overwritten(self, mock_frappe):
        """A message that already has text (e.g. notifications) is left untouched."""
        instance = self._make_instance(template="welcome-template", message="Already sent text")

        instance.before_save()

        mock_frappe.get_doc.assert_not_called()
        self.assertEqual(instance.message, "Already sent text")

    @patch("openwa_bridge.whatsapp_message.frappe")
    def test_jinja_render_strips_bidi_control_chars(self, mock_frappe):
        """RLE/PDF and other bidi control chars should not reach the customer."""
        instance = self._make_instance(template="Sales Invoice-en")
        mock_tmpl = self._mock_template("\u202B {{ doc.name }} \u202C")
        mock_frappe.get_doc.side_effect = lambda doctype, name: {
            "WhatsApp Templates": mock_tmpl,
            "Sales Invoice": MagicMock(),
        }[doctype]
        mock_frappe.render_template.return_value = "\u202B Dear \u2066Acme Corp\u2069 \u202C"

        instance.before_save()

        self.assertNotIn("\u202B", instance.message)
        self.assertNotIn("\u202C", instance.message)
        self.assertNotIn("\u2066", instance.message)
        self.assertNotIn("\u2069", instance.message)
        self.assertIn("Dear Acme Corp", instance.message)
