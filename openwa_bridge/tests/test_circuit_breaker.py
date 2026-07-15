"""Tests for OpenWACircuitBreaker."""
import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.utils import OpenWACircuitBreaker


class TestOpenWACircuitBreaker(IntegrationTestCase):
    """Test the circuit breaker pattern."""

    def setUp(self):
        super().setUp()
        self.cb = OpenWACircuitBreaker("test-account", threshold=3, cooldown_seconds=60)
        # Clean slate
        self.cb.record_success()

    def test_starts_closed(self):
        """Circuit should start closed."""
        self.assertFalse(self.cb.is_open())
        self.assertEqual(self.cb.remaining_cooldown(), 0)

    def test_trips_after_threshold(self):
        """Circuit should trip after N consecutive failures."""
        self.cb.record_failure()
        self.assertFalse(self.cb.is_open())

        self.cb.record_failure()
        self.assertFalse(self.cb.is_open())

        self.cb.record_failure()
        self.assertTrue(self.cb.is_open())

    def test_success_resets_counter(self):
        """A success should reset the failure counter."""
        self.cb.record_failure()
        self.cb.record_failure()
        self.cb.record_success()
        self.assertFalse(self.cb.is_open())

        # Two more failures should not trip (counter was reset)
        self.cb.record_failure()
        self.cb.record_failure()
        self.assertFalse(self.cb.is_open())

    def test_cooldown_expiry(self):
        """Circuit should close after cooldown expires."""
        # Trip the circuit
        for _ in range(3):
            self.cb.record_failure()
        self.assertTrue(self.cb.is_open())

        # Simulate cooldown expiry by manipulating the cached timestamp
        import datetime
        old_time = (datetime.datetime.now() - datetime.timedelta(seconds=120)).isoformat()
        frappe.cache().set_value(f"{self.cb._key_tripped}", old_time, expires_in_sec=600)

        self.assertFalse(self.cb.is_open())

    def test_remaining_cooldown(self):
        """remaining_cooldown should return seconds until circuit closes."""
        self.assertEqual(self.cb.remaining_cooldown(), 0)

        # Trip the circuit
        for _ in range(3):
            self.cb.record_failure()

        remaining = self.cb.remaining_cooldown()
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 60)
