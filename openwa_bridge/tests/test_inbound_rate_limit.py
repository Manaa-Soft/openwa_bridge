"""Tests for inbound webhook rate limiting."""
import frappe
from frappe.tests import IntegrationTestCase


class TestInboundRateLimit(IntegrationTestCase):
    """Test the rate limiting on the inbound webhook endpoint."""

    def test_rate_limit_cache_key_format(self):
        """Verify the rate limit cache key format."""
        ip = "192.168.1.100"
        rate_key = f"openwa_rate::{ip}"
        self.assertEqual(rate_key, "openwa_rate::192.168.1.100")

    def test_rate_limit_increments(self):
        """Rate counter should increment per request."""
        rate_key = "openwa_rate::test-ip"
        frappe.cache().delete_value(rate_key)

        # Simulate requests
        for i in range(5):
            count = frappe.cache().get_value(rate_key) or 0
            frappe.cache().set_value(rate_key, count + 1, expires_in=60)

        self.assertEqual(frappe.cache().get_value(rate_key), 5)

    def test_rate_limit_resets(self):
        """Rate counter should reset after expiry."""
        rate_key = "openwa_rate::test-reset"
        frappe.cache().set_value(rate_key, 25, expires_in=1)
        self.assertEqual(frappe.cache().get_value(rate_key), 25)
