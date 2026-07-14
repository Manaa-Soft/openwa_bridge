# Improvement Plan

Comprehensive improvement plan for OpenWA Bridge — 6 phases, 50+ items.
Based on analysis of `frappe_whatsapp-master`, `OpenWA-main`, and `openwa_bridge-develop` codebases.

**Created**: 2026-07-14
**Branch**: `feature/improvements`
**Total estimated effort**: 23-33 hours across 9-12 sessions

---

## Phase 1: Code Quality & Correctness

**Effort**: 2-3 hours | **Impact**: High | **Status**: In Progress

### 1.1 Extract Duplicated Helpers to `utils.py`

- [ ] **`_get_api_key(account) -> str`** — eliminates 7+ copy-pasted `get_password("openwa_api_key")` patterns
  - Current locations: `utils.py`, `whatsapp_account.py` (2x), `whatsapp_message.py` (2x), `whatsapp_notification.py`, `tasks.py` (2x)
  - Replace all with single helper call

- [ ] **Move `_is_openwa_account(name) -> bool`** to `utils.py`
  - Currently duplicated in `whatsapp_notification.py:16-19` and `whatsapp_templates.py:10-14`
  - Both files should import from `utils.py`

- [ ] **`_get_cached_account(account_name) -> Document`** — cache WhatsApp Account doc with 5min TTL
  - Eliminates repeated `frappe.get_doc("WhatsApp Account", ...)` calls
  - Current: loaded 6+ times per message flow

### 1.2 Fix Documentation Inaccuracies

- [ ] **`hooks.py:10`** — `use_json_request_body = True` still present but KNOWN_ISSUES #17 says removed. Remove it or update docs.
- [ ] **`FLOWS.md:13`** — Says `after_insert → notify()` but actually `before_insert → notify()`. Correct the flow.
- [ ] **`ARCHITECTURE.md:31`** — Says `before_save()` for OverrideWhatsAppTemplates but actual hooks are `validate/after_insert/on_update`. Fix method names.
- [ ] **`DEPLOYMENT.md:137`** — Webhook events list wrong (`message, message.any`) vs actual (`message.received, message.ack, message.failed, session.status`). Fix events list.
- [ ] **`CUSTOM_FIELDS.md:60`** — Says "6 fields" for Templates but actually 8. Fix count.

### 1.3 Add `required_apps` to `hooks.py`

- [ ] Add `required_apps = ["frappe_whatsapp"]` — prevents installation without dependency

### 1.4 Add `before_install` Hook

- [ ] Verify `frappe_whatsapp` is installed before app install
- [ ] Add `before_install` function in `hooks.py`

### 1.5 Add `before_uninstall` Hook

- [ ] Clean up OpenWA sessions when app is uninstalled
- [ ] Remove orphaned webhooks from OpenWA

### 1.6 Fix `frappe.msgprint()` in Background Context

- [ ] `whatsapp_notification.py:234` — `_send_openwa_text()` uses `msgprint` (lost in background)
- [ ] `whatsapp_notification.py:265` — `_send_openwa_template()` uses `msgprint` (lost in background)
- [ ] Replace with `frappe.logger().info()` for background-safe logging

### 1.7 Remove Fragile `__new__()` Instantiation

- [ ] `tasks.py:333-343` — Manual attribute copying is fragile when upstream adds fields
- [ ] Replace with `frappe.get_doc()` or pass the doc directly

### 1.8 Consistent `from __future__ import annotations`

- [ ] Add to `inbound.py`, `whatsapp_message.py`, `whatsapp_notification.py`, `whatsapp_templates.py`
- [ ] Already present in: `utils.py`, `whatsapp_account.py`, `tasks.py`

---

## Phase 2: Security Hardening

**Effort**: 2-3 hours | **Impact**: High | **Status**: Not Started

### 2.1 Add Media Size Limit on Inbound

- [ ] Add `MAX_MEDIA_SIZE_MB = 10` (configurable via System Settings)
- [ ] Check `len(raw_data)` before base64 decode in `_attach_openwa_media()`
- [ ] Log and reject oversized media instead of storing

### 2.2 Add `openwa_hmac_strict` Field per Account

- [ ] New Check field on WhatsApp Account: `openwa_hmac_strict`
- [ ] When checked + secret configured: reject missing signatures
- [ ] Default: lenient (current behavior)
- [ ] Update `inbound.py` HMAC verification logic

### 2.3 Add Role Checks to Whitelisted Methods

- [ ] `setup_openwa_session()` — require System Manager or WhatsApp Manager role
- [ ] `get_openwa_session_status()` — require read permission on WhatsApp Account
- [ ] `get_openwa_qr()` — require read permission on WhatsApp Account
- [ ] `stop_openwa_session()` — require write permission on WhatsApp Account

### 2.4 Validate `openwa_base_url` Against SSRF

- [ ] Warn when private IP detected in URL
- [ ] Validate URL format on save
- [ ] Add `openwa_url_validated` field to track validation status

### 2.5 Remove Plaintext API Key Fallback

- [ ] Remove `account.get("openwa_api_key")` fallback in 8+ locations
- [ ] Always use `account.get_password("openwa_api_key")` (encrypted)
- [ ] Update `_get_api_key()` helper to use only `get_password()`

### 2.6 Add `X-Forwarded-For` Support for Rate Limiting

- [ ] Check `X-Forwarded-For` header before `remote_addr`
- [ ] Handle comma-separated list (take first IP)
- [ ] Prevent rate limit bypass behind reverse proxies

---

## Phase 3: Performance & Reliability

**Effort**: 3-4 hours | **Impact**: Medium | **Status**: Not Started

### 3.1 Cache WhatsApp Account Doc

- [ ] Create `_get_cached_account(account_name)` with 5min TTL
- [ ] Use `frappe.cache().set_value()` / `get_value()` pattern
- [ ] Update all 6+ locations that load the account doc

### 3.2 Fix N+1 in `_run_health_check()`

- [ ] Batch account loading with `frappe.get_all()` including API keys
- [ ] Remove per-account `frappe.get_doc()` calls
- [ ] Use batch API key retrieval from initial query

### 3.3 Use `requests.Session()` for Connection Pooling

- [ ] Create module-level `requests.Session()` in `utils.py`
- [ ] Replace all `requests.post/get()` calls with session methods
- [ ] Enable connection pooling (default: 10 connections)

### 3.4 Fix `process_pending_outbox` SQL Filtering

- [ ] Move Python filtering to SQL (next_retry_at <= now)
- [ ] Handle NULL `next_retry_at` in SQL query
- [ ] Reduce batch size from 50 to configurable value

### 3.5 Replace `time.sleep()` with Polling

- [ ] `whatsapp_account.py:89` — Replace 5s sleep with 1s polling (6 attempts)
- [ ] `whatsapp_account.py:104` — Replace 3s retry delay with polling
- [ ] `whatsapp_message.py:128` — Replace 5s sleep with polling
- [ ] `tasks.py:134` — Replace 3s sleep with polling

### 3.6 Make Timeouts Configurable

- [ ] Add `openwa_api_timeout` field to WhatsApp Account (default: 30s)
- [ ] Add `openwa_session_start_timeout` field (default: 60s)
- [ ] Update all timeout references to use configurable values

---

## Phase 4: Features & Integration

**Effort**: 8-12 hours | **Impact**: High | **Status**: Not Started

### 4.1 Leverage OpenWA Python SDK

- [ ] Install SDK: `bench pip install openwa` (or vendor it)
- [ ] Replace raw `requests` calls in `openwa_api()` with SDK client
- [ ] Use SDK type hints for better IDE support
- [ ] Update `whatsapp_message.py` to use SDK message methods

### 4.2 Add Bulk Message Support

- [ ] Add `bulk_message` field (Select: Yes/No) to WhatsApp Message
- [ ] Add `Bulk Targets` child table (phone_number, status per target)
- [ ] Use OpenWA `POST /messages/send-bulk` endpoint
- [ ] Process via outbox with priority queue
- [ ] Add progress tracking (sent/failed/total)

### 4.3 Add Frappe Workspace

- [ ] Create `openwa_bridge/openwa_bridge/workspace/whatsapp/whatsapp.json`
- [ ] Links: WhatsApp Account, WhatsApp Message, Templates, Notifications
- [ ] Quick actions: Send Message, Setup Session, View Outbox
- [ ] Charts: Queue depth, Success rate

### 4.4 Add Outbox Dashboard

- [ ] Create dashboard page with:
  - Queue depth (Pending/Sending/Sent/Failed counts)
  - Circuit breaker status per account
  - Success/failure rates (last 24h, 7d)
  - Average send time
- [ ] Use Frappe Chart API for visualizations

### 4.5 Make Hardcoded Values Configurable

- [ ] Rate limit: 60/min → WhatsApp Account field
- [ ] Idempotency TTL: 3600s → System Settings
- [ ] Circuit breaker threshold: 5 → WhatsApp Account field
- [ ] Circuit breaker cooldown: 300s → WhatsApp Account field
- [ ] Max outbox attempts: 5 → WhatsApp Account field
- [ ] Outbox batch size: 50 → System Settings
- [ ] Media size limit: 10MB → System Settings

### 4.6 Add Webhook Replay / Backfill

- [ ] Add `replay_webhooks(account_name, since)` whitelisted method
- [ ] Fetch missed messages from OpenWA API
- [ ] Create WhatsApp Message docs with proper deduplication
- [ ] Add UI button on WhatsApp Account form

---

## Phase 5: Testing

**Effort**: 6-8 hours | **Impact**: High | **Status**: Not Started

### 5.1 Integration Tests

- [ ] **`test_inbound_full_flow()`** — Webhook → HMAC → idempotency → message creation → profile
- [ ] **`test_outbox_full_flow()`** — Message create → outbox → send → status update
- [ ] **`test_outbox_retry_flow()`** — Send failure → backoff → retry → success
- [ ] **`test_circuit_breaker_integration()`** — 5 failures → open → cooldown → close
- [ ] **`test_notification_jinja_flow()`** — Notification → Jinja render → outbox → send
- [ ] **`test_notification_template_flow()`** — Notification → template → outbox → send
- [ ] **`test_template_sync_flow()`** — Save template → sync to OpenWA → stale recovery

### 5.2 Unit Tests

- [ ] **`_handle_status_update()`** — Valid status, missing message_id, unknown message
- [ ] **`_handle_session_status()`** — All status values, unknown account
- [ ] **`_resolve_account_by_session()`** — Found, not found, empty session_id
- [ ] **`_ensure_whatsapp_profile()`** — New profile, existing profile update
- [ ] **`on_account_update()`** — Webhook create, update, error handling
- [ ] **`on_account_trash()`** — Session delete, OpenWA unreachable
- [ ] **`render_doc_as_image()`** — PDF generation, missing print format
- [ ] **`openwa_api()`** — Success, error, timeout

### 5.3 Test Infrastructure

- [ ] Create `openwa_bridge/tests/fixtures/` with sample data
- [ ] Add conftest.py with common fixtures
- [ ] Mock OpenWA API responses for unit tests
- [ ] Add test runner configuration

### 5.4 Coverage Target

- [ ] Current: ~15-20%
- [ ] Target: 80%+
- [ ] Priority order: inbound → outbox → notification → template → session

---

## Phase 6: Documentation & Developer Experience

**Effort**: 2-3 hours | **Impact**: Low | **Status**: Not Started

### 6.1 Fix All Documentation Inaccuracies

- [ ] Update `hooks.py` to remove `use_json_request_body = True`
- [ ] Fix `FLOWS.md` Flow 1 order (before_insert, not after_insert)
- [ ] Fix `ARCHITECTURE.md` method names for Templates override
- [ ] Fix `DEPLOYMENT.md` webhook events list
- [ ] Fix `CUSTOM_FIELDS.md` field count for Templates

### 6.2 Add Inline Code Documentation

- [ ] Add docstrings to all public methods
- [ ] Include Args, Returns, Raises, Example
- [ ] Add module-level docstrings
- [ ] Document all `# noqa` suppressions with reason

### 6.3 Add CHANGELOG.md

- [ ] Track changes by version
- [ ] Format: Added, Fixed, Changed, Removed
- [ ] Link to commits
- [ ] Semantic versioning

### 6.4 Add Contributing Guidelines

- [ ] Development setup instructions
- [ ] Test running commands
- [ ] Code style requirements (ruff, pre-commit)
- [ ] PR template

### 6.5 Add Architecture Decision Records (ADRs)

- [ ] ADR-001: Why outbox pattern (not direct send)
- [ ] ADR-002: Why circuit breaker (not simple retry)
- [ ] ADR-003: Why lenient HMAC (not strict)
- [ ] ADR-004: Why override class pattern (not hooks)
- [ ] ADR-005: Why `after_insert` for outbox (not `before_insert`)

---

## Quick Wins Checklist

Low-effort, high-impact items that can be done in any session:

- [ ] Add `required_apps = ["frappe_whatsapp"]` — 1 line
- [ ] Extract `_get_api_key()` helper — 1 function, update 7 call sites
- [ ] Move `_is_openwa_account()` to `utils.py` — 1 function, update 2 files
- [ ] Fix `frappe.msgprint()` in background — 2 call sites
- [ ] Remove `use_json_request_body = True` — 1 line
- [ ] Fix 5 documentation inaccuracies — targeted edits
- [ ] Add `before_install` hook — 5 lines
- [ ] Add media size limit — 5 lines in `inbound.py`

---

## Progress Tracker

| Phase | Status | Items Done | Total |
|---|---|---|---|
| Phase 1: Code Quality | In Progress | 0 | 15 |
| Phase 2: Security | Not Started | 0 | 12 |
| Phase 3: Performance | Not Started | 0 | 14 |
| Phase 4: Features | Not Started | 0 | 15 |
| Phase 5: Testing | Not Started | 0 | 20 |
| Phase 6: Documentation | Not Started | 0 | 12 |
| **Total** | | **0** | **88** |
