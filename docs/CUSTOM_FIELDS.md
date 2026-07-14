# Custom Fields & UI Reference

All custom fields are defined in `fixtures/custom_field.json` and auto-created on `bench migrate`.

Property Setters (also in `custom_field.json`) set `depends_on` on Meta fields to toggle visibility.

---

## WhatsApp Account UI Toggle

Uses Frappe's built-in `depends_on` mechanism — zero JavaScript needed.

- **OpenWA section**: Always visible (collapsible), no `depends_on`
- **OpenWA fields** (base_url, session_id, column_break, api_key, webhook_secret): `depends_on: "eval:doc.openwa_enabled"` → only show when checked
- **OpenWA Enabled checkbox**: Always visible (no `depends_on`)
- **Meta fields**: Property Setters set `depends_on: "eval:!doc.openwa_enabled"` → only show when unchecked
- **Separator**: A "Meta Cloud API" Section Break after the OpenWA section prevents the OpenWA `depends_on` from hiding Meta fields

| Mode | Visible | Hidden |
|---|---|---|
| **OpenWA enabled** | OpenWA fields (Base URL, Session ID, API Key, Webhook Secret, HMAC Strict, QR Code) | Meta fields (Token, URL, Version, Phone ID, App ID, Business ID) |
| **OpenWA disabled** | Meta fields | OpenWA fields |
| **Both modes** | Account Name, Status, Is Default Incoming, Is Default Outgoing, Allow Auto Read Receipt, OpenWA Enabled toggle | — |

---

## WhatsApp Account (10 fields)

Added to the `WhatsApp Account` DocType.

| # | Field Name | Fieldtype | Label | Description |
|---|---|---|---|---|
| 1 | `openwa_gateway_section` | Section Break | OpenWA Gateway | Section header (always visible, collapsible) |
| 2 | `openwa_enabled` | Check | OpenWA Enabled | Check to route messages through OpenWA instead of Meta API |
| 3 | `openwa_base_url` | Data | OpenWA Base URL | Gateway URL, e.g. `http://localhost:2785`. depends_on: openwa_enabled |
| 4 | `openwa_session_id` | Data | OpenWA Session ID | Connected session UUID (read-only, auto-populated by Setup button). depends_on: openwa_enabled |
| 5 | `openwa_column_break` | Column Break | | Visual separator. depends_on: openwa_enabled |
| 6 | `openwa_api_key` | Password | OpenWA API Key | Encrypted API key for OpenWA REST API authentication. depends_on: openwa_enabled |
| 7 | `openwa_webhook_secret` | Password | OpenWA Webhook Secret | Secret for HMAC webhook verification. depends_on: openwa_enabled |
| 8 | `openwa_hmac_strict` | Check | Require HMAC Signature | When checked, incoming webhooks without a valid HMAC signature are rejected. depends_on: openwa_enabled |
| 9 | `openwa_qr_html` | HTML | QR Code | QR code display area (auto-populated by Setup button or Show QR button) |
| 10 | `openwa_meta_separator` | Section Break | Meta Cloud API | Separator between OpenWA and Meta fields |

### Meta Fields (via Property Setters)

7 Property Setters set `depends_on: "eval:!doc.openwa_enabled"` on these Meta-only fields:

`token`, `url`, `version`, `webhook_verify_token`, `phone_id`, `app_id`, `business_id`

### QR Code Setup Flow

The `openwa_qr_html` HTML field is used by the client script to display:
- QR code image (auto-refreshes every 55 seconds)
- Connection status messages
- Error messages

The QR is populated by clicking "Setup OpenWA" (creates session + fetches QR) or "Show QR Code" (fetches QR for existing session).

---

## WhatsApp Templates (8 fields)

Added to the `WhatsApp Templates` DocType.

| # | Field Name | Fieldtype | Label | Depends On | Description |
|---|---|---|---|---|---|
| 1 | `openwa_sync_section` | Section Break | OpenWA Sync | | Section header |
| 2 | `openwa_synced` | Check | Synced to OpenWA | | Auto-checked when template is synced |
| 3 | `openwa_template_id` | Data | OpenWA Template ID | `openwa_synced` | UUID assigned by OpenWA (read-only, set by sync) |
| 4 | `openwa_dynamic_header` | Check | Dynamic Image Header | | Enable dynamic image header rendering |
| 5 | `openwa_print_format` | Link (Print Format) | Print Format for Header | `openwa_dynamic_header` | Print Format to render as image for header |
| 6a | `openwa_include_letterhead` | Check | Include Letter Head | `openwa_dynamic_header` | Toggle on/off (default: checked) |
| 6b | `openwa_letterhead` | Link (Letter Head) | Letter Head for Image | `eval:doc.openwa_dynamic_header && doc.openwa_include_letterhead` | Which Letter Head doc to use |

### Field Position Notes

- `openwa_synced` is placed **after** the `status` field
- `openwa_template_id` is placed **after** `openwa_synced`
- `openwa_dynamic_header` is placed **after** `header_type`
- `openwa_print_format` is placed **after** `openwa_dynamic_header` and depends on it
- `openwa_include_letterhead` is placed **after** `openwa_print_format`
- `openwa_letterhead` is placed **after** `openwa_include_letterhead` and depends on both dynamic header AND include toggle

---

## WhatsApp Notification (1 field)

Added to the `WhatsApp Notification` DocType.

| # | Field Name | Fieldtype | Label | Options | Description |
|---|---|---|---|---|---|
| 1 | `openwa_send_type` | Select | OpenWA Send Type | `Jinja` / `Template` | Controls how the notification sends via OpenWA |

### Send Type Behavior

| Value | Behavior |
|---|---|
| `Template` | Sends via OpenWA `send-template` endpoint with live doc values from `fields` child table |
| `Jinja` | Renders `code` field as Jinja template, sends as plain text via `send-text`. Dynamic image header also sent if configured on the linked template |
| _(empty)_ | Falls back: if `template` is set, sends as template; otherwise uses parent behavior (Meta API) |

---

## Notification Fields Child Table

The `fields` child table on WhatsApp Notification maps template placeholders to document fields. Each row has:

| Field | Fieldtype | Description |
|---|---|---|
| `field_name` | Data | The document field to read the value from |

**Example for Sales Invoice**:

| Row | field_name | Resolves To |
|---|---|---|
| 1 | `customer_name` | `"Faissal Mannaa"` |
| 2 | `name` | `"ACC-SINV-2026-00047"` |
| 3 | `grand_total` | `"50"` |
| 4 | `currency` | `"YER"` |
| 5 | `due_date` | `"2026-07-10"` |

Values are read at send time using `doc.get_formatted(field_name)` for live data.

---

## Where Fields Are Used

| Field | Used By | File |
|---|---|---|
| `openwa_enabled` | `whatsapp_message.py:20` | Routing decision in `notify()` |
| `openwa_enabled` | `whatsapp_templates.py` | Skip Meta API for OpenWA accounts |
| `openwa_enabled` | `whatsapp_notification.py:19` | `_is_openwa_account()` check |
| `openwa_base_url` | `whatsapp_message.py:49` | API base URL |
| `openwa_session_id` | `whatsapp_message.py:50` | Session ID for API calls |
| `openwa_api_key` | `whatsapp_message.py:51` | Authentication header |
| `openwa_webhook_secret` | `inbound.py` | HMAC verification |
| `openwa_qr_html` | `public/js/whatsapp_account.js` | QR code display area |
| `openwa_synced` | `whatsapp_templates.py` | Sync status |
| `openwa_template_id` | `whatsapp_message.py:63` | Template lookup for send-template |
| `openwa_template_id` | `whatsapp_templates.py` | UUID storage after sync |
| `openwa_dynamic_header` | `whatsapp_templates.py` | Always sync header to OpenWA (regardless of toggle) |
| `openwa_dynamic_header` | `whatsapp_notification.py:88` | Trigger image send (Template + Jinja paths) |
| `openwa_print_format` | `whatsapp_notification.py:88` | Print Format for image rendering (forced Chrome) |
| `openwa_include_letterhead` | `whatsapp_notification.py:134` | Toggle: include/exclude letterhead from image |
| `openwa_letterhead` | `whatsapp_notification.py:135` | Which Letter Head doc to pass to `frappe.get_print()` |
| `openwa_send_type` | `whatsapp_notification.py:106` | Routing decision in `notify()` |
| `on_account_trash` | `whatsapp_account.py` | Delete OpenWA session when WhatsApp Account is deleted |
| `setup_openwa_session` | `whatsapp_account.py` | One-click: create session, start, fetch QR |
| `get_openwa_qr` | `whatsapp_account.py` | Fetch QR code for existing session |
| `get_openwa_session_status` | `whatsapp_account.py` | Get session status |
| `stop_openwa_session` | `whatsapp_account.py` | Disconnect session |

---

## OpenWA Outbox DocType

Created by `openwa_bridge/openwa_bridge/doctype/openwa_outbox/`.

| Field | Type | Description |
|---|---|---|
| `whatsapp_message` | Link (WhatsApp Message) | The message being sent |
| `whatsapp_account` | Link (WhatsApp Account) | Target account |
| `content_type` | Data | Message content type |
| `status` | Select | Pending / Sending / Sent / Failed |
| `attempts` | Int | Current attempt count |
| `max_attempts` | Int | Maximum retries (default: 5) |
| `next_retry_at` | Datetime | When to retry (exponential backoff) |
| `last_error` | Long Text | Last failure reason |

### Status Flow

```
Pending → Sending → Sent
    ↑         │
    └─────────┘  (retry on failure, exponential backoff)

Pending → Failed  (after max_attempts exhausted)
```

### Backoff Schedule

| Attempt | Delay | Cumulative |
|---|---|---|
| 1 | 30s | 30s |
| 2 | 60s | 1m30s |
| 3 | 120s | 3m30s |
| 4 | 300s | 8m30s |
| 5 | Failed | — |
