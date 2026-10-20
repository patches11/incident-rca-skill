# Database lock contention

**Mechanism.** Long transactions, migrations or hot rows make queries wait on locks; latency
and connection hold time rise even though the DB CPU is not saturated.

**Signature**
- `lock_wait_ms` and `slow_queries_rate` up; DB CPU not pinned.
- Errors: `deadlock detected`, `lock timeout`, `canceling statement due to lock timeout`.
- Often aligned with a migration, backfill or batch job in `recent_changes`.

**Confirm**
- DB `lock_wait_ms` over the window; logs mentioning lock/deadlock.

**Refute**
- Lock wait flat while symptoms occur.
