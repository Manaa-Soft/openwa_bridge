# Changelog

All notable changes to OpenWA Bridge will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

## [Unreleased] - feature/improvements branch

### Added
- **OpenWA Outbox DocType** — async message queue with exponential backoff retry
- **Circuit breaker** — blocks sends after N consecutive failures per account
- **Two-phase notification pattern** — sync outbox creation + async background send
- **Inbound webhook hardening** — always HTTP 200, HMAC lenient/strict mode, idempotency, rate limiting
- **Auto-sync webhook secret** — saving WhatsApp Account auto-syncs secret to OpenWA
- **Session health check** — hourly/daily scheduler restarts disconnected sessions
- **Pre-send session verification** — auto-restarts sessions before sending messages
- **Dynamic image headers** — renders documents as PNG via Print Format + PyMuPDF
- **SSRF validation** — URL validation warns on private/internal IPs
- **Role-based access** — whitelisted methods check Frappe permissions
- **Configurable values** — rate limit, circuit breaker, timeouts, max attempts all configurable per account
- **OpenWA Bridge Settings DocType** — system-wide config for idempotency TTL, batch size, media limits
- **WhatsApp Workspace** — shortcuts to all key DocTypes
- **`get_api_key()` helper** — eliminates duplicated `get_password()` patterns
- **`is_openwa_account()` helper** — single source of truth for account checks
- **`get_cached_account()` helper** — 5-minute TTL cache for WhatsApp Account docs
- **`get_account_setting()` helper** — reads configurable values from account docs
- **`validate_openwa_url()` helper** — SSRF-safe URL validation
- **`required_apps`** — prevents install without frappe_whatsapp
- **`before_install` hook** — verifies frappe_whatsapp dependency
- **`from __future__ import annotations`** — consistent type annotation support
- **Comprehensive test suite** — 40+ unit and integration tests
- **HTTP connection pooling** — module-level `requests.Session()` for all API calls
- **Polling loops** — replaced `time.sleep()` with faster ready-state detection

### Fixed
- **UnboundLocalError in outbox processor** — `account` referenced before definition at `tasks.py:179`, causing every outbox attempt to crash. Messages stuck as Pending forever.
- **Template sent as raw Meta dict** — `_send_openwa_template()` stored the entire Meta payload dict as `msg.message` instead of rendered text.
- **Dynamic header skipped template** — When image sent successfully, outbox returned early, skipping the template send entirely.
- **Jinja sent as raw template** — `_send_openwa_text()` set `template` on the doc, causing `send-template` path with empty vars instead of `send-text`.
- **`requests` not defined** — 7 bare `requests.post()` calls missed during connection pooling migration.
- **Template vs text send guard** — Changed `_send_via_openwa` guard from `if self.template` to `if self.use_template and self.template` so Jinja messages use `send-text`.
- **Jinja send type bypassed OpenWA** — `send_template_message()` fell through to parent's Meta API for `send_type != "Template"`. Now allows `"Jinja"` through.
- **Empty vars sent to OpenWA** — Bulk WhatsApp Messages with empty `template_variables` sent `vars: {}` to `send-template`, causing 500. Now validates before sending and throws clear error. Also handles `body_param` as both dict and list format.
- **"Password not found" error** — account resolution before `_is_openwa_account()` check
- **`frappe.msgprint()` in background** — replaced with `frappe.logger().info()`
- **Fragile `__new__()` instantiation** — uses overridden doc directly via `override_doctype_class`
- **N+1 queries in health check** — cached account loading
- **Outbox SQL filtering** — Python filtering moved to SQL with NULL handling
- **Documentation inaccuracies** — FLOWS.md, ARCHITECTURE.md, DEPLOYMENT.md, CUSTOM_FIELDS.md corrected
- **`use_json_request_body = True`** — removed (was causing 417 rejection)

### Changed
- **All `requests.get/post` calls** — replaced with `_http_session` for connection pooling
- **All `get_password("openwa_api_key")` calls** — replaced with `get_api_key()` helper
- **All `_is_openwa_account()` definitions** — consolidated to single `is_openwa_account()` in utils.py
- **`get_api_key()` function** — removed plaintext fallback, always uses encrypted `get_password()`
- **Inbound rate limiting** — moved after account resolution for per-account config
- **WhatsApp Account custom fields** — expanded from 9 to 16 fields
- **WhatsApp Templates custom fields** — expanded from 6 to 8 fields

## [0.1.0] - 2026-07-01

### Added
- Initial release
- OpenWA integration via `override_doctype_class`
- Outbound message routing (text, image, video, audio, document, location, contact, poll, reaction, reply, template)
- Inbound message reception via webhook
- Template sync to OpenWA
- Notification system (Jinja, Template, Dynamic Image Header)
- QR code setup flow
- Auto-reconnect scheduled tasks
