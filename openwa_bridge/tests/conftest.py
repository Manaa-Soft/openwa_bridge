"""Shared test fixtures and mocks for OpenWA Bridge tests."""
from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase


def make_webhook_payload(event: str, data: dict, session_id: str = "test-session-001") -> dict:
    """Build a realistic OpenWA webhook payload."""
    return {
        "event": event,
        "sessionId": session_id,
        "data": data,
    }


def make_message_data(
    sender: str = "1234567890@c.us",
    body: str = "Hello from test",
    msg_type: str = "text",
    msg_id: str = "msg-001",
    from_me: bool = False,
    **kwargs,
) -> dict:
    """Build a realistic OpenWA message.received data payload."""
    base = {
        "from": sender,
        "body": body,
        "type": msg_type,
        "id": msg_id,
        "fromMe": from_me,
        "chatId": sender,
    }
    base.update(kwargs)
    return base


def make_status_data(msg_id: str = "msg-001", status: str = "DELIVERED") -> dict:
    """Build a realistic OpenWA message.ack data payload."""
    return {"messageId": msg_id, "status": status, "id": msg_id}


def make_session_status_data(status: str = "ready") -> dict:
    """Build a realistic OpenWA session.status data payload."""
    return {"status": status}


def sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Generate a valid HMAC-SHA256 signature header."""
    sig = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def mock_openwa_api(method: str = "GET", status_code: int = 200, json_data: dict | None = None):
    """Return a mock requests.Response for OpenWA API calls."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data or {}
    mock_resp.text = json.dumps(json_data or {})
    if status_code >= 400:
        mock_resp.raise_for_status.side_effect = frappe.ValidationError(f"HTTP {status_code}")
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp


class OpenWABaseTestCase(IntegrationTestCase):
    """Base test case with common OpenWA Bridge test utilities."""

    def setUp(self):
        super().setUp()
        self.test_secret = "test-webhook-secret-12345"
        self.test_api_key = "test-api-key-67890"
        self.test_session_id = "test-session-001"

    def _create_test_account(self, name: str = "test-wa-account", **kwargs) -> str:
        """Create a minimal WhatsApp Account for testing."""
        defaults = {
            "doctype": "WhatsApp Account",
            "account_name": name,
            "status": "Active",
            "openwa_enabled": 1,
            "openwa_base_url": "http://localhost:2785",
            "openwa_session_id": self.test_session_id,
        }
        defaults.update(kwargs)
        try:
            doc = frappe.get_doc(defaults)
            doc.insert(ignore_permissions=True)
            return doc.name
        except Exception:
            return name

    def _make_inbound_request(
        self,
        event: str = "message.received",
        data: dict | None = None,
        session_id: str = "test-session-001",
        secret: str | None = None,
        sign: bool = True,
        idempotency_key: str | None = None,
    ):
        """Simulate an inbound webhook request with proper payload and headers."""
        payload_data = data or make_message_data()
        payload = make_webhook_payload(event, payload_data, session_id)
        raw_body = json.dumps(payload).encode()

        headers = {"Content-Type": "application/json"}
        if sign and secret:
            headers["X-OpenWA-Signature"] = sign_payload(raw_body, secret)
        if idempotency_key:
            headers["X-OpenWA-Idempotency-Key"] = idempotency_key

        return raw_body, payload, headers
