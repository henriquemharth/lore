# ADR-0005 — Add a GraphQL gateway for the web app

- Status: Rejected
- Date: 2026-01-15

## Context

The web team asked for a GraphQL layer to cut the number of round trips on the checkout page.

## Decision

Rejected. The checkout page now uses one aggregated REST endpoint
(`GET /checkout/summary`, in `api/checkout/routes.py`) instead. Revisit if more than three pages
need aggregation.
