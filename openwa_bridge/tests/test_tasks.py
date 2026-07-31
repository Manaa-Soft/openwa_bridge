"""Unit tests for the OpenWA session health check (OpenWA 0.12.x aware)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from frappe.tests import IntegrationTestCase


class _Acct:
    """Minimal stand-in for a WhatsApp Account dict returned by get_all."""

    name = "test-wa"
    openwa_base_url = "http://localhost:2785"
    openwa_session_id = "session-001"

    def get_password(self, field: str) -> str:
        return "test-api-key"


def _make_session(**kwargs) -> dict:
    data = {"status": "ready", "phone": "1234567890"}
    data.update(kwargs)
    return data


def _patch_http_session(**post_kwargs):
    """Patch tasks._http_session so .post returns a configured fake."""
    fake_resp = MagicMock()
    fake_resp.status_code = post_kwargs.get("status_code", 201)
    fake_resp.json.return_value = post_kwargs.get("json", {"id": "new-session-id"})
    fake_session = MagicMock()
    fake_session.post.return_value = fake_resp
    return patch("openwa_bridge.tasks._http_session", fake_session)


class TestHealthCheckEngineLoaded(IntegrationTestCase):
    """Health check must not restart a reconnecting (engine-loaded) session."""

    @patch("openwa_bridge.tasks.frappe")
    def test_disconnected_with_live_engine_not_restarted(self, mock_frappe):
        """disconnected + engineLoaded=true = reconnect backoff, leave it."""
        with (
            patch("openwa_bridge.tasks._get_openwa_accounts", return_value=[_Acct()]),
            patch("openwa_bridge.tasks.get_cached_account", return_value=_Acct()),
            patch(
                "openwa_bridge.tasks._check_session_status",
                return_value=_make_session(status="disconnected", engineLoaded=True),
            ),
            patch("openwa_bridge.tasks._start_session") as mock_start,
            patch("openwa_bridge.tasks._set_account_status") as mock_set_status,
        ):
            from openwa_bridge.tasks import _run_health_check
            _run_health_check()

        mock_start.assert_not_called()
        mock_set_status.assert_called_once_with("test-wa", "disconnected")

    @patch("openwa_bridge.tasks.frappe")
    def test_disconnected_no_engine_restarted(self, mock_frappe):
        """disconnected + engineLoaded=false = stopped, needs a start."""
        with (
            patch("openwa_bridge.tasks._get_openwa_accounts", return_value=[_Acct()]),
            patch("openwa_bridge.tasks.get_cached_account", return_value=_Acct()),
            patch(
                "openwa_bridge.tasks._check_session_status",
                return_value=_make_session(status="disconnected", engineLoaded=False),
            ),
            patch("openwa_bridge.tasks._start_session", return_value=True) as mock_start,
            patch("openwa_bridge.tasks._poll_status", return_value="ready") as mock_poll,
            patch("openwa_bridge.tasks._set_account_status") as mock_set_status,
        ):
            from openwa_bridge.tasks import _run_health_check
            _run_health_check()

        mock_start.assert_called_once()
        mock_poll.assert_called_once()
        mock_set_status.assert_called_once_with("test-wa", "ready")

    @patch("openwa_bridge.tasks.frappe")
    def test_disconnected_no_engine_field_restarted(self, mock_frappe):
        """Gateway < 0.12.1 omits engineLoaded — fall back to old behaviour."""
        with (
            patch("openwa_bridge.tasks._get_openwa_accounts", return_value=[_Acct()]),
            patch("openwa_bridge.tasks.get_cached_account", return_value=_Acct()),
            patch(
                "openwa_bridge.tasks._check_session_status",
                return_value=_make_session(status="disconnected"),
            ),
            patch("openwa_bridge.tasks._start_session", return_value=True) as mock_start,
            patch("openwa_bridge.tasks._poll_status", return_value="ready"),
            patch("openwa_bridge.tasks._set_account_status"),
        ):
            from openwa_bridge.tasks import _run_health_check
            _run_health_check()

        mock_start.assert_called_once()


class TestHealthCheckActionRequired(IntegrationTestCase):
    """Health check auto-recovers action_required via stop->start."""

    @patch("openwa_bridge.tasks.frappe")
    def test_action_required_stop_then_start(self, mock_frappe):
        with (
            patch("openwa_bridge.tasks._get_openwa_accounts", return_value=[_Acct()]),
            patch("openwa_bridge.tasks.get_cached_account", return_value=_Acct()),
            patch(
                "openwa_bridge.tasks._check_session_status",
                return_value=_make_session(
                    status="action_required", engineLoaded=True,
                    lastError="WhatsApp keeps showing its onboarding modal",
                ),
            ),
            patch("openwa_bridge.tasks._stop_session", return_value=True) as mock_stop,
            patch("openwa_bridge.tasks._start_session", return_value=True) as mock_start,
            patch("openwa_bridge.tasks._poll_status", return_value="ready"),
            patch("openwa_bridge.tasks._set_account_status"),
        ):
            from openwa_bridge.tasks import _run_health_check
            _run_health_check()

        mock_stop.assert_called_once()
        mock_start.assert_called_once()
        mock_frappe.log_error.assert_not_called()

    @patch("openwa_bridge.tasks.frappe")
    def test_action_required_still_stuck_logs_operator_alert(self, mock_frappe):
        with (
            patch("openwa_bridge.tasks._get_openwa_accounts", return_value=[_Acct()]),
            patch("openwa_bridge.tasks.get_cached_account", return_value=_Acct()),
            patch(
                "openwa_bridge.tasks._check_session_status",
                return_value=_make_session(status="action_required"),
            ),
            patch("openwa_bridge.tasks._stop_session", return_value=True),
            patch("openwa_bridge.tasks._start_session", return_value=True),
            patch(
                "openwa_bridge.tasks._poll_status",
                return_value="action_required",
            ),
            patch("openwa_bridge.tasks._set_account_status"),
        ):
            from openwa_bridge.tasks import _run_health_check
            _run_health_check()

        mock_frappe.log_error.assert_called_once()


class TestHealthCheckFailed(IntegrationTestCase):
    """Health check recreates failed sessions without a force-kill (0.12.0+)."""

    @patch("openwa_bridge.tasks.frappe")
    def test_failed_recreates_without_force_kill(self, mock_frappe):
        with (
            patch("openwa_bridge.tasks._get_openwa_accounts", return_value=[_Acct()]),
            patch("openwa_bridge.tasks.get_cached_account", return_value=_Acct()),
            patch(
                "openwa_bridge.tasks._check_session_status",
                return_value=_make_session(status="failed"),
            ),
            patch("openwa_bridge.tasks._delete_session") as mock_delete,
            patch("openwa_bridge.tasks._start_session", return_value=True) as mock_start,
            patch("openwa_bridge.tasks._poll_status", return_value="qr_ready"),
            patch("openwa_bridge.tasks._set_account_status"),
            _patch_http_session(status_code=201, json={"id": "new-session-id"}),
        ):
            from openwa_bridge.tasks import _run_health_check
            _run_health_check()

        mock_delete.assert_called_once()
        # _start_session is called for the fresh session only, never a force-kill.
        mock_start.assert_called_once()
