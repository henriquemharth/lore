# ADR-0003 — Publish order events through a transactional outbox

- Status: Accepted
- Date: 2025-06-20

## Context

Emails and stock updates were sometimes sent for orders whose transaction later rolled back.

## Decision

The API writes order events to an `outbox` table in the same transaction as the order.
A worker (`workers/outbox_relay.py`) reads the outbox and publishes to the queue, at least once.
Consumers must be idempotent and key on `event_id`.

## Consequences

- No more emails for orders that do not exist.
- Consumers see duplicates; see the known issue about double stock reservations.
