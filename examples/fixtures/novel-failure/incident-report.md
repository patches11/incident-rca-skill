# Incident report: inventory-svc workers OOM-killed by unbounded bulk-quote memo cache → checkout-api 502 bursts

**Status:** Root cause identified · **Confidence:** high
**Paged service:** checkout-api · **Onset (T0):** 2026-10-19 12:05 UTC (first 5xx burst); memory acceleration from ~11:50 UTC · **Investigated at:** 2026-10-19 13:40 UTC
**Owner of root-cause service:** catalog / @catalog-oncall (inventory-svc)

## Summary
checkout-api is returning 502s in 1–2 minute bursts because its dependency inventory-svc keeps losing gunicorn workers to OOM kills. Each burst lines up exactly with one `[gunicorn] Worker ... was sent SIGKILL! Perhaps out of memory?` event, and connections in flight on that worker are reset. inventory-svc memory climbs without ever being released until it nears the 1024 MB limit. Deploy inv-1890 (commit e4b7a20, 2026-10-16) added a module-level `_quote_cache` dict to `bulk_quote()` with no size limit and no eviction. The growth rate tracks `/quote/bulk` traffic from `partner-bulk`, which rose about 10x at ~11:50 UTC. **Do now:** roll back inv-1890 or turn off the memoization. Until that is done, each worker will OOM again about every 15–25 minutes.

## Impact
- checkout-api error_rate rose from a ~0.2–0.4% baseline to 0.1419–0.2619 during six bursts: 12:05, 12:19, 12:39, 12:46, 13:13 and 13:27 (q005). This burned 3.2x of the 1h error budget, per the page.
- checkout-api p99 hit 1197.5 ms at 13:13, against the 800 ms SLO (q006).
- All burst errors are `upstream inventory-svc connection reset by peer` with status 502. That is 199 errors in 2h (q011).

## Timeline (UTC)
| Time | Event | Evidence |
|---|---|---|
| 2026-10-16 14:40 | inv-1890 deploy, commit e4b7a20: "Memoize bulk quotes for partner API (CAT-902)", adds unbounded `_quote_cache` | q023 |
| 2026-10-19 07:40 | cfg-8812: inventory-svc LOG_SAMPLING_RATE 0.01 -> 0.02 (benign, see hypotheses) | q008 |
| 07:40–07:55 | inventory-svc RSS flat at ~500 MB (502.6 → 500.5) | q016 |
| ~08:00 | First partner-bulk `/quote/bulk` calls appear; RSS starts slow growth (~1.2 MB/min) | q022, q016 |
| 11:10 | cfg-8840: payments-svc card-gateway connect timeout 1500ms -> 1200ms (unrelated, see hypotheses) | q008 |
| ~11:40–11:50 | partner-bulk log volume rises from ~3 to 24–32 per 30-min bucket; RSS slope steepens to ~15 MB/min (787.3 at 11:49 → 1010.7 at 12:04) | q021, q013 |
| 12:05 | First worker SIGKILL (pid:59); restarts 0 → 1; checkout-api 502 burst 0.1419 | q014, q012, q005 |
| 12:19, 12:39, 12:46, 13:13, 13:27 | Repeated SIGKILLs, each followed by a checkout 502 burst; restarts reach 6.0 | q014, q012, q005 |
| 13:31 | Page fires | — |

## Root cause
1. **Trigger (code):** deploy inv-1890, commit `e4b7a20` (q023), changed `sandbox/services/inventory_svc/pricing.py`:
   ```diff
   +_quote_cache: dict[tuple, Quote] = {}
   ...
   +    key = (client_id, tuple(sorted((i.sku, i.qty) for i in items)))
   +    if key not in _quote_cache:
   +        _quote_cache[key] = _compute_quote(items)
   +    return _quote_cache[key]
   ```
   The cache is a process-global dict that only ever grows. It has no maxsize, TTL or eviction. Each distinct bulk request keeps its 168–400-item key tuple and `Quote` in memory for the life of the worker.
2. **Load:** partner-bulk `/quote/bulk` traffic began at ~08:00 and stepped up about 10x at ~11:40–11:50 (q021, q022). Over that period inventory-svc cache_hit_ratio (~0.93) and db_queries_per_request (~2.1) stayed flat (q018, q019). request_rate rose only gradually, 27.5 → 47.6 (q017).
3. **Resource:** inventory-svc memory_rss_mb was flat at ~500 MB until bulk traffic started. It then grew slowly, and from 11:50 it grew about 12x faster. Each time it reached 1010.7–1020.0 MB it fell back sharply (q013, q016). memory_limit_mb is 1024.0 throughout (q015).
4. **Symptom in inventory-svc:** at each peak a gunicorn worker is sent SIGKILL ("Perhaps out of memory?"). The kills are at 12:05:30, 12:19:30, 12:39:30, 12:46:30, 13:13:00 and 13:27:30 (q014). container_restarts climbs 0 → 6.0 at the same times (q012), and error_rate spikes to as much as 0.2876 (q010).
5. **Symptom in checkout-api:** in-flight `/reserve` calls on the killed worker fail with `upstream inventory-svc connection reset by peer` and return 502 (q011). That produces the 1–2 minute 5xx bursts in the same minutes (q005).

The minutes match exactly at every step, and nothing changes between bursts. That explains the "short bursts" shape of the page: a sawtooth leak with a kill at each peak.

## Hypotheses considered
| Hypothesis | Test (query that could disprove it) | Result | Status |
|---|---|---|---|
| inventory-svc memory leak from unbounded `_quote_cache` (inv-1890), driven by partner bulk traffic | Memory sawtooth vs limit, OOM kills aligned with bursts, bulk volume vs memory slope (q013, q014, q015, q016, q021, q023) | Every link aligned; growth starts with bulk traffic and steepens with it | **survived** |
| payments-svc failure (or its cfg-8840 timeout change) | payments-svc error_rate during bursts (q009) | Max 0.0028, flat during every burst | refuted |
| checkout-api traffic surge | checkout-api request_rate (q007) | 17.6–24.8 rps, no spike at bursts | refuted |
| redis/cache failure or stampede in inventory-svc | cache_hit_ratio and db_queries_per_request (q018, q019) | Flat at ~0.93 and ~2.1; only scattered redis 50ms timeout warnings, not aligned with bursts (q014) | refuted |
| cfg-8812 LOG_SAMPLING_RATE change causing memory growth | RSS right after 07:40 (q016) | Flat 502.6 → 500.5 until ~08:00; growth tracks bulk traffic instead | refuted |

## Mitigation (proposed — a human must execute)
1. **Roll back inventory-svc to the version before inv-1890 (2026.10.16-1 / e4b7a20), or ship a hotfix that removes the memoization.** Why: the diff adds unbounded process-global retention, and OOM kills line up with every checkout 502 burst (q014, q005, q023). Risk: low. The memo only saves recompute for repeated partner quotes, so partner `/quote/bulk` latency may go up a little (it is ~300–850 ms today, q021). Confirm by: inventory-svc memory_rss_mb levels off well under 1024 MB instead of climbing, container_restarts stops rising, and checkout-api error_rate stays at the ~0.3% baseline through at least one 25-minute cycle.
2. **Stopgap if a rollback can't happen right away: rate-limit or throttle `client=partner-bulk` on `/quote/bulk`.** Why: the memory growth rate tracks bulk volume (q021, q013). Risk: affects a partner integration, so coordinate with the partner owner. Confirm by: memory slope returns to the pre-11:50 rate of ~1.2 MB/min.
3. **Do not raise the memory limit as the fix.** A higher limit only spaces the kills further apart, because the cache never frees anything.

## Fix
No code was generated. The inventory-svc repo (`sandbox/services/inventory_svc`) is not in this workspace, and the catalog has no `test_command` for it. Proposed fix for @catalog-oncall: replace the module-level dict with a bounded cache, e.g. `cachetools.TTLCache(maxsize=N, ttl=...)` or an LRU with a byte budget, or move memoization to redis with a TTL. Also add a regression test asserting cache size stays at or below maxsize after N distinct `bulk_quote` calls. Before re-enabling, check CAT-902's premise that partners repeat identical quotes. If most keys are unique, the memo gives no benefit at all.

## Missing data / blind spots
- No heap profile or per-object memory breakdown for inventory-svc, so attributing RSS to `_quote_cache` is inferred from the diff and dose-response. To confirm: run tracemalloc / py-spy dump on a live worker, or check `len(_quote_cache)`.
- Logs are sampled. Bulk-request counts (q021) are relative indicators, not absolute request rates. A per-route request_rate metric would help.
- memory_rss_mb looks aggregated across pods (7c9fa/b/c). Per-pod series would show which worker is next to be killed.
- Why partner-bulk volume rose ~10x at ~11:50 is unknown. Ask the partner / API-gateway owner.

## Follow-ups
- Alert on inventory-svc `container_restarts` increase and on memory_rss_mb > 90% of memory_limit_mb. This was visible about 1.5h before the page.
- Runbook `checkout-latency`: add "502 connection reset by peer → check the upstream for OOM kills / container_restarts".
- Add per-route / per-client request_rate metrics for inventory-svc.
- Proposed overlay entry (for human review): *inventory-svc: steady RSS climb plus gunicorn "SIGKILL! Perhaps out of memory?" is not benign. It shows up in checkout-api as 502 "connection reset by peer" bursts.*

## Verification
`verify_report`: **18/18 claims verified** (c1–c18 PASS).

## Machine-readable summary
```yaml
verdict: root_cause
confidence: high
root_cause:
  service: inventory-svc
  category: code_regression
  change_id: inv-1890
  commit: e4b7a20
  summary: Deploy inv-1890 added an unbounded module-level _quote_cache to bulk_quote(), so inventory-svc memory grows with partner-bulk /quote/bulk traffic until gunicorn workers are OOM-killed at the 1024 MB limit, resetting in-flight connections and causing checkout-api 502 bursts.
fix_proposed: false
missing_data: []
claims:
  - id: c1
    kind: observation
    text: checkout-api error_rate spiked in short bursts, peaking at 0.2619 at 13:13, with bursts of 0.1419 at 12:05 and 0.1544 at 13:27, against a baseline around 0.003
    evidence: [q005]
    values: [0.2619, "2026-10-19T13:13:00Z", 0.1419, "2026-10-19T12:05:00Z", 0.1544, "2026-10-19T13:27:00Z"]
  - id: c2
    kind: observation
    text: all checkout-api error logs are "upstream inventory-svc connection reset by peer" with status 502, 199 matches
    evidence: [q011]
    values: ["upstream inventory-svc connection reset by peer", 502, 199]
  - id: c3
    kind: observation
    text: inventory-svc error_rate spiked in the same minutes as checkout-api, max 0.2876 at 13:13, 0.1552 at 12:05
    evidence: [q010]
    values: [0.2876, "2026-10-19T13:13:00Z", 0.1552, "2026-10-19T12:05:00Z"]
  - id: c4
    kind: observation
    text: inventory-svc gunicorn workers were sent SIGKILL (possible OOM) at 12:05:30, 12:19:30, 12:39:30, 12:46:30, 13:13:00 and 13:27:30
    evidence: [q014]
    values: ["[gunicorn] Worker (pid:<n>) was sent SIGKILL! Perhaps out of memory?", "2026-10-19T12:05:30.000Z", "2026-10-19T12:19:30.000Z", "2026-10-19T12:39:30.000Z", "2026-10-19T12:46:30.000Z", "2026-10-19T13:13:00.000Z", "2026-10-19T13:27:30.000Z"]
  - id: c5
    kind: observation
    text: inventory-svc container_restarts rose from 0.0 to 6.0 between 12:05 and 13:28
    evidence: [q012]
    values: [0.0, 6.0, "2026-10-19T13:28:00Z"]
  - id: c6
    kind: observation
    text: inventory-svc memory_rss_mb climbed to 1010.7 at 12:04 and 1020.0 at 13:12 before dropping, against a constant memory_limit_mb of 1024.0
    evidence: [q013, q015]
    values: [1010.7, "2026-10-19T12:04:00Z", 1020.0, "2026-10-19T13:12:00Z", 1024.0]
  - id: c7
    kind: observation
    text: inventory-svc memory was flat around 500 MB from 07:40 to 07:55 (502.6 to 500.5) and then grew steadily to 785.7 by 11:45
    evidence: [q016]
    values: [502.6, 500.5, 785.7, "2026-10-19T11:45:00Z"]
  - id: c8
    kind: observation
    text: memory growth accelerated from 787.3 at 11:49 to 1010.7 at 12:04
    evidence: [q013]
    values: [787.3, "2026-10-19T11:49:00Z", 1010.7, "2026-10-19T12:04:00Z"]
  - id: c9
    kind: observation
    text: partner-bulk /quote/bulk log volume rose from 1-5 per 30-min bucket before 11:40 to 24-32 per bucket after, with requests of 168 to 395 items
    evidence: [q021]
    values: ["partner-bulk", "bulk quote ok", 24, 32, 29, 395]
  - id: c10
    kind: observation
    text: no /quote/bulk requests were logged between 07:40 and 08:00; the first appears at 08:01:11
    evidence: [q022]
    values: ["2026-10-19T08:01:11.463Z", 316]
  - id: c11
    kind: observation
    text: deploy inv-1890 (commit e4b7a20) on 2026-10-16 added an unbounded module-level _quote_cache dict memoizing bulk_quote results
    evidence: [q023]
    values: ["inv-1890", "e4b7a20", "2026-10-16T14:40:00Z", "_quote_cache: dict[tuple, Quote] = {}", "Memoize bulk quotes for partner API (CAT-902)"]
  - id: c12
    kind: observation
    text: payments-svc error_rate stayed at most 0.0028 throughout, including during bursts
    evidence: [q009]
    values: [0.0028]
  - id: c13
    kind: observation
    text: checkout-api request_rate stayed between 17.6 and 24.8 with no spikes at bursts
    evidence: [q007]
    values: [17.6, 24.8]
  - id: c14
    kind: observation
    text: inventory-svc cache_hit_ratio stayed between 0.9242 and 0.9371 and db_queries_per_request between 2.0452 and 2.1445
    evidence: [q018, q019]
    values: [0.9242, 0.9371, 2.0452, 2.1445]
  - id: c15
    kind: observation
    text: checkout-api p99 latency peaked at 1197.5 ms at 13:13
    evidence: [q006]
    values: [1197.5, "2026-10-19T13:13:00Z"]
  - id: c16
    kind: inference
    text: inventory-svc workers are OOM-killed when RSS reaches the 1024 MB limit, and each kill resets in-flight connections, producing the checkout-api 502 bursts
    based_on: [c1, c2, c3, c4, c5, c6]
  - id: c17
    kind: inference
    text: the memory growth is driven by partner-bulk /quote/bulk traffic retained in the unbounded _quote_cache from inv-1890, since growth starts with the first bulk requests and accelerates with bulk volume
    based_on: [c7, c8, c9, c10, c11]
  - id: c18
    kind: inference
    text: payments-svc, checkout traffic and redis/cache failure are not the cause
    based_on: [c12, c13, c14]
```
