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
- `docs/CUSTOM_FIELDS.md` — All 11 custom fields across 3 DocTypes
- `docs/KNOWN_ISSUES.md` — Active bugs, resolved bugs, server environment
- `docs/DEPLOYMENT.md` — Server setup, install, testing checklist
- `docs/SESSION_LOG.md` — This file
- `README.md` — Updated with all features

---

## Summary of All Files Modified

| File | Lines | Purpose |
|---|---|---|
| `hooks.py` | ~50 | App hooks, overrides, fixtures |
| `utils.py` | ~165 | Shared utilities (HMAC, API, JID, rendering) |
| `whatsapp_message.py` | ~260 | Outbound message router |
| `whatsapp_templates.py` | ~200 | Template sync to OpenWA |
| `whatsapp_notification.py` | ~315 | Notification routing + dynamic image |
| `inbound.py` | ~300 | Inbound webhook handler |
| `fixtures/custom_field.json` | ~400 | 11 custom fields |

## Summary of Custom Fields

| DocType | Count | Fields |
|---|---|---|
| WhatsApp Account | 5 | enabled, base_url, session_id, api_key, section |
| WhatsApp Templates | 5 | synced, template_id, dynamic_header, print_format, section |
| WhatsApp Notification | 1 | openwa_send_type |
| **Total** | **11** | |
