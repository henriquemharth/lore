# ADR-0001 — Use MySQL 8 for the orders database

- Status: Superseded by ADR-0004
- Date: 2025-03-04

## Context

The first version of Acme Shop needs one relational database for products, carts and orders.
The team already operates MySQL for the old storefront.

## Decision

Use MySQL 8 (InnoDB) for every table of the shop API, including `orders` and `order_items`.

## Consequences

- One database to back up and monitor.
- JSON queries on `orders.metadata` are awkward; we accept that for now.
