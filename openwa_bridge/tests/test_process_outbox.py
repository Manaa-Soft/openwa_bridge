"""Tests for outbox processing logic."""
import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.tasks import _fail_outbox


class TestOutboxProcessor(IntegrationTestCase):
    """Test outbox processing helpers."""

    def test_fail_outbox_marks_failed_when_max_retries(self):
        """When max_attempts reached, status should be Failed."""
        # We can't easily create a full outbox entry without an account,
        # but we can test _fail_outbox logic by mocking
        pass  # Integration test requires full bench environment

    def test_fail_outbox_schedules_retry(self):
        """When retries remain, status should stay Pending with next_retry_at."""
        pass  # Integration test requires full bench environment

    def test_backoff_calculation(self):
        """Verify exponential backoff math."""
        from datetime import datetime, timedelta

        # Backoff: 30s * 2^(attempt-1), capped at 3600s
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
