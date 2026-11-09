# Runbook — Deploy

## Before every deploy

1. CI green on `main`.
2. Check that next month's partitions exist (ADR-0004):
   `python scripts/partitions.py --check` must print `ok`.
3. If the release has a migration, run it first: `alembic upgrade head`.

## Deploy

`scripts/deploy.sh production` builds the images, runs the migrations and restarts API and
workers one at a time.

## Rollback

`scripts/deploy.sh rollback` goes back to the previous image. Migrations are **not** rolled back
automatically; write a forward fix instead.

## Last incidents

- Release `4be71c2` (orders to PostgreSQL) needed a manual partition for the current month:
  the partition script ran in UTC and the first order arrived at 23:58 local time.
