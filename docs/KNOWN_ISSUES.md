# Known Issues & Fixes

## Current Active Issues

### 1. send-template 400 Bad Request (under investigation)

**Status**: Under investigation
**Error**: `400 Client Error: Bad Request for url: .../messages/send-template`
**What we know**: Template ID synced from OpenWA, payload format matches DTO. May be stale UUID or session state issue.
**Fix applied**: Detailed error logging captures full request/response bodies.
**Next step**: Test after session restart, check Error Log for "OpenWA API Error" entry.

### 2. Typing indicator redundancy

**Status**: By design — configurable
**Description**: OpenWA v0.10.9+ has built-in `SIMULATE_TYPING=true` (anti-ban). Bridge also sends its own typing indicator before non-template/non-reaction messages via `_send_typing_indicator()`. This causes double typing indicators.
**Fix**: Disable one or the other:
- Keep OpenWA's `SIMULATE_TYPING=true`, disable bridge's auto-typing: set `openwa_auto_typing=0` in OpenWA Bridge Settings
- Or set `SIMULATE_TYPING=false` in OpenWA `.env` and keep bridge's auto-typing

---

## Resolved Issues

### 2. Messages marked Sent when session disconnected
**Symptom**: Outbox entries marked as "Sent" even when the WhatsApp session was manually disconnected or unreachable.
**Root cause**: OpenWA engine accepts messages even when disconnected — `ready` status means engine alive, NOT WhatsApp linked. The 500 false-positive code also assumed delivery on any HTTP 500.
**Fix applied**:
1. Pre-send session check in outbox processor verifies `status=ready` AND `phone` field before sending (`tasks.py`)
2. Removed false-positive 500→Sent assumption — ANY HTTP error now retries with backoff (`whatsapp_message.py`)
3. Ack status downgrade protection — statuses only advance, never downgrade (`inbound.py`)
**Commit**: `c36deb3`

### 3. Redis config breaks ERPNext when sharing same server
**Symptom**: After configuring Redis for OpenWA, ERPNext background jobs stall, email sending fails, real-time updates break.
**Root cause**: Adding `requirepass` or `allkeys-lru` to global `/etc/redis/redis.conf` conflicts with ERPNext's existing Redis instances (ports 6379/6380/6381).
**Fix**: Create a dedicated Redis instance for OpenWA on a separate port (6385). See [Redis Isolation](DEPLOYMENT.md#redis-isolation-erpnext--openwa-on-same-server) for full guide.
**Prevention**: Never modify the global Redis config when ERPNext is running on the same server.

### 4. "Password not found for WhatsApp Account X token"
**Fix**: `send_template_message()` now resolves account from `self.whatsapp_account` or default outgoing BEFORE checking `_is_openwa_account()`. When OpenWA account detected, `super()` (which reads Meta token) is never called.
**File**: `whatsapp_notification.py` → `send_template_message()`

### 4. send-image 400 Bad Request (mimetype)
**Fix**: Added `"mimetype": "image/png"` to send-image payload. Also sends image with text as caption (1 message instead of 2).
**File**: `whatsapp_notification.py`, `tasks.py` → `_send_dynamic_header_for_outbox()`

### 5. OpenWA Enabled checkbox not visible on new account
**Fix**: Removed `depends_on` from Section Break, added to individual fields.
**File**: `fixtures/custom_field.json`

### 6. frappe.db.get_value() TypeError on every inbound message
**Symptom**: `TypeError: Database.get_value() got an unexpected keyword argument 'fields'` on every `message.sent` webhook, causing 500 errors on status updates.
**Root cause**: Three calls in `inbound.py` used `frappe.db.get_value(doctype, filters=..., fields=["name"], pluck="name")` with `fields=` keyword. `frappe.db.get_value()` does not accept `fields=` — it uses a positional `fieldname` parameter.
**Fix applied**: Changed all 3 calls to use positional `fieldname` parameter: `frappe.db.get_value(doctype, filters, "name", pluck="name")`.
**File**: `inbound.py`
**Commit**: `a14c6cd`

### 7. API timeout during setup (read timeout=15)
**Fix**: Increased `openwa_api` timeout to 30s, `_start_session` to 60s. Added connectivity check.
**Files**: `utils.py`, `whatsapp_account.py`

### 8. Deleting WhatsApp Account doesn't delete OpenWA session
**Fix**: `on_trash` doc_events hook calls `DELETE /api/sessions/:id`.
**File**: `whatsapp_account.py`, `hooks.py`

### 9. Sessions don't reconnect after VM restart
**Fix**: `tasks.py` with `hourly()` and `daily()` scheduler hooks restart disconnected sessions. Pre-send check in `whatsapp_message.py` auto-restarts before every send.
**Files**: `tasks.py`, `hooks.py`, `whatsapp_message.py`

### 10. jinja `doc.items` AttributeError
**Fix**: Use `frappe.get_doc()` to get proper Document object, not `as_dict()`.
**File**: `whatsapp_notification.py` → `_resolve_document()`

### 11. 409 Conflict on Template Sync
**Fix**: Stale ID recovery — 404 → clear ID → find by name → POST fresh.
**File**: `whatsapp_templates.py` → `_sync_to_openwa()`

### 12. `content_type` AttributeError
**Fix**: Hardcode `"text"` instead of `self.content_type` in WhatsApp Message creation.
**File**: `whatsapp_notification.py` → `_send_openwa_template()`, `_send_openwa_text()`

### 13. `@lid` JID Format Not Handled
**Fix**: Added `@lid` to `strip_jid_suffix()` suffix list.
**File**: `utils.py` → `strip_jid_suffix()`

### 14. frappe.cache() kwarg
**Fix**: Use `frappe.cache().set_value()` with `expires_in_sec=` (not Redis native `set()` with `ex=`).
**File**: `inbound.py`

### 15. frappe.throw() kwarg
**Fix**: Remove `statusCode` kwarg (not supported). Inbound webhook now always returns 200.
**File**: `inbound.py`

### 16. Sample Values Auto-fill Removed
**Fix**: Removed `_SAMPLEDefaults` mixin. Values now read from live doc at send time.
**File**: `whatsapp_templates.py`

### 17. Error log flood from HMAC verification
**Fix**: "Missing HMAC signature" changed from `frappe.log_error()` to `frappe.logger().info()`. Catch-all handler now includes traceback. Success confirmation logged after doc insert.
**File**: `inbound.py`

### 18. use_json_request_body causing 417
**Fix**: Removed `use_json_request_body = True` from hooks.py — Frappe middleware was rejecting webhook payloads.
**File**: `hooks.py`

### 19. frappe.request.get_data(as_bytes=False) invalid kwarg
**Fix**: Changed to `get_data()` without kwargs — `as_bytes` not supported in this Frappe version.
**File**: `inbound.py`

### 20. HMAC verification too strict
**Fix**: When secret is configured but OpenWA sends no signature, processes the message with a warning log instead of rejecting. Prevents drops when HMAC isn't enabled on the OpenWA side.
**File**: `inbound.py`

### 21. Webhook secret not synced to OpenWA
**Fix**: `on_update` hook auto-syncs webhook secret — lists webhooks, finds by URL match, updates via PUT or creates via POST.
**File**: `whatsapp_account.py` → `on_account_update()`

---

## Server Environment

| Item | Value |
|---|---|
| Server IP | 192.168.1.15 |
| OpenWA API | http://localhost:2785 |
| OpenWA Dashboard | http://localhost:2886 |
| OpenWA Session | "manaa" (UUID: 14445ea6-6cb0-486c-ae81-4bd135fb21bd) |
| OpenWA Phone | +967777713637 |
| OpenWA API Key | *(stored in WhatsApp Account Password field)* |
| OpenWA Webhook ID | *(configured in OpenWA dashboard)* |
| Webhook Secret | *(stored in WhatsApp Account Password field)* |
| PyMuPDF | Installed on server (fitz import works) |
| Git Repo | https://github.com/Manaa-Soft/openwa_bridge.git (branch: develop) |
| GitHub Auth | PAT required (password auth disabled) |

---

## OpenWA Key Behaviors

1. **send-template is text-only**: It renders header+body+footer → joins with `\n\n` → sends as plain text via `sendText()`. No media support.
2. **Template vars are `Record<string, string>`**: Keys must match `{{key}}` in the template body.
3. **Template uniqueness**: Names are unique per session (not globally).
4. **SSRF blocks private IPs**: Must set `SSRF_ALLOWED_HOSTS` in `OpenWA/data/.env.generated`.
5. **Webhook signature header**: `X-Openwa-Signature` (lowercase 'wa').
6. **HMAC format**: `sha256=<hex-digest>` of `HMAC-SHA256(secret, JSON.stringify(payload))`.
