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

### POST /messages/send-sticker

Send a sticker message. Supports URL or base64.

```json
{
  "chatId": "967777715787@c.us",
  "url": "https://example.com/sticker.webp"
}
```

**DTO fields**:
| Field | Type | Required | Notes |
|---|---|---|---|
| `chatId` | string | Yes | `phone@c.us` or `groupId@g.us` |
| `url` | string | One of url/base64 | HTTP(S) URL to sticker image |
| `base64` | string | One of url/base64 | Raw base64 sticker data |

---

### POST /messages/edit

Edit an already-sent message body.

```json
{
  "chatId": "967777715787@c.us",
  "messageId": "true_967777715787@c.us_3EB0...",
  "body": "Updated message text"
}
```

**DTO fields**:
| Field | Type | Required | Notes |
|---|---|---|---|
| `chatId` | string | Yes | Chat where the message was sent |
| `messageId` | string | Yes | ID of the message to edit |
| `body` | string | Yes | New message body |

---

### POST /messages/send-bulk

Send a text message to multiple contacts.

```json
{
  "chatIds": ["967777715787@c.us", "967777711111@c.us"],
  "text": "Hello everyone!"
}
```

**DTO fields**:
| Field | Type | Required | Notes |
|---|---|---|---|
| `chatIds` | string[] | Yes | Array of WhatsApp JIDs |
| `text` | string | Yes | Message body |

---

### POST /messages/forward

Forward an existing message to another chat.

```json
{
  "messageId": "true_967777715787@c.us_3EB0...",
  "chatId": "967777711111@c.us"
}
```

---

### DELETE /messages/:messageId

Delete a message. Add `?revoke=true` to delete for everyone (revoke).

```
DELETE /api/sessions/:sessionId/messages/true_967777715787@c.us_3EB0...?revoke=true
```

---

### POST /chats/typing

Send a typing indicator to a chat.

```json
{
  "chatId": "967777715787@c.us",
  "state": "typing"
}
```

**States**: `typing`, `recording`, `paused`

---

### POST /pairing-code

Request an 8-character pairing code as an alternative to QR scanning.

```json
{
  "phoneNumber": "967777715787"
}
```

**Response** (201):
```json
{
  "pairingCode": "ABCD1234"
}
```

---

### GET /contacts/check/:number

Check if a phone number is registered on WhatsApp.

```
GET /api/sessions/:sessionId/contacts/check/967777715787
```

**Response** (200):
```json
{
  "isRegistered": true
}
```

---

### POST /contacts/:jid/block

Block a contact.

```
POST /api/sessions/:sessionId/contacts/967777715787@c.us/block
```

---

### DELETE /contacts/:jid/block

Unblock a contact.

```
DELETE /api/sessions/:sessionId/contacts/967777715787@c.us/block
```

---

## Status (Stories)

### GET /statuses

List all contact statuses (stories).

```
GET /api/sessions/:sessionId/statuses
```

### GET /statuses/:statusId/media

Download the media for a specific status.

```
GET /api/sessions/:sessionId/statuses/{statusId}/media
```

---

## Channels

### POST /channels/subscribe

Subscribe to a WhatsApp channel via invite code.

```json
{
  "inviteCode": "https://whatsapp.com/channel/0029Vabc..."
}
```

### DELETE /channels/:channelId

Unsubscribe from a channel.

```
DELETE /api/sessions/:sessionId/channels/{channelId}
```

### GET /channels/:channelId/messages

List messages in a channel.

```
GET /api/sessions/:sessionId/channels/{channelId}?limit=50
```

---

## Message Reactions

### GET /messages/:messageId/reactions

Get all reactions for a specific message.

```
GET /api/sessions/:sessionId/messages/{messageId}/reactions
```

---

## Batch Operations

### DELETE /messages/batch/:batchId

Cancel a pending batch send operation.

```
DELETE /api/sessions/:sessionId/messages/batch/{batchId}
```

---

## Statistics

### GET /stats/overview

Get session overview statistics.

```
GET /api/sessions/:sessionId/stats/overview
```

### GET /stats/messages

Get message statistics for a time period.

```
GET /api/sessions/:sessionId/stats/messages?period=24h
```

**Query params**: `period` — `1h`, `24h`, `7d`, `30d`

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

OpenWA sends webhooks to Frappe for incoming messages and status changes.

### Supported Events

| Event | Description | Handler |
|---|---|---|
| `message.received` | Incoming message from a contact | `_handle_inbound_message()` — creates WhatsApp Message, Communication, Lead |
| `message.sent` | Outgoing message acknowledged by server | `_handle_message_sent()` — updates message status |
| `message.ack` | Delivery/read receipt | `_handle_status_update()` — updates message status (ack downgrade protection) |
| `message.failed` | Send failure | `_handle_status_update()` — marks message as Failed |
| `message.revoked` | Message deleted by sender | `_handle_message_revoked()` — marks message as Revoked |
| `message.reaction` | Reaction to a message | `_handle_message_reaction()` — logs reaction |
| `message.edited` | Message edited by sender | `_handle_message_edited()` — updates message body |
| `session.status` | Session status changed | `_handle_session_status()` — updates account Active/Inactive |
| `session.qr` | QR code generated (needs scan) | `_handle_session_qr()` — sets account Inactive |
| `session.authenticated` | Session authenticated successfully | `_handle_session_authenticated()` — sets account Active |
| `session.disconnected` | Session disconnected | `_handle_session_status()` — sets account Inactive |
| `session.reconnect_loop` | Session stuck in reconnect loop | `_handle_session_reconnect_loop()` — logs error with recovery suggestion |
| `group.join` | Participant(s) joined a group | `_handle_group_membership()` — logs join event |
| `group.leave` | Participant(s) left a group | `_handle_group_membership()` — logs leave event |
| `group.update` | Group metadata changed (subject, description, etc.) | `_handle_group_update()` — logs changes |
| `call.received` | Incoming voice/video call | `_handle_call_received()` — logs call event |
| `status.received` | Contact posted a status/story | `_handle_status_received()` — logs status event |

### Webhook Payload Structure

```json
{
  "event": "message",
  "data": {
    "from": "967777715787@c.us",
    "to": "967777713637@c.us",
    "body": "Hello!",
    "type": "text",
    "kind": "individual",
    "id": "true_967777715787@c.us_3EB0...",
    "timestamp": 1719312000,
    "chatId": "967777715787@c.us",
    "session": "manaa",
    "author": "967777715787@c.us",
    "pushName": "John Doe"
  }
}
```

**`kind` field** (v0.10.9+): Discriminator for message origin:
| Value | Description |
|---|---|
| `individual` | Direct message (1:1 chat) |
| `group` | Group message |
| `channel` | Channel message |
| `status` | Status/story message |
| `broadcast` | Broadcast list message |
| `unknown` | Unrecognized origin |

### HMAC Verification

OpenWA signs every webhook with:
- Header: `X-OpenWA-Signature: sha256=<hex-digest>`
- Computation: `HMAC-SHA256(webhook_secret, raw_body)`

**Lenient mode**: When a webhook secret is configured but OpenWA sends no `X-OpenWA-Signature` header, the message is processed with a warning log. This prevents message drops when HMAC isn't enabled on the OpenWA side. Only invalid signatures are rejected.

### Rate Limiting

- 60 requests per minute per IP
- Exceeding the limit returns `{"status": "error", "message": "Rate limit exceeded"}` (HTTP 200)

### Idempotency

OpenWA sends `X-OpenWA-Idempotency-Key` header. Duplicate keys within 1 hour are rejected with `{"status": "duplicate"}` (HTTP 200).

### Response

The endpoint **always returns HTTP 200** to prevent OpenWA retry loops. Errors are returned in the response body:
```json
{"status": "error", "message": "Invalid JSON"}
```

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

### _ensure_session_ready (internal)

Not whitelisted — called internally by `_send_via_openwa()` before every send.

**Method**: `OverrideWhatsAppMessage._ensure_session_ready()`

**Behavior**:
1. GET `/api/sessions/:id` — check status
2. If `ready` → return immediately
3. If not → POST `/start` with 60s timeout
4. Wait 5s, re-check status
5. If still not `ready` → throw error with instructions

**Error messages**:
- "Session restart failed: HTTP {code}"
- "Session is {status} and restart failed: {error}"
- "Session restarted but requires QR re-scan"
- "Session is {status} and could not be recovered automatically"

### stop_openwa_session

Disconnect the session.

**Method**: `openwa_bridge.whatsapp_account.stop_openwa_session`

**Args**: `{ account_name: string }`

**Returns**: `{ status: "disconnected" }`

### on_account_update (doc_events hook)

Not whitelisted — called automatically when WhatsApp Account is saved.

**Method**: `openwa_bridge.whatsapp_account.on_account_update`

**Behavior**:
1. Checks `openwa_enabled` and `openwa_session_id`
2. Gets webhook secret from Password field
3. `GET /webhooks` → list all webhooks
4. Find webhook matching Frappe URL
5. If found → `PUT /webhooks/:id { secret }`
6. If not found → `POST /webhooks { url, events, secret }`
7. On error → `frappe.log_error()` (best-effort)

---

### check_whatsapp_number

Check if a phone number is registered on WhatsApp.

**Method**: `openwa_bridge.whatsapp_account.check_whatsapp_number`

**Args**: `{ account_name: string, number: string }`

**Returns**: `{ exists: true, jid: "12345@c.us" }` or `{ exists: false }`

---

### block_contact

Block a contact via OpenWA.

**Method**: `openwa_bridge.whatsapp_account.block_contact`

**Args**: `{ account_name: string, contact_id: string }`

**Returns**: `{ status: "blocked" }`

---

### unblock_contact

Unblock a contact via OpenWA.

**Method**: `openwa_bridge.whatsapp_account.unblock_contact`

**Args**: `{ account_name: string, contact_id: string }`

**Returns**: `{ status: "unblocked" }`

---

### send_typing_indicator

Send a typing indicator to a chat.

**Method**: `openwa_bridge.whatsapp_account.send_typing_indicator`

**Args**: `{ account_name: string, chat_id: string, state: "typing"|"recording"|"paused" }`

**Returns**: `{ status: "ok" }`

---

### send_bulk_openwa

Send a text message to multiple contacts via OpenWA's send-bulk endpoint.

**Method**: `openwa_bridge.whatsapp_account.send_bulk_openwa`

**Args**: `{ account_name: string, contacts: string, message: string }`

- `contacts`: comma-separated phone numbers or JIDs

**Returns**: `{ status: "ok", sent: 2, failed: 0 }`

---

### forward_message

Forward an existing message to another chat.

**Method**: `openwa_bridge.whatsapp_account.forward_message`

**Args**: `{ account_name: string, message_id: string, chat_id: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### delete_message

Delete a message. Set `revoke=1` to delete for everyone.

**Method**: `openwa_bridge.whatsapp_account.delete_message`

**Args**: `{ account_name: string, message_id: string, revoke?: 0|1 }`

**Returns**: `{ status: "deleted", message_id: "..." }`

---

### request_pairing_code

Request an 8-character pairing code as an alternative to QR scanning.

**Method**: `openwa_bridge.whatsapp_account.request_pairing_code`

**Args**: `{ account_name: string, phone_number: string }`

**Returns**: `{ status: "ok", pairingCode: "ABCD1234" }`

---

### send_sticker

Send a sticker message by URL or base64.

**Method**: `openwa_bridge.whatsapp_account.send_sticker`

**Args**: `{ account_name: string, chat_id: string, url?: string, base64?: string }`

**Returns**: `{ status: "ok", messageId: "..." }`

---

### edit_message

Edit an already-sent message body.

**Method**: `openwa_bridge.whatsapp_account.edit_message`

**Args**: `{ account_name: string, chat_id: string, message_id: string, body: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### post_status_text

Post a text status/story.

**Method**: `openwa_bridge.whatsapp_account.post_status_text`

**Args**: `{ account_name: string, text: string, background_color?: string, font?: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### post_status_image

Post an image status/story.

**Method**: `openwa_bridge.whatsapp_account.post_status_image`

**Args**: `{ account_name: string, url?: string, base64?: string, caption?: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### post_status_video

Post a video status/story.

**Method**: `openwa_bridge.whatsapp_account.post_status_video`

**Args**: `{ account_name: string, url?: string, base64?: string, caption?: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### get_statuses

List all contact statuses (stories).

**Method**: `openwa_bridge.whatsapp_account.get_statuses`

**Args**: `{ account_name: string }`

**Returns**: `{ statuses: [...] }`

---

### reject_call

Reject an incoming voice/video call.

**Method**: `openwa_bridge.whatsapp_account.reject_call`

**Args**: `{ account_name: string, call_id: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### mark_chat_read

Mark all messages in a chat as read.

**Method**: `openwa_bridge.whatsapp_account.mark_chat_read`

**Args**: `{ account_name: string, chat_id: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### mark_chat_unread

Mark a chat as unread.

**Method**: `openwa_bridge.whatsapp_account.mark_chat_unread`

**Args**: `{ account_name: string, chat_id: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### get_chat_history

Retrieve message history for a specific chat.

**Method**: `openwa_bridge.whatsapp_account.get_chat_history`

**Args**: `{ account_name: string, chat_id: string, limit?: number }`

**Returns**: `{ messages: [...] }`

---

### search_messages

Search messages by query string.

**Method**: `openwa_bridge.whatsapp_account.search_messages`

**Args**: `{ account_name: string, query: string, limit?: number }`

**Returns**: `{ messages: [...] }`

---

### list_groups

List all groups the session is part of.

**Method**: `openwa_bridge.whatsapp_account.list_groups`

**Args**: `{ account_name: string }`

**Returns**: `{ groups: [...] }`

---

### create_group

Create a new WhatsApp group.

**Method**: `openwa_bridge.whatsapp_account.create_group`

**Args**: `{ account_name: string, name: string, participants: string }`

- `participants`: comma-separated phone numbers or JIDs

**Returns**: `{ status: "ok", groupId: "..." }`

---

### add_group_participants

Add participants to a group.

**Method**: `openwa_bridge.whatsapp_account.add_group_participants`

**Args**: `{ account_name: string, group_id: string, participants: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### remove_group_participants

Remove participants from a group.

**Method**: `openwa_bridge.whatsapp_account.remove_group_participants`

**Args**: `{ account_name: string, group_id: string, participants: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### promote_group_admins

Promote participants to group admin.

**Method**: `openwa_bridge.whatsapp_account.promote_group_admins`

**Args**: `{ account_name: string, group_id: string, participants: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### demote_group_admins

Demote group admins to regular participants.

**Method**: `openwa_bridge.whatsapp_account.demote_group_admins`

**Args**: `{ account_name: string, group_id: string, participants: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### set_group_name

Change the group name/subject.

**Method**: `openwa_bridge.whatsapp_account.set_group_name`

**Args**: `{ account_name: string, group_id: string, name: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### leave_group

Leave a group.

**Method**: `openwa_bridge.whatsapp_account.leave_group`

**Args**: `{ account_name: string, group_id: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### list_labels

List all labels for the session.

**Method**: `openwa_bridge.whatsapp_account.list_labels`

**Args**: `{ account_name: string }`

**Returns**: `{ labels: [...] }`

---

### add_label_to_chat

Assign a label to a chat.

**Method**: `openwa_bridge.whatsapp_account.add_label_to_chat`

**Args**: `{ account_name: string, label_id: string, chat_id: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### remove_label_from_chat

Remove a label from a chat.

**Method**: `openwa_bridge.whatsapp_account.remove_label_from_chat`

**Args**: `{ account_name: string, label_id: string, chat_id: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### send_bulk_with_progress

Send a bulk message and get progress updates.

**Method**: `openwa_bridge.whatsapp_account.send_bulk_with_progress`

**Args**: `{ account_name: string, contacts: string, message: string }`

**Returns**: `{ status: "ok", batchId: "...", sent: 2, failed: 0 }`

---

### set_profile_name

Set the profile push name.

**Method**: `openwa_bridge.whatsapp_account.set_profile_name`

**Args**: `{ account_name: string, name: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### set_profile_status

Set the profile about/status text.

**Method**: `openwa_bridge.whatsapp_account.set_profile_status`

**Args**: `{ account_name: string, status: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### set_profile_picture

Set the profile picture by URL or base64.

**Method**: `openwa_bridge.whatsapp_account.set_profile_picture`

**Args**: `{ account_name: string, url?: string, base64?: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### get_session_stats

Get session-level statistics.

**Method**: `openwa_bridge.whatsapp_account.get_session_stats`

**Args**: `{ account_name: string }`

**Returns**: `{ stats: {...} }`

---

### list_channels

List all WhatsApp channels.

**Method**: `openwa_bridge.whatsapp_account.list_channels`

**Args**: `{ account_name: string }`

**Returns**: `{ channels: [...] }`

---

### get_channel_messages

List messages in a specific channel.

**Method**: `openwa_bridge.whatsapp_account.get_channel_messages`

**Args**: `{ account_name: string, channel_id: string, limit?: number }`

**Returns**: `{ messages: [...] }`

---

### get_contact_statuses

Get all statuses (stories) for a specific contact.

**Method**: `openwa_bridge.whatsapp_account.get_contact_statuses`

**Args**: `{ account_name: string, contact_id: string }`

**Returns**: `{ statuses: [...] }`

---

### get_status_media

Download media for a specific status.

**Method**: `openwa_bridge.whatsapp_account.get_status_media`

**Args**: `{ account_name: string, status_id: string }`

**Returns**: `{ media: {...} }`

---

### subscribe_channel

Subscribe to a WhatsApp channel via invite code.

**Method**: `openwa_bridge.whatsapp_account.subscribe_channel`

**Args**: `{ account_name: string, invite_code: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### unsubscribe_channel

Unsubscribe from a WhatsApp channel.

**Method**: `openwa_bridge.whatsapp_account.unsubscribe_channel`

**Args**: `{ account_name: string, channel_id: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### get_message_reactions

Get all reactions for a specific message.

**Method**: `openwa_bridge.whatsapp_account.get_message_reactions`

**Args**: `{ account_name: string, chat_id: string, message_id: string }`

**Returns**: `{ reactions: [...] }`

---

### cancel_batch

Cancel a pending batch send operation.

**Method**: `openwa_bridge.whatsapp_account.cancel_batch`

**Args**: `{ account_name: string, batch_id: string }`

**Returns**: `{ status: "ok", result: {...} }`

---

### get_overview_stats

Get session overview statistics.

**Method**: `openwa_bridge.whatsapp_account.get_overview_stats`

**Args**: `{ account_name: string }`

**Returns**: `{ stats: {...} }`

---

### get_message_stats

Get message statistics for a time period.

**Method**: `openwa_bridge.whatsapp_account.get_message_stats`

**Args**: `{ account_name: string, period?: string }`

- `period`: `1h`, `24h` (default), `7d`, `30d`

**Returns**: `{ stats: {...} }`
