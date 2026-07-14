# ADR-004: Why Override Class Pattern (Not Hooks)

**Status**: Accepted
**Date**: 2026-07-14
**Deciders**: Manaa Soft

## Context

We need to intercept `frappe_whatsapp` DocType operations and redirect them through OpenWA instead of Meta Cloud API. Options:

1. **Frappe `doc_events` hooks**: Can intercept `before_insert`, `on_update`, etc. But cannot call `super()` methods or modify the return value of existing methods.
2. **`override_doctype_class`**: Replaces the parent class at runtime. Our subclass can selectively override methods and call `super()` for fallback.

## Decision

We use `override_doctype_class` for all three DocTypes:

```python
override_doctype_class = {
    "WhatsAppMessage": "openwa_bridge.whatsapp_message.OverrideWhatsAppMessage",
    "WhatsAppTemplates": "openwa_bridge.whatsapp_templates.OverrideWhatsAppTemplates",
    "WhatsAppNotification": "openwa_bridge.whatsapp_notification.OverrideWhatsAppNotification",
}
```

Each override class extends the parent and selectively intercepts:
- **OverrideWhatsAppMessage**: Intercepts `notify()` — routes via OpenWA when enabled, falls back to Meta API otherwise.
- **OverrideWhatsAppTemplates**: Intercepts `validate()` and `after_insert()` — skips Meta API calls for OpenWA accounts.
- **OverrideWhatsAppNotification**: Overrides `send_template_message()` — routes by `openwa_send_type`.

## Consequences

### Positive
- **Seamless fallback**: Non-OpenWA accounts continue working via Meta API
- **Method-level control**: Override only what's needed, inherit everything else
- **No code duplication**: Parent class logic is preserved via `super()`
- **Clean separation**: OpenWA logic lives in our app, not in frappe_whatsapp

### Negative
- **Fragile to upstream changes**: If `frappe_whatsapp` changes method signatures, our overrides may break
- **Runtime replacement**: Class replacement happens at import time — debugging stack traces can be confusing
- **Testing complexity**: Must test both override and parent paths

## Alternatives Considered

1. **`doc_events` hooks**: Cannot call `super()` or modify return values — insufficient for routing
2. **Monkey-patching**: Fragile, hard to maintain, no IDE support
3. **Fork frappe_whatsapp**: Maintenance burden, diverges from upstream
4. **Middleware/interceptor pattern**: Overkill for three DocTypes

## References

- `openwa_bridge/hooks.py` — `override_doctype_class` registration
- `openwa_bridge/whatsapp_message.py` — `OverrideWhatsAppMessage`
- `openwa_bridge/whatsapp_templates.py` — `OverrideWhatsAppTemplates`
- `openwa_bridge/whatsapp_notification.py` — `OverrideWhatsAppNotification`
