# Incident report: checkout-api p99 latency SLO burn caused by N+1 queries in inventory-svc deploy inv-1931

**Status:** Root cause identified · **Confidence:** high
**Paged service:** checkout-api · **Onset (T0):** 2026-10-15 14:53 UTC · **Investigated at:** 2026-10-15 15:20 UTC
**Owner of root-cause service:** catalog team / @catalog-oncall (inventory-svc)

## Summary
checkout-api p99 latency jumped from ~320ms to ~1000ms at 14:53 UTC with flat traffic. The cause is
inventory-svc deploy `inv-1931` (commit `c9d3f17`, 14:52 UTC, CAT-877), which replaced a single
batched `SELECT ... WHERE sku IN (...)` with two queries per cart item inside a loop (an N+1 pattern).
inventory-svc DB queries per request went from ~2.1 to ~14, its /reserve p99 went from ~95ms to
~1150ms, and checkout-api inherits that latency. payments-svc is unaffected. **Recommended action:
roll back inventory-svc to the pre-`inv-1931` version** (a human must do this).

## Impact
- checkout-api p99 latency up to 1060.0ms (max at 15:19), vs ~300–350ms baseline; latency SLO is 99% < 800ms (q005).
- checkout-api error rate stays below 1% (max 0.0066), so this is latency-only (q006).
- Request rate is flat at ~21–22 rps, so traffic did not change (q007).

## Timeline (UTC)
| Time | Event | Evidence |
|---|---|---|
| 14:40 | payments-svc deploy pay-5540 (httpx bump); no latency change follows | q008, q010 |
| 14:52 | inventory-svc deploy inv-1931, commit c9d3f17 (CAT-877) | q008 |
| 14:52–14:55 | inventory-svc cache_hit_ratio dips to 0.3211 (expected post-deploy warm-up), recovers by 14:56 | q014 |
| 14:53 | inventory-svc db_queries_per_request 2.0194 → 4.9947 → 13.4 at 14:54 | q011 |
| 14:53 | inventory-svc p99 473.1ms → 1231.5ms at 14:54 | q009 |
| 14:53 | checkout-api p99 521.9ms → 993.3ms at 14:54 (T0) | q005 |
| 14:54 | postgres-inventory active connections 9.0 → 22.8 | q017 |
| 15:11 | Page fires; latency still elevated through 15:20 | q005 |

## Root cause
Change `inv-1931` (commit `c9d3f17`, author maria.ortiz) changed inventory-svc `reserve()` (q008):

    -    rows = session.execute(
    -        select(stock).where(stock.c.sku.in_([i.sku for i in req.items]))
    -    ).all()
    +    for item in req.items:
    +        row = session.execute(select(stock).where(stock.c.sku == item.sku)).one()
    +        row.warehouses = session.execute(
    +            select(warehouse_stock).where(warehouse_stock.c.sku == item.sku)
    +        ).all()

One batched query became 2 queries per item. Mechanism, end to end:
1. **Trigger:** deploy at 14:52 (q008).
2. **More work per request:** inventory-svc `db_queries_per_request` stepped from ~2.1 to 13–15 (q011).
   Traces show the per-item `SELECT stock WHERE sku = $1` / `SELECT warehouse_stock WHERE sku = $1`
   pairs, e.g. trace fb1f81e4fac2865d with `items: 9`, `db.query_count: 18` (q013). Logs show
   query count scaling with cart size (`items: 9, db_queries: 19, duration_ms: 1124`) (q016).
3. **DB load:** postgres-inventory active connections rose from ~8 to ~22 (q017); inventory-svc pool wait rose from <0.5ms to up to 10.3ms (q015).
4. **Symptom in suspect:** inventory-svc /reserve p99 from ~95ms to ~1150ms; 811 new "slow request: POST /reserve" warnings that did not appear before 14:50 (q009, q016).
5. **Symptom in paged service:** checkout-api calls inventory-svc /reserve synchronously (catalog q002; runbook q004), so checkout p99 rose to ~1000ms (q005).

## Hypotheses considered
| Hypothesis | Test (query that could disprove it) | Result | Status |
|---|---|---|---|
| inventory-svc deploy inv-1931 introduced N+1 queries | Before/after db_queries_per_request and p99 at 14:52 (q011, q009); traces (q013) | Queries/request 2.0194 → 13.4; p99 139.8 → 1231.5ms; traces show 2 queries per item | **survived** |
| payments-svc deploy pay-5540 (httpx bump) | payments-svc p99 across 14:40 and T0 (q010) | Flat 212.9–279.4ms throughout | refuted |
| inventory-svc post-deploy cache warm-up (overlay) | cache_hit_ratio duration (q014) | Recovered to 0.9361 by 14:56; latency stayed high to 15:20 | refuted |
| Traffic surge | checkout-api request_rate (q007) | Flat 19.0–23.9 rps | refuted |
| inventory-svc config cfg-8812 (log sampling) | Timing (q008) | Applied 09:20, ~5.5h before T0 | refuted |

## Mitigation (proposed — a human must execute)
1. **Roll back inventory-svc to the version before `inv-1931` (`2026.10.15-1` / `c9d3f17`)** — why: onset matches the deploy to the minute, and the diff explains the extra queries. Risk: low; it removes the CAT-877 per-warehouse availability field from /reserve responses (check that no storefront client already depends on it). Confirm by: inventory-svc `db_queries_per_request` back to ~2, inventory-svc p99 < 150ms, checkout-api p99 back to ~300–350ms within a few minutes. Expect a ~3 min cache warm-up blip after the rollback (normal per the org overlay).
2. Do **not** roll back payments-svc pay-5540; there is no evidence against it.

## Fix
No code was generated: the inventory-svc repo (`sandbox/services/inventory_svc`) is not in this
workspace, and the catalog has no `test_command` for it. Proposed fix for @catalog-oncall: keep the
CAT-877 feature but batch it. Fetch `stock` with a single `WHERE sku IN (...)` as before, plus one
`SELECT warehouse_stock WHERE sku IN (...)` grouped by sku, so each request makes 2 queries no
matter how many items are in the cart. Add a test asserting the query count stays constant as item count grows.

## Missing data / blind spots
- checkout-api trace sampling caught only 27 traces after 14:55 (p99 470.8ms), and none ≥800ms (q012, q013). The slowest requests were not traced, so the per-span breakdown of the worst requests is inferred from inventory-svc metrics and logs, not observed directly.
- postgres-inventory CPU/slow-query metrics were not queried. They would show how much of the per-query latency comes from DB contention.

## Follow-ups
- Add a CI or pre-deploy check on query count per request for inventory-svc /reserve.
- Alert on inventory-svc `db_queries_per_request` step changes and on the /reserve latency SLO (300ms). inventory-svc breached its own SLO about 18 minutes before checkout paged.
- Increase trace sampling for slow requests (tail-based sampling) in checkout-api.
- Proposed overlay entry (for human review): "inventory-svc: if p99 stays elevated > 10 min after a deploy while cache_hit_ratio has recovered, it is not cache warm-up. Check db_queries_per_request."

## Verification
`verify_report`: **13/13 claims verified** (c1–c13 all PASS).

## Machine-readable summary
```yaml
verdict: root_cause
confidence: high
root_cause:
  service: inventory-svc
  category: code_regression
  change_id: inv-1931
  commit: c9d3f17
  summary: inventory-svc deploy inv-1931 replaced a batched stock query with two queries per cart item (N+1), raising /reserve latency, which checkout-api inherits.
fix_proposed: false
missing_data: []
claims:
  - id: c1
    kind: observation
    text: checkout-api p99 latency stepped from 331.5ms at 14:52 to 521.9ms at 14:53 and 993.3ms at 14:54, peaking at 1060.0ms
    evidence: [q005]
    values: [331.5, 521.9, 993.3, 1060.0]
  - id: c2
    kind: observation
    text: checkout-api request rate was flat between 19.0 and 23.9 rps
    evidence: [q007]
    values: [19.0, 23.9]
  - id: c3
    kind: observation
    text: inventory-svc deploy inv-1931 commit c9d3f17 at 2026-10-15T14:52:00Z replaced the batched stock select with per-item queries in a loop
    evidence: [q008]
    values: ["inv-1931", "c9d3f17", "2026-10-15T14:52:00Z"]
  - id: c4
    kind: observation
    text: inventory-svc db_queries_per_request rose from 2.0194 at 14:52 to 4.9947 at 14:53 and 13.4 at 14:54
    evidence: [q011]
    values: [2.0194, 4.9947, 13.4]
  - id: c5
    kind: observation
    text: inventory-svc p99 latency rose from 139.8ms at 14:52 to 473.1ms at 14:53 and 1231.5ms at 14:54
    evidence: [q009]
    values: [139.8, 473.1, 1231.5]
  - id: c6
    kind: observation
    text: checkout traces show inventory-svc /reserve issuing per-item SELECT stock WHERE sku = $1 and SELECT warehouse_stock WHERE sku = $1, with items 9 and db.query_count 18
    evidence: [q013]
    values: ["SELECT stock WHERE sku = $1", "SELECT warehouse_stock WHERE sku = $1", 18]
  - id: c7
    kind: observation
    text: 'inventory-svc logged 811 "slow request: POST /reserve took <n>ms" warnings, with db_queries scaling with items'
    evidence: [q016]
    values: [811, "slow request: POST /reserve took <n>ms"]
  - id: c8
    kind: observation
    text: payments-svc p99 latency stayed between 212.9ms and 279.4ms
    evidence: [q010]
    values: [212.9, 279.4]
  - id: c9
    kind: observation
    text: inventory-svc cache_hit_ratio dipped to 0.3211 and recovered to 0.9361 at 14:56
    evidence: [q014]
    values: [0.3211, 0.9361]
  - id: c10
    kind: observation
    text: postgres-inventory active connections rose from 9.0 at 14:52 to 22.8 at 14:54
    evidence: [q017]
    values: [9.0, 22.8]
  - id: c11
    kind: observation
    text: checkout-api depends on payments-svc and inventory-svc
    evidence: [q002]
    values: ["payments-svc", "inventory-svc"]
  - id: c12
    kind: inference
    text: The N+1 query pattern from inv-1931 increased inventory-svc /reserve latency, which checkout-api inherits as a synchronous dependency, causing the p99 SLO burn
    based_on: [c1, c3, c4, c5, c6, c7, c10, c11]
  - id: c13
    kind: inference
    text: payments-svc deploy, cache warm-up, and traffic surge are refuted as causes
    based_on: [c2, c8, c9, c1]
```
