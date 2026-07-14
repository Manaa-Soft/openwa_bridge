# ADR-002: Why Circuit Breaker (Not Simple Retry)

**Status**: Accepted
**Date**: 2026-07-14
**Deciders**: Manaa Soft

## Context

When OpenWA is down, every outbox entry retries independently, creating a thundering herd:
- 50 pending messages each retry every 30s = 100 requests/minute to a dead server
- Each retry wastes resources, logs errors, and delays recovery
- Simple retry doesn't differentiate between "transient hiccup" and "service down"

## Decision

We implement a **per-account Circuit Breaker** with three states:

1. **Closed** (normal): Requests flow through. Failures increment counter.
2. **Open** (tripped): After N consecutive failures, circuit opens for M seconds. All requests fail fast with a clear error message.
3. **Half-Open** (probe): After cooldown, one request is allowed through to test recovery.

Configuration (per account):
- `openwa_cb_threshold`: failures before opening (default: 5)
- `openwa_cb_cooldown`: seconds to stay open (default: 300)

State is stored in Frappe cache (Redis-backed when available, in-memory otherwise).

## Consequences

### Positive
- **Prevents cascade failures**: Dead server doesn't get hammered
- **Faster recovery**: Circuit closes immediately when service recovers
- **Clear visibility**: Dashboard shows circuit state per account
- **Configurable**: Threshold and cooldown adjustable per account

### Negative
- **State loss**: Cache reset (Redis restart) resets circuit state — acceptable since it's a safety mechanism, not a correctness requirement
- **False positives**: Transient hiccup could trip the circuit — mitigated by configurable threshold

## Alternatives Considered

1. **Simple retry with backoff**: Doesn't prevent thundering herd
2. **Global rate limiter**: Doesn't account for per-account health
3. **Fixed delay between retries**: Too conservative for transient failures
4. **External circuit breaker (Hystrix-style)**: Overkill for single-server setup

## References

- `openwa_bridge/utils.py` — `OpenWACircuitBreaker`
- `openwa_bridge/tasks.py:223-235` — circuit breaker check in outbox processor
