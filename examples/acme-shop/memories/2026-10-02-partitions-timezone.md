---
type: feedback
created: 2026-10-02
verified: false
evidence:
  - docs/runbooks/deploy.md (Last incidents)
  - docs/known-issues.md (Order dates in reports)
---

# Partition checks must use the shop's local time, not UTC

When creating or checking monthly order partitions, compute "next month" in the shop's local
time zone. The incident after release `4be71c2` happened because the script used UTC.

**Why:** an order at 23:58 local time on the last day of the month landed in a partition that did
not exist yet.

**How to apply:** any new script touching `orders.created_at` partitions should take the zone from
`SHOP_TIMEZONE`, as `scripts/partitions.py` now does.
