# Session Log

Chronological log of all development work on the OpenWA Bridge project.

---

## Session 1: Initial Bridge Setup

**Date**: Early development  
**Goal**: Bridge frappe_whatsapp with OpenWA

### What was built:
- `utils.py`: HMAC verification, JID helpers, type mapping, OpenWA REST helper
- `whatsapp_message.py`: OverrideWhatsAppMessage with content-type routing
- `whatsapp_templates.py`: OverrideWhatsAppTemplates with OpenWA sync
- `whatsapp_notification.py`: OverrideWhatsAppNotification with Jinja/Template routing
- `inbound.py`: Webhook endpoint with HMAC verification and idempotency
- `hooks.py`: DocType overrides, fixtures
- `fixtures/custom_field.json`: Custom fields

### Key discoveries:
- `hooks.py` has `require_type_annotated_api_methods = True` — all whitelisted functions need type hints
- `frappe.cache().set()` uses `ex=` not `expires_in=` (Redis)
- `frappe.throw()` doesn't accept `statusCode` kwarg
- OpenWA SSRF blocks private IPs — fixed with `SSRF_ALLOWED_HOSTS` in `.env`
- Template sync: Frappe `{{1}}` → OpenWA `{{param1}}`

---

## Session 2: Template Sync Fixes

**Date**: Development continued  
**Goal**: Fix template sync issues

### Fixes applied:
1. **Status fix**: Force `self.status = "APPROVED"` after sync (removed `if not self.status` guard)
2. **409 Conflict fix**: Stale ID recovery — 404 → clear ID → find by name → POST fresh
3. **actual_name auto-set**: Set `self.actual_name = name` from payload
4. **Sample values removed**: Deleted `_SAMPLEDefaults` mixin and `_auto_fill_sample_values()`

### Key code in `whatsapp_templates.py`:
```python
# Stale ID recovery
if resp.status_code == 404:
    frappe.db.set_value("WhatsApp Templates", self.name, "openwa_template_id", None)
    self.openwa_template_id = None
    # Find by name, recreate...
```

---

## Session 3: Notification System

**Date**: Development continued  
**Goal**: Make notifications work with OpenWA

### Fixes applied:
1. **`content_type` AttributeError**: Hardcoded `"text"` instead of `self.content_type`
2. **`@lid` JID handling**: Added `@lid` to `strip_jid_suffix()` suffix list
3. **`openwa_send_type` field**: New Select field on WhatsApp Notification (Jinja/Template)
4. **`send_template_message()` override**: Skips parent's attachment/header/button logic for OpenWA
5. **`code` field**: Set to `read_only=1` (set via API, not UI)

### Key design decisions:
- Template sends create a WhatsApp Message doc first, then trigger `notify()` via override
- Live doc values read from `self.fields` child table using `doc.get_formatted()`
- Jinja path: renders `code` field → sends as plain text

---

## Session 4: Dynamic Image Headers

**Date**: Development continued  
**Goal**: Send print format images as WhatsApp message headers

### What was built:
1. **`openwa_dynamic_header`** field on WhatsApp Templates (Check)
2. **`openwa_print_format`** field on WhatsApp Templates (Link to Print Format)
3. **`render_doc_as_image()`** in `utils.py`: PDF → PNG via PyMuPDF
4. **`_send_dynamic_header_image()`** in `whatsapp_notification.py`: Two-step send
5. **Two-step approach**: Image via `send-image` base64, then template text via `send-template`

### Key discoveries:
- OpenWA `send-template` is **text-only** — renders header+body+footer → `sendText()`
- No media header support in `send-template`
- `send-image` requires `mimetype` field when using `base64`
- Base64 must be raw — no `data:` prefix
- PyMuPDF (fitz) not installed initially — user needs `bench pip install PyMuPDF`

### Template synced:
- Name: `sales-invoice-en-2`
- OpenWA ID: `85f89390-6054-4d79-a4f0-15b973a38c60`
- `openwa_dynamic_header`: 1
- `openwa_print_format`: "Sales Invoice Standard"

---

## Session 5: Testing & Bug Fixes

**Date**: 2026-07-10  
**Goal**: Test notification flow and fix bugs

### Test scenario:
- Sales Invoice submit via POS → WhatsApp Notification "Sales" fires
- Template: `sales-invoice-en-2` (5 placeholders: customer_name, name, grand_total, currency, due_date)
- Contact mobile: `+967777715787`

### Error 1: `send-image` 400 Bad Request
- **Cause**: `mimetype` field missing from send-image payload
- **Fix**: Added `"mimetype": "image/png"` to payload
- **Status**: Applied locally, NOT yet deployed to server

### Error 2: `send-template` 400 Bad Request
- **Cause**: Unknown — need response body to diagnose
- **Fix**: Added detailed error logging (request body + response body) to `whatsapp_message.py`
- **Status**: Applied locally, NOT yet deployed to server

### Template field name fixes:
- Correct Sales Invoice fields: `customer_name`, `name`, `grand_total`, `currency`, `due_date`
- NOT: `invoice_name`, `amount` (these don't exist on Sales Invoice)

### Field position fixes:
- `openwa_dynamic_header` moved to after `header_type` (not after `openwa_template_id`)
- `openwa_print_format` moved to after `openwa_dynamic_header` (depends_on: `openwa_dynamic_header`)

---

## Session 6: Documentation

**Date**: 2026-07-10  
**Goal**: Create comprehensive docs for other developers/AI

### Files created:
- `docs/ARCHITECTURE.md` — System design, override mechanism, credential flow
- `docs/API_REFERENCE.md` — All OpenWA endpoints with payloads and DTOs
- `docs/FLOWS.md` — 5 message flow diagrams (outbound, template, notification, sync, inbound)
- `docs/CUSTOM_FIELDS.md` — All 14 custom fields across 3 DocTypes
- `docs/KNOWN_ISSUES.md` — Active bugs, resolved bugs, server environment
- `docs/DEPLOYMENT.md` — Server setup, install, testing checklist
- `docs/SESSION_LOG.md` — This file
- `README.md` — Updated with all features

---

## Session 6: Letterhead & Jinja Dynamic Image

**Date**: 2026-07-11  
**Goal**: Add letterhead control and extend dynamic image to Jinja path

### Changes applied:

1. **Letter head dual control** (on WhatsApp Templates):
   - `openwa_include_letterhead` (Check, default 1) — toggle on/off
   - `openwa_letterhead` (Link to Letter Head doctype) — which letterhead to use
   - Both fields only visible when `openwa_dynamic_header` is enabled

2. **`render_doc_as_image()` updated**:
   - Accepts `letterhead` parameter (string name or None)
   - If `letterhead` is provided: `frappe.get_print(doctype, name, print_format, no_letterhead=0)` → letterhead included
   - If `letterhead` is None: `no_letterhead=1` → no letterhead
   - PDF generator hardcoded to Chrome (overrides whatever Print Format record says)

3. **Jinja path now sends dynamic image header**:
   - Before: Jinja path only sent text via `send-text`
   - After: If template has `openwa_dynamic_header` enabled, sends image via `send-image` first, then text
   - Both `Template` and `Jinja` send types now support dynamic image headers

4. **Header always synced to OpenWA**:
   - Before: `openwa_dynamic_header=1` caused header to be skipped in sync payload
   - After: Header always synced (with `frappe_to_openwa_vars()` conversion)
   - OpenWA stores the header with `{{1}}`→`{{param1}}` conversion for use in template matching

### Field count updated:
- WhatsApp Account: 7 fields (section, enabled, base_url, session_id, column_break, api_key, webhook_secret)
- WhatsApp Templates: 8 fields (sync section, synced, template_id, dynamic_header, print_format, include_letterhead, letterhead, column_break)
- WhatsApp Notification: 1 field (openwa_send_type)

### Key code changes:
```python
# whatsapp_notification.py - Jinja path
if doc.template and doc.openwa_template_doc.openwa_dynamic_header:
    self._send_dynamic_header_image(doc, doc.openwa_template_doc)

# whatsapp_notification.py - _send_dynamic_header_image()
if self.openwa_include_letterhead and self.openwa_letterhead:
    letterhead = self.openwa_letterhead
else:
    letterhead = None
image_bytes = render_doc_as_image(..., letterhead=letterhead)

# utils.py - render_doc_as_image()
if letterhead:
    print_doc = frappe.get_print(doctype, name, print_format, no_letterhead=0, letterhead=letterhead)
else:
    print_doc = frappe.get_print(doctype, name, print_format, no_letterhead=1)
```

### Template synced:
- Name: `sales-invoice-en-2`
- OpenWA ID: `85f89390-6054-4d79-a4f0-15b973a38c60`
- `openwa_dynamic_header`: 1
- `openwa_print_format`: "Sales Invoice Standard"
- Header synced to OpenWA with `{{1}}`→`{{param1}}` conversion

---

## Session 7: WhatsApp Account UI Toggle

**Date**: 2026-07-12  
**Goal**: Conditional field visibility on WhatsApp Account based on OpenWA mode

### What was built:
1. **Custom Fields** (8 fields on WhatsApp Account):
   - OpenWA section with `depends_on: "eval:doc.openwa_enabled"` on the Section Break
   - "Meta Cloud API" Section Break separator between OpenWA and Meta fields
2. **Property Setters** (7 entries):
   - `depends_on: "eval:!doc.openwa_enabled"` on: token, url, version, webhook_verify_token, phone_id, app_id, business_id

### What was tried and abandoned:
- **File-based Client Script** (`client_scripts/openwa_toggle.js`) — Frappe only auto-discovers for same-app DocTypes
- **Client Script fixture** — Frappe asset bundling caching prevented the script from loading
- Both approaches were abandoned in favor of pure declarative `depends_on`

### Key discovery:
- Frappe's `depends_on` on a Section Break hides ALL fields until the next Section Break
- Without a separator, hiding the OpenWA section also hid Meta fields, Account Name, Status, etc.
- Fix: added `openwa_meta_separator` Section Break between the two groups

### Field visibility rules:

| Mode | Visible | Hidden |
|---|---|---|
| OpenWA enabled | OpenWA fields (Base URL, Session ID, API Key, Webhook Secret) | Meta fields (Token, URL, Version, Phone ID, App ID, Business ID) |
| OpenWA disabled | Meta fields | OpenWA fields |
| Both modes | Account Name, Status, Is Default Incoming/Outgoing, Allow Auto Read Receipt, OpenWA Enabled | — |

---

## Summary of All Files Modified

| File | Lines | Purpose |
|---|---|---|
| `hooks.py` | ~50 | App hooks, overrides, fixtures |
| `utils.py` | ~165 | Shared utilities (HMAC, API, JID, rendering) |
| `whatsapp_message.py` | ~260 | Outbound message router |
| `whatsapp_templates.py` | ~200 | Template sync to OpenWA |
| `whatsapp_notification.py` | ~315 | Notification routing + dynamic image + letterhead |
| `inbound.py` | ~300 | Inbound webhook handler |
| `fixtures/custom_field.json` | ~450 | 16 custom fields + 7 Property Setters |

## Summary of Custom Fields & Property Setters

| DocType | Count | Fields |
|---|---|---|
| WhatsApp Account | 8 | section, enabled, base_url, session_id, column_break, api_key, webhook_secret, meta_separator |
| WhatsApp Templates | 8 | sync section, synced, template_id, dynamic_header, print_format, include_letterhead, letterhead |
| WhatsApp Notification | 1 | openwa_send_type |
| Property Setters | 7 | depends_on for: token, url, version, webhook_verify_token, phone_id, app_id, business_id |
| **Total** | **24** | |

---

## Session 8: Enterprise Reliability & Security Hardening

**Date**: 2026-07-13  
**Goal**: Add offline queue, retry logic, circuit breaker, and security hardening

### What was built:

1. **OpenWA Outbox DocType** (`openwa_bridge/openwa_bridge/doctype/openwa_outbox/`):
   - Fields: whatsapp_message, whatsapp_account, content_type, status, attempts, max_attempts, next_retry_at, last_error
   - Status flow: Pending → Sending → Sent/Failed
   - Created in `after_insert()` after doc is persisted (self.name available)

2. **Two-phase notification pattern** (`whatsapp_message.py`):
   - Phase 1 (sync, <1s): Create WhatsApp Message doc → outbox entry → return to user
   - Phase 2 (async background): Render PDF → send image → send text/template
   - Eliminates 10-40s form submission delay

3. **Exponential backoff retry** (`tasks.py` → `_fail_outbox()`):
   - Schedule: 30s, 60s, 120s, 300s, capped at 1 hour
   - Max 5 attempts before marking as Failed
   - `frappe.log_error()` only on final failure (prevents error log flood)

4. **Circuit breaker** (`utils.py` → `OpenWACircuitBreaker`):
   - Threshold: 5 consecutive failures per account
   - Cooldown: 5 minutes (300s)
   - Redis-backed (uses `frappe.cache().set_value()` with `expires_in_sec`)
   - Prevents cascade failures when OpenWA is down

5. **Scheduler safety-net** (`tasks.py` → `process_pending_outbox()`):
   - Runs every ~4 minutes via `"all"` scheduler event
   - Picks up orphaned Pending entries (worker crash, Redis restart)
   - Re-enqueues to `long` queue with deduplication

6. **Inbound webhook hardening** (`inbound.py`):
   - Always returns HTTP 200 (prevents OpenWA retry loops)
   - `get_json(force=True)` for robust JSON parsing
   - Rate limiting: 60 req/min per IP
   - Lenient HMAC: no signature + secret configured → warn, not reject
   - Idempotency: `frappe.cache().set_value()` with `expires_in_sec=3600`
   - `session.status` event handler updates WhatsApp Account status
   - Full traceback in catch-all error handler

7. **Auto-sync webhook secret** (`whatsapp_account.py` → `on_account_update()`):
   - Registered via `doc_events` on WhatsApp Account `on_update`
   - Lists OpenWA webhooks, finds by URL match
   - Updates via PUT or creates via POST
   - Events: `message.received`, `message.ack`, `message.failed`, `session.status`

8. **Dynamic image optimization** (`tasks.py`):
   - Image sent with text as caption (1 message instead of 2)
   - Moved from sync path to background worker
   - Returns bool to skip separate text send

### Commits:
- `271f361`: Removed premature frappe.db.commit() from notify() inside before_insert()
- `081f630`: Moved outbox creation from notify() to after_insert()
- `6ebe47d`: Fixed expires_in → expires_in_sec in CircuitBreaker
- `f526453`: Moved openwa_send_type field, added required validation
- `6894591`: Two-phase pattern — moved image send to background worker
- `10884e6`: Added template field to outbox, image+caption optimization
- `d06c6e6`: Fixed expires_in → expires_in_sec in inbound rate limiter
- `9392890`: Full inbound webhook rewrite (always 200, rate limit, lenient HMAC)
- `a042430`: Removed use_json_request_body from hooks.py
- `3ff8b9f`: Fixed frappe.request.get_data() kwarg
- `6c842a4`: HMAC lenient mode (no signature = warn, not reject)
- `bb26d91`: Auto-sync webhook secret on account save
- `40139b3`: Error log cleanup (HMAC info-level, success logging, traceback)
- `7f4d55a`: Fixed idempotency cache kwarg (frappe.cache().set_value())
- `52df0cc`: Fixed send_template_message account resolution before OpenWA check

### Key discoveries:
- `frappe.cache().set()` calls Redis native `set()` which doesn't accept `expires_in_sec`
- `frappe.request.get_data(as_bytes=False)` — `as_bytes` not valid kwarg in this Frappe version
- `frappe.throw()` doesn't accept `http_status_code=` kwarg
- Base `frappe_whatsapp` has wildcard `doc_events = {"*": {...}}` that fires for ALL doctypes
- `before_insert()` → `self.name` is None; `after_insert()` → `self.name` is assigned
- Base WhatsAppMessage has NO `after_insert()` — safe to add without breaking

---

## Session 9: Documentation & "Password not found" Fix

**Date**: 2026-07-14  
**Goal**: Fix token crash and comprehensive documentation update

### Fix applied:
- **"Password not found for WhatsApp Account X token"**: `send_template_message()` now resolves account from `self.whatsapp_account` or default outgoing BEFORE checking `_is_openwa_account()`. Changed `frappe.throw()` on empty `openwa_send_type` to silent return (wildcard doc_events hook fires for all doc types).

### Documentation updated:
- `README.md` — Added enterprise features section, updated file structure and key methods
- `ARCHITECTURE.md` — Added outbox pattern diagram, on_update hook, enterprise design decisions
- `FLOWS.md` — Updated inbound flow (lenient HMAC, rate limiting), added outbox and webhook auto-sync flows
- `KNOWN_ISSUES.md` — Moved 18 issues to resolved, updated active issues
- `DEPLOYMENT.md` — Updated branch reference, added outbox/security testing checklist
- `API_REFERENCE.md` — Added HMAC lenient mode, rate limiting, idempotency, on_account_update docs
- `CUSTOM_FIELDS.md` — Added OpenWA Outbox DocType reference
- `SESSION_LOG.md` — Added session 8 and 9 entries

### Commit:
- `52df0cc`: Fixed send_template_message account resolution before OpenWA check
