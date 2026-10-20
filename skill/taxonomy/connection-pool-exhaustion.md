# Connection pool exhaustion

**Mechanism.** A client-side pool (DB, HTTP) has a fixed size. If connections are held longer
(slow queries, leaks on error paths, long transactions) or the pool is shrunk, requests queue
for a connection, time out, and fail — while the server behind the pool is often *idle*.

**Signature**
- `db_pool_in_use` pinned at `db_pool_max`; `db_pool_wait_ms` rising toward the acquire timeout.
- Errors like `QueuePool limit ... reached, connection timed out`, `pool timeout`, `too many clients`.
- Latency floor near the acquire timeout (e.g. ~1s) in traces (`db.pool.acquire` spans).
- Database `active_connections` ≈ pool size × instances, far below `max_connections`; DB CPU flat or *down*.

**Confirm**
- `query_metrics(<svc>, db_pool_in_use)` vs `db_pool_max` over [T0-30m, now].
- `search_logs(<svc>, "level=error pool")`.
- `get_traces(<svc>, filter="status=error")` → time spent in acquire.

**Refute**
- Pool below max during the symptom → not exhaustion.
- DB CPU/locks saturated → the DB is slow and the pool is a victim (see db-lock-contention).

**Distinguish the cause of exhaustion**
- *Leak:* in-use climbs and never drops even when traffic drops; warnings about connections
  garbage-collected / not returned; usually follows a deploy touching session handling.
- *Slow holders:* in-use tracks latency of queries; slow-query rate up.
- *Pool shrunk:* `db_pool_max` itself changed; config change in `recent_changes`.
- *More callers:* request rate up (see capacity-traffic-surge).
