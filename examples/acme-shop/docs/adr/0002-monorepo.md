# ADR-0002 — Keep API, workers and web in one repository

- Status: Accepted
- Date: 2025-03-11

## Context

Three deployables (`api/`, `workers/`, `web/`) share the order model and its validation rules.
Keeping them in separate repositories made every schema change a three-PR dance.

## Decision

One repository, one CI pipeline, one version number. Shared code lives in `packages/domain/`.

## Consequences

- A change to `packages/domain/orders.py` runs the tests of all three deployables.
- Release notes are written once, in `CHANGELOG.md`.
