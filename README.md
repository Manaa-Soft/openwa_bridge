# OpenWA Bridge

**Seamlessly bridge frappe\_whatsapp with OpenWA for self-hosted WhatsApp messaging.**

OpenWA Bridge connects the [frappe\_whatsapp](https://github.com/Shridar2101/frappe_whatsapp) module with the [OpenWA](https://github.com/rmyndharis/OpenWA) WhatsApp Web API Gateway, allowing your Frappe/ERPNext instance to send and receive WhatsApp messages through your own WhatsApp Web session instead of the Meta Cloud API.

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
- **Sticker messages** -- send stickers by URL or base64
- **Location sharing** with latitude, longitude, and address
- **Contact cards** sharing
- **Poll messages** with multiple options
- **Reply handling** -- reply to specific messages with quoted context
- **Reaction messages** -- react to messages with emojis
- **Template messages** -- send approved templates for new contacts
- **Bulk messaging** -- send text to multiple contacts via OpenWA's send-bulk endpoint
- **Message forwarding** -- forward messages to other chats
- **Message deletion** -- delete for self or revoke (delete for everyone)
- **Message editing** -- edit sent message text via `POST /messages/edit`
- **@mention support** -- auto-extracts `@NNNN@c.us` JIDs from text messages
- **Contact management** -- check WhatsApp number, block/unblock contacts
- **Typing indicators** -- auto-send typing indicator before each message (non-blocking)
- **Note**: OpenWA v0.10.9+ has built-in `SIMULATE_TYPING=true` (anti-ban). To avoid duplicate typing indicators, set `SIMULATE_TYPING=false` in OpenWA `.env` when using the bridge's indicator, or disable the bridge's indicator by commenting out the `_send_typing_indicator` call in `whatsapp_message.py`.
- **Pairing code authentication** -- link via phone number (alternative to QR scan)

### Inbound Messages (WhatsApp -> Desk)

- **Text message reception** with automatic Communication and Lead/Contact creation
- **Media downloads** -- images, videos, audio, documents stored as Frappe File attachments
- **Location reception** with GPS coordinates
- **Message status tracking** -- sent, delivered, read, revoked indicators
- **Profile auto-creation** for new contacts
- **Group message support** with author extraction
- **@lid JID handling** -- supports WhatsApp LID-format sender IDs
- **Message edit tracking** -- `message.edited` webhook updates WhatsApp Message body
- **Reaction logging** -- inbound reactions recorded in logs
- **Extended webhook events** -- `message.sent`, `message.revoked`, `message.reaction`, `message.edited`, `session.qr`, `session.authenticated`, `session.disconnected`, `session.reconnect_loop`, `group.join`, `group.leave`, `group.update`, `call.received`

### Status/Stories

- **Post text status** -- share text updates as WhatsApp status
- **Post image status** -- share images as WhatsApp status
- **Post video status** -- share videos as WhatsApp status
- **Read statuses** -- list all visible status updates
- **Custom styling** -- custom background colors and font styles

### Group Management

- **List groups** -- enumerate all groups the session belongs to
- **Create groups** -- create new WhatsApp groups with participants
- **Add/remove participants** -- manage group membership
- **Promote/demote admins** -- manage group admin roles
- **Set group name** -- update group subject
- **Leave groups** -- exit a WhatsApp group

### WhatsApp Business Labels

- **List labels** -- enumerate all WhatsApp Business labels
- **Add label to chat** -- tag chats with labels
- **Remove label from chat** -- untag chats from labels

### Chat Management

- **Mark chat as read/unread** -- update chat read status
- **Chat history** -- fetch live chat history from WhatsApp client
- **Full-text search** -- search messages across all sessions
- **Batch messaging** -- async batch with progress tracking and cancellation

### Profile Management

- **Set display name** -- update WhatsApp profile name
- **Set about text** -- update WhatsApp status/about
- **Set profile picture** -- update WhatsApp profile photo

### Call Handling

- **Incoming call logging** -- log call.received webhook events
- **Call rejection** -- reject incoming WhatsApp calls

### Channels/Newsletters

- **List channels** -- enumerate subscribed WhatsApp Channels
- **Channel messages** -- fetch messages from a WhatsApp Channel

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

### Enterprise Reliability

- **Async outbox queue** -- outbound messages are queued via `OpenWA Outbox` and processed by background workers, eliminating form submission delays
- **Exponential backoff retry** -- failed sends retry with 30s, 60s, 120s, 300s backoff (capped at 1h), up to configurable max attempts
- **Circuit breaker** -- after N consecutive failures (configurable per account), the circuit opens for a cooldown period, preventing cascade failures
- **Scheduler safety-net** -- hourly sweep catches orphaned outbox entries from worker crashes or Redis restarts
- **HMAC-SHA256 webhook verification** -- cryptographically verified; configurable strict/lenient mode per account
- **Idempotent message handling** -- duplicate messages are automatically rejected (configurable TTL)
- **Rate limiting** -- configurable requests/minute per IP on the inbound webhook endpoint
- **Auto-sync webhook secret** -- saving a WhatsApp Account auto-syncs the secret to OpenWA webhooks
- **Credential encryption** -- API keys and secrets stored encrypted in Frappe
- **Media size limits** -- configurable max inbound media size (default 10MB)
- **SSRF protection** -- URL validation warns on private/internal IPs
- **Role-based access** -- whitelisted methods check Frappe permissions

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
- **Dedicated Redis instance** for OpenWA (if running on the same server as ERPNext — see [Redis Isolation](docs/DEPLOYMENT.md#redis-isolation-erpnext--openwa-on-same-server))

---

## Installation

### 1. Install OpenWA Gateway

Follow the [OpenWA Production Setup](https://github.com/Manaa-Soft/openwa_bridge/wiki/OpenWA-Production-Setup) guide for a full production deployment (Docker or Bare Metal), or install quickly:

```bash
# Quick install (development)
npm install -g @open-wa/wa-automate
openwa --port 2785
```

**Production** — use systemd to keep OpenWA running:

```bash
cd ~/OpenWA
npm ci && npm run build

sudo tee /etc/systemd/system/openwa.service << 'EOF'
[Unit]
Description=OpenWA WhatsApp API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/home/your-user/OpenWA
ExecStart=/usr/bin/node dist/main
Restart=always
RestartSec=5
Environment=NODE_ENV=production
Environment=PUPPETEER_ARGS="--no-sandbox --disable-setuid-sandbox --disable-dev-shm-usage"

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable openwa
sudo systemctl restart openwa
```

> **Critical**: The `PUPPETEER_ARGS` line is **required**. Without it, Chrome's security sandbox is blocked by systemd and the WhatsApp engine silently freezes with `"Engine initialization timed out"`. See [Troubleshooting](#could-not-find-chrome-error) for details.

See [OpenWA Production Setup](https://github.com/Manaa-Soft/openwa_bridge/wiki/OpenWA-Production-Setup) for PostgreSQL, Docker Compose, Nginx, and HTTPS configuration.

### 2. Connect your WhatsApp number

```bash
# Start OpenWA and scan the QR code with your phone
openwa --port 2785
# Or use the Frappe Desk: WhatsApp Account → Setup OpenWA → Scan QR
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

### Auto-Reconnect (Scheduled Tasks)

The app includes scheduled tasks that automatically manage OpenWA sessions:

- **Hourly**: Checks all OpenWA accounts — force-kills failed sessions (keeps auth, no QR re-scan), then deletes and recreates as last resort
- **Daily**: Cleanup of old outbox entries

The scheduler is enabled in `hooks.py`. Frappe's worker must be running for scheduled tasks to execute:

```bash
bench --site erp.manaasoft.com scheduler enable
bench restart
```

**Pre-send check**: Before every outbound message, the system verifies the session is `ready`. If not, it automatically attempts a restart and waits for the session to recover. If recovery fails (e.g., requires QR re-scan), the send is blocked with a clear error message.

**Status sync**: The WhatsApp Account `status` field (Active/Inactive) is automatically updated to match the OpenWA session status.

### WhatsApp Account Setup (One-Click)

1. Go to **WhatsApp > WhatsApp Account** in Frappe Desk
2. Open your existing account (or create one)
3. Scroll to the **OpenWA Gateway** section
4. Check **OpenWA Enabled**
5. Fill in:

| Field | Description | Example |
|---|---|---|
| **OpenWA Enabled** | Check to route messages through OpenWA | `Yes` |
| **OpenWA Base URL** | OpenWA gateway URL | `http://localhost:2785` |
| **OpenWA API Key** | Your OpenWA API key | `owa_k1_...` |
| **OpenWA Webhook Secret** | Shared secret for HMAC verification | `my-shared-secret-123` |

6. Click **Setup OpenWA** button — this auto-creates a session in OpenWA, starts it, and displays a QR code
7. Scan the QR code with your phone — status changes to "Connected"

> **Note**: The Session ID is auto-populated by the Setup button. You don't need to create sessions manually in OpenWA.

### OpenWA Webhook Setup

Create a webhook in OpenWA to forward inbound messages to Frappe:

- **URL**: `https://your-site.local/api/method/openwa_bridge.inbound.receive_openwa_message`
- **Method**: POST
- **Events**: `message.received`, `message.ack`, `message.failed`, `message.sent`, `message.revoked`, `message.reaction`, `session.status`, `session.qr`, `session.authenticated`, `session.disconnected`
- **Headers**: `X-OpenWA-Signature` (auto-generated by OpenWA)

> **Note**: If a webhook is already configured, saving the WhatsApp Account auto-syncs the secret to OpenWA. No manual update needed.

### OpenWA SSRF Configuration

OpenWA blocks webhook delivery to private/internal addresses by default (SSRF protection). When OpenWA and Frappe are on the same server, you need to whitelist the Frappe site so webhooks can be delivered.

**Recommended (secure)** — keep global protection ON, whitelist your Frappe site:

```bash
# In ~/OpenWA/.env
SSRF_ALLOWED_HOSTS=your-site-name,your-server-ip,localhost,127.0.0.1,minio
# your-site-name = your Frappe/ERPNext site name (e.g., my-site)
# your-server-ip = the IP used to connect from VM to host (e.g., 192.168.1.15)
# minio = MinIO service hostname (if used for file storage)
```

> **Why include the site name?** OpenWA resolves webhook target hosts. When Frappe's webhook URL uses a site name (e.g., `your-site-name` or `erp.example.com`), that hostname must appear in the allow list — otherwise OpenWA treats it as an internal host and blocks delivery. If you use MinIO for file storage, include `minio` as well.

### Dashboard Blank White Page (HTTP without SSL)

If you access the dashboard over plain HTTP (e.g., `http://SERVER_IP:2785`) without an SSL certificate or Nginx reverse proxy, browsers will block dashboard scripts due to Content Security Policy (CSP) settings — resulting in a blank white page.

**Fix**: Add this to your `.env` and restart OpenWA:

```bash
echo 'CSP_UPGRADE_INSECURE_REQUESTS=false' >> ~/OpenWA/.env
sudo systemctl restart openwa
```

**Alternative (closed networks only)** — disable protection entirely:

```bash
WEBHOOK_SSRF_PROTECT=false
```

After updating `.env`, restart:

```bash
sudo systemctl restart openwa
```

### Dashboard Unreachable from Browser (ERR_CONNECTION_REFUSED)

If you get `ERR_CONNECTION_REFUSED` when accessing `http://SERVER_IP:2785`, but it works from inside the server:

**Root cause**: Without `HOST=0.0.0.0`, Node.js defaults to `127.0.0.1` — only accessible from inside the server. If you were using VS Code with Remote SSH, VS Code automatically created an SSH port tunnel that forwarded `127.0.0.1:2785` to your local browser. Closing VS Code collapsed that tunnel.

**Fix**: Add `HOST=0.0.0.0` to your `.env` and restart:

```bash
echo 'HOST=0.0.0.0' >> ~/OpenWA/.env
sudo systemctl restart openwa
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

> **Note**: Jinja path also supports dynamic image headers. If the linked template has **Dynamic Image Header** enabled, the image is sent before the text message.

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
2. Check **Dynamic Image Header** on the template
3. Set **Print Format** to the format you want rendered as an image (e.g., `Sales Invoice Standard`)
4. Optionally configure **Letter Head**:
   - **Include Letter Head** (default: checked) — toggle on/off
   - **Letter Head for Image** — select which Letter Head doc to use from the dropdown
5. In the notification, configure the **Fields** child table to map template placeholders to document fields (e.g., `grand_total` -> `grand_total`)
6. Set **Send Type** to `Template` (or `Jinja` — both support dynamic image headers)

When triggered:
1. The doc is rendered as a PNG image via the Print Format (Chrome PDF generator)
2. Letter Head is included in the image only if **Include Letter Head** is checked AND a Letter Head is selected
3. The image is sent first via `send-image` with `mimetype: "image/png"`
4. The template text (with resolved variables) is sent immediately after

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
| Sticker | Yes | -- |
| Location | Yes | Yes |
| Contact | Yes | -- |
| Poll | Yes | -- |
| Reaction | Yes | Yes |
| Reply (Quote) | Yes | Yes |
| Template | Yes | -- |

---

## Custom Fields Reference

The bridge adds custom fields to three DocTypes plus Property Setters for UI toggling. Global settings are in the **OpenWA Bridge Settings** Single DocType.

### WhatsApp Account UI Toggle

Uses Frappe's built-in `depends_on` mechanism — zero JavaScript needed.

- OpenWA Section Break: always visible (collapsible)
- OpenWA fields (Base URL, Session ID, API Key, Webhook Secret): `depends_on: "eval:doc.openwa_enabled"` → only show when checked
- OpenWA Enabled checkbox: always visible
- Meta fields: Property Setters set `depends_on: "eval:!doc.openwa_enabled"` → only show when unchecked
- "Meta Cloud API" Section Break separates the two groups

| Mode | Visible | Hidden |
|---|---|---|
| **OpenWA enabled** | OpenWA fields (Base URL, Session ID, API Key, Webhook Secret, QR Code) | Meta fields (Token, URL, Version, Phone ID, App ID, Business ID) |
| **OpenWA disabled** | Meta fields | OpenWA fields |
| **Both modes** | Account Name, Status, Is Default Incoming, Is Default Outgoing, Allow Auto Read Receipt, OpenWA Enabled | — |

### WhatsApp Account (16 fields)

| Field | Type | Description |
|---|---|---|
| **OpenWA Gateway** | Section Break | Section header (always visible) |
| **OpenWA Enabled** | Check | Enable OpenWA routing |
| **OpenWA Base URL** | Data | Gateway URL (e.g., `http://localhost:2785`) |
| **OpenWA Session ID** | Data (read-only) | Auto-populated by Setup button |
| Column Break | Column Break | Visual separator |
| **OpenWA API Key** | Password | API key for authentication |
| **OpenWA Webhook Secret** | Password | Secret for HMAC verification |
| **Settings** | Section Break | Collapsible — per-account config |
| **Require HMAC Signature** | Check | Reject webhooks without valid HMAC signature |
| **API Timeout (seconds)** | Int | HTTP timeout for OpenWA REST API calls (default: 30) |
| **Session Start Timeout (seconds)** | Int | HTTP timeout when starting a session (default: 60) |
| Column Break | Column Break | Visual separator |
| **Webhook Rate Limit (req/min)** | Int | Max inbound requests per minute per IP (default: 60) |
| **Circuit Breaker Threshold** | Int | Consecutive failures before circuit breaker trips (default: 5) |
| Column Break | Column Break | Visual separator |
| **Circuit Breaker Cooldown (seconds)** | Int | Seconds to wait before retrying after trip (default: 300) |
| **Max Outbox Retry Attempts** | Int | Max delivery retries per entry (default: 5) |
| **QR Code** | HTML | QR code display area (auto-populated) |
| **Meta Cloud API** | Section Break | Separator between OpenWA and Meta fields |

### OpenWA Bridge Settings (3 fields — global)

Single DocType for system-wide configuration.

| Field | Type | Default | Description |
|---|---|---|---|
| **General** | Section Break | — | — |
| **Idempotency TTL** | Int | 3600 | How long to remember duplicate webhook events (seconds) |
| **Outbox Batch Size** | Int | 25 | Max outbox entries re-enqueued per scheduler cycle |
| Column Break | Column Break | Visual separator |
| **Media** | Section Break | — | — |
| **Max Inbound Media Size** | Int | 10 | Maximum size for inbound media attachments (MB) |

### WhatsApp Templates (8 fields)

| Field | Type | Description |
|---|---|---|
| **OpenWA Sync** | Section Break | Section header |
| **Synced to OpenWA** | Check | Indicates template is synced |
| **OpenWA Template ID** | Data (read-only) | UUID assigned by OpenWA |
| **Dynamic Image Header** | Check | Enable dynamic image header |
| **Print Format for Header** | Link (Print Format) | Print Format for image rendering |
| **Include Letter Head** | Check | Toggle letterhead on/off (default: on) |
| **Letter Head for Image** | Link (Letter Head) | Which Letter Head doc to use |
| Column Break | Column Break | Visual separator |

### WhatsApp Notification (1 field)

| Field | Type | Options | Description |
|---|---|---|---|
| **OpenWA Send Type** | Select | `Jinja`, `Template` | Controls notification send path |

---

## Troubleshooting

### "Password not found for WhatsApp Account X token"

This error occurs when `frappe_whatsapp`'s wildcard doc_events hook fires `send_template_message()` on an OpenWA account where the `token` field is empty (it's a Meta API field).

**Fix**: Ensure `openwa_bridge` is installed and overrides are active. The override resolves the account before checking `_is_openwa_account()` and skips the Meta token read when the account is OpenWA.

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
2. Check that the **Print Format for Header** field is set on the template
3. Verify the **Dynamic Image Header** checkbox is checked
4. If you want letterhead: check **Include Letter Head** is on AND **Letter Head for Image** is selected
5. Look for "OpenWA: Dynamic header image failed" in error logs -- the full response body is now logged
6. OpenWA's `send-image` endpoint requires `mimetype: "image/png"` in the payload
7. Both **Template** and **Jinja** send types support dynamic image headers

### Setup timeout / "Cannot connect to OpenWA"

1. Verify OpenWA is running: `curl http://localhost:2785/api/sessions`
2. If OpenWA is down, restart it: `sudo systemctl restart openwa`
3. The setup flow has increased timeouts (30s for API calls, 60s for session start)
4. Check that `openwa_base_url` in the WhatsApp Account matches your OpenWA address

### Inbound messages not received

1. Test the HMAC signature: the `X-Openwa-Signature` header must match `sha256=<hex>`
2. Check the webhook URL is accessible from OpenWA
3. Verify `SSRF_ALLOWED_HOSTS` includes your Frappe site name AND server IP (e.g., `SSRF_ALLOWED_HOSTS=your-site-name,your-server-ip,localhost,127.0.0.1,minio`)
   - `your-site-name` = your Frappe/ERPNext site name (e.g., `my-site`)
   - `your-server-ip` = the IP used to connect from VM to host (e.g., `192.168.1.15`)
   - `minio` = MinIO service hostname (if used for file storage)
4. Review OpenWA webhook logs for delivery status

### Sessions don't reconnect after VM restart

1. Enable Frappe scheduler: `bench --site erp.manaasoft.com scheduler enable`
2. Verify scheduler is running: `bench --site erp.manaasoft.com doctor`
3. Check OpenWA is running: `curl http://localhost:2785/api/sessions`
4. The pre-send check auto-restarts sessions on next message attempt
5. If session shows "requires QR re-scan", open WhatsApp Account form and scan again

### Session connects then disconnects after 1-2 minutes

**Cause**: OpenWA auto-downloads media from all incoming messages. On reconnect, the catch-up flood of media downloads overwhelms Chrome/Puppeteer.

**Fix**: Disable media pre-download in OpenWA's `.env`:

```bash
# In ~/OpenWA/.env
MEDIA_DOWNLOAD_ENABLED=false
STORE_EPHEMERAL_MESSAGES=false

# Delete generated env if it exists
rm -f ~/OpenWA/data/.env.generated

# Restart
sudo systemctl restart openwa
```

Our bridge handles media on-demand via webhooks, so you lose nothing. The health check also now automatically recovers failed sessions (force-kill → delete+recreate).

### "Could not find Chrome" error

Puppeteer looks for Chrome in the **running user's** cache (`/root/.cache/puppeteer/` vs `/home/you/.cache/puppeteer/`).

**Fix** (pick one):
1. Change systemd `User=root` to `User=your-username`
2. Set `PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium` in OpenWA's `.env`
3. Install Chrome for root: `sudo npx puppeteer browsers install chrome`

### Template translation errors

The app includes fallback logic -- if `frappe.get_doc()` fails, it falls back to `frappe.db.get_value()` for template lookup. Check the error log if templates aren't rendering correctly.

### ERPNext background jobs broken after OpenWA Redis config

**Cause**: Adding `requirepass` or `allkeys-lru` to the global `/etc/redis/redis.conf` breaks ERPNext's existing Redis connections on ports 6379/6380/6381.

**Fix**: Do NOT modify the global Redis config. Create a dedicated Redis instance for OpenWA on a separate port (e.g., 6385). See [Redis Isolation](docs/DEPLOYMENT.md#redis-isolation-erpnext--openwa-on-same-server) for the full guide.

---

For the full troubleshooting guide, see the [Troubleshooting](https://github.com/Manaa-Soft/openwa_bridge/wiki/Troubleshooting) wiki page.

---

## File Structure

```
openwa_bridge/
├── hooks.py                    # App hooks, DocType overrides, fixtures, doc_events
├── utils.py                    # HMAC verification, JID helpers, type mapping,
│                               #   OpenWA API helper, render_doc_as_image(),
│                               #   OpenWACircuitBreaker, get_api_key(),
│                               #   is_openwa_account(), get_cached_account(),
│                               #   get_account_setting(), validate_openwa_url()
├── install.py                  # Pre-install dependency check
├── tasks.py                    # Session health check, outbox processing (retry),
│                               #   circuit breaker, scheduler safety-net,
│                               #   daily outbox cleanup/archival
├── whatsapp_account.py         # QR code display, one-click setup, session management,
│                               #   webhook auto-sync (on_update), SSRF validation,
│                               #   contact mgmt, typing, bulk msg, stickers, pairing code
├── whatsapp_message.py         # Outbound message routing, outbox creation
│                               #   (OverrideWhatsAppMessage)
├── whatsapp_templates.py       # Template sync to OpenWA (OverrideWhatsAppTemplates)
├── whatsapp_notification.py    # Notification: Jinja OR template, dynamic image header
│                               #   (OverrideWhatsAppNotification)
├── inbound.py                  # Inbound webhook: HMAC (lenient/strict), idempotency,
│                               #   rate limiting, status events, always HTTP 200
├── openwa_bridge/
│   ├── doctype/
│   │   ├── openwa_outbox/      # Async outbox DocType for reliable message delivery
│   │   └── openwa_bridge_settings/  # System-wide config (timeouts, limits, HMAC, rate limit, CB)
│   └── workspace/
│       └── whatsapp/           # Frappe Workspace with shortcuts
├── public/
│   └── js/
│       └── whatsapp_account.js # Client script: QR display, setup button, status
├── fixtures/
│   └── custom_field.json       # Custom fields on Account, Templates, Notification
└── tests/
    ├── conftest.py             # Shared test fixtures and mocks
    ├── test_utils.py           # Utility function tests
    ├── test_utils_new.py       # Additional utility tests
    ├── test_circuit_breaker.py # Circuit breaker tests
    ├── test_inbound_rate_limit.py  # Rate limiting tests
    ├── test_inbound_handlers.py    # Inbound handler tests
    ├── test_outbound.py        # Outbound message tests
    ├── test_notification.py    # Notification flow tests
    ├── test_templates.py       # Template sync tests
    ├── test_account.py         # Account management tests
    ├── test_outbox.py          # Outbox DocType tests
    ├── test_process_outbox.py  # Outbox processing tests
    ├── test_contact_management.py  # Contact mgmt & typing indicator tests
    ├── test_bulk_messaging.py  # Bulk msg, forward, delete tests
    └── test_pairing_sticker.py # Pairing code & sticker tests
```

### Key Methods

| File | Method | Purpose |
|---|---|---|
| `utils.py` | `openwa_api()` | REST helper for all OpenWA API calls |
| `utils.py` | `verify_openwa_signature()` | HMAC-SHA256 webhook verification |
| `utils.py` | `strip_jid_suffix()` | Strip `@c.us` / `@g.us` / `@lid` from JIDs |
| `utils.py` | `frappe_to_openwa_vars()` | Convert `{{1}}` to `{{param1}}` |
| `utils.py` | `render_doc_as_image()` | PDF -> PNG via PyMuPDF for dynamic headers |
| `whatsapp_account.py` | `setup_openwa_session()` | One-click: create session, start, fetch QR |
| `whatsapp_account.py` | `get_openwa_qr()` | Fetch QR code for existing session |
| `whatsapp_account.py` | `get_openwa_session_status()` | Get session status (ready/disconnected/etc) |
| `whatsapp_account.py` | `stop_openwa_session()` | Disconnect session |
| `tasks.py` | `hourly()` | Scheduled health check — restarts disconnected sessions |
| `tasks.py` | `daily()` | Daily backup health check |
| `tasks.py` | `_run_health_check()` | Core: checks all OpenWA sessions, restarts if needed |
| `whatsapp_account.py` | `on_account_trash()` | Delete OpenWA session when account is deleted |
| `whatsapp_account.py` | `on_account_update()` | Auto-sync webhook secret to OpenWA on save |
| `whatsapp_account.py` | `check_whatsapp_number()` | Verify if a phone number is on WhatsApp |
| `whatsapp_account.py` | `block_contact()` | Block a contact via OpenWA |
| `whatsapp_account.py` | `unblock_contact()` | Unblock a contact via OpenWA |
| `whatsapp_account.py` | `send_typing_indicator()` | Send typing/recording/paused state |
| `whatsapp_account.py` | `send_bulk_openwa()` | Bulk text send to multiple contacts |
| `whatsapp_account.py` | `forward_message()` | Forward message to another chat |
| `whatsapp_account.py` | `delete_message()` | Delete or revoke a message |
| `whatsapp_account.py` | `request_pairing_code()` | Get 8-char pairing code (alt to QR) |
| `whatsapp_account.py` | `send_sticker()` | Send sticker by URL or base64 |
| `tasks.py` | `cleanup_old_outbox()` | Daily archival of old outbox entries |
| `tasks.py` | `process_outbox_entry()` | Process single outbox entry with circuit breaker |
| `tasks.py` | `_send_outbox_message()` | Send message from outbox (image+caption optimization) |
| `tasks.py` | `_send_dynamic_header_for_outbox()` | Render and send dynamic header image in background |
| `tasks.py` | `_fail_outbox()` | Handle retry with exponential backoff |
| `tasks.py` | `process_pending_outbox()` | Scheduler safety-net: re-enqueue orphaned entries |
| `whatsapp_message.py` | `_send_via_openwa()` | Routes outbound messages by content type |
| `whatsapp_templates.py` | `_sync_to_openwa()` | Create/update/delete templates on OpenWA |
| `whatsapp_notification.py` | `send_template_message()` | Override: skip parent header/attachment logic |
| `whatsapp_notification.py` | `notify()` | Route by `openwa_send_type` (Template/Jinja) |
| `whatsapp_notification.py` | `_send_dynamic_header_image()` | Render doc as PNG and send via send-image |
| `whatsapp_notification.py` | `_extract_template_parameters()` | Read live doc values via fields child table |
| `whatsapp_message.py` | `_ensure_session_ready()` | Pre-send session health check — restarts if not ready |

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
