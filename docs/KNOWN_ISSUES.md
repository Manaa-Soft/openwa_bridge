# Known Issues & Fixes

## Current Active Issues

### 1. send-template 400 Bad Request

**Status**: Under investigation  
**Error**: `400 Client Error: Bad Request for url: .../messages/send-template`  
**First seen**: 2026-07-10  

**What happens**:
When a notification fires (Sales Invoice submit via POS), the template send fails with 400. The dynamic image send also fails with 400 (separate issue, see #2).

**What we know**:
- The `send-template` DTO expects: `{ chatId, templateId?, templateName?, vars? }`
- We send: `{ chatId: "967777715787@c.us", templateId: "85f89390-...", vars: { param1: "...", ... } }`
- The `vars` is a `Record<string, string>` (dict) — matches the DTO
- Template ID was synced from OpenWA: `85f89390-6054-4d79-a4f0-15b973a38c60`

**Possible causes**:
1. Template was deleted from OpenWA dashboard (stale UUID)
2. Session "manaa" went inactive/disconnected
3. Some NestJS validation pipe rejecting the payload
4. `vars` values contain unexpected content

**Fix applied**:
- Added detailed error logging in `whatsapp_message.py` (line ~213) that captures:
  - Full request URL
  - Full request body (JSON)
  - Full response body from OpenWA
- This will reveal the actual error message on next test

**Next step**: Deploy updated code, test, check Error Log for "OpenWA API Error" entry with full response body.

---

### 2. send-image 400 Bad Request (mimetype)

**Status**: Fix applied locally, NOT yet deployed to server  
**Error**: `400 Client Error: Bad Request for url: .../messages/send-image`  
**Root cause**: `mimetype` field missing from send-image payload  

**OpenWA requirement**: When using `base64`, the `mimetype` field is **required**. Without it, OpenWA returns: `"mimetype is required when using base64 data"`.

**Fix applied** in `whatsapp_notification.py` line ~161:
```python
payload = {
    "chatId": chat_id,
    "base64": base64.b64encode(image_bytes).decode("utf-8"),
    "mimetype": "image/png",  # ← ADDED
}
```

Also replaced `openwa_api()` call with direct `requests.post()` for better error logging.

**Next step**: Deploy to server and test. This fix now applies to BOTH Template and Jinja send types.

---

## Resolved Issues

### 3. jinja `doc.items` AttributeError
**Fix**: Use `frappe.get_doc()` to get proper Document object, not `as_dict()`.
**File**: `whatsapp_notification.py` → `_resolve_document()`

### 4.409 Conflict on Template Sync
**Fix**: Stale ID recovery — if PUT returns 404, clear `openwa_template_id`, find by name via GET, POST if not found.  
**File**: `whatsapp_templates.py` → `_sync_to_openwa()`

### 5. Template Status Not Set
**Fix**: Force `self.status = "APPROVED"` and `self.actual_name = name` after sync.  
**File**: `whatsapp_templates.py` → `_sync_to_openwa()`

### 6. `content_type` AttributeError
**Fix**: Hardcode `"text"` instead of `self.content_type` in WhatsApp Message creation.  
**File**: `whatsapp_notification.py` → `_send_openwa_template()`, `_send_openwa_text()`

### 7. `@lid` JID Format Not Handled
**Fix**: Added `@lid` to `strip_jid_suffix()` suffix list.  
**File**: `utils.py` → `strip_jid_suffix()`

### 8. frappe.cache() kwarg
**Fix**: Use `ex=3600` not `expires_in=3600` (Redis kwarg).  
**File**: `inbound.py`

### 9. frappe.throw() kwarg
**Fix**: Remove `statusCode` kwarg (not supported).  
**File**: `inbound.py`

### 10. Sample Values Auto-fill Removed
**Fix**: Removed `_SAMPLEDefaults` mixin and `_auto_fill_sample_values()`. Values now read from live doc at send time via notification's `fields` child table.  
**File**: `whatsapp_templates.py`

---

## Server Environment

| Item | Value |
|---|---|
| Server IP | 192.168.1.15 |
| OpenWA API | http://localhost:2785 |
| OpenWA Dashboard | http://localhost:2886 |
| OpenWA Session | "manaa" (UUID: 14445ea6-6cb0-486c-ae81-4bd135fb21bd) |
| OpenWA Phone | +967777713637 |
| OpenWA API Key | owa_k1_66d53f37a4c26410104a9a20a01555b6084ee115406345ef2a89880adc6432c8 |
| OpenWA Webhook ID | 2124417e-17ad-4ce5-b84e-d4ee11674d20 |
| Webhook Secret | my-shared-secret-123 |
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
