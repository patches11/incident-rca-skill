# Incident report: checkout-api 5xx caused by payments-svc DB connection leak on declined charges (pay-5531)

**Status:** Root cause identified · **Confidence:** high
**Paged service:** checkout-api · **Onset (T0):** 2026-10-14 09:43 UTC · **Investigated at:** 2026-10-14 09:58 UTC
**Owner of root-cause service:** payments / @payments-oncall

## Summary
At 09:38 UTC payments-svc deploy pay-5531 (commit a41f9e2, FRAUD-231) added a DB insert on the
card-declined path of `/charge`. That path then raises HTTP 402 without `session.close()`, so every
declined charge leaks one pooled connection. The 20-connection pool filled by 09:43 and has stayed
pinned. Since then, `/charge` requests time out after 1s acquiring a connection (503), and
checkout-api returns about 16–18% 5xx. **Do now:** roll back payments-svc to the version before
pay-5531. A restart only buys a few minutes, because the leak refills the pool as declines come in.

## Impact
- checkout-api 5xx ratio went from ~0.3% baseline to 0.0679 at 09:43, peaked at 0.1829 at 09:56, and was 0.1724 at 09:58 (q005).
- payments-svc error rate peaked at 0.191 at 09:56 (q007). checkout-api is the only dependent of payments-svc (q002).
- Failing checkouts take ~1052.5 ms p50, almost all of it in `db.pool.acquire` (q018).

## Timeline (UTC)
| Time | Event | Evidence |
|---|---|---|
| 09:38 | payments-svc deploy pay-5531 / a41f9e2 "Record declined charge attempts for fraud scoring (FRAUD-231)" | q011 |
| 09:39 | payments-svc db_pool_in_use starts climbing from a ~3–6 baseline (8.0) | q009 |
| 09:42 | first `QueuePool limit of size 20 overflow 0 reached` error | q012 |
| 09:43 | pool pinned at 20/20; checkout-api and payments-svc errors step up (T0) | q009, q010, q005, q007 |
| 09:44–09:58 | pool stays at 20 with steady traffic; pool wait ~716–828 ms | q009, q019, q006 |
| 09:51 | SEV2 page fires | page |

## Root cause
**Trigger and cause:** deploy pay-5531 (commit a41f9e2) to `sandbox/services/payments_svc/app.py` (q011):
```diff
     session = SessionLocal()
     result = gateway.charge(req.card_token, req.amount_cents)
     if result.declined:
+        session.execute(insert(declined_attempts).values(user_id=req.user_id, code=result.code))
+        log.info("declined charge attempt recorded for fraud scoring")
         raise HTTPException(status_code=402, detail=result.code)
     session.execute(insert(payments).values(...))
     session.commit()
     session.close()
```
Before this change, the declined branch never used the session, so no connection was checked out.
Now `session.execute` checks out a pooled connection and opens a transaction. The `raise` then skips
both `commit()` and `close()`, so the connection is never returned to the pool. Declines are a
normal part of traffic: "card declined by issuer" appears throughout the window, and "declined
charge attempt recorded for fraud scoring" lines appear after 09:38 (q017). Each decline therefore
leaks one connection.

**Mechanism → symptom:** db_pool_in_use rose from 8.0 at 09:39 to 20.0 at 09:43 and has stayed at
20.0 (= db_pool_max 20.0) through 09:58 (q009, q010). It does not fall back while request rate is
steady, which points to accumulation (a leak), not load. Requests then wait up to the 1s acquire
timeout (db_pool_wait_ms 811.9–828.0, q019) and fail with
`sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 0 reached` as 503s (q012, q018).
checkout-api passes these through as 5xx (q005). In traces, `db.pool.acquire` takes 1001.3 ms of a
1062.9 ms checkout, while inventory `/reserve` takes 37.6 ms (q018).

## Hypotheses considered
| Hypothesis | Test (query that could disprove it) | Result | Status |
|---|---|---|---|
| payments-svc pool leak from pay-5531 | pool below max, or no step at deploy (q009, q010, q012, q011) | pool pinned 20/20 from 09:43 after the 09:38 deploy; error names QueuePool | **survived** |
| inventory-svc degradation (incl. inv-1902 / cfg-8812) | inventory error rate flat at T0 (q008); reserve span fast (q018) | max 0.0016, ok spans ~18–38 ms | refuted |
| card-gateway (vendor) degradation | upstream latency/errors rise at T0 (q015, q016) | flat ~158–203.6 ms p99, ≤0.0019 errors | refuted |
| postgres-payments slow / lock contention (pool as victim) | lock_wait rises at T0 (q014) | 1.2–2.7 ms through 09:56 | refuted |
| traffic surge | checkout request_rate jumps at T0 (q006) | 14.1–18.1 rps, close to baseline | refuted |

## Mitigation (proposed — a human must execute)
1. **Roll back payments-svc to the version before 2026.10.14-1 (pay-5531)** — why: the leak starts at this deploy and the diff shows the leaking path (q011, q009). Risk: low. FRAUD-231 declined-attempt recording stops until the fix ships. Confirm by: payments-svc `db_pool_in_use` back to ~3–6, `db_pool_wait_ms` < 1 ms, checkout-api `error_rate` back to ~0.003.
2. **If the rollback is delayed: rolling restart of payments-svc** — it returns leaked connections, but the pool refills as declines arrive (it took ~5 min after the deploy). Use only as a stopgap. Confirm with the same metrics, then watch for re-climb.
3. Do not raise the pool size as the fix. A bigger pool only delays exhaustion and adds load on postgres-payments.

## Fix
No code was generated: the payments-svc repo (`sandbox/services/payments_svc`) is not in this
workspace. Proposed fix (owner: @payments-oncall): make sure the session is always closed. Either
use `with SessionLocal() as session:` / `try: ... finally: session.close()` around the whole handler,
or call `session.commit(); session.close()` before raising the 402 on the declined branch. Add a
regression test that makes ≥ pool_size declined charges and then asserts a successful charge still
gets a connection (or asserts `pool.checkedout() == 0` after a declined request).

## Missing data / blind spots
- `max_connections` for postgres-payments was not queried. The DB saturation claim rests only on lock_wait and active_connections (23.0).
- Logs are sampled (q017), so the exact number of leaked connections per decline can't be counted from logs. The pool metric (q009) is the authoritative signal.

## Follow-ups
- Add an alert on payments-svc `db_pool_in_use / db_pool_max ≥ 0.9` for 2 min. It would have fired ~09:42, before the SLO page.
- checkout-latency runbook: add "if payments `/charge` returns 503 with QueuePool errors, check the payments-db-pool runbook and recent payments deploys".
- Lint/review rule for payments-svc: sessions must use a context manager.
- Proposed overlay entry (for human review): *payments-svc: `QueuePool limit ... reached` errors are never benign; check for a session leak on error/early-return paths (PM for 2026-10-14).*

## Verification
verify_report: **14/14 claims verified** (c1–c14 PASS).

## Machine-readable summary
```yaml
verdict: root_cause
confidence: high
root_cause:
  service: payments-svc
  category: code_regression
  change_id: pay-5531
  commit: a41f9e2
  summary: Deploy pay-5531 added a DB insert on the declined-charge path that raises HTTP 402 without closing the session, leaking one pooled connection per decline until the 20-connection pool was exhausted.
fix_proposed: false
missing_data: []
claims:
  - id: c1
    kind: observation
    text: checkout-api 5xx ratio rose from ~0.003 baseline to 0.0679 at 09:43 and peaked at 0.1829 at 09:56
    evidence: [q005]
    values: [0.0679, "2026-10-14T09:43:00Z", 0.1829, "2026-10-14T09:56:00Z"]
  - id: c2
    kind: observation
    text: payments-svc error_rate stepped up at 09:43 (0.0692) and peaked at 0.191 at 09:56
    evidence: [q007]
    values: [0.0692, "2026-10-14T09:43:00Z", 0.191]
  - id: c3
    kind: observation
    text: inventory-svc error_rate stayed flat with max 0.0016
    evidence: [q008]
    values: [0.0016]
  - id: c4
    kind: observation
    text: payments-svc deploy pay-5531 commit a41f9e2 at 09:38 added session.execute(insert(declined_attempts)) before raise HTTPException(status_code=402) on the declined path
    evidence: [q011]
    values: ["pay-5531", "a41f9e2", "2026-10-14T09:38:00Z", "declined_attempts", "status_code=402"]
  - id: c5
    kind: observation
    text: payments-svc db_pool_in_use climbed from 8.0 at 09:39 to 20.0 at 09:43 and remained 20.0 through 09:58; db_pool_max is 20.0
    evidence: [q009, q010]
    values: [8.0, "2026-10-14T09:39:00Z", 20.0, "2026-10-14T09:43:00Z", "2026-10-14T09:58:00Z"]
  - id: c6
    kind: observation
    text: payments-svc errors are all "QueuePool limit of size 20 overflow 0 reached, connection timed out, timeout 1.00" (610 matches, none before 09:38)
    evidence: [q012]
    values: ["QueuePool limit of size 20 overflow 0 reached, connection timed out, timeout 1.00", 610, "sqlalchemy.exc.TimeoutError"]
  - id: c7
    kind: observation
    text: failing checkout traces spend ~1001.3 ms in payments-svc db.pool.acquire out of 1062.9 ms, while inventory-svc /reserve took 37.6 ms
    evidence: [q018]
    values: [1001.3, 1062.9, 37.6, "db.pool.acquire"]
  - id: c8
    kind: observation
    text: payments-svc db_pool_wait_ms rose from 0.562 at 09:40 to 811.9 at 09:44 and max 828.0
    evidence: [q019]
    values: [0.562, 811.9, 828.0]
  - id: c9
    kind: observation
    text: card-gateway upstream latency p99 (max 203.6 ms) and upstream error rate (max 0.0019) stayed flat
    evidence: [q015, q016]
    values: [203.6, 0.0019]
  - id: c10
    kind: observation
    text: postgres-payments lock_wait_ms stayed between 1.2099 and 2.7113 ms through 09:56
    evidence: [q014]
    values: [1.2099, 2.7113]
  - id: c11
    kind: observation
    text: checkout-api request_rate stayed between 11.8 and 18.1 rps with mean 14.5, no surge at T0
    evidence: [q006]
    values: [11.8, 18.1, 14.5]
  - id: c12
    kind: observation
    text: declined charges occur throughout the window ("card declined by issuer"), and "declined charge attempt recorded for fraud scoring" appears after the deploy
    evidence: [q017]
    values: ["card declined by issuer", "declined charge attempt recorded for fraud scoring", 26, 8]
  - id: c13
    kind: inference
    text: the declined path in pay-5531 checks out a connection and raises before session.close(), leaking one connection per decline; with steady declines the pool accumulated to its 20-connection max, independent of traffic level
    based_on: [c4, c5, c11, c12]
  - id: c14
    kind: inference
    text: pool exhaustion in payments-svc causes 1s acquire timeouts returned as 503, which checkout-api surfaces as the paged 5xx; inventory, card-gateway, the database and traffic are not the cause
    based_on: [c1, c2, c3, c6, c7, c8, c9, c10, c11, c13]
```
