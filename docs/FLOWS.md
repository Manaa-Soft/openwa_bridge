# Message Flows

## Flow 1: Outbound Plain Text (Desk → WhatsApp)

```
User creates WhatsApp Message in Desk
  │
  ▼
frappe.get_doc(new_doc).insert()
  │
  ▼
after_insert → outbox entry (if OpenWA + outbox needed)
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
  ├─ self.use_template AND self.template is set?
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
  ├─ self.template set but self.use_template NOT set?
  │    └─ Jinja path: falls through to content_type check below
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
  ├─ openwa_send_type not in ("Template", "Jinja")? → super().send_template_message() → Meta API
  │
  └─ OpenWA path: builds data dict, calls self.notify(data, doc_data)
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
        │    { type: "Outgoing", message_type: "Template",
        │      use_template: 1, template: ...,
        │      template_parameters: '["val1","val2",...]',
        │      message: <rendered notification code>,
        │      content_type: "text" }
        │    │
        │    ▼
        │  frappe.get_doc(new_doc).insert()
        │    │
        │    ▼
        │  after_insert → outbox entry → process_outbox_entry
        │    │
        │    ▼
        │  _send_outbox_message():
        │    ├─ Dynamic header enabled on template?
        │    │    ├─ caption = rendered header + body + footer (placeholders filled)
        │    │    │   (free text / Jinja: caption = composed message)
        │    │    ├─ _send_dynamic_header_for_outbox(msg, account, caption=caption)
        │    │    │    ├─ Image sent? → done — image+caption is the ONLY delivery,
        │    │    │    │    no separate template/text bubble (no duplicate text)
        │    │    │    └─ Image failed and not already delivered? → fall through
        │    └─ _send_via_openwa()
        │         ├─ use_template=1 AND template set? → POST /messages/send-template
        │         └─ Otherwise → POST /messages/send-text
        │
        ├─ "Jinja" → notify() with rendered message
        │    │
        │    ▼
        │  1. Render self.code via frappe.render_template(self.code, {"doc": doc})
        │    │
        │    ▼
        │  2. _send_openwa_text(account, data, rendered_message, doc_data)
        │    │    Creates WhatsApp Message with template set (for dynamic header)
        │    │    but use_template NOT set → send-text path
        │    │
        │    ▼
        │  after_insert → outbox entry → process_outbox_entry
        │    │
        │    ▼
        │  _send_outbox_message():
        │    ├─ Dynamic header enabled on template?
        │    │    ├─ caption = msg.message (rendered Jinja text)
        │    │    ├─ _send_dynamic_header_for_outbox(msg, account, caption=caption)
        │    │    │    ├─ Image sent? → done — image+caption is the ONLY delivery
        │    │    │    └─ Image failed and not already delivered? → fall through
        │    └─ _send_via_openwa()
        │         └─ use_template NOT set → content_type == "text" → POST /messages/send-text
        │              (sends rendered Jinja text as plain text)
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
        │    └─ Still sync header to OpenWA (with {{1}}→{{param1}} conversion)
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
  Headers: X-OpenWA-Signature: sha256=<hmac-hex> (optional)
  Headers: X-OpenWA-Idempotency-Key: <unique-id> (optional)
  Body: { event, sessionId, data: { from, to, body, type, ... } }
  │
  ▼
receive_openwa_message()
  │
  1. Parse request body (get_json(force=True))
  2. Validate event field present
  3. Rate limiting: 60 req/min per IP (frappe.cache)
  4. Resolve WhatsApp Account by session ID
  │
  5. HMAC verification (lenient mode):
       │
       ├─ Secret configured + signature present + valid → continue
       ├─ Secret configured + signature present + INVALID → return error (200)
       ├─ Secret configured + NO signature → warn + continue (lenient)
       └─ No secret configured → skip verification
  │
  6. Idempotency check: frappe.cache().set_value("owb_msg:{key}", 1, expires_in_sec=3600)
       │
       ├─ Already set → return {"status": "duplicate"} (200)
       └─ New message → continue
  │
   7. Route by event type (always returns 200):
        │
        ├─ "message.received" → _handle_inbound_message()
        │    ├─ Skip if fromMe (outgoing echo)
        │    ├─ Extract sender JID → strip_jid_suffix() (handles @lid)
        │    ├─ Group messages → extract actual author
        │    ├─ Create WhatsApp Message doc (type="Incoming")
        │    ├─ If has media → download and attach as File
        │    ├─ If is_reply → link to reply_to_message_id
        │    ├─ Create WhatsApp Profile
        │    ├─ _create_communication() → create Communication doc linked to Contact
        │    │    └─ No contact found? → auto-create Lead + Contact first
        │    └─ Log success with doc name
        │
        ├─ "message.sent" → _handle_status_update()
        │    └─ Update WhatsApp Message status field
        │
        ├─ "message.ack" / "message.failed" → _handle_status_update()
        │    ├─ Status priority: pending(0) < Sent(1) < Delivered(2) < Read(3)
        │    ├─ Only allows forward transitions (never downgrades)
        │    ├─ PLAYED (Baileys ack 5) normalized to Read
        │    ├─ Reconciles stuck outbox entries on sent/delivered/read
        │    └─ Logs status transitions
        │
        ├─ "message.revoked" → _handle_message_revoked()
        │    └─ Set WhatsApp Message status to "Revoked"
        │
        ├─ "message.reaction" → _handle_message_reaction()
        │    └─ Log reaction in Frappe error log
        │
        ├─ "session.status" → _handle_session_status()
        │    └─ Update WhatsApp Account status (Active/Inactive)
        │
        ├─ "session.qr" → _handle_session_qr()
        │    └─ Set WhatsApp Account status to Inactive (needs QR scan)
        │
        ├─ "session.authenticated" → _handle_session_authenticated()
        │    └─ Set WhatsApp Account status to Active
        │
         └─ "session.disconnected" → _handle_session_status()
              └─ Set WhatsApp Account status to Inactive
         │
         ├─ "message.edited" → _handle_message_edited()
         │    └─ Update WhatsApp Message message field with edited text
         │
         ├─ "session.reconnect_loop" → _handle_session_reconnect_loop()
         │    └─ Log error with recovery suggestion
         │
         ├─ "group.join" / "group.leave" → _handle_group_membership()
         │    └─ Log join/leave event with group ID and participants
         │
         ├─ "group.update" → _handle_group_update()
         │    └─ Log group metadata changes (subject, description, etc.)
         │
         ├─ "call.received" → _handle_call_received()
         │    └─ Log incoming voice/video call event
         │
         └─ "status.received" → _handle_status_received()
              └─ Log contact status/story event
  │
  ▼
  Return {"status": "ok"} (200)
  ── All errors return HTTP 200 to prevent OpenWA retry loops ──
```

## Flow 6: One-Click Session Setup (WhatsApp Account → OpenWA)

```
User opens WhatsApp Account form
  │
  ▼
refresh() → get_openwa_session_status()
  │
  ├─ openwa_session_id empty?
  │    │
  │    ▼
  │  Show "Setup OpenWA" button
  │    │
  │    ▼
  │  User clicks "Setup OpenWA"
  │    │
  │    ▼
  │  setup_openwa_session(account_name)
  │    │
  │    ├─ 1. Connectivity check: GET /api/sessions (10s timeout)
  │    │      ├─ ConnectionError → "Cannot connect to OpenWA"
  │    │      └─ Timeout → "OpenWA did not respond in time"
  │    │
  │    ├─ 2. Sanitize account name → session name
  │    │      "My Shop" → "my-shop"
  │    │
  │    ├─ 3. GET /api/sessions → find existing session by name
  │    │
  │    ├─ 4. If not found → POST /api/sessions { name: "my-shop" }
  │    │      (409 Conflict → re-fetch list to find existing)
  │    │
  │    ├─ 5. Save openwa_session_id to WhatsApp Account doc
  │    │
  │    ├─ 6. Check session status
  │    │      ├─ ready → return (already connected)
  │    │      └─ disconnected/created/failed → POST /start (60s timeout)
  │    │
  │    └─ 7. GET /qr → return QR data URL
  │
  │    ▼
  │  JS renders QR in openwa_qr_html field
  │  Auto-refreshes every 55 seconds
  │    │
  │    ▼
  │  User scans QR with phone
  │    │
  │    ▼
  │  Session status → "ready"
  │  Form reloads → green indicator + "Disconnect" button
  │
  └─ openwa_session_id exists?
       │
       ▼
     Check live status → show QR / Connected / Disconnected
```

## Flow 7: Account Deletion (Frappe → OpenWA)

```
User deletes WhatsApp Account in Frappe Desk
  │
  ▼
doc_events["on_trash"] → on_account_trash(doc, method)
  │
  ├─ openwa_enabled = 0? → return (no session to delete)
  ├─ openwa_session_id empty? → return (no session to delete)
  │
  └─ DELETE /api/sessions/:session_id
       │
       ├─ 204 No Content → session deleted from OpenWA
       └─ Error → log error, don't block Frappe delete
```

## Flow 8: Scheduled Health Check (Auto-Reconnect)

```
Frappe Scheduler (hourly/daily)
  │
  ▼
tasks.hourly() or tasks.daily()
  │
  ▼
_run_health_check()
  │
  ├─ _get_openwa_accounts()
  │    └─ Query: openwa_enabled=1 AND openwa_session_id IS SET
  │
  └─ For each account:
       │
       ▼
     _check_session_status(base_url, session_id, api_key)
       │
       ├─ Connection error → _set_account_status(name, "disconnected")
       │
       ├─ 404 → clear stale session_id, attempt auto-recovery
       │
       ├─ 429 → skip (rate-limited)
       │
       ├─ status = "ready" AND phone set → _set_account_status(name, "ready")
       │
       ├─ status = "ready" but NO phone → restart (engine alive but WhatsApp not linked)
       │
       └─ status in (disconnected, created, failed):
            │
            ▼
          _start_session(base_url, session_id, api_key)
            │
            ├─ POST /api/sessions/:id/start (60s timeout)
            ├─ Wait 15s for initialization
            ├─ Re-check status
            └─ _set_account_status(name, new_status)
```

## Flow 9: Pre-Send Session Check (Before Every Send)

```
_send_via_openwa(account, meta_payload)
  │
  ▼
_ensure_session_ready(account)
  │
  ├─ 1. GET /api/sessions/:id (10s timeout)
  │      │
  │      ├─ status = "ready" AND phone set → return (truly connected)
  │      ├─ status = "ready" but NO phone → continue to restart (engine alive but WhatsApp not linked)
  │      ├─ status != "ready" → continue to restart
  │      ├─ status = 429 → return (rate-limited, assume connected)
  │      └─ status = 404 → throw "session deleted"
  │
  ├─ 2. POST /api/sessions/:id/start (60s timeout)
  │      │
  │      ├─ 200/201/400 → continue (400 = already started)
  │      └─ Other → throw "restart failed: HTTP {code}"
  │
  ├─ 3. Poll status up to 15s (1s intervals)
  │      │
  │      ├─ status = "ready" AND phone set → return (recovered!)
  │      ├─ status = "qr_ready" → throw "requires QR re-scan"
  │      └─ Other → continue polling
  │
  └─ 5. throw "session is {status} and could not be recovered"
       → User must open WhatsApp Account form and click "Reconnect"
```

## Flow 10: Outbox Processing (Background Worker)

```
WhatsApp Message created (type="Outgoing")
  │
  ▼
OverrideWhatsAppMessage.notify() [inside before_insert]
  │
  ├─ openwa_enabled? → self._openwa_outbox_needed = True → return
  │
  ▼
OverrideWhatsAppMessage.after_insert() [self.name now assigned]
  │
  ├─ _openwa_outbox_needed?
  │    │
  │    ▼
  │  Create OpenWA Outbox doc:
  │    { whatsapp_message, whatsapp_account, content_type,
  │      status: "Pending", max_attempts: 100 }
  │    │
  │    ▼
  │  frappe.enqueue(process_outbox_entry, queue="long", timeout=300)
  │
  ▼ [background worker — long queue]
process_outbox_entry(outbox_name)
  │
  1. Load outbox (for_update=True)
  2. Guard: status must be "Pending"
  3. Guard: next_retry_at must be NULL or <= now
  4. Load linked WhatsApp Message + WhatsApp Account (before guard checks)
  5. Guard: openwa_enabled still True
  6. Guard: attempts < max_attempts
  7. Mark status="Sending", increment attempts
  │
  8. Pre-send session check (NEW):
       │
       ├─ GET /api/sessions/:id (10s timeout)
       │    │
       │    ├─ status=ready AND phone set → continue (truly connected)
       │    ├─ status != ready or no phone → _fail_outbox("not connected")
       │    └─ 404 → _fail_outbox("session deleted")
       │
       └─ On connection error → fall through to _ensure_session_ready
  │
  9. Circuit breaker check:
       ├─ OpenWACircuitBreaker(account).is_open()
       │    ├─ Yes → fail with cooldown message, schedule retry
       │    └─ No → continue
  │
  10. _send_outbox_message(msg, account, outbox)
        │
        ├─ msg.template set AND template has openwa_dynamic_header?
        │    ├─ caption = rendered header + body + footer (template send)
        │    │            or msg.message (free text / Jinja)
        │    ├─ _send_dynamic_header_for_outbox(msg, account, caption=caption)
        │    │    ├─ Image sent? → done — image+caption is the ONLY delivery
        │    │    └─ Image failed + not already delivered? → continue below
        │
        └─ _send_via_openwa()
             ├─ use_template=1 AND template set? → send-template with vars
             └─ Otherwise → send-text (plain text or rendered Jinja)
  │
  11. On success:
       ├─ breaker.record_success()
       └─ status = "Sent"
  │
  12. On failure:
       ├─ breaker.record_failure()
       └─ _fail_outbox():
            ├─ attempts < max_attempts?
            │    └─ Schedule retry with exponential backoff:
            │         30s, 60s, 120s, 300s, capped at 1 hour
            │         status remains "Pending", set next_retry_at
            └─ attempts >= max_attempts?
                 └─ status = "Failed", frappe.log_error()
```

## Flow 11: Webhook Auto-Sync (Account Save → OpenWA)

```
User saves WhatsApp Account in Frappe Desk
  │
  ▼
doc_events["on_update"] → on_account_update(doc, method)
  │
  ├─ openwa_enabled = 0? → return
  ├─ openwa_session_id empty? → return
  │
  └─ Sync webhook secret:
       │
       ├─ Build frappe_url = get_url("/api/method/openwa_bridge.inbound.receive_openwa_message")
       │
       ├─ GET /webhooks → list all webhooks for session
       │    │
       │    ├─ Find webhook with matching URL
       │    │    │
       │    │    └─ Found → PUT /webhooks/:id { secret: <new-secret> }
       │    │
         │    └─ Not found → POST /webhooks {
         │         url: frappe_url,
         │         events: ["message.received", "message.sent", "message.ack",
         │                  "message.failed", "message.revoked", "message.reaction",
         │                  "message.edited", "session.status", "session.qr",
         │                  "session.authenticated", "session.disconnected",
         │                  "session.reconnect_loop", "group.join", "group.leave",
         │                  "group.update", "call.received", "status.received"],
         │         secret: <new-secret>
         │       }
        │
        └─ On error → frappe.log_error() (best-effort, doesn't block save)
```

## Flow 12: Contact Management & Advanced Messaging (Desk → OpenWA)

```
User calls a whitelisted method from Desk or JS
  │
  ▼
Check frappe.has_permission()
  │
  ├─ No permission → frappe.throw(PermissionError)
  │
  └─ Has permission → route by method:
       │
       ├─ check_whatsapp_number(account, number)
       │    └─ GET /contacts/check/:number → { exists, whatsappId }
       │
       ├─ block_contact(account, jid)
       │    └─ POST /contacts/:jid/block → { status: "blocked" }
       │
       ├─ unblock_contact(account, jid)
       │    └─ DELETE /contacts/:jid/block → { status: "unblocked" }
       │
       ├─ send_typing_indicator(account, chat_id, state)
       │    └─ POST /chats/typing { chatId, state } → { status: "ok" }
       │
       ├─ send_bulk_openwa(account, contacts, message)
       │    ├─ Parse comma-separated contacts → format to JIDs (chunk ≤100)
       │    └─ POST /messages/send-bulk { messages[] } → 202 { batchId } → poll GET /messages/batch/:id → { sent, failed, pending }
       │
       ├─ forward_message(account, message_id, chat_id)
       │    ├─ Resolve source chat via _resolve_message_chat_id()
       │    └─ POST /messages/forward { fromChatId, toChatId, messageId }
       │
       ├─ delete_message(account, message_id, revoke)
       │    ├─ Resolve chat via _resolve_message_chat_id()
       │    └─ POST /messages/delete { chatId, messageId, forEveryone }
       │       (503 → treated as applied, uncertain: true)
       │
       ├─ request_pairing_code(account, phone)
       │    └─ POST /pairing-code { phoneNumber } → { pairingCode: "ABCD1234" }
       │
        ├─ send_sticker(account, chat_id, url/base64)
        │    └─ POST /messages/send-sticker { chatId, url/base64 }
        │
        ├─ edit_message(account, chat_id, message_id, body)
        │    └─ POST /messages/edit { chatId, messageId, body }
        │
        ├─ post_status_text(account, text, background_color, font)
        │    └─ POST /statuses { text, background_color, font }
        │
        ├─ post_status_image(account, url/base64, caption)
        │    └─ POST /statuses { url/base64, caption, type: "image" }
        │
        ├─ post_status_video(account, url/base64, caption)
        │    └─ POST /statuses { url/base64, caption, type: "video" }
        │
        ├─ get_statuses(account)
        │    └─ GET /statuses → { statuses: [...] }
        │
        ├─ reject_call(account, call_id)
        │    └─ POST /calls/:callId/reject → { status: "ok" }
        │
       ├─ mark_chat_read(account, chat_id)
       │    └─ POST /chats/read { chatId } → { status: "ok" }
       │
       ├─ mark_chat_unread(account, chat_id)
       │    └─ POST /chats/unread { chatId } → { status: "ok" }
       │
       ├─ get_chat_history(account, chat_id, limit)
       │    └─ GET /messages/:chatId/history?limit=N → { messages: [...] }
        │
        ├─ search_messages(account, query, limit)
        │    └─ GET /messages/search?q=query&limit=N → { messages: [...] }
        │
        ├─ list_groups(account)
        │    └─ GET /groups → { groups: [...] }
        │
        ├─ create_group(account, name, participants)
        │    └─ POST /groups { name, participants } → { groupId }
        │
        ├─ add_group_participants(account, group_id, participants)
        │    └─ POST /groups/:id/participants { participants } → { status }
        │
        ├─ remove_group_participants(account, group_id, participants)
        │    └─ DELETE /groups/:id/participants { participants } → { status }
        │
        ├─ promote_group_admins(account, group_id, participants)
        │    └─ POST /groups/:id/admins { participants } → { status }
        │
        ├─ demote_group_admins(account, group_id, participants)
        │    └─ DELETE /groups/:id/admins { participants } → { status }
        │
        ├─ set_group_name(account, group_id, name)
        │    └─ PUT /groups/:id/subject { name } → { status }
        │
        ├─ leave_group(account, group_id)
        │    └─ DELETE /groups/:id → { status }
        │
        ├─ list_labels(account)
        │    └─ GET /labels → { labels: [...] }
        │
        ├─ add_label_to_chat(account, label_id, chat_id)
        │    └─ POST /labels/:id/chats/:chatId → { status }
        │
        ├─ remove_label_from_chat(account, label_id, chat_id)
        │    └─ DELETE /labels/:id/chats/:chatId → { status }
        │
       ├─ send_bulk_with_progress(account, contacts, message)
       │    └─ POST /messages/send-bulk { messages[] } → 202 { batchId }
        │
        ├─ set_profile_name(account, name)
        │    └─ PUT /profile/name { name } → { status }
        │
        ├─ set_profile_status(account, status)
        │    └─ PUT /profile/status { status } → { status }
        │
        ├─ set_profile_picture(account, url/base64)
        │    └─ PUT /profile/picture { url/base64 } → { status }
        │
        ├─ get_session_stats(account)
        │    └─ GET /stats/overview → { stats }
        │
        ├─ list_channels(account)
        │    └─ GET /channels → { channels: [...] }
        │
        ├─ get_channel_messages(account, channel_id, limit)
        │    └─ GET /channels/:id/messages?limit=N → { messages: [...] }
        │
        ├─ get_contact_statuses(account, contact_id)
        │    └─ GET /contacts/:id/statuses → { statuses: [...] }
        │
        ├─ get_status_media(account, status_id)
        │    └─ GET /statuses/:id/media → { media }
        │
        ├─ subscribe_channel(account, invite_code)
        │    └─ POST /channels/subscribe { inviteCode } → { status }
        │
        ├─ unsubscribe_channel(account, channel_id)
        │    └─ DELETE /channels/:id → { status }
        │
        ├─ get_message_reactions(account, chat_id, message_id)
        │    └─ GET /messages/:id/reactions → { reactions: [...] }
        │
        ├─ cancel_batch(account, batch_id)
        │    └─ DELETE /messages/batch/:id → { status }
        │
        ├─ get_overview_stats(account)
        │    └─ GET /stats/overview → { stats }
        │
        ├─ get_message_stats(account, period)
        │    └─ GET /stats/messages?period=24h → { stats }
        │
        ├─ get_group(account, group_id)
        │    └─ GET /groups/:groupId → { group }
        │
        ├─ join_group_by_code(account, invite_code)
        │    └─ POST /groups/join { inviteCode } → { result }
        │
        ├─ get_group_settings(account, group_id)
        │    └─ GET /groups/:groupId/settings → { settings }
        │
        ├─ set_group_settings(account, group_id, settings)
        │    └─ PUT /groups/:groupId/settings { settings } → { status }
        │
        ├─ set_group_description(account, group_id, description)
        │    └─ PUT /groups/:groupId/description { description } → { status }
        │
        ├─ get_group_invite_code(account, group_id)
        │    └─ GET /groups/:groupId/invite-code → { inviteCode }
        │
        ├─ revoke_group_invite_code(account, group_id)
        │    └─ POST /groups/:groupId/invite-code/revoke → { status }
        │
        ├─ list_contacts(account)
        │    └─ GET /contacts → { contacts: [...] }
        │
        ├─ get_contact(account, contact_id)
        │    └─ GET /contacts/:contactId → { contact }
        │
        ├─ get_contact_profile_picture(account, contact_id)
        │    └─ GET /contacts/:contactId/profile-picture → { profilePicture }
        │
        ├─ get_contact_phone(account, contact_id)
        │    └─ GET /contacts/:contactId/phone → { phone }
        │
       ├─ list_profile_pictures(account, contacts)
       │    └─ GET /contacts/profile-pictures?ids=... (cap 50) → { pictures: [...] }
        │
        ├─ delete_chat(account, chat_id)
        │    └─ POST /chats/delete { chatId } → { status }
        │
        ├─ delete_status(account, status_id)
        │    └─ DELETE /status/:statusId → { status }
        │
        ├─ get_label(account, label_id)
        │    └─ GET /labels/:labelId → { label }
        │
        ├─ get_chat_labels(account, chat_id)
        │    └─ GET /labels/chat/:chatId → { labels: [...] }
        │
        ├─ get_batch_status(account, batch_id)
        │    └─ GET /messages/batch/:batchId → { batchStatus }
        │
        ├─ test_webhook(account, webhook_id)
        │    └─ POST /webhooks/:webhookId/test → { result }
        │
        ├─ get_catalog(account)
        │    └─ GET /catalog → 501 Not Implemented
        │
        ├─ get_catalog_products(account)
        │    └─ GET /catalog/products → 501 Not Implemented
        │
        ├─ get_catalog_product(account, product_id)
        │    └─ GET /catalog/products/:productId → 501 Not Implemented
        │
        ├─ send_product_message(account, chat_id, product_id)
        │    └─ POST /messages/send-product → 501 Not Implemented
        │
        └─ send_catalog_message(account, chat_id, catalog_id)
             └─ POST /messages/send-catalog → 501 Not Implemented
```

## Flow 13: Outbox Cleanup (Daily Scheduler)

```
Frappe Scheduler (daily)
  │
  ▼
tasks.cleanup_old_outbox()
  │
  ├─ DELETE OpenWA Outbox WHERE status='Sent' AND creation < (now - 7 days)
  │    └─ Removes old successfully-sent entries
  │
  └─ DELETE OpenWA Outbox WHERE status='Failed' AND creation < (now - 30 days)
       └─ Removes old failed entries
```

---

## Flow 14: WhatsApp Catalog Product (Desk to Chat)

```
User creates WhatsApp Catalog Product (linked to ERPNext Item)
  └→ _auto_fetch_item_fields() populates name, desc, price, image from Item
       └→ Editable overrides for WhatsApp-specific presentation
  └→ Send to Chat button
       └→ send_product_to_chat(product_name, chat_id)
            └→ _send_fallback_product_message(account, chat_id, product)
                 └→ POST /messages/send-text
                      { chatId, text: "*Product Name*\n\ndesc\n\n*Price:* USD 29.99",
                        mediaUrl: ".../image.png" }
```

### Key Points
- **OpenWA does not support catalog** — neither engine implements WhatsApp Business catalog operations (create/read/update). Catalog endpoints always return 501.
- **All products are sent as fallback** — richly formatted text+image messages with product name, description, price, availability, and image
- **Item auto-fetch** — name, description, image, and valuation_rate are pulled from the linked Item
- **All fields are editable** — WhatsApp-specific overrides don't affect the original Item
