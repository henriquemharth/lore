# Architecture

Acme Shop is a small e-commerce backend: a REST API, background workers and a web storefront,
all in one repository (ADR-0002).

## Components

| Part | Path | What it does |
|---|---|---|
| API | `api/` | REST endpoints for catalog, cart, checkout and orders |
| Workers | `workers/` | Outbox relay, emails, stock reservations |
| Web | `web/` | Storefront (server-rendered) |
| Domain | `packages/domain/` | Order model and validation shared by all three |

## Data

- Orders, order items and the outbox: PostgreSQL 16, partitioned by month (ADR-0004).
- Catalog and customers: MySQL 8 (the rest of ADR-0001 that still applies until a new decision).
- Events: transactional outbox → queue, at least once (ADR-0003).

## Money

Prices are integers in cents everywhere (`packages/domain/money.py`). Never use floats for money.
Taxes are computed at checkout and stored on the order; reports must not recompute them.
