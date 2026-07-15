# ADR-005: Why `after_insert` for Outbox (Not `before_insert`)

**Status**: Accepted
**Date**: 2026-07-14
**Deciders**: Manaa Soft

## Context

The outbox entry needs a `whatsapp_message` foreign key to link back to the message doc. But during `before_insert()`, `self.name` is `None` — the doc hasn't been persisted yet.

Options:
1. Create outbox in `before_insert()` — `self.name` is None, FK would be null
2. Create outbox in `after_insert()` — `self.name` is assigned, FK works
3. Create outbox in `on_update()` — works but runs on every save, not just insert

## Decision

We use a **two-phase pattern**:

1. **Phase 1 — `before_insert()`**: `notify()` sets `_openwa_outbox_needed = True` (in-memory flag only)
2. **Phase 2 — `after_insert()`**: `after_insert()` checks the flag and creates the `OpenWA Outbox` doc with the real `self.name`

```python
def notify(self, data):
    if account.openwa_enabled:
        self._openwa_outbox_needed = True
        return  # don't create outbox yet

def after_insert(self):
    if not getattr(self, "_openwa_outbox_needed", False):
        return
    outbox = frappe.get_doc({
        "doctype": "OpenWA Outbox",
        "whatsapp_message": self.name,  # now available!
        ...
    })
    outbox.insert()
    frappe.enqueue("openwa_bridge.tasks.process_outbox_entry", ...)
```

## Consequences

### Positive
- **Valid FK**: `whatsapp_message` field always has a real doc name
- **Atomic**: Outbox creation is part of the same transaction as the message insert
- **No orphaned entries**: If the message insert fails, no outbox entry is created

### Negative
- **Two-phase complexity**: Flag must be carried across method calls
- **Flag persistence risk**: If `after_insert()` is somehow skipped, the flag is lost (mitigated by scheduler safety-net)

## Alternatives Considered

1. **`before_insert()` with temporary ID**: Fragile, violates Frappe conventions
2. **`on_update()`**: Runs on every save, not just insert — would create duplicate outbox entries
3. **Post-commit hook**: Frappe doesn't have a reliable post-commit hook
4. **Deferred FK**: Create outbox without FK, update later — adds complexity, risks orphaned entries

## References

- `openwa_bridge/whatsapp_message.py:18-75` — `notify()`, `after_insert()`
- `openwa_bridge/openwa_bridge/doctype/openwa_outbox/` — Outbox DocType
- `openwa_bridge/tasks.py:159-251` — `process_outbox_entry()`
