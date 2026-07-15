"""Unit tests for utils module — get_api_key, is_openwa_account, get_account_setting, validate_openwa_url."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from openwa_bridge.utils import (
    get_api_key,
    is_openwa_account,
    get_account_setting,
    validate_openwa_url,
    get_cached_account,
)


class TestGetApiKey(IntegrationTestCase):
    """Test get_api_key helper."""

    def test_returns_decrypted_key(self):
        """Should return the decrypted API key from a doc with get_password."""
        mock_account = MagicMock()
        mock_account.get_password.return_value = "decrypted-key-123"
        result = get_api_key(mock_account)
        self.assertEqual(result, "decrypted-key-123")
        mock_account.get_password.assert_called_once_with("openwa_api_key")

    def test_raises_on_no_key(self):
        """Should raise when account has no API key set."""
        mock_account = MagicMock()
        mock_account.get_password.side_effect = frappe.ValidationError("not set")
        with self.assertRaises(frappe.ValidationError):
            get_api_key(mock_account)


class TestIsOpenwaAccount(IntegrationTestCase):
    """Test is_openwa_account helper."""

    @patch("openwa_bridge.utils.frappe")
    def test_returns_true_when_enabled(self, mock_frappe):
        mock_frappe.db.get_value.return_value = 1
        self.assertTrue(is_openwa_account("test-account"))

    @patch("openwa_bridge.utils.frappe")
    def test_returns_false_when_disabled(self, mock_frappe):
        mock_frappe.db.get_value.return_value = 0
        self.assertFalse(is_openwa_account("test-account"))

    @patch("openwa_bridge.utils.frappe")
    def test_returns_false_when_none(self, mock_frappe):
        self.assertFalse(is_openwa_account(None))

    @patch("openwa_bridge.utils.frappe")
    def test_returns_false_when_empty_string(self, mock_frappe):
        self.assertFalse(is_openwa_account(""))


class TestGetAccountSetting(IntegrationTestCase):
    """Test get_account_setting helper."""

    def test_reads_from_doc(self):
        """Should read field from doc object."""
        mock_account = MagicMock()
        mock_account.openwa_cb_threshold = 10
        result = get_account_setting(mock_account, "openwa_cb_threshold", 5)
        self.assertEqual(result, 10)

    def test_returns_default_when_none(self):
        """Should return default when field is None."""
        mock_account = MagicMock()
        mock_account.openwa_cb_threshold = None
        result = get_account_setting(mock_account, "openwa_cb_threshold", 5)
        self.assertEqual(result, 5)

    def test_returns_default_when_zero(self):
        """Should return default when field is 0 (falsy)."""
        mock_account = MagicMock()
        mock_account.openwa_cb_threshold = 0
        result = get_account_setting(mock_account, "openwa_cb_threshold", 5)
        self.assertEqual(result, 5)

    def test_returns_none_when_no_default(self):
        """Should return None when field missing and no default."""
        mock_account = MagicMock(spec=[])  # no attributes
        result = get_account_setting(mock_account, "nonexistent", None)
        self.assertIsNone(result)


class TestValidateOpenwaUrl(IntegrationTestCase):
    """Test validate_openwa_url helper."""

    def test_valid_http_url(self):
        """Should accept valid HTTP URL."""
        result = validate_openwa_url("http://localhost:2785")
        self.assertEqual(result, "http://localhost:2785")

    def test_valid_https_url(self):
        """Should accept valid HTTPS URL."""
        result = validate_openwa_url("https://openwa.example.com")
        self.assertEqual(result, "https://openwa.example.com")

    def test_strips_whitespace(self):
        """Should strip whitespace from URL."""
        result = validate_openwa_url("  http://localhost:2785  ")
        self.assertEqual(result, "http://localhost:2785")

    def test_rejects_ftp(self):
        """Should reject non-http(s) schemes."""
        with self.assertRaises(frappe.ValidationError):
            validate_openwa_url("ftp://example.com")

    def test_rejects_empty(self):
        """Should reject empty URL."""
        with self.assertRaises(frappe.ValidationError):
            validate_openwa_url("")

    def test_rejects_no_hostname(self):
        """Should reject URL without hostname."""
        with self.assertRaises(frappe.ValidationError):
            validate_openwa_url("http://")

    @patch("openwa_bridge.utils.frappe")
    def test_warns_on_private_ip(self, mock_frappe):
        """Should warn (but not reject) private IP addresses."""
        mock_frappe.logger.return_value = MagicMock()
        result = validate_openwa_url("http://192.168.1.1:2785")
        self.assertEqual(result, "http://192.168.1.1:2785")
        mock_frappe.logger.return_value.info.assert_called_once()
