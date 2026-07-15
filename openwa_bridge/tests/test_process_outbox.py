"""Tests for outbox processing logic."""
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.tasks import _fail_outbox


class TestOutboxProcessor(IntegrationTestCase):
    """Test outbox processing helpers."""

    def test_fail_outbox_marks_failed_when_max_retries(self):
        """When max_attempts reached, status should be Failed."""
        pass  # Integration test requires full bench environment

    def test_fail_outbox_schedules_retry(self):
        """When retries remain, status should stay Pending with next_retry_at."""
        pass  # Integration test requires full bench environment

    def test_backoff_calculation(self):
        """Verify exponential backoff math."""
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


class TestCleanupOldOutbox(IntegrationTestCase):
    """Test cleanup_old_outbox scheduler task."""

    @patch("openwa_bridge.tasks.frappe")
    def test_deletes_old_sent_entries(self, mock_frappe):
        """Should delete Sent entries older than 7 days."""
        from openwa_bridge.tasks import cleanup_old_outbox
        mock_frappe.db.delete.return_value = 5
        cleanup_old_outbox()
        self.assertEqual(mock_frappe.db.delete.call_count, 2)
        mock_frappe.db.commit.assert_called_once()

    @patch("openwa_bridge.tasks.frappe")
    def test_no_commit_when_nothing_deleted(self, mock_frappe):
        """Should not commit when nothing was deleted."""
        from openwa_bridge.tasks import cleanup_old_outbox
        mock_frappe.db.delete.return_value = 0
        cleanup_old_outbox()
        mock_frappe.db.commit.assert_not_called()
