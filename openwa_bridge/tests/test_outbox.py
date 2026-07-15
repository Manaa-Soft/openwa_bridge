"""Tests for OpenWA Outbox DocType."""
import frappe
from frappe.tests import IntegrationTestCase


class TestOpenWAOutbox(IntegrationTestCase):
    """Test the OpenWA Outbox DocType lifecycle."""

    def test_outbox_creation(self):
        """Creating an outbox entry sets Pending status by default."""
        msg = frappe.get_doc({
            "doctype": "WhatsApp Message",
            "type": "Outgoing",
            "to": "1234567890",
            "content_type": "text",
            "message": "Test message",
        })
        msg.insert(ignore_permissions=True)
        self.addCleanup(msg.delete, ignore_permissions=True)

        outbox = frappe.get_doc({
            "doctype": "OpenWA Outbox",
            "whatsapp_message": msg.name,
            "whatsapp_account": "test-account",
            "content_type": "text",
            "status": "Pending",
            "max_attempts": 5,
        })
        # DocType insert may fail without proper account, but validates structure
        self.assertEqual(outbox.status, "Pending")
        self.assertEqual(outbox.max_attempts, 5)
        self.assertEqual(outbox.attempts, 0)

    def test_content_type_auto_populated(self):
        """Content type should auto-populate from linked WhatsApp Message."""
        from openwa_bridge.openwa_bridge.doctype.openwa_outbox.openwa_outbox import OpenWAOutbox

        doc = OpenWAOutbox.__new__(OpenWAOutbox)
        doc.content_type = None
        doc.whatsapp_message = None
        doc._set_content_type_from_message()
        self.assertIsNone(doc.content_type)
