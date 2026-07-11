# Custom Fields Reference

All custom fields are defined in `fixtures/custom_field.json` and auto-created on `bench migrate`.

---

## WhatsApp Account (5 fields)

Added to the `WhatsApp Account` DocType.

| # | Field Name | Fieldtype | Label | Description |
|---|---|---|---|---|
| 1 | `openwa_section` | Section Break | OpenWA Gateway | Section header |
| 2 | `openwa_enabled` | Check | OpenWA Enabled | Check to route messages through OpenWA instead of Meta API |
| 3 | `openwa_base_url` | Data | OpenWA Base URL | Gateway URL, e.g. `http://localhost:2785` |
| 4 | `openwa_session_id` | Data | OpenWA Session ID | Connected session UUID from OpenWA |
| 5 | `openwa_api_key` | Password | OpenWA API Key | Encrypted API key for OpenWA REST API authentication |

---

## WhatsApp Templates (6 fields)

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
| `openwa_synced` | `whatsapp_templates.py` | Sync status |
| `openwa_template_id` | `whatsapp_message.py:63` | Template lookup for send-template |
| `openwa_template_id` | `whatsapp_templates.py` | UUID storage after sync |
| `openwa_dynamic_header` | `whatsapp_templates.py` | Always sync header to OpenWA (regardless of toggle) |
| `openwa_dynamic_header` | `whatsapp_notification.py:88` | Trigger image send (Template + Jinja paths) |
| `openwa_print_format` | `whatsapp_notification.py:88` | Print Format for image rendering (forced Chrome) |
| `openwa_include_letterhead` | `whatsapp_notification.py:134` | Toggle: include/exclude letterhead from image |
| `openwa_letterhead` | `whatsapp_notification.py:135` | Which Letter Head doc to pass to `frappe.get_print()` |
| `openwa_send_type` | `whatsapp_notification.py:106` | Routing decision in `notify()` |
