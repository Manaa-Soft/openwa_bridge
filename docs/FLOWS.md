# Message Flows

## Flow 1: Outbound Plain Text (Desk → WhatsApp)

```
User creates WhatsApp Message in Desk
  │
  ▼
frappe.get_doc(new_doc).insert()
  │
  ▼
after_insert → WhatsAppMessage.notify(data)
  │
  ▼
OverrideWhatsAppMessage.notify(data)
  │
  ├─ account.openwa_enabled = false?
  │    └─ YES → super().notify(data) → Meta Cloud API
  │
  ├─ content_type in (interactive, flow)?
  │    └─ YES → super().notify(data) → Meta Cloud API (fallback)
  │
  └─ OpenWA path → _send_via_openwa(account, data)
       │
       ▼
  POST /api/sessions/:id/messages/send-text
  { chatId, text }
       │
       ▼
  resp.json() → messageId → update WhatsApp Message status to "Sent"
```

## Flow 2: Outbound Template (Desk → WhatsApp)

```
User creates WhatsApp Message with template set
  │
  ▼
OverrideWhatsAppMessage.notify(data)
  │
  ▼
_send_via_openwa(account, data)
  │
  ├─ self.template is set?
  │    │
  │    ▼
  │  openwa_tid = frappe.db.get_value("WhatsApp Templates", self.template, "openwa_template_id")
  │    │
  │    ├─ openwa_tid exists?
  │    │    │
  │    │    ▼
  │    │  Build vars from body_param or template_parameters:
  │    │    body_param format:     { "1": "val1", "2": "val2" } → { "param1": "val1", "param2": "val2" }
  │    │    template_parameters:   ["val1", "val2"]              → { "param1": "val1", "param2": "val2" }
  │    │    │
  │    │    ▼
  │    │  POST /messages/send-template
  │    │  { chatId, templateId, vars: { param1: "val1", ... } }
  │    │    │
  │    │    ▼
  │    │  OpenWA: renderTemplate(header+body+footer, vars) → sendText()
  │    │
  │    └─ openwa_tid is empty?
  │         │
  │         ▼
  │       Fallback: _translate_template_payload() → manual substitution
  │       POST /messages/send-text
  │       { chatId, text: "Dear Faissal, your invoice..." }
  │
  ▼
Update WhatsApp Message: message_id + status="Sent"
```

## Flow 3: Notification (After Submit → WhatsApp)

```
DocType event triggers (e.g., Sales Invoice submit)
  │
  ▼
WhatsAppNotification.send_template_message(doc)
  │
  ▼
OverrideWhatsAppNotification.send_template_message(doc)
  │
  ├─ not OpenWA account? → super().send_template_message() → Meta API
  │
  ├─ openwa_send_type != "Template"? → super().send_template_message() → Meta API
  │
  └─ OpenWA Template path:
       │
       ▼
     1. Check disabled → return if yes
     2. Check condition → return if fails
     3. Get template doc
     4. Get phone number from field_name
     5. Build data dict with template name, language, components
     6. Extract parameters from fields child table (live doc values)
       │
       ├─ Dynamic header enabled?
       │    │
       │    ▼
       │  _send_dynamic_header_image(doc, template, data)
       │    │
       │    ├─ render_doc_as_image(doctype, name, print_format)
       │    │    ├─ frappe.get_print() → PDF bytes
       │    │    └─ fitz.open() → page 0 → pixmap → PNG bytes
       │    │
       │    └─ POST /messages/send-image
       │       { chatId, base64: <png>, mimetype: "image/png" }
       │       (errors are caught and logged, don't block template send)
       │
       ▼
     7. notify(data, doc_data)
       │
       ▼
     OverrideWhatsAppNotification.notify(data)
       │
       ▼
     openwa_send_type routing:
       │
       ├─ "Template" → _send_openwa_template(account, data, doc_data)
       │    │
       │    ▼
       │  Create WhatsApp Message doc:
       │    { type: "Outgoing", message_type: "Template", template: ...,
       │      template_parameters: '["val1","val2",...]', content_type: "text" }
       │    │
       │    ▼
       │  frappe.get_doc(new_doc).insert()
       │    │
       │    ▼
       │  after_insert → OverrideWhatsAppMessage.notify()
       │    │
       │    ▼
       │  _send_via_openwa() → POST /messages/send-template
       │
       ├─ "Jinja" → _send_openwa_text(account, data, rendered_message, doc_data)
       │    │
       │    ▼
       │  frappe.render_template(self.code, {"doc": doc})
       │  Create WhatsApp Message doc with rendered text
       │  → OverrideWhatsAppMessage.notify() → POST /messages/send-text
       │
       └─ (empty) → fallback to _send_openwa_template()
```

## Flow 4: Template Sync (Save on WhatsApp Templates)

```
User saves WhatsApp Templates doc
  │
  ▼
OverrideWhatsAppTemplates.before_save()
  │
  ├─ not OpenWA account? → super().before_save() → Meta API sync
  │
  └─ OpenWA path → _sync_to_openwa()
       │
       ▼
     Build payload: { name, body, header, footer }
       │
       ├─ openwa_dynamic_header enabled?
       │    └─ Set header=None in payload (skip syncing header)
       │
       ├─ openwa_template_id exists?
       │    │
       │    ├─ Try PUT /templates/:id
       │    │    │
       │    │    ├─ 200 OK → done
       │    │    │
       │    │    └─ 404 Not Found (stale ID)?
       │    │         │
       │    │         ▼
       │    │    Clear openwa_template_id
       │    │    GET /templates → find by name
       │    │         │
       │    │         ├─ Found → PUT with correct ID
       │    │         └─ Not found → POST (create fresh)
       │    │
       │    └─ Other error → log and continue
       │
       └─ No openwa_template_id?
            │
            ▼
          POST /templates (create)
            │
            ▼
          Save openwa_template_id = response.id
          Set status = "APPROVED"
          Set actual_name = name from payload
```

## Flow 5: Inbound Message (WhatsApp → Desk)

```
OpenWA receives message from WhatsApp
  │
  ▼
OpenWA webhook → POST /api/method/openwa_bridge.inbound.receive_openwa_message
  │
  Headers: X-Openwa-Signature: sha256=<hmac-hex>
  Body: { event, data: { from, to, body, type, ... } }
  │
  ▼
receive_openwa_message()
  │
  1. Parse request body
  2. Extract X-Openwa-Signature header (lowercase 'wa' in 'Openwa')
  3. Get webhook secret from site_config or WhatsApp Account
  4. Verify HMAC: verify_openwa_signature(payload_bytes, secret, sig_header)
       │
       ├─ Invalid → frappe.throw("Invalid signature", 403)
       │
       └─ Valid → continue
  │
  5. Idempotency check: frappe.cache().set(f"owb_msg:{msg_id}", True, ex=3600)
       │
       ├─ Already set → return ("Duplicate", 200)
       │
       └─ New message → continue
  │
  6. Route by event type:
       │
       ├─ "message.reaction" → _handle_reaction(data)
       │
       ├─ "message" / "message.any" → _handle_message(data)
       │    │
       │    ├─ Extract sender JID → strip_jid_suffix() (handles @lid)
       │    ├─ Find or create Contact/Lead
       │    ├─ Create WhatsApp Message doc (type="Incoming")
       │    ├─ If has media → download and attach as File
       │    ├─ If is_reply → link to reply_to_message_id
       │    └─ Create Communication record
       │
       └─ Other events → log and ignore
  │
  ▼
  Return "OK" (200)
```
