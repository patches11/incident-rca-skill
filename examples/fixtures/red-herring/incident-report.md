# Incident report: checkout-api 5xx caused by card-gateway (vendor) timeouts and 503s via payments-svc

**Status:** Root cause identified · **Confidence:** high
**Paged service:** checkout-api · **Onset (T0):** 2026-10-16 10:01 UTC · **Investigated at:** 2026-10-16 10:12 UTC
**Owner of root-cause service:** card-gateway is an external vendor (status page: status.cardgateway.example); the integration belongs to payments (@payments-oncall)

## Summary
At 10:01 UTC the external card-gateway vendor started timing out (httpx.ReadTimeout at 2500ms) and returning 503s to payments-svc. payments-svc /charge p99 jumped from ~240ms to ~2.5–2.8s. That is longer than checkout-api's 2000ms timeout for payments-svc, so checkout-api returns 504 on roughly 25–30% of POST /checkout. Nothing changed on our side near T0, traffic is flat, and inventory-svc and postgres-payments are healthy. The notification-worker deploy at 09:50 and its errors are a red herring: they are off-path and were already at baseline. Immediate action: get payments on-call to escalate to the vendor, check the vendor status page, and fail over or degrade the charge path if a secondary processor or async-charge mode is available.

## Impact
- checkout-api 5xx ratio rose from ~0.3% baseline to a peak of 0.3261 at 10:06 and 0.2877 at 10:12 (q004). The SLO is 99.9% non-5xx (q002).
- checkout-api p99 is pinned at 2040.0 ms, which is the 2s upstream timeout (q005, q023).
- payments-svc error_rate peaked at 0.1223 at 10:08 (q008).

## Timeline (UTC)
| Time | Event | Evidence |
|---|---|---|
| 04:12 | inventory-svc config cfg-8812 (log sampling 0.01→0.02). Off the failing path | q007 |
| 09:50 | notification-worker deploy nw-2231 (SMTP 421 retry backoff). Not in the checkout path | q007, q002 |
| 10:00 | payments-svc upstream (card-gateway) p99 starts rising, 397.1 ms | q015 |
| 10:01 | checkout-api error_rate 0.018, p99 659.3 ms: **T0** | q004, q005 |
| 10:02 | card-gateway p99 2312.9 ms, upstream_error_rate 0.0887. checkout-api error_rate 0.1357 | q015, q016, q004 |
| 10:06 | checkout-api error_rate peak 0.3261 | q004 |
| 10:12 | Still ongoing: checkout-api 0.2877, card-gateway p99 2464.1 ms | q004, q015 |

## Root cause
**Trigger and mechanism:** card-gateway (external, `POST /v2/charges`) degraded at about 10:00. payments-svc logs show `card-gateway request timed out after 2500ms` (104 occurrences, `httpx.ReadTimeout`) and `card-gateway returned 503 Service Unavailable` (47) from 09:59:30 onward. Before that, only a handful of benign `card declined by issuer` warnings appear (q017). payments-svc's own upstream metrics went from a ~175ms baseline to 2312.9–2551.4ms p99, with upstream_error_rate reaching 0.1189 (q015, q016).

**Symptom in payments-svc:** /charge p99 reached 2530.5–2802.0 ms and error_rate reached 0.1223 (q009, q008).

**Symptom in checkout-api:** checkout-api calls payments-svc with a 2s timeout (q023). Charges that previously finished in ~240ms now take ~2.7s, so they exceed the timeout and checkout-api returns 504: `timeout calling payments-svc after 2000ms` is the only error template, with 170 occurrences (q012). Error traces show nearly all the time in the `card-gateway POST /v2/charges` span (2376.4 ms, httpx.ReadTimeout; 2379.8 ms, HTTP 503). In the same traces db.pool.acquire takes ≤0.55 ms and inventory /reserve takes ~16 ms (q018).

Note: checkout-api's 5xx ratio (~28%) is higher than payments-svc's own error rate (~11%). Charges that payments-svc eventually completes, but after more than 2s, still count as failures in checkout-api. This is a timeout-budget mismatch: checkout's 2000ms is lower than payments' 2500ms gateway timeout. The vendor is the cause; the mismatch amplifies the damage.

## Hypotheses considered
| Hypothesis | Test (query that could disprove it) | Result | Status |
|---|---|---|---|
| card-gateway (vendor) degradation | Gateway spans/upstream metrics normal while payments slow (q015, q016, q017, q018) | Upstream p99 175→2500ms at T0; gateway timeout and 503 errors; gateway span is the dominant span in traces | **survived** |
| payments-svc DB pool exhaustion / postgres-payments lock contention | Pool pinned at max, pool wait high, lock_wait spike (q013, q014, q020, q019) | Pool 12–15.2 of 20 (never saturated). Pool wait ≤4.1ms. Lock wait 0.87–3.5ms, flat. Pool rise is a consequence of slower calls | refuted |
| inventory-svc degradation | inventory RED at T0 (q010, q011) | error_rate ≤0.0013 and p99 ~82–106ms throughout; /reserve spans ~16ms | refuted |
| Recent change (notification-worker nw-2231 at 09:50, cfg-8812) | Path + temporal + 7-day lookback (q002, q007, q021, q022) | notification-worker is not in the checkout path, and its error_rate is a flat 0.13–0.19 baseline (SMTP 421, a known-benign overlay entry). No payments-svc or checkout-api changes in 7 days | refuted |
| Traffic surge | checkout-api request_rate (q006) | Flat at 15–19 rps across T0 | refuted |

## Mitigation (proposed — a human must execute)
1. **Escalate to the card-gateway vendor and check status.cardgateway.example** (owner: @payments-oncall). Why: all failing spans terminate in card-gateway timeouts/503s (q017, q018). Risk: none. Confirm by: payments-svc `upstream_latency_p99_ms` returns to ~175ms and `upstream_error_rate` to ~0.001.
2. **If available, fail over to a secondary processor or enable degraded/async charge mode** (payments decision). Why: the vendor is the only failing component (q015, q016). Risk: different decline behaviour and reconciliation work; needs payments sign-off. Confirm by: checkout-api `error_rate` < 0.005 and p99 < 800ms.
3. **Do not roll back the notification-worker deploy nw-2231 or the inventory config cfg-8812**. Neither is on the failing path (q002, q021), so rolling them back would only waste time.
4. Optional, and only after weighing double-charge risk: do not raise checkout's 2s timeout ad hoc. Late-completing charges that checkout reports as 504 may have succeeded at the gateway. Payments should check for charges that succeeded while checkout returned 504, and reconcile any orders that need it.

## Fix
No code generated. The category is `dependency_failure`: the fix is vendor recovery, failover, or degradation, which is a human/vendor decision. Follow-up hardening (proposed for payments + storefront owners, not part of the incident fix):
- Align timeout budgets. payments-svc's card-gateway timeout (2500ms) is longer than checkout-api's payments-svc timeout (2000ms). Charges therefore finish after checkout has already given up, which risks user-visible failures for charges that actually succeeded.
- Add a circuit breaker / fast-fail on card-gateway in payments-svc, and idempotency keys so clients can safely retry charges.

## Missing data / blind spots
- card-gateway has no metrics in our catalog (q002). The vendor-side cause is unknown. Get the vendor status page and incident ticket from the payments team.
- Whether the 504'd checkouts left successful charges at the gateway has not been verified. Get this from payments-svc logs for status=200 /charge with duration >2000ms over 10:01–now, and from vendor-side reconciliation.

## Follow-ups
- Alert gap: there is no alert on payments-svc `upstream_error_rate` / `upstream_latency_p99_ms`. Such an alert would have paged payments directly, rather than only storefront.
- The page links runbook `checkout-latency` for an availability alert. Add a step to the runbook: "if spans end in card-gateway, page @payments-oncall and check the vendor status page".
- Proposed overlay entry (for human review): "checkout-api 504 `timeout calling payments-svc after 2000ms` with payments-svc `card-gateway request timed out after 2500ms` = vendor degradation; page @payments-oncall and check status.cardgateway.example. Note the checkout 2s vs payments 2.5s timeout mismatch inflates checkout 5xx relative to payments 5xx."

## Verification
`verify_report`: **17/17 claims verified** (c1–c17 PASS).

## Machine-readable summary
```yaml
verdict: root_cause
confidence: high
root_cause:
  service: card-gateway
  category: dependency_failure
  change_id: null
  commit: null
  summary: External card-gateway began timing out and returning 503s at ~10:00 UTC, pushing payments-svc /charge latency past checkout-api's 2000ms timeout and causing checkout-api 504s.
fix_proposed: false
missing_data: []
claims:
  - id: c1
    kind: observation
    text: checkout-api error_rate rose from baseline ~0.003 to 0.018 at 10:01 and peaked at 0.3261 at 10:06
    evidence: [q004]
    values: [0.003, 0.018, "10:01", 0.3261, "10:06"]
  - id: c2
    kind: observation
    text: checkout-api latency_p99_ms rose to 659.3 at 10:01 and is pinned at 2040.0
    evidence: [q005]
    values: [659.3, 2040.0]
  - id: c3
    kind: observation
    text: checkout-api request_rate stayed flat between 12.7 and 19.0 rps
    evidence: [q006]
    values: [12.7, 19.0]
  - id: c4
    kind: observation
    text: the only checkout-api error template is "timeout calling payments-svc after <n>ms" with 170 matches, status 504, upstream payments-svc
    evidence: [q012]
    values: ["timeout calling payments-svc after <n>ms", 170, 504, "payments-svc"]
  - id: c5
    kind: observation
    text: payments-svc upstream_latency_p99_ms rose from ~175 ms baseline to 397.1 at 10:00 and 2312.9 at 10:02, max 2551.4
    evidence: [q015]
    values: [397.1, "10:00", 2312.9, "10:02", 2551.4]
  - id: c6
    kind: observation
    text: payments-svc upstream_error_rate rose to 0.0887 at 10:02 and max 0.1189
    evidence: [q016]
    values: [0.0887, 0.1189]
  - id: c7
    kind: observation
    text: payments-svc logs show "card-gateway request timed out after <n>ms" (104) and "card-gateway returned <n> Service Unavailable" (47), with httpx.ReadTimeout
    evidence: [q017]
    values: ["card-gateway request timed out after <n>ms", 104, "card-gateway returned <n> Service Unavailable", 47, "httpx.ReadTimeout"]
  - id: c8
    kind: observation
    text: in error traces the card-gateway POST /v2/charges span took 2376.4 ms (httpx.ReadTimeout) while db.pool.acquire took 0.3 ms and inventory-svc POST /reserve took 15.9 ms
    evidence: [q018]
    values: ["POST /v2/charges", 2376.4, "httpx.ReadTimeout", 0.3, 15.9]
  - id: c9
    kind: observation
    text: payments-svc latency_p99_ms rose to 2530.5 at 10:02 and max 2802.0, error_rate max 0.1223
    evidence: [q009, q008]
    values: [2530.5, 2802.0, 0.1223]
  - id: c10
    kind: observation
    text: payments-svc db_pool_in_use max 15.2 against db_pool_max 20.0, and db_pool_wait_ms max 4.1116
    evidence: [q013, q014, q020]
    values: [15.2, 20.0, 4.1116]
  - id: c11
    kind: observation
    text: postgres-payments lock_wait_ms stayed between 0.8727 and 3.5483
    evidence: [q019]
    values: [0.8727, 3.5483]
  - id: c12
    kind: observation
    text: inventory-svc error_rate max 0.0013 and latency_p99_ms max 105.8 throughout
    evidence: [q010, q011]
    values: [0.0013, 105.8]
  - id: c13
    kind: observation
    text: only changes in the last 6h were inventory-svc cfg-8812 at 04:12 and notification-worker nw-2231 (commit 8d21b4e) at 09:50; the 7-day lookback adds only notification-worker nw-2214
    evidence: [q007, q022]
    values: ["cfg-8812", "nw-2231", "8d21b4e", "09:50", "nw-2214"]
  - id: c14
    kind: observation
    text: notification-worker is described as not in the checkout request path, and its error_rate stayed between 0.1306 and 0.1943 across the window
    evidence: [q002, q021]
    values: ["Not in the checkout request path", 0.1306, 0.1943]
  - id: c15
    kind: observation
    text: checkout-api returns 504 when a dependency call exceeds its 2s timeout
    evidence: [q023]
    values: ["504", "2s timeout"]
  - id: c16
    kind: inference
    text: card-gateway degradation starting ~10:00 is the root cause; it pushed payments-svc /charge past checkout-api's 2s timeout, producing checkout-api 504s
    based_on: [c1, c2, c4, c5, c6, c7, c8, c9, c15]
  - id: c17
    kind: inference
    text: payments-svc DB pool, postgres-payments, inventory-svc, traffic and recent changes are refuted as causes
    based_on: [c3, c8, c10, c11, c12, c13, c14]
```
