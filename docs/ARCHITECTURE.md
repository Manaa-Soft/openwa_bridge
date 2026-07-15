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
│  │  │    └─ validate() → set_whatsapp_account()             │   │  │
│  │  │         after_insert → _sync_to_openwa()              │   │  │
│  │  │         on_update → _sync_to_openwa() + auto-sync     │   │  │
│  │  │         Creates/updates/deletes on OpenWA             │   │  │
│  │  │         Stale ID recovery (404 → name lookup → POST)  │   │  │
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
│  │                                                              │  │
│  │  ┌──────────────────────────────────────────────────────┐   │  │
│  │  │                    tasks.py                           │   │  │
│  │  │  Scheduled health checks (hourly, daily)              │   │  │
│  │  │  Query all OpenWA-enabled accounts, check status,     │   │  │
│  │  │  restart disconnected sessions, sync status field     │   │  │
│  │  │  Daily cleanup: archive old outbox entries            │   │  │
│  │  └──────────────────────────────────────────────────────┘   │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                 Custom Fields (fixtures)                    │  │
│  │  WhatsApp Account:    16 fields (OpenWA section +             │  │
│  │                          settings + QR HTML)                 │  │
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
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              doc_events (hooks.py)                          │  │
│  │  WhatsApp Account on_trash → delete OpenWA session         │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │           scheduler_events (hooks.py)                      │  │
│  │  hourly → tasks.hourly → _run_health_check()              │  │
│  │  daily  → tasks.daily  → _run_health_check()              │  │
│  │  daily  → tasks.cleanup_old_outbox()                      │  │
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
│    /api/sessions/:id/messages/send-sticker│
│    /api/sessions/:id/messages/send-bulk  │
│    /api/sessions/:id/messages/forward    │
│    /api/sessions/:id/messages/:id (DEL)  │
│    /api/sessions/:id/chats/typing        │
│    /api/sessions/:id/contacts/check/:num │
│    /api/sessions/:id/contacts/:jid/block │
│    /api/sessions/:id/pairing-code        │
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

- **OverrideWhatsAppMessage**: Intercepts `notify()` — if OpenWA enabled, routes via OpenWA; otherwise falls back to parent (Meta API). Supports `sticker` content type and `@mention` extraction in text messages.
- **OverrideWhatsAppTemplates**: Intercepts `before_save()` — skips Meta API calls for OpenWA accounts, syncs to OpenWA REST API instead.
- **OverrideWhatsAppNotification**: Overrides `send_template_message()` and `notify()` — routes by `openwa_send_type` (Template/Jinja/fallback).
- **whatsapp_account.py**: Not an override class — provides whitelisted methods for QR code display, one-click session setup, contact management, typing indicators, bulk messaging, stickers, and pairing code auth. Registered via `doc_events` for `on_trash` cleanup.
- **inbound.py**: Webhook endpoint handles 10 event types: `message.received`, `message.sent`, `message.ack`, `message.failed`, `message.revoked`, `message.reaction`, `session.status`, `session.qr`, `session.authenticated`, `session.disconnected`. Auto-creates Communication and Lead/Contact for new inbound messages.

### doc_events Hooks

```python
doc_events = {
    "WhatsApp Account": {
        "on_update": "openwa_bridge.whatsapp_account.on_account_update",
        "on_trash": "openwa_bridge.whatsapp_account.on_account_trash"
    }
}
```

- **on_update**: When a WhatsApp Account is saved, `on_account_update()` syncs the webhook secret to OpenWA — finds existing webhook by URL and updates it, or creates a new one.
- **on_trash**: When a WhatsApp Account is deleted, `on_account_trash()` calls `DELETE /api/sessions/:id` on OpenWA to clean up the session.

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
3. **use_template flag**: `send-template` vs `send-text` is determined by the `use_template` field (set to 1 by `_send_openwa_template`, not set by `_send_openwa_text`). The `template` field alone is used for dynamic header lookups and should not control the send path.
4. **Two-step image+text**: When `openwa_dynamic_header` is enabled on a template, the image is sent via `send-image` with the rendered text as caption. The recipient receives one message (image + caption), not two separate messages.
4. **Live doc values**: Notification parameters are resolved from the actual document at send time (not from pre-filled sample values).
5. **Graceful degradation**: If OpenWA is down, the error is logged but the doc is still saved. If dynamic image fails, the template text still sends.
6. **Async outbox**: Outbound messages flow through `OpenWA Outbox` — created in `after_insert()` after the doc is persisted, processed by background workers with exponential backoff retry (30s–1h cap, 5 attempts max).
7. **Circuit breaker**: After 5 consecutive failures on an account, the circuit opens for 5 minutes (Redis-backed, per-account). Prevents cascade failures when OpenWA is down.
8. **Lenient HMAC**: When a webhook secret is configured but OpenWA sends no signature header, the message is processed with a warning log instead of rejecting. Prevents drops when HMAC isn't enabled on the OpenWA side.
9. **Always HTTP 200**: The inbound webhook never throws — all errors return HTTP 200 with an error body to prevent OpenWA retry loops.

## Outbox Pattern

```
notify() [before_insert]
  │
  ├─ openwa_enabled? → set _openwa_outbox_needed = True, return
  │
after_insert()
  │
  ├─ _openwa_outbox_needed? → create OpenWA Outbox doc
  │                           → frappe.enqueue(process_outbox_entry, queue="long")
  │
  ▼ [background worker]
process_outbox_entry()
  │
  ├─ load WhatsApp Message + Account (first, for settings checks)
  ├─ openwa_enabled still True? attempts < max?
  ├─ circuit breaker open? → fail with cooldown message
  ├─ _send_dynamic_header_for_outbox() → send image with rendered text as caption
  │    ├─ image sent? → done (1 message with caption, not 2)
  │    └─ no image? → fall through
  ├─ _send_via_openwa()
  │    ├─ use_template=1? → send-template with vars
  │    └─ Otherwise → send-text (plain text or rendered Jinja)
  ├─ success → record_success(), status=Sent
  └─ failure → record_failure(), exponential backoff retry
                (30s, 60s, 120s, 300s, capped at 1h)
                → after 5 attempts → status=Failed, log_error()
```

The scheduler safety-net (`process_pending_outbox`) runs every ~4 minutes and re-enqueues any Pending entries whose `next_retry_at` has passed — catches orphaned entries from worker crashes or Redis restarts.
