# Incident report: payments-svc holds DB connections across the new card-gateway risk-score call (pay-live-1), exhausting its pool and failing checkout

**Status:** Root cause identified · **Confidence:** high
**Paged service:** checkout-api · **Onset (T0):** 2026-10-20T20:09Z · **Investigated at:** 2026-10-21T00:25:17Z
**Owner of root-cause service:** payments / @payments-oncall

## Summary
At 20:08:09Z payments-svc deploy `pay-live-1` (commit ed3be52, FRAUD-231) added a synchronous card-gateway
`risk_score` call *after* a pooled DB connection is checked out and before commit. Every successful charge now
holds one of the 10 pool connections for the duration of a ~1–2s external call. The pool pinned at 10/10 from
the next minute onward, other /charge requests time out acquiring a connection (`QueuePool limit of size 10
overflow 0 reached ... timeout 1.00`) and return 503, and checkout-api turns those into 502/504 — roughly 30%
5xx and p99 ≈ 2.9s, sustained for ~4h before the page. **Do now:** roll back payments-svc pay-live-1 (or deploy
the fix on branch `rca/payments-pool-held-across-risk-score`).

## Impact
- checkout-api error_rate mean 0.2836, max 0.4032 over the last 2h (q024); p99 latency mean 2942.4 ms (q025) vs SLO 99% < 800ms (q002).
- Sustained since 20:09 — checkout-api error_rate 0.0 at 20:08 then 0.1888 at 20:09 (q008). The burn alert fired ~4h after onset.
- Traffic is flat at ~11.8 rps (q026), so roughly a third of checkouts are failing.

## Timeline (UTC)
| Time | Event | Evidence |
|---|---|---|
| 20:07:32 | payments-svc starts on `commit: baseline`, pool size 10, overflow 0 | q030 |
| 20:08:00 | Baseline sample: payments pool in use 0.0, pool wait 4.969 ms, gateway p99 121.0 ms, checkout errors 0.0 | q010, q017, q014, q008 |
| 20:08:09 | Deploy pay-live-1 (ed3be52) "Score every charge with card-gateway risk API" | q022 |
| 20:08:10 | payments-svc restarts, logs `commit: 863745e` | q030 |
| 20:08:25 | First `QueuePool limit of size 10 overflow 0 reached` error | q023 |
| 20:09 | Pool in use 10.0, pool wait 1831.8 ms, gateway p99 1963.2 ms; payments errors 0.0452; checkout errors 0.1888 | q010, q017, q014, q009, q008 |
| 20:09 → 00:25 | Steady state: checkout ~28% 5xx, p99 ~2.94s | q024, q025 |

## Root cause
Change `pay-live-1`, commit `ed3be52` (q022), in `sandbox/services/payments_svc/app.py` `/charge`:

```diff
         _acquire(session)
         session.execute(insert(payments).values(user_id=req.user_id, amount_cents=req.amount_cents))
+        # FRAUD-231: score every successful charge and store the score with the payment
+        score = gateway.risk_score(req.user_id, req.amount_cents)
+        session.execute(payments.update().where(payments.c.user_id == req.user_id).values(risk_score=score))
         session.commit()
```

Mechanism:
1. **Trigger:** the `risk_score` HTTP call to card-gateway now runs between `_acquire(session)` and `commit()`,
   so each successful charge holds a pooled connection for the full external call. Gateway call p99 went from
   121.0 ms to 1963.2 ms at 20:09 (q014); successful /charge requests now take 1088.0–1968.2 ms (q029).
2. **Resource:** the pool (size 10, overflow 0, timeout 1s — q030, q023) is pinned at 10.0 from 20:09 (q010)
   and pool acquire wait is ~1.9s (q017).
3. **Suspect symptom:** /charge requests that can't get a connection fail after ~1s with
   `sqlalchemy.exc.TimeoutError` → 503 (q023). QueuePool timeouts are
   20557 of the payments-svc error logs (q023).
4. **Paged-service symptom:** checkout-api logs `upstream payments-svc returned <n>` (20557 — the same count as
   the payments QueuePool errors) and `timeout calling payments-svc after <n>ms` (30732) as its top errors
   (q027), returning 502/504.

Declined charges, which call only `gateway.charge` and return before taking a connection, stay fast after the
deploy (30.4–103.4 ms, q028). So the card-gateway charge path is healthy. The latency is in the new risk-score
call, and the damage comes from holding a DB connection across it.

A second bug in the same diff: `UPDATE ... WHERE user_id = :user_id` overwrites `risk_score` on **all** of
that user's past payments, not only the new one. Data integrity: payments rows written since 20:08 may have
wrong risk scores (reproduced in the regression test below).

## Hypotheses considered
| Hypothesis | Test (query that could disprove it) | Result | Status |
|---|---|---|---|
| payments-svc pool exhaustion caused by pay-live-1 holding connections across the risk-score call | Pool saturation and onset vs deploy (q022, q010, q017, q023) | Pool 0→10.0 and wait 4.969→1831.8 ms in the minute after the 20:08:09 deploy; errors name QueuePool timeout | **survived** |
| card-gateway generally degraded (dependency failure) | Latency of declines, which call only the gateway charge API (q028) | Declines 30.4–103.4 ms after onset — the charge API is fast | refuted (for the charge path) |
| inventory-svc failure | inventory-svc error_rate around T0 (q011) | 0.0 throughout | refuted |
| Traffic surge / capacity | checkout-api request_rate (q026) | Flat, 10.6–13.3 rps | refuted |
| postgres-payments slow/locking | lock_wait_ms (q018) | Metric not available (only cpu_util) | untested — not needed: errors are pool-acquire timeouts on the client side, not query slowness |

## Mitigation (proposed — a human must execute)
1. **Roll back payments-svc to the pre-pay-live-1 build** (the previous image/commit before ed3be52). Why: the
   pool pinned within one minute of this deploy (q022, q010) and there is no other change in the last 6h
   (q022). Risk: FRAUD-231 risk scoring stops; charges are unaffected. Confirm by: payments-svc `db_pool_in_use`
   dropping below 10 and `db_pool_wait_ms` back to single-digit ms; checkout-api `error_rate` → ~0 and
   `latency_p99_ms` < 800.
2. **Alternative if rollback isn't possible:** deploy the fix on branch `rca/payments-pool-held-across-risk-score`
   (below) through the normal pipeline. Don't use a bigger `DB_POOL_SIZE` as the fix: it only raises the
   threshold, and postgres max_connections couldn't be checked (q018).
3. **Data cleanup (payments team, after mitigation):** payments rows for users who charged since 20:08:09Z may
   have had `risk_score` overwritten by a later charge. Recompute or null those scores before anything relies on them.

## Fix
Branch `rca/payments-pool-held-across-risk-score` (local, not pushed or committed). Call the risk API **before**
taking a pooled connection, and write the score in the INSERT so it only applies to the new row:

```diff
--- a/sandbox/services/payments_svc/app.py
+++ b/sandbox/services/payments_svc/app.py
@@
         if result["declined"]:
             raise HTTPException(status_code=402, detail=result["code"])
-        _acquire(session)
-        session.execute(insert(payments).values(user_id=req.user_id, amount_cents=req.amount_cents))
-        # FRAUD-231: score every successful charge and store the score with the payment
-        score = gateway.risk_score(req.user_id, req.amount_cents)
-        session.execute(payments.update().where(payments.c.user_id == req.user_id).values(risk_score=score))
+        # FRAUD-231: score every successful charge and store the score with the payment.
+        # Call the gateway before taking a pooled connection so a slow risk API can't pin the pool.
+        score = gateway.risk_score(req.user_id, req.amount_cents)
+        _acquire(session)
+        session.execute(insert(payments).values(user_id=req.user_id, amount_cents=req.amount_cents,
+                                                risk_score=score))
         session.commit()
```

Regression tests added to `sandbox/services/payments_svc/tests/test_charge.py`:
- `test_risk_score_call_does_not_hold_a_db_connection`: asserts `engine.pool.checkedout() == 0` while `risk_score` runs.
- `test_risk_score_is_stored_only_on_the_new_payment`: two charges for the same user keep scores `[11, 22]`.

Before the fix (`uv run --with-requirements sandbox/services/requirements-dev.txt pytest -q sandbox/services/payments_svc/tests`):
```
E       assert [1] == [0]          # one pooled connection held during risk_score
E       assert [22, 22] == [11, 22] # earlier payment's score overwritten
FAILED ...::test_risk_score_call_does_not_hold_a_db_connection
FAILED ...::test_risk_score_is_stored_only_on_the_new_payment
2 failed, 2 passed, 3 warnings in 0.53s
```
After the fix:
```
4 passed, 3 warnings in 0.40s
```

Follow-up for the payments team (design, not in this minimal fix): give the risk call its own short timeout or
run it asynchronously after commit. With the httpx 2.5s timeout, a slow risk API still adds up to 2.5s to every
successful charge, and checkout-api gives up on payments at 2000 ms (q027).

## Missing data / blind spots
- **Pre-deploy baseline is ~40s long:** payments-svc first started at 20:07:32 (q030) and metrics begin at 20:08:00.
  Onset lines up with the deploy to the minute, but there's no longer history to rule out an earlier pattern.
- **No distributed tracing** (q012). So there's no per-span proof that the time goes to `/v2/risk-score`. It's
  inferred from fast declines (q028) vs slow successes (q029) and the code path.
- **Commit mismatch:** the change record says `ed3be52` (q022), but the restarted process logs `commit: 863745e`
  (q030). `863745e` isn't a commit in this repo. Deploy tooling should be checked to confirm which build is running.
- postgres-payments exposes only `cpu_util` (q018); no connection or lock metrics.
- card-gateway (vendor) exposes no metrics; risk-score endpoint latency is visible only in aggregate in `upstream_latency_p99_ms`.

## Follow-ups
- Alerting: the SLO-burn page fired ~4h after onset (q008 vs page time). Add a payments-svc alert on
  `db_pool_in_use == db_pool_max` for 5m and on `db_pool_wait_ms` p99 > 100ms.
- Instrumentation: label upstream gateway latency by endpoint (`/v2/charges` vs `/v2/risk-score`); enable tracing.
- Code review checklist / lint: no external network calls while a DB connection or transaction is held.
- Runbook `payments-db-pool`: add "check for recent deploys that added I/O inside the transaction".
- Proposed overlay entry (for human review): *payments-svc: pool pinned at 10/10 with `QueuePool limit ... timeout 1.00`
  usually means a connection is held across slow I/O, not that the DB is slow. Look at recent diffs to /charge first.*

## Verification
Final `verify_report` (round 2; round 1 failed only because claims cited relative-window `now-…` queries, which were re-run with absolute windows):
```
[PASS] c1 … [PASS] c16
16/16 claims verified
```

## Machine-readable summary
```yaml
verdict: root_cause
confidence: high
root_cause:
  service: payments-svc
  category: code_regression
  change_id: pay-live-1
  commit: ed3be52
  summary: Commit ed3be52 calls the card-gateway risk-score API while holding a pooled DB connection, pinning payments-svc's 10-connection pool so other /charge requests time out (503), which checkout-api surfaces as 5xx and >2s latency.
fix_proposed: true
missing_data: []
claims:
  - id: c1
    kind: observation
    text: payments-svc deploy pay-live-1 (commit ed3be52) at 2026-10-20T20:08:09Z added a gateway.risk_score call and an update by user_id inside the /charge session, and is the only change in the 6h window
    evidence: [q022]
    values: ["pay-live-1", "ed3be52", "2026-10-20T20:08:09Z", "gateway.risk_score", "payments.c.user_id == req.user_id"]
  - id: c2
    kind: observation
    text: payments-svc db_pool_in_use went from 0.0 at 20:08 to 10.0 at 20:09 and stayed pinned
    evidence: [q010]
    values: [0.0, 10.0, "2026-10-20T20:08:00Z", "2026-10-20T20:09:00Z"]
  - id: c3
    kind: observation
    text: payments-svc db_pool_wait_ms went from 4.969 at 20:08 to 1831.8 at 20:09
    evidence: [q017]
    values: [4.969, 1831.8]
  - id: c4
    kind: observation
    text: payments-svc upstream (card-gateway) latency p99 went from 121.0 ms to 1963.2 ms at 20:09
    evidence: [q014]
    values: [121.0, 1963.2]
  - id: c5
    kind: observation
    text: payments-svc's dominant error is the SQLAlchemy QueuePool timeout for pool size 10 overflow 0, first seen at 20:08:25, 20557 occurrences
    evidence: [q023]
    values: ["QueuePool limit of size 10 overflow 0 reached, connection timed out, timeout 1.00", 20557, "2026-10-20T20:08:25.266Z", "sqlalchemy.exc.TimeoutError"]
  - id: c6
    kind: observation
    text: checkout-api error_rate went from 0.0 at 20:08 to 0.1888 at 20:09
    evidence: [q008]
    values: [0.0, 0.1888]
  - id: c7
    kind: observation
    text: checkout-api error_rate averaged 0.2836 (max 0.4032) and latency p99 averaged 2942.4 ms over the last 2h
    evidence: [q024, q025]
    values: [0.2836, 0.4032, 2942.4]
  - id: c8
    kind: observation
    text: checkout-api top errors are payments-svc timeouts after 2000ms and payments-svc error responses
    evidence: [q027]
    values: ["timeout calling payments-svc after <n>ms", 30732, "upstream payments-svc returned <n>", 20557]
  - id: c9
    kind: observation
    text: declined charges (gateway charge only, no DB connection) stayed fast after onset, 30.4-103.4 ms
    evidence: [q028]
    values: [30.4, 103.4]
  - id: c10
    kind: observation
    text: successful charges took 1088.0-1968.2 ms after onset
    evidence: [q029]
    values: [1088.0, 1968.2]
  - id: c11
    kind: observation
    text: inventory-svc error_rate was 0.0 throughout the onset window
    evidence: [q011]
    values: [0.0]
  - id: c12
    kind: observation
    text: checkout-api request_rate was flat, 10.6 to 13.3 rps
    evidence: [q026]
    values: [10.6, 13.3]
  - id: c13
    kind: observation
    text: payments-svc restarted at 20:08:10 logging commit 863745e with db_pool_size 10 and db_max_overflow 0; prior start at 20:07:32 was commit baseline
    evidence: [q030]
    values: ["863745e", "baseline", "2026-10-20T20:08:10.190Z", "2026-10-20T20:07:32.074Z", 10]
  - id: c14
    kind: inference
    text: holding a pooled connection across the slow risk-score call pins the 10-connection pool, so other /charge requests time out acquiring a connection
    based_on: [c1, c2, c3, c4, c5, c9, c10]
  - id: c15
    kind: inference
    text: card-gateway's charge API is not degraded; inventory-svc and traffic are not the cause
    based_on: [c9, c11, c12]
  - id: c16
    kind: inference
    text: payments-svc pool exhaustion is the cause of checkout-api 5xx and latency
    based_on: [c5, c6, c7, c8, c14, c15]
```
