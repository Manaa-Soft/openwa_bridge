# OpenWA API Reference

All endpoints are relative to: `http://<host>:2785/api/sessions/<sessionId>/`

Authentication: `X-API-Key: <api_key>` header on all requests.

---

## Messages

### POST /messages/send-text

Send a plain text message.

```json
{
  "chatId": "967777715787@c.us",
  "text": "Hello world"
}
```

**Response** (201):
```json
{
  "messageId": "true_967777715787@c.us_3EB0...",
  "timestamp": 1719312000
}
```

---

### POST /messages/send-image

Send an image. Supports URL or base64.

```json
{
  "chatId": "967777715787@c.us",
  "base64": "<raw base64 string, NO data: prefix>",
  "mimetype": "image/png",
  "caption": "Optional caption"
}
```

**Important**:
- `mimetype` is **required** when using `base64`
- Base64 must be raw — no `data:image/png;base64,` prefix
- Max decoded size: 50 MiB
- `url` and `base64` are mutually exclusive (if both, `base64` wins)

**DTO fields**:
| Field | Type | Required | Notes |
|---|---|---|---|
| `chatId` | string | Yes | `phone@c.us` or `groupId@g.us` |
| `url` | string | One of url/base64 | HTTP(S) URL to image |
| `base64` | string | One of url/base64 | Raw base64 data |
| `mimetype` | string | When using base64 | e.g. `image/png`, `image/jpeg` |
| `filename` | string | No | Max 255 chars |
| `caption` | string | No | Max 1024 chars |
| `mentions` | string[] | No | WIDs to @mention |

---

### POST /messages/send-video

Same structure as send-image but for video.

```json
{
  "chatId": "967777715787@c.us",
  "url": "https://example.com/video.mp4",
  "caption": "Check this out"
}
```

---

### POST /messages/send-audio

```json
{
  "chatId": "967777715787@c.us",
  "url": "https://example.com/audio.mp3"
}
```

---

### POST /messages/send-document

```json
{
  "chatId": "967777715787@c.us",
  "url": "https://example.com/file.pdf",
  "filename": "invoice.pdf"
}
```

---

### POST /messages/send-template

Send a stored template with variable substitution. **Text-only** — renders header+body+footer, joins with `\n\n`, sends as plain text.

```json
{
  "chatId": "967777715787@c.us",
  "templateId": "85f89390-6054-4d79-a4f0-15b973a38c60",
  "vars": {
    "param1": "Faissal Mannaa",
    "param2": "ACC-SINV-2026-00047",
    "param3": "50",
    "param4": "YER",
    "param5": "2026-07-10"
  }
}
```

**DTO fields**:
| Field | Type | Required | Notes |
|---|---|---|---|
| `chatId` | string | Yes | |
| `templateId` | string | Conditional | Required when `templateName` absent |
| `templateName` | string | Conditional | Required when `templateId` absent |
| `vars` | Record<string,string> | No | Keys match `{{key}}` in template |

**How it works internally**:
1. Resolves template from DB by ID or name
2. Renders header, body, footer with `renderTemplate(segment, vars)`
3. Joins with `\n\n`
4. Sends as plain text via `sendText()`

**This means**: even if the template was created with a header type of "IMAGE", send-template will **always** send plain text. For image headers, you must use the two-step approach (send-image first, then send-template).

---

### POST /messages/reply

Reply to a specific message with a quote.

```json
{
  "chatId": "967777715787@c.us",
  "quotedMessageId": "true_967777715787@c.us_3EB0...",
  "text": "This is my reply"
}
```

---

### POST /messages/react

React to a message with an emoji.

```json
{
  "chatId": "967777715787@c.us",
  "messageId": "true_967777715787@c.us_3EB0...",
  "emoji": "👍"
}
```

---

### POST /messages/send-location

```json
{
  "chatId": "967777715787@c.us",
  "latitude": -6.2088,
  "longitude": 106.8456,
  "description": "Office location",
  "address": "Jakarta, Indonesia"
}
```

---

### POST /messages/send-contact

```json
{
  "chatId": "967777715787@c.us",
  "contactName": "John Doe",
  "contactNumber": "+1234567890"
}
```

---

### POST /messages/send-poll

```json
{
  "chatId": "967777715787@c.us",
  "name": "What's your favorite color?",
  "options": ["Red", "Blue", "Green"],
  "allowMultipleAnswers": false
}
```

---

## Templates

Templates are scoped per session. Name must be unique within a session.

### GET /templates

List all templates for the session.

### GET /templates/:templateId

Get a specific template by UUID.

### POST /templates

Create a new template.

```json
{
  "name": "sales-invoice-en-2",
  "body": "Dear {{param1}},\n\nInvoice {{param2}}\nTotal: {{param3}} {{param4}}\nDue: {{param5}}",
  "header": "Optional header text",
  "footer": "Optional footer text"
}
```

**Entity fields**:
| Field | DB Type | Notes |
|---|---|---|
| `id` | UUID (auto) | Generated on creation |
| `sessionId` | varchar | FK to session |
| `name` | varchar(100) | Unique per session |
| `body` | text | Template body with `{{paramN}}` |
| `header` | text, nullable | Header section |
| `footer` | text, nullable | Footer section |

### PUT /templates/:templateId

Update a template. Same body as POST.

### DELETE /templates/:templateId

Delete a template by UUID.

---

## Template Variable Rendering

OpenWA uses this regex for variable substitution:

```regex
/\{\{\s*([\w.-]+)\s*\}\}|\{(\w+)\}/g
```

This matches both:
- `{{param1}}` (canonical)
- `{param1}` (legacy single-brace)

Unmatched placeholders remain as-is in the output.

**Conversion from Frappe format**:
- Frappe: `{{1}}`, `{{2}}`, `{{3}}`
- OpenWA: `{{param1}}`, `{{param2}}`, `{{param3}}`
- Conversion function: `frappe_to_openwa_vars()` in `utils.py`

---

## Inbound Webhooks

OpenWA sends webhooks to Frappe for incoming messages.

### Webhook Payload Structure

```json
{
  "event": "message",
  "data": {
    "from": "967777715787@c.us",
    "to": "967777713637@c.us",
    "body": "Hello!",
    "type": "text",
    "id": "true_967777715787@c.us_3EB0...",
    "timestamp": 1719312000,
    "chatId": "967777715787@c.us",
    "session": "manaa",
    "author": "967777715787@c.us",
    "pushName": "John Doe"
  }
}
```

### HMAC Verification

OpenWA signs every webhook with:
- Header: `X-Openwa-Signature: sha256=<hex-digest>`
- Computation: `HMAC-SHA256(webhook_secret, JSON.stringify(payload))`

Frappe verifies in `inbound.py` using `verify_openwa_signature()`.

---

## Error Handling

### 400 Bad Request

Common causes:
- `mimetype` missing when using base64 on send-image
- `chatId` missing or empty
- Neither `templateId` nor `templateName` provided
- `vars` not an object when provided
- Session not active

### 404 Not Found

- Template UUID doesn't exist in OpenWA DB
- Session doesn't exist

### SSRF Blocked

OpenWA blocks requests to private IPs by default. Fix:
```bash
# In OpenWA's .env file (typically OpenWA/data/.env.generated)
SSRF_ALLOWED_HOSTS=192.168.1.15,localhost
```

---

## Frappe Whitelisted Methods (openwa_bridge)

These are called from the client JS on the WhatsApp Account form.

### setup_openwa_session

One-click setup: create session in OpenWA, start it, fetch QR code.

**Method**: `openwa_bridge.whatsapp_account.setup_openwa_session`

**Args**: `{ account_name: string }`

**Returns**:
```json
{
  "qr_code": "data:image/png;base64,...",
  "status": "qr_ready",
  "session_id": "uuid"
}
```

Or on error:
```json
{
  "status": "error",
  "error": "Cannot connect to OpenWA at http://localhost:2785"
}
```

Or if already connected:
```json
{
  "status": "ready",
  "session_id": "uuid",
  "phone": "967777713637",
  "push_name": "manaa mnaa"
}
```

### get_openwa_qr

Fetch QR code for an existing session.

**Method**: `openwa_bridge.whatsapp_account.get_openwa_qr`

**Args**: `{ account_name: string }`

**Returns**: `{ qr_code: "data:image/png;base64,...", status: "qr_ready" }`

### get_openwa_session_status

Get current session status.

**Method**: `openwa_bridge.whatsapp_account.get_openwa_session_status`

**Args**: `{ account_name: string }`

**Returns**: `{ status, phone, push_name, connected_at, last_active }`

### stop_openwa_session

Disconnect the session.

**Method**: `openwa_bridge.whatsapp_account.stop_openwa_session`

**Args**: `{ account_name: string }`

**Returns**: `{ status: "disconnected" }`
