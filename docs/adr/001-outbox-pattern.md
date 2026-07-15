# ADR-001: Why Outbox Pattern (Not Direct Send)

**Status**: Accepted
**Date**: 2026-07-14
**Deciders**: Manaa Soft

## Context

When a WhatsApp Message doc is created in Frappe, the parent class `WhatsAppMessage` calls `notify()` synchronously during `before_insert()`. If the external API (OpenWA) is slow or down, the entire Frappe transaction blocks or fails.

We need a way to:
1. Guarantee message delivery even if OpenWA is temporarily unavailable
2. Not block the Frappe user's request
3. Provide retry capability with exponential backoff
4. Track delivery status per message

## Decision

We use the **Outbox Pattern**:

1. `notify()` intercepts during `before_insert()` and sets a flag `_openwa_outbox_needed = True`
2. `after_insert()` creates an `OpenWA Outbox` doc (now that `self.name` is available)
3. A background worker processes the outbox entry asynchronously
4. The worker calls `_send_via_openwa()` and updates status on success/failure
5. Failed entries retry with exponential backoff (30s, 60s, 120s, 300s, capped at 1h)

## Consequences

### Positive
- **Non-blocking**: Frappe transaction completes immediately
- **Reliable delivery**: Messages survive server restarts, worker crashes
- **Observable**: Outbox doc provides audit trail and status tracking
- **Configurable retry**: Max attempts, backoff timing per account

### Negative
- **Eventual delivery**: Users see "Queued" instead of immediate "Sent"
- **Complexity**: Extra DocType, background worker, scheduler safety-net
- **Duplicate risk**: Worker crash after send but before status update could cause retry (mitigated by idempotency on OpenWA side)

## Alternatives Considered

1. **Direct send in `before_insert()`**: Blocks Frappe, fails if OpenWA is down
2. **Direct send in `after_insert()`**: Still synchronous, blocks the request
3. **Redis queue (BullMQ-style)**: Adds Redis dependency, complex for single-server setup
4. **Frappe Background Jobs only**: No persistence, lost on worker restart

## References

- `openwa_bridge/whatsapp_message.py` — `notify()`, `after_insert()`
- `openwa_bridge/tasks.py` — `process_outbox_entry()`, `process_pending_outbox()`
- `openwa_bridge/openwa_bridge/doctype/openwa_outbox/` — Outbox DocType
