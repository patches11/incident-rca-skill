# Incident report: checkout-api 502 bursts caused by inventory-svc workers OOM-killed (unbounded bulk-quote memo cache)

**Status:** Root cause identified · **Confidence:** medium
**Paged service:** checkout-api · **Onset (T0):** 2026-10-19 12:05 UTC · **Investigated at:** 2026-10-19 13:40 UTC
**Owner of root-cause service:** catalog / @catalog-oncall (inventory-svc)

## Summary
checkout-api is returning 502s in 1–2 minute bursts because its dependency inventory-svc keeps having gunicorn workers SIGKILLed for running out of memory. When a worker dies, the connections it held from checkout-api are reset. inventory-svc RSS climbs steadily to about 1010–1020 MB against a 1024 MB limit, drops when a worker is killed, then climbs again. That is accumulation, not load: request rate is roughly flat. Deploy inv-1890 (commit e4b7a20, 2026-10-16) added an unbounded, process-global `_quote_cache` dict keyed per partner request. Growth sped up at about 11:50 when `/quote/bulk` traffic from `partner-bulk` rose. Do now: have @catalog-oncall cap or disable the memo cache (roll back inv-1890 or hotfix). Rolling restarts of inventory-svc only buy time.

## Impact
- checkout-api error_rate rose from a ~0.002–0.004 baseline to bursts of 0.1419–0.2619 at 12:05, 12:19, 12:39, 12:46, 13:13 and 13:27 (q005).
- checkout-api p99 reached 1197.5 ms at 13:13, above the 800 ms latency SLO, during bursts (q006).
- 199 checkout-api error logs, all `upstream inventory-svc connection reset by peer` (status 502) (q011).

## Timeline (UTC)
| Time | Event | Evidence |
|---|---|---|
| 2026-10-16 14:40 | inventory-svc deploy inv-1890 (e4b7a20) adds unbounded `_quote_cache` | q015 |
| 10-19 07:40–11:40 | inventory-svc RSS climbs steadily, 502.9 → 776.4 MB, at steady load | q019 |
| ~11:40–11:50 | `/quote/bulk` log volume rises (3 → 24 per 30 min bucket, sampled); RSS growth accelerates | q020, q013 |
| 12:04–12:05 | RSS 1010.7 MB (limit 1024); first worker SIGKILL; checkout 5xx 0.1419 | q013, q016, q014, q005 |
| 12:19, 12:39, 12:46, 13:13, 13:27 | Repeat cycle: RSS ~1010–1020 MB → SIGKILL → restarts counter +1 → checkout 502 burst | q012, q013, q014, q005 |
| 13:31 | Page fired (1h burn 3.2x) | page |

## Root cause
1. **Code regression (vulnerability):** deploy `inv-1890` / commit `e4b7a20` (2026-10-16T14:40Z, CAT-902) added a module-level dict with no size bound, TTL or eviction (q015):
   ```python
   +_quote_cache: dict[tuple, Quote] = {}
   ...
   +    key = (client_id, tuple(sorted((i.sku, i.qty) for i in items)))
   +    if key not in _quote_cache:
   +        _quote_cache[key] = _compute_quote(items)
   ```
   Every distinct (client, basket) gets cached forever in each worker process.
2. **Accumulation:** inventory-svc RSS grew steadily from 502.9 MB (07:40) to 776.4 MB (11:40) (q019) while request rate stayed at 41.2–49.1 rps (q017). It never falls except when a worker is killed (q013).
3. **Trigger:** `partner-bulk` `/quote/bulk` requests (159–395 items each) rose about 8–10x in sampled logs starting in the 11:40 bucket (q020). RSS growth went from ~1.2 MB/min to ~14 MB/min (q013).
4. **Exhaustion:** RSS peaks at 1010.7, 1013.2, 1013.5, 1007.8, 1020.0 and 1011.3 MB against a 1024 MB `memory_limit_mb` (q013, q016). Each peak is followed by `[gunicorn] Worker (pid:<n>) was sent SIGKILL! Perhaps out of memory?` (q014). `container_restarts` goes from 0 to 6 in the same minutes (q012).
5. **Symptom in suspect and paged service:** inventory-svc error_rate spikes in those minutes (q009). checkout-api gets `connection reset by peer` from inventory-svc and returns 502 (q011, q005).

Why medium, not high: there is no heap profile, so the claim that `_quote_cache` is what fills the heap is inferred. It rests on the diff, the accumulation shape, and the growth rate tracking bulk-quote volume. It was not measured directly.

## Hypotheses considered
| Hypothesis | Test (query that could disprove it) | Result | Status |
|---|---|---|---|
| inventory-svc memory accumulation → OOM kills (code: unbounded cache inv-1890) | Kill times should match bursts; RSS should be near the limit and climbing at flat load (q012, q013, q014, q016, q017, q019) | SIGKILLs at each burst; RSS 1007–1020 of 1024 MB; growth at flat rps | survived |
| payments-svc degradation (incl. cfg-8840 timeout change at 11:10) | payments-svc error_rate should spike with the bursts (q008) | flat, max 0.0028 | refuted |
| checkout-api itself crashing / restarting | checkout-api container_restarts (q010) | 0 throughout | refuted |
| Traffic surge / capacity | request rates (q007, q017) | checkout 17.6–24.8 rps, inventory 41.2–49.1 rps, no step | refuted as cause (bulk-quote mix change is the trigger only) |
| redis-cache degradation | inventory warn/error logs (q014) | only 10 `redis GET timed out` warnings, scattered, not aligned with bursts | refuted as cause of bursts |
| Other recent changes (nw-2214 notification-worker; cfg-8812 log sampling) | recent_changes (q015); catalog says notification-worker is off-path (q002) | off-path / logging-only | refuted |

## Mitigation (proposed — a human must execute)
1. **Roll back inventory-svc to the version before inv-1890 (2026.10.16-1), or hotfix the cache to be bounded or disabled.** Owner: @catalog-oncall. Why: the diff adds unbounded per-process memory growth, and each OOM kill matches a checkout burst. Risk: partner bulk quotes get slower without memoization (bulk quotes already take ~338–811 ms). Confirm by: inventory-svc `memory_rss_mb` plateaus instead of ramping, `container_restarts` stops incrementing, and checkout-api `error_rate` stays near 0.003.
2. **Stopgap if rollback is delayed:** rolling restart of inventory-svc pods during a quiet minute, or temporarily rate-limiting `partner-bulk` on `/quote/bulk`. Why: either resets or slows the growth, which buys time before the next kill. Risk: a restart causes a small reset burst of its own and the problem comes back within ~15–25 min at current rates. The rate limit affects the partner. Confirm by: RSS slope in `memory_rss_mb`.
3. Do **not** just raise the memory limit. Growth is unbounded, so it only delays the next kill.

## Fix
No code was generated. Confidence is medium, and the inventory-svc repo (`sandbox/services/inventory_svc`) is not in this workspace. The catalog also lists no `test_command` for it.
Proposed fix (owner @catalog-oncall): replace the module-level `_quote_cache` dict with a bounded LRU/TTL cache (e.g. `cachetools.TTLCache(maxsize=..., ttl=...)`) or a shared cache in redis with a TTL. Add a regression test that issues N distinct bulk quotes and asserts the cache size stays at or under `maxsize`. Quote prices can change, so a TTL is also needed for correctness, not just memory.

## Missing data / blind spots
- No heap profile or per-object memory breakdown for inventory-svc. A `tracemalloc` / py-spy dump from one worker would directly confirm that `_quote_cache` dominates.
- No memory data from before the 2026-10-16 deploy. A 7-day `memory_rss_mb` view would show whether the ramp started with inv-1890.
- `/quote/bulk` volume comes from sampled logs (LOG_SAMPLING_RATE). A per-route request_rate metric would give exact volumes.
- `memory_rss_mb` looks aggregated across pods (7c9fa/b/c). Per-pod series would explain why RSS doesn't fully reset after a kill.

## Follow-ups
- Alerting: no alert fired on inventory-svc OOM kills or on RSS above 90% of the limit. Only the downstream checkout burn alert fired. Add one to inventory-svc.
- Runbook `checkout-latency` covers 504 timeouts but not 502 `connection reset by peer` bursts. Add: "check dependency container_restarts / OOM logs."
- Catalog: add a `test_command` for inventory-svc.
- Proposed overlay entry (for human review): "inventory-svc `redis GET timed out after 50ms, falling back to postgres` warnings appear at low volume (~10/2h) at baseline; not by themselves indicative of an incident."

## Verification
verify_report: c1–c14 all PASS — **14/14 claims verified**.

## Machine-readable summary
```yaml
verdict: root_cause
confidence: medium
root_cause:
  service: inventory-svc
  category: code_regression
  change_id: inv-1890
  commit: e4b7a20
  summary: An unbounded per-process memo cache (_quote_cache) added in inv-1890 grows with distinct partner bulk-quote requests until gunicorn workers hit the 1024 MB limit and are SIGKILLed, resetting checkout-api's connections and producing 502 bursts.
fix_proposed: false
missing_data:
  - heap profile of an inventory-svc worker to confirm _quote_cache dominates memory
  - inventory-svc memory_rss_mb from before 2026-10-16T14:40Z deploy
claims:
  - id: c1
    kind: observation
    text: checkout-api error_rate spiked in short bursts, max 0.2619 at 13:13, with bursts of 0.1419 at 12:05 and 0.1544 at 13:27, against a baseline near 0.002
    evidence: [q005]
    values: [0.2619, "2026-10-19T13:13:00Z", 0.1419, "2026-10-19T12:05:00Z", 0.1544, "2026-10-19T13:27:00Z"]
  - id: c2
    kind: observation
    text: all 199 checkout-api error logs are "upstream inventory-svc connection reset by peer" with status 502
    evidence: [q011]
    values: [199, "upstream inventory-svc connection reset by peer", 502]
  - id: c3
    kind: observation
    text: inventory-svc error_rate spiked in the same minutes, max 0.2876 at 13:13 and 0.1552 at 12:05
    evidence: [q009]
    values: [0.2876, "2026-10-19T13:13:00Z", 0.1552, "2026-10-19T12:05:00Z"]
  - id: c4
    kind: observation
    text: inventory-svc gunicorn workers were SIGKILLed with "Perhaps out of memory?" at 12:05:30, 12:19:30, 12:39:30, 12:46:30, 13:13:00 and 13:27:30
    evidence: [q014]
    values: ["Perhaps out of memory?", "2026-10-19T12:05:30.000Z", "2026-10-19T12:19:30.000Z", "2026-10-19T12:39:30.000Z", "2026-10-19T12:46:30.000Z", "2026-10-19T13:13:00.000Z", "2026-10-19T13:27:30.000Z"]
  - id: c5
    kind: observation
    text: inventory-svc container_restarts rose from 0 to 6 between 12:05 and 13:28
    evidence: [q012]
    values: [0.0, 6.0, "2026-10-19T13:28:00Z"]
  - id: c6
    kind: observation
    text: inventory-svc memory_rss_mb peaked at 1010.7 at 12:04 and 1020.0 at 13:12 before dropping, against a memory_limit_mb of 1024
    evidence: [q013, q016]
    values: [1010.7, "2026-10-19T12:04:00Z", 1020.0, "2026-10-19T13:12:00Z", 1024.0]
  - id: c7
    kind: observation
    text: inventory-svc memory_rss_mb climbed steadily from 502.9 at 07:40 to 776.4 at 11:40
    evidence: [q019]
    values: [502.9, "2026-10-19T07:40:00Z", 776.4, "2026-10-19T11:40:00Z"]
  - id: c8
    kind: observation
    text: inventory-svc request_rate stayed between 41.2 and 49.1 rps
    evidence: [q017]
    values: [41.2, 49.1]
  - id: c9
    kind: observation
    text: deploy inv-1890 (commit e4b7a20) on 2026-10-16T14:40 added an unbounded module-level _quote_cache dict memoizing bulk_quote per client and basket
    evidence: [q015]
    values: ["inv-1890", "e4b7a20", "2026-10-16T14:40:00Z", "_quote_cache"]
  - id: c10
    kind: observation
    text: sampled /quote/bulk logs from partner-bulk rose from 3 per 30-minute bucket at 11:10 to 24 at 11:40 and 32 at 12:10
    evidence: [q020]
    values: ["partner-bulk", 3, 24, 32, "2026-10-19T11:40:00Z"]
  - id: c11
    kind: observation
    text: payments-svc error_rate stayed flat with max 0.0028, and checkout-api container_restarts stayed at 0
    evidence: [q008, q010]
    values: [0.0028, 0.0]
  - id: c12
    kind: inference
    text: inventory-svc memory is accumulating rather than tracking load, so workers hit the memory limit and are OOM-killed
    based_on: [c4, c5, c6, c7, c8]
  - id: c13
    kind: inference
    text: the unbounded _quote_cache from inv-1890 is the accumulating memory, and the rise in partner-bulk quote traffic accelerated its growth
    based_on: [c7, c9, c10, c12]
  - id: c14
    kind: inference
    text: each worker OOM kill resets checkout-api's connections to inventory-svc, producing the checkout-api 502 bursts; payments-svc and checkout-api itself are not the cause
    based_on: [c1, c2, c3, c4, c11, c12]
```
