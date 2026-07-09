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
- **Reply handling** — reply to specific messages with quoted context
- **Reaction messages** — react to messages with emojis
- **Template messages** — send approved templates for new contacts

### Inbound Messages (WhatsApp -> Desk)

- **Text message reception** with automatic Lead/Communication creation
- **Media downloads** — images, videos, audio, documents stored as Frappe File attachments
- **Location reception** with GPS coordinates
- **Message status tracking** — sent, delivered, read indicators
- **Profile auto-creation** for new contacts
- **Group message support** with author extraction

### Notification System

- **Jinja template rendering** — write custom message templates with full Frappe Jinja (e.g., Sales Invoice details, Payment reminders)
- **Template fallback** — fall back to Meta-approved templates for new contacts
- **Condition-based triggers** — set conditions on when notifications send

### Security

- **HMAC-SHA256 webhook verification** — all inbound webhooks are cryptographically verified
- **Idempotent message handling** — duplicate messages are automatically rejected
- **Credential encryption** — API keys and secrets stored encrypted in Frappe

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
- **OpenWA Gateway** running and connected (tested with [OpenWA-Node](https://github.com/Open-WA/wa-automate-nodejs))
- A WhatsApp account linked to OpenWA via QR code

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
3. Write a **Jinja Code** template:

```jinja
{% set company = frappe.get_doc("Company", doc.company) %}
Dear {{ doc.customer_name }},

Your invoice {{ doc.name }} is ready.
Amount: {{ doc.grand_total }} {{ doc.currency }}
Due Date: {{ doc.due_date }}

Thank you!
```

4. Set a **Condition** if needed:

```python
doc.grand_total > 0
```

5. Save and submit a Sales Invoice — the notification sends automatically.

### Receiving Messages

Inbound messages are received automatically via webhook. They appear in:

- **WhatsApp > WhatsApp Message** — with status "Received"
- **Communication** linked to the Contact/Lead
- **Lead** auto-created for new numbers

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
| Contact | Yes | — |
| Poll | Yes | — |
| Reaction | Yes | Yes |
| Reply (Quote) | Yes | Yes |
| Template | Yes | — |

---

## Troubleshooting

### Messages not sending

1. Verify OpenWA session is connected: check the OpenWA dashboard
2. Confirm **OpenWA Enabled** is checked on the WhatsApp Account
3. Check the Frappe error log for API response details
4. Verify the API key matches between OpenWA and Frappe

### Inbound messages not received

1. Test the HMAC signature: the `X-Openwa-Signature` header must match `sha256=<hex>`
2. Check the webhook URL is accessible from OpenWA
3. Verify `SSRF_ALLOWED_HOSTS` includes your Frappe server IP
4. Review OpenWA webhook logs for delivery status

### Template translation errors

The app includes fallback logic — if `frappe.get_doc()` fails, it falls back to `frappe.db.get_value()` for template lookup. Check the error log if templates aren't rendering correctly.

---

## File Structure

```
openwa_bridge/
├── hooks.py                    # App hooks, DocType overrides, fixtures
├── utils.py                    # HMAC verification, JID helpers, type mapping
├── whatsapp_message.py         # Outbound message routing (OverrideWhatsAppMessage)
├── whatsapp_templates.py       # Template sync bypass for OpenWA (OverrideWhatsAppTemplates)
├── whatsapp_notification.py    # Notification Jinja rendering (OverrideWhatsAppNotification)
├── inbound.py                  # Inbound webhook endpoint + message handlers
└── fixtures/
    └── custom_field.json       # Custom fields on WhatsApp Account DocType
```

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
