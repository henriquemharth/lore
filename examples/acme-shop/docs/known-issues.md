# Known issues

## Double stock reservations

The stock consumer (`workers/stock.py`) sometimes reserves twice for the same order when the
outbox relay retries (ADR-0003 is at-least-once). Workaround: the nightly job
`workers/stock_reconcile.py` releases duplicates. Real fix: key reservations on `event_id`.

## Order dates in reports

Orders are stored in UTC. The finance report groups by local date in `reports/daily.py`; anything
else that groups by `created_at::date` will be off by one day near midnight.
