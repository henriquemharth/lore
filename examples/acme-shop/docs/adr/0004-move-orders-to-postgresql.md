# ADR-0004 — Move orders to PostgreSQL 16

- Status: Accepted
- Date: 2025-11-02

## Context

Order search filters on JSON metadata and date ranges are slow on MySQL, and the reporting
team wants partitioned tables by month.

## Decision

This decision supersedes ADR-0001. Orders, order items and the outbox (ADR-0003) move to
PostgreSQL 16 with monthly partitions on `orders.created_at`. Catalog tables stay on MySQL
until a later decision.

The migration is `9f3c2a7b1d40` (`migrations/versions/9f3c2a7b1d40_orders_to_pg.py`), released in
commit `4be71c2`.

## Consequences

- Two databases for a while; `api/db.py` routes by table.
- Monthly partitions must be created ahead of time — see the deploy runbook.
