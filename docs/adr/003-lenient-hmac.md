# ADR-003: Why Lenient HMAC (Not Strict)

**Status**: Accepted
**Date**: 2026-07-14
**Deciders**: Manaa Soft

## Context

HMAC-SHA256 verification ensures inbound webhooks are authentic. However:
- OpenWA may not have HMAC configured (default setup has no secret)
- Users enable HMAC at different times — during transition, some webhooks lack signatures
- Strict rejection drops valid messages during the transition period

## Decision

We implement **lenient HMAC by default** with an opt-in strict mode:

1. **Lenient (default)**: If a webhook secret is configured on the WhatsApp Account but OpenWA sends no signature header, the message is processed with a warning log instead of rejection.
2. **Strict mode**: When `openwa_hmac_strict` is checked, missing signatures are rejected with HTTP 401.
3. **No secret**: If no secret is configured, HMAC verification is skipped entirely.

## Consequences

### Positive
- **No message loss during transition**: Users can enable HMAC without breaking existing webhooks
- **Clear upgrade path**: Check `openwa_hmac_strict` when ready to enforce
- **Backward compatible**: Works with old OpenWA versions that don't send HMAC

### Negative
- **Weaker default security**: Lenient mode accepts unverified webhooks — acceptable for internal network deployments
- **User must opt-in**: Strict mode requires manual checkbox

## Alternatives Considered

1. **Always strict**: Drops messages during HMAC setup transition
2. **No HMAC at all**: Insecure for production deployments
3. **Per-account HMAC with migration wizard**: Too complex for current user base

## References

- `openwa_bridge/inbound.py` — HMAC verification logic
- `openwa_bridge/fixtures/custom_field.json` — `openwa_hmac_strict` field
