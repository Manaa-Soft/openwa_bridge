# Changelog

All notable changes to OpenWA Bridge will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

## [Unreleased] - feature/improvements branch

### Added
- **Contact management API** — `check_whatsapp_number`, `block_contact`, `unblock_contact` via OpenWA REST endpoints
- **Typing indicators** — `send_typing_indicator` (typing/recording/paused states) via OpenWA `/chats/typing`
- **Bulk messaging** — `send_bulk_openwa` via OpenWA `/messages/send-bulk` endpoint
- **Message forwarding** — `forward_message` via OpenWA `/messages/forward` endpoint
- **Message deletion** — `delete_message` with optional revoke (delete for everyone) via `DELETE /messages/:id`
- **Pairing code authentication** — `request_pairing_code` as alternative to QR scanning via `/pairing-code`
- **Sticker messages** — `send_sticker` (URL or base64) + `sticker` content type in outbound dispatcher
- **@mention support** — auto-extracts `@NNNN@c.us` JIDs from text messages, passes to OpenWA mentions array
- **Extended webhook events** — `message.sent`, `message.revoked`, `message.reaction`, `session.qr`, `session.authenticated`, `session.disconnected`
- **Inbound Communication/Lead auto-creation** — `_create_communication()` creates Communication doc linked to Contact; auto-creates Lead+Contact for new numbers
- **Outbox cleanup scheduler** — daily `cleanup_old_outbox()` deletes Sent entries >7 days, Failed entries >30 days
- **Circuit breaker** — blocks sends after N consecutive failures per account
- **Two-phase notification pattern** — sync outbox creation + async background send
- **Inbound webhook hardening** — always HTTP 200, HMAC lenient/strict mode, idempotency, rate limiting
- **Idempotency key collision fix** — augments `_unknown_` keys with MD5 of body+sender to prevent silent dedup of rapid-fire messages during reconnect cycles
- **Auto-sync webhook secret** — saving WhatsApp Account auto-syncs secret to OpenWA
- **Session health check** — hourly/daily scheduler restarts disconnected sessions
- **Pre-send session verification** — auto-restarts sessions before sending messages
- **Dynamic image headers** — renders documents as PNG via Print Format + PyMuPDF
- **SSRF validation** — URL validation warns on private/internal IPs
- **SSRF protection bypass** — `WEBHOOK_SSRF_PROTECT=false` in OpenWA `.env` to allow webhook delivery to private IPs (192.168.x.x) on local network
- **Role-based access** — whitelisted methods check Frappe permissions
- **Configurable values** — rate limit, circuit breaker, timeouts, max attempts all configurable via OpenWA Bridge Settings
- **OpenWA Bridge Settings DocType** — system-wide config for idempotency TTL, batch size, media limits, HMAC strict, rate limit, circuit breaker, timeouts, max outbox attempts
- **WhatsApp Workspace** — shortcuts to all key DocTypes
- **`get_api_key()` helper** — eliminates duplicated `get_password()` patterns
- **`is_openwa_account()` helper** — single source of truth for account checks
- **`get_cached_account()` helper** — 5-minute TTL cache for WhatsApp Account docs
- **`validate_openwa_url()` helper** — SSRF-safe URL validation
- **`required_apps`** — prevents install without frappe_whatsapp
- **`before_install` hook** — verifies frappe_whatsapp dependency
- **`from __future__ import annotations`** — consistent type annotation support
- **Comprehensive test suite** — 40+ unit and integration tests
- **HTTP connection pooling** — module-level `requests.Session()` for all API calls
- **Polling loops** — replaced `time.sleep()` with faster ready-state detection
- **Distributed lock** — Redis-based lock prevents duplicate outbox processing
- **Replay webhooks API** — fetches messages from OpenWA and creates WhatsApp Message docs
- **Outbox dashboard API** — system health, success rates, queue depth, circuit breaker status
- **Architecture Decision Records** — 5 ADRs documenting key design decisions
- **Uninstall cleanup** — `before_uninstall()` deletes OpenWA sessions and webhooks

### Fixed
- **PDF messages invisible in CRM thread** — a PDF sent from a Sales Invoice (or any non-CRM doctype) was linked only to that document, so it never appeared in the CRM Deal/Lead/Contact thread. `send_document_pdf` now links the message to the CRM record matching the recipient number (via `crm.integrations.api.get_contact_lead_or_deal_from_number`) while keeping the rendered document in new `openwa_render_doctype`/`openwa_render_name` fields; `_send_via_openwa()` renders the PDF from those (falling back to the reference for existing messages).
- **Reference fields clobbered by CRM validate hook** — `send_document_pdf` (and template/dialog sends) had `reference_doctype`/`reference_name` silently overwritten (or nulled) by the CRM app's `doc_events` validate hook, which resolves the recipient's number to a Contact/Lead/Deal. `OverrideWhatsAppMessage` now snapshots the reference in `before_validate()` and restores it in `before_save()`. Incoming messages (no explicit reference) are untouched, so CRM auto-linking still works.
- **Messages marked Sent when session disconnected** — Added pre-send session check in outbox processor that verifies `status=ready` AND `phone` field is set before attempting to send. Prevents sending to a disconnected engine that accepts messages (201+messageId) but can never deliver them.
- **500 HTTP false-positive marked messages as Sent** — Removed incorrect assumption that HTTP 500 from OpenWA means "engine delivered before error". OpenWA's `persistSentState` always returns 201+messageId on success (swallows DB errors); `failSend` always throws 500 with NO messageId. ANY 500 now triggers retry with backoff.
- **Ack status downgrade** — `message.ack` handler now only allows forward transitions (pending → Sent → Delivered → Read), never downgrades. Late "sent" acks after "delivered" are silently skipped. Matches OpenWA's own `ackStatusTransitionFrom` guard.
- **PLAYED ack not normalized** — Baileys ack 5 (PLAYED, e.g. voice note auto-read) now maps to `Read` instead of creating an invalid `Played` status.
- **Duplicate text on dynamic header template sends** — a template with `openwa_dynamic_header` sent the approved text twice: once as the image caption and once as a separate template bubble. The image+caption (header/body/footer, placeholders rendered) is now the **only** delivery and the template bubble is skipped. If the image fails, the approved template text is sent as a fallback.
- **Duplicate message bug** — notification flow with dynamic header sent duplicate messages (user confirmed resolved)
- **Atomic image+caption** — dynamic header image failures now raise exception for outbox retry instead of falling through to text send (which created duplicates)
- **Templates with no variables always threw** — templates without `{{...}}` placeholders now send without requiring variables
- **Stale template ID after session recreate** — recovery code now looks up template by name on OpenWA, re-creates it if missing, and verifies content matches before returning ID
- **`_recover_template_id` used non-existent field** — changed `openwa_name` to `actual_name` (the real Frappe field)
- **`frappe.db.reload_doc` crash** — removed invalid call that crashed template recovery
- **Recovery returned stale ID** — `_sync_to_openwa()` used `db.set_value()` but didn't update in-memory doc attribute; now sets `self.openwa_template_id` directly
- **Recovery didn't verify sync success** — now re-reads from DB after sync and checks ID actually changed
- **`_translate_template_payload` crashed on list body_param** — `.values()` on list raised `AttributeError`; now handles both dict and list formats
- **`_find_openwa_by_name` silently swallowed errors** — now logs exceptions instead of `pass`
- **Image size unbounded** — dynamic header images rendered at 2x zoom could exceed WhatsApp limits; now auto-compresses to JPEG with 2x→1x fallback and 3MB cap
- **Image mimetype hardcoded** — now auto-detects JPEG vs PNG from file header bytes
- **Broken test imports** — corrected `is_openwa_account` import path, outbox test import path, frappe cache `expires_in_sec` param name
- **`RedisWrapper.set_value()` `only_set` unsupported** — Frappe's RedisWrapper doesn't support `only_set`. Changed to `get_value` + `set_value` pattern.
- **Global settings on wrong DocType** — Moved 7 settings (HMAC strict, API timeout, session start timeout, rate limit, CB threshold, CB cooldown, max outbox attempts) from per-account WhatsApp Account to global OpenWA Bridge Settings.
- **UnboundLocalError in outbox processor** — `account` referenced before definition at `tasks.py:179`, causing every outbox attempt to crash. Messages stuck as Pending forever.
- **Template sent as raw Meta dict** — `_send_openwa_template()` stored the entire Meta payload dict as `msg.message` instead of rendered text.
- **Dynamic header skipped template** — when the dynamic header image is sent successfully, the image+caption is the whole delivery and the separate template text is intentionally skipped so the recipient never receives the text twice; if the image fails, the approved template text is sent as a fallback.
- **Jinja sent as raw template** — `_send_openwa_text()` set `template` on the doc, causing `send-template` path with empty vars instead of `send-text`.
- **`requests` not defined** — 7 bare `requests.post()` calls missed during connection pooling migration.
- **Template vs text send guard** — Changed `_send_via_openwa` guard from `if self.template` to `if self.use_template and self.template` so Jinja messages use `send-text`.
- **Jinja send type bypassed OpenWA** — `send_template_message()` fell through to parent's Meta API for `send_type != "Template"`. Now allows `"Jinja"` through.
- **Empty vars sent to OpenWA** — Bulk WhatsApp Messages with empty `template_variables` sent `vars: {}` to `send-template`, causing 500. Now validates before sending and throws clear error. Also handles `body_param` as both dict and list format.
- **`get_account_setting` treats `0` as falsy** — Setting threshold/cooldown to `0` returned the default instead. Changed `val if val` to `val if val is not None`.
- **`time.sleep()` blocking workers** — Reduced `_ensure_session_ready()` polling from 20s to 3s to prevent worker starvation.
- **Outbox race condition** — Added Redis-based distributed lock with 30s TTL to prevent duplicate processing.
- **`frappe.db.commit()` anti-pattern** — Removed explicit commit from inbound webhook endpoint.
- **Idempotency race condition** — Changed to atomic `set_value` with `only_set=True` for dedup.
- **`replay_webhooks` wrong field** — Changed `"to"` to `"from"` for incoming message creation.
- **Wrong `max_attempts` source** — Changed from `System Settings.max_auto_retry_count` to constant `5`.
- **Redundant wrappers** — Removed `_is_openwa_account` wrappers in notification and templates.
- **Inconsistent `_http_session` usage** — Replaced `import requests as _req` with `_http_session` in notification.
- **"Password not found" error** — account resolution before `_is_openwa_account()` check
- **`frappe.msgprint()` in background** — replaced with `frappe.logger().info()`
- **Fragile `__new__()` instantiation** — uses overridden doc directly via `override_doctype_class`
- **N+1 queries in health check** — cached account loading
- **Outbox SQL filtering** — Python filtering moved to SQL with NULL handling
- **Documentation inaccuracies** — FLOWS.md, ARCHITECTURE.md, DEPLOYMENT.md, CUSTOM_FIELDS.md corrected
- **`use_json_request_body = True`** — removed (was causing 417 rejection)

### Changed
- **OpenWA v0.18 API contract support** — all endpoints upgraded to the v0.18 DTOs:
  - `send_bulk_openwa` / `send_bulk_with_progress` now submit per-message items (`chatId` + `type` + `content`) in chunks of ≤100 (OpenWA's `send-bulk` cap) to `POST /messages/send-bulk`. OpenWA drains the batch asynchronously and returns `202` + `batchId`; the bridge polls `GET /messages/batch/:batchId` (≤30 × 2s) and reports `sent`/`failed`/`pending`/`cancelled`/`total` counts.
  - `forward_message` now sends `fromChatId` + `toChatId` + `messageId` (v0.18 DTO). The source chat is resolved from the stored WhatsApp Message via the new `_resolve_message_chat_id()` helper.
  - `delete_message` now uses `POST /messages/delete` with `{chatId, messageId, forEveryone}` (replaces `DELETE /messages/:id?revoke=`). A `503` from OpenWA (outcome uncertain — "may or may not have been applied") is treated as applied rather than an error so callers don't retry and duplicate.
  - `mark_chat_read` / `mark_chat_unread` now POST to `/chats/read` / `/chats/unread` with `{chatId}` in the JSON body.
  - `get_chat_history` now reads `GET /messages/:chatId/history`.
  - `add_group_participants` / `remove_group_participants` now use `POST` / `DELETE /groups/:groupId/participants` (replaces `/participants/add` and `/participants/remove`).
  - `get_session_stats` now reads `/api/sessions/stats/overview` via the raw call path.
  - `get_contact_statuses` now unwraps the `{statuses: [...]}` envelope returned by v0.18.
  - `list_profile_pictures` now accepts an optional `contacts` argument (comma-separated JIDs, capped at 50) and reads `{pictures: [...]}`.
- **`check_whatsapp_number` v0.18 response** — OpenWA v0.18 returns `{exists, whatsappId}` instead of `isRegistered`; the bridge now reads `exists`/`whatsappId` and returns `jid` = `whatsappId`.
- **`replay_webhooks` v0.18 field mapping** — reads `createdAt` and `waMessageId` (v0.18 webhook field names), lowercases `direction`, skips anything that is not `incoming`, and normalises epoch-seconds vs millisecond timestamps.
- **Send-as-PDF dialog preview removed** — the live iframe preview pane and its status line were dropped from the "Send To Whatsapp" dialog. The dialog still verifies the document renders with the chosen Print Format/Language/Letter Head/Print Settings (`get_html_and_style`) before enabling Send; render failures surface as a message instead of inline status text. Errors from cross-doctype print formats are unchanged.
- **Settings moved to WhatsApp Account** — 7 settings (HMAC strict, API timeout, session start timeout, rate limit, CB threshold, CB cooldown, max outbox attempts) moved from OpenWA Bridge Settings to per-account WhatsApp Account. Each account now has independent config.
- **WhatsApp Account custom fields** — expanded from 9 to 16 fields (7 new settings in collapsible "Settings" section)
- **OpenWA Bridge Settings** — reduced from 10 to 3 fields (only idempotency TTL, outbox batch size, media size limit remain global)
- **All `requests.get/post` calls** — replaced with `_http_session` for connection pooling
- **All `get_password("openwa_api_key")` calls** — replaced with `get_api_key()` helper
- **All `_is_openwa_account()` definitions** — consolidated to single `is_openwa_account()` in utils.py
- **`get_api_key()` function** — removed plaintext fallback, always uses encrypted `get_password()`
- **Inbound rate limiting** — moved after account resolution for per-account config
- **WhatsApp Account custom fields** — reduced from 16 to 9 fields (7 moved to global settings)
- **WhatsApp Templates custom fields** — expanded from 6 to 8 fields
- **Global settings** — HMAC strict, API timeout, session start timeout, rate limit, CB threshold, CB cooldown, max outbox attempts moved to OpenWA Bridge Settings

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
