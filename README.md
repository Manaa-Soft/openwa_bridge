# OpenWA Bridge

**Seamlessly bridge frappe\_whatsapp with OpenWA for self-hosted WhatsApp messaging.**

OpenWA Bridge connects the [frappe\_whatsapp](https://github.com/Shridar2101/frappe_whatsapp) module with the [OpenWA](https://github.com/Open-WA/) WhatsApp Web API Gateway, allowing your Frappe/ERPNext instance to send and receive WhatsApp messages through your own WhatsApp Web session instead of the Meta Cloud API.

---

## Why OpenWA Bridge?

| | Meta Cloud API (frappe\_whatsapp) | OpenWA Bridge |
|---|---|---|
| **Setup** | Meta Business verification required | Connect with QR code |
| **Cost** | Per-message fees | Free (self-hosted) |
| **Number** | Dedicated WhatsApp Business number | Any WhatsApp number |
| **Templates** | Pre-approved Meta templates only | Send free-form messages + templates |
| **Media** | Limited by Meta API | Full media support |
| **Hosting** | Cloud-dependent | Fully self-hosted |

---

## Features

### Outbound Messages (Desk -> WhatsApp)

- **Text messages** with full Unicode support (Arabic, English, etc.)
- **Media messages**: images, videos, audio, documents
- **Location sharing** with latitude, longitude, and address
- **Contact cards** sharing
- **Poll messages** with multiple options
- **Reply handling** -- reply to specific messages with quoted context
- **Reaction messages** -- react to messages with emojis
- **Template messages** -- send approved templates for new contacts

### Inbound Messages (WhatsApp -> Desk)

- **Text message reception** with automatic Lead/Communication creation
- **Media downloads** -- images, videos, audio, documents stored as Frappe File attachments
- **Location reception** with GPS coordinates
- **Message status tracking** -- sent, delivered, read indicators
- **Profile auto-creation** for new contacts
- **Group message support** with author extraction
- **@lid JID handling** -- supports WhatsApp LID-format sender IDs

### Notification System

- **Jinja template rendering** -- write custom message templates with full Frappe Jinja (e.g., Sales Invoice details, Payment reminders)
- **OpenWA template sending** -- send synced templates with live doc values resolved at send time
- **Dynamic image headers** -- automatically render docs as images via Print Formats and send before template text
- **Send type control** -- choose between Jinja code, Template, or auto-fallback per notification
- **Condition-based triggers** -- set conditions on when notifications send

### Template Management

- **Auto-sync** -- Frappe templates sync to OpenWA on save (name, body, header, footer)
- **Stale ID recovery** -- if a template is deleted from the OpenWA dashboard, re-syncing automatically recreates it
- **Dynamic header support** -- mark a template's header as dynamic and link a Print Format for image generation

### Security

- **HMAC-SHA256 webhook verification** -- all inbound webhooks are cryptographically verified
- **Idempotent message handling** -- duplicate messages are automatically rejected
- **Credential encryption** -- API keys and secrets stored encrypted in Frappe

---

## Architecture

```
┌─────────────────────────────────────────────┐
│              Frappe / ERPNext                │
│                                             │
│  ┌──────────────┐  ┌─────────────────────┐  │
│  │  WhatsApp     │  │  OpenWA Bridge      │  │
│  │  DocTypes     │──│  (this app)         │  │
│  │  (Messages,   │  │                     │  │
│  │   Templates,  │  │  Override classes:  │  │
│  │   Notifications)│ │  - WhatsApp Message│  │
│  └──────────────┘  │  - WhatsApp Template│  │
│                    │  - WhatsApp Notif.  │  │
│                    └──────────┬──────────┘  │
│                               │              │
└───────────────────────────────┼──────────────┘
                                │
                          Outbound API
                                │
                                ▼
                    ┌───────────────────────┐
                    │   OpenWA Gateway       │
                    │   (localhost:2785)     │
                    │                       │
                    │  - REST API           │
                    │  - Webhook receiver   │
                    │  - WhatsApp Web conn. │
                    └───────────┬───────────┘
                                │
                          Inbound Webhooks
                                │
                                ▼
                    ┌───────────────────────┐
                    │   WhatsApp Web         │
                    │   (Your phone number)  │
                    └───────────────────────┘
```

---

## Requirements

- **Frappe Framework** v15+ / **ERPNext** v15+
- **frappe\_whatsapp** app installed
- **OpenWA Gateway** running and connected
- A WhatsApp account linked to OpenWA via QR code
- **PyMuPDF** (for dynamic image headers): `bench pip install PyMuPDF`

---

## Installation

### 1. Install OpenWA Gateway

Follow the [OpenWA documentation](https://docs.openwa.dev/) to set up the gateway on your server.

```bash
# Example: install OpenWA via npm
npm install -g @open-wa/wa-automate
```

### 2. Connect your WhatsApp number

```bash
# Start OpenWA and scan the QR code with your phone
openwa --port 2785
```

### 3. Install OpenWA Bridge

```bash
cd ~/frappe-bench
bench get-app https://github.com/Manaa-Soft/openwa_bridge.git --branch develop
bench --site your-site.local install-app openwa_bridge
bench --site your-site.local migrate
bench restart
```

### 4. Install PyMuPDF (optional, for dynamic image headers)

```bash
bench pip install PyMuPDF
```

---

## Configuration

### WhatsApp Account Setup

1. Go to **WhatsApp > WhatsApp Account** in Frappe Desk
2. Open your existing account (or create one)
3. Scroll to the **OpenWA Gateway** section
4. Fill in:

| Field | Description | Example |
|---|---|---|
| **OpenWA Enabled** | Check to route messages through OpenWA | `Yes` |
| **OpenWA Base URL** | OpenWA gateway URL | `http://localhost:2785` |
| **OpenWA Session ID** | The connected session ID from OpenWA | `your-session-id` |
| **OpenWA API Key** | Your OpenWA API key | `owa_k1_...` |
| **OpenWA Webhook Secret** | Shared secret for HMAC verification | `my-shared-secret-123` |

### OpenWA Webhook Setup

Create a webhook in OpenWA to forward inbound messages to Frappe:

- **URL**: `https://your-site.local/api/method/openwa_bridge.inbound.receive_openwa_message`
- **Method**: POST
- **Events**: `message`, `message.any`, `message.reaction`
- **Headers**: `X-Openwa-Signature` (auto-generated by OpenWA)

### OpenWA SSRF Configuration

If OpenWA and Frappe are on the same server, allow the Frappe IP in OpenWA's `.env`:

```bash
# In OpenWA's .env file
SSRF_ALLOWED_HOSTS=192.168.1.15,localhost
```

### Site Config (Alternative)

You can also configure credentials in `site_config.json`:

```json
{
  "openwa_api_key": "owa_k1_...",
  "openwa_api_secret": "your-api-secret",
  "openwa_webhook_secret": "my-shared-secret-123",
  "openwa_webhook_id": "your-webhook-id"
}
```

---

## Usage

### Sending Messages from Desk

1. Go to **WhatsApp > WhatsApp Message**
2. Click **New**
3. Select the **WhatsApp Account** (with OpenWA enabled)
4. Enter the recipient's phone number (with country code, no `+`)
5. Choose message type (Text, Image, Video, etc.)
6. Click **Send**

### Automated Notifications

1. Go to **WhatsApp > WhatsApp Notification**
2. Create a new notification linked to a DocType (e.g., Sales Invoice)
3. Choose **one** of three approaches:

#### Option A: Jinja Code (free-form text)

Set **Send Type** to `Jinja` and write a **Jinja Code** template:

```jinja
{% set company = frappe.get_doc("Company", doc.company) %}
Dear {{ doc.customer_name }},

Your invoice {{ doc.name }} is ready.
Amount: {{ doc.grand_total }} {{ doc.currency }}
Due Date: {{ doc.due_date }}

Thank you!
```

#### Option B: OpenWA Template (variable-based)

1. First, create a template at **WhatsApp > WhatsApp Templates**
2. Set the **Template Label** and **Template** body using `{{1}}`, `{{2}}` placeholders:

```
Hello {{1}}, your order {{2}} has been confirmed.
Total: {{3}} {{4}}
```

3. Save -- the template auto-syncs to OpenWA (check **Synced to OpenWA** field)
4. In the notification, select this template under the **Template** field
5. Leave **Send Type** as `Template` (or blank)

#### Option C: Template with Dynamic Image Header

1. Create a template at **WhatsApp > WhatsApp Templates** as in Option B
2. Check **Dynamic Header** on the template
3. Set **Print Format** to the format you want rendered as an image (e.g., `Sales Invoice Standard`)
4. In the notification, configure the **Fields** child table to map template placeholders to document fields (e.g., `grand_total` -> `grand_total`)
5. Set **Send Type** to `Template`

When triggered:
1. The doc is rendered as a PNG image via the Print Format
2. The image is sent first via `send-image`
3. The template text (with resolved variables) is sent immediately after

> **Note**: Dynamic image headers require **PyMuPDF** (`bench pip install PyMuPDF`).

4. Set a **Condition** if needed:

```python
doc.grand_total > 0
```

5. Save and submit a Sales Invoice -- the notification sends automatically.

### Notification Fields Child Table

The **Fields** child table on WhatsApp Notification maps template placeholders to document fields:

| Field | Description | Example |
|---|---|---|
| **Field Name** | Document field to read the value from | `customer_name` |

Values are read from the live document at send time using `doc.get_formatted()`. For Sales Invoice, common mappings:

| Placeholder | Field Name | Example Value |
|---|---|---|
| `{{1}}` | `customer_name` | `Faissal Mannaa` |
| `{{2}}` | `name` | `ACC-SINV-2026-00047` |
| `{{3}}` | `grand_total` | `50` |
| `{{4}}` | `currency` | `YER` |
| `{{5}}` | `due_date` | `2026-07-10` |

### Receiving Messages

Inbound messages are received automatically via webhook. They appear in:

- **WhatsApp > WhatsApp Message** -- with status "Received"
- **Communication** linked to the Contact/Lead
- **Lead** auto-created for new numbers

---

## OpenWA Templates

Templates created in Frappe automatically sync to OpenWA's template system.

### Creating a Template

1. Go to **WhatsApp > WhatsApp Templates**
2. Click **New**
3. Fill in:
   - **Template Label**: unique name (e.g., `order-confirmation`)
   - **Template**: body text with `{{1}}`, `{{2}}` placeholders
   - **Language**: select the language
   - **WhatsApp Account**: select your OpenWA-enabled account
   - **(Optional) Dynamic Header**: check to enable image header (see below)
   - **(Optional) Print Format**: select a Print Format for dynamic headers
4. Save -- the template syncs to OpenWA automatically
5. Check the **OpenWA Sync** section for sync status and template ID

### Template Variable Syntax

| Frappe Style | OpenWA Style | Description |
|---|---|---|
| `{{1}}` | `{{param1}}` | First parameter |
| `{{2}}` | `{{param2}}` | Second parameter |
| `{{N}}` | `{{paramN}}` | Nth parameter |

Variables are automatically converted when syncing to OpenWA.

### Dynamic Image Headers

When **Dynamic Header** is checked on a template:

1. The **Header** field is not synced to OpenWA (it remains `null`)
2. At send time, the notification renders the document using the linked **Print Format**
3. The PDF is converted to PNG via PyMuPDF
4. The image is sent as a standalone message before the template text

This is useful for sending branded invoices, receipts, or any document layout as a visual preview.

### Template Sync Behavior

- **Create**: Template is POSTed to OpenWA and gets a UUID (`openwa_template_id`)
- **Update**: Template is PUT'd to OpenWA; header is skipped if `Dynamic Header` is enabled
- **Delete**: Template is removed from OpenWA; `openwa_template_id` is cleared
- **Stale ID Recovery**: If a template is deleted from the OpenWA dashboard, the bridge detects the 404, looks up by name, and recreates it automatically

### Using Templates in Messages

When sending a WhatsApp Message with a template:

1. Set **Use Template** to Yes
2. Select the **Template**
3. Fill in **Template Parameters** (JSON array of values)
4. OpenWA sends the template with proper variable substitution

---

## Supported Message Types

| Type | Outbound | Inbound |
|---|---|---|
| Text | Yes | Yes |
| Image | Yes | Yes |
| Video | Yes | Yes |
| Audio | Yes | Yes |
| Document | Yes | Yes |
| Location | Yes | Yes |
| Contact | Yes | -- |
| Poll | Yes | -- |
| Reaction | Yes | Yes |
| Reply (Quote) | Yes | Yes |
| Template | Yes | -- |

---

## Custom Fields Reference

The bridge adds custom fields to three DocTypes via fixtures:

### WhatsApp Account (5 fields)

| Field | Type | Description |
|---|---|---|
| **OpenWA Gateway** | Section Break | Section header |
| **OpenWA Enabled** | Check | Enable OpenWA routing |
| **OpenWA Base URL** | Data | Gateway URL (e.g., `http://localhost:2785`) |
| **OpenWA Session ID** | Data | Connected session UUID |
| **OpenWA API Key** | Password | API key for authentication |

### WhatsApp Templates (5 fields)

| Field | Type | Description |
|---|---|---|
| **OpenWA Sync** | Section Break | Section header |
| **Synced to OpenWA** | Check | Indicates template is synced |
| **OpenWA Template ID** | Data (read-only) | UUID assigned by OpenWA |
| **Dynamic Header** | Check | Enable dynamic image header |
| **Print Format** | Link (Print Format) | Print Format for image rendering |

### WhatsApp Notification (1 field)

| Field | Type | Options | Description |
|---|---|---|---|
| **OpenWA Send Type** | Select | `Jinja`, `Template` | Controls notification send path |

---

## Troubleshooting

### Messages not sending

1. Verify OpenWA session is connected: check the OpenWA dashboard
2. Confirm **OpenWA Enabled** is checked on the WhatsApp Account
3. Check the Frappe error log for API response details (now includes full request/response bodies)
4. Verify the API key matches between OpenWA and Frappe

### Template sending fails with 400

1. Check if the template exists in OpenWA: the bridge auto-recovers stale IDs, but verify the **OpenWA Template ID** field is populated
2. Verify the **Fields** child table on the notification maps correctly to document field names
3. Check the **OpenWA API Error** log entry for the full response body from OpenWA

### Dynamic image header not working

1. Verify **PyMuPDF** is installed: `bench pip install PyMuPDF`
2. Check that the **Print Format** field is set on the template
3. Verify the **Dynamic Header** checkbox is checked
4. Look for "OpenWA: Dynamic header image failed" in error logs -- the full response body is now logged
5. OpenWA's `send-image` endpoint requires `mimetype: "image/png"` in the payload

### Inbound messages not received

1. Test the HMAC signature: the `X-Openwa-Signature` header must match `sha256=<hex>`
2. Check the webhook URL is accessible from OpenWA
3. Verify `SSRF_ALLOWED_HOSTS` includes your Frappe server IP
4. Review OpenWA webhook logs for delivery status

### Template translation errors

The app includes fallback logic -- if `frappe.get_doc()` fails, it falls back to `frappe.db.get_value()` for template lookup. Check the error log if templates aren't rendering correctly.

---

## File Structure

```
openwa_bridge/
├── hooks.py                    # App hooks, DocType overrides, fixtures
├── utils.py                    # HMAC verification, JID helpers, type mapping,
│                               #   OpenWA API helper, render_doc_as_image()
├── whatsapp_message.py         # Outbound message routing (OverrideWhatsAppMessage)
├── whatsapp_templates.py       # Template sync to OpenWA (OverrideWhatsAppTemplates)
├── whatsapp_notification.py    # Notification: Jinja OR template, dynamic image header
│                               #   (OverrideWhatsAppNotification)
├── inbound.py                  # Inbound webhook endpoint + message handlers
└── fixtures/
    └── custom_field.json       # Custom fields on Account, Templates, Notification
```

### Key Methods

| File | Method | Purpose |
|---|---|---|
| `utils.py` | `openwa_api()` | REST helper for all OpenWA API calls |
| `utils.py` | `verify_openwa_signature()` | HMAC-SHA256 webhook verification |
| `utils.py` | `strip_jid_suffix()` | Strip `@c.us` / `@g.us` / `@lid` from JIDs |
| `utils.py` | `frappe_to_openwa_vars()` | Convert `{{1}}` to `{{param1}}` |
| `utils.py` | `render_doc_as_image()` | PDF -> PNG via PyMuPDF for dynamic headers |
| `whatsapp_message.py` | `_send_via_openwa()` | Routes outbound messages by content type |
| `whatsapp_templates.py` | `_sync_to_openwa()` | Create/update/delete templates on OpenWA |
| `whatsapp_notification.py` | `send_template_message()` | Override: skip parent header/attachment logic |
| `whatsapp_notification.py` | `notify()` | Route by `openwa_send_type` (Template/Jinja) |
| `whatsapp_notification.py` | `_send_dynamic_header_image()` | Render doc as PNG and send via send-image |
| `whatsapp_notification.py` | `_extract_template_parameters()` | Read live doc values via fields child table |

---

## Contributing

This app uses `pre-commit` for code formatting and linting:

```bash
cd apps/openwa_bridge
pre-commit install
```

Tools used: ruff, eslint, prettier, pyupgrade.

---

## License

[GPL-3.0](license.txt)
