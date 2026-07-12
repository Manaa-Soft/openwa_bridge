# Architecture

## Overview

OpenWA Bridge is a Frappe app that intercepts `frappe_whatsapp` DocType operations via `override_doctype_class` and redirects them through a self-hosted OpenWA (WhatsApp Web API Gateway) instance instead of the Meta Cloud API.

## System Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                     Frappe / ERPNext Server                       │
│                       (192.168.1.15)                              │
│                                                                    │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                    frappe_whatsapp                          │  │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────────┐   │  │
│  │  │ WhatsAppMsg   │ │ WhatsAppTmpl │ │ WhatsAppNotif    │   │  │
│  │  │ (parent)      │ │ (parent)     │ │ (parent)         │   │  │
│  │  └──────┬───────┘ └──────┬───────┘ └────────┬─────────┘   │  │
│  │         │                 │                   │              │  │
│  │         ▼                 ▼                   ▼              │  │
│  │  ┌──────────────────────────────────────────────────────┐   │  │
│  │  │            Override Classes (openwa_bridge)           │   │  │
│  │  │                                                      │   │  │
│  │  │  OverrideWhatsAppMessage                             │   │  │
│  │  │    └─ notify() → _send_via_openwa()                  │   │  │
│  │  │         Routes: text/image/video/audio/doc/reaction/  │   │  │
│  │  │                  location/contact/poll/reply/template │   │  │
│  │  │                                                      │   │  │
│  │  │  OverrideWhatsAppTemplates                           │   │  │
│  │  │    └─ before_save() → _sync_to_openwa()              │   │  │
│  │  │         Creates/updates/deletes on OpenWA            │   │  │
│  │  │         Stale ID recovery (404 → name lookup → POST) │   │  │
│  │  │                                                      │   │  │
│  │  │  OverrideWhatsAppNotification                        │   │  │
│  │  │    └─ send_template_message() → notify()             │   │  │
│  │  │         Routes by openwa_send_type:                   │   │  │
│  │  │           Template → _send_openwa_template()          │   │  │
│  │  │           Jinja    → render code → _send_openwa_text()│   │  │
│  │  │         Dynamic image header: _send_dynamic_header_   │   │  │
│  │  │                                image()                │   │  │
│  │  └──────────────────────────────────────────────────────┘   │  │
│  │                                                              │  │
│  │  ┌──────────────────────────────────────────────────────┐   │  │
│  │  │                    inbound.py                         │   │  │
│  │  │  receive_openwa_message() - webhook endpoint         │   │  │
│  │  │  HMAC-SHA256 verification → idempotency → handlers   │   │  │
│  │  └──────────────────────────────────────────────────────┘   │  │
│  │                                                              │  │
│  │  ┌──────────────────────────────────────────────────────┐   │  │
│  │  │                    utils.py                           │   │  │
│  │  │  openwa_api()          - REST helper                  │   │  │
│  │  │  verify_openwa_signature() - HMAC verification       │   │  │
│  │  │  strip_jid_suffix()    - @c.us/@g.us/@lid cleanup    │   │  │
│  │  │  frappe_to_openwa_vars() - {{1}} → {{param1}}        │   │  │
│  │  │  openwa_to_frappe_vars() - {{param1}} → {{1}}        │   │  │
│  │  │  render_doc_as_image() - PDF → PNG via PyMuPDF       │   │  │
│  │  └──────────────────────────────────────────────────────┘   │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                 Custom Fields (fixtures)                    │  │
│  │  WhatsApp Account:     8 fields (OpenWA section +          │  │
│  │                          separator)                        │  │
│  │  WhatsApp Templates:   8 fields (sync + dynamic header     │  │
│  │                          + letterhead control)             │  │
│  │  WhatsApp Notification: 1 field  (openwa_send_type)        │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              Property Setters (fixtures)                    │  │
│  │  WhatsApp Account: 7 fields with depends_on                │  │
│  │    - token, url, version, webhook_verify_token             │  │
│  │    - phone_id, app_id, business_id                         │  │
│  │    - depends_on: "eval:!doc.openwa_enabled"                │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
                     HTTP REST API
                     localhost:2785
                               │
                               ▼
┌──────────────────────────────────────────┐
│           OpenWA Gateway                  │
│           (localhost:2785)                │
│                                          │
│  REST API:                               │
│    /api/sessions/:id/messages/send-text  │
│    /api/sessions/:id/messages/send-image │
│    /api/sessions/:id/messages/send-video │
│    /api/sessions/:id/messages/send-audio │
│    /api/sessions/:id/messages/send-doc   │
│    /api/sessions/:id/messages/send-tmpl  │
│    /api/sessions/:id/messages/reply      │
│    /api/sessions/:id/messages/react      │
│    /api/sessions/:id/messages/send-loc   │
│    /api/sessions/:id/messages/send-contact│
│    /api/sessions/:id/messages/send-poll  │
│    /api/sessions/:id/templates (CRUD)    │
│                                          │
│  Dashboard: localhost:2886               │
│  Webhooks: POST to Frappe inbound.py     │
│                                          │
│  Session: "manaa" (UUID shown in config) │
│  Phone: +967777713637                    │
└──────────────────────────────┬───────────┘
                               │
                     WhatsApp Web Protocol
                               │
                               ▼
┌──────────────────────────────────────────┐
│         WhatsApp Servers                  │
│    (Meta / WhatsApp infrastructure)      │
└──────────────────────────────────────────┘
```

## Override Mechanism

Frappe's `override_doctype_class` in `hooks.py` replaces the parent class at runtime:

```python
override_doctype_class = {
    "WhatsAppMessage": "openwa_bridge.whatsapp_message.OverrideWhatsAppMessage",
    "WhatsAppTemplates": "openwa_bridge.whatsapp_templates.OverrideWhatsAppTemplates",
    "WhatsAppNotification": "openwa_bridge.whatsapp_notification.OverrideWhatsAppNotification",
}
```

Each override class extends the parent and selectively intercepts methods:

- **OverrideWhatsAppMessage**: Intercepts `notify()` — if OpenWA enabled, routes via OpenWA; otherwise falls back to parent (Meta API).
- **OverrideWhatsAppTemplates**: Intercepts `before_save()` — skips Meta API calls for OpenWA accounts, syncs to OpenWA REST API instead.
- **OverrideWhatsAppNotification**: Overrides `send_template_message()` and `notify()` — routes by `openwa_send_type` (Template/Jinja/fallback).

## hooks.py Constraints

```python
require_type_annotated_api_methods = True
```

ALL `@frappe.whitelist()` functions MUST have type annotations:

```python
@frappe.whitelist()
def receive_openwa_message() -> None:  # ← type hint required
    ...
```

## Credential Flow

```
WhatsApp Account doc (Frappe)
  ├── openwa_enabled (Check)         → routing decision
  ├── openwa_base_url (Data)         → "http://localhost:2785"
  ├── openwa_session_id (Data)       → session UUID
  ├── openwa_api_key (Password)      → encrypted, accessed via get_password()
  └── openwa_webhook_secret (Password) → for inbound HMAC verification
```

## Key Design Decisions

1. **Override, not replace**: We extend parent classes so non-OpenWA accounts continue working via Meta API.
2. **Message doc as carrier**: Template sends create a WhatsApp Message doc first, which triggers `notify()` → routes to OpenWA. This preserves Frappe's message history.
3. **Two-step image+text**: OpenWA's `send-template` is text-only. Dynamic image headers require sending the image first via `send-image`, then the template text via `send-template`.
4. **Live doc values**: Notification parameters are resolved from the actual document at send time (not from pre-filled sample values).
5. **Graceful degradation**: If OpenWA is down, the error is logged but the doc is still saved. If dynamic image fails, the template text still sends.
