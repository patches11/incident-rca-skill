"""Generate every fixture scenario. Seeded and reproducible:

    uv run python scenarios/_generator/generate.py            # all scenarios
    uv run python scenarios/_generator/generate.py pool-exhaustion
"""

from __future__ import annotations

import sys
from pathlib import Path

from common import Scenario, at, baseline, checkout_traces, derive_checkout, ramp

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = {}


def scenario(fn):
    SCENARIOS[fn.__name__.replace("_", "-")] = fn
    return fn


# -----------------------------------------------------------------------------
# 1. Connection pool exhaustion: symptom in checkout-api, cause in payments-svc
# -----------------------------------------------------------------------------

POOL_LEAK_DIFF = '''\
--- a/sandbox/services/payments_svc/app.py
+++ b/sandbox/services/payments_svc/app.py
@@ def charge(req: ChargeRequest):
     session = SessionLocal()
     result = gateway.charge(req.card_token, req.amount_cents)
     if result.declined:
+        # FRAUD-231: record declined attempts so fraud scoring can see velocity
+        session.execute(insert(declined_attempts).values(user_id=req.user_id, code=result.code))
+        log.info("declined charge attempt recorded for fraud scoring")
         raise HTTPException(status_code=402, detail=result.code)
     session.execute(insert(payments).values(user_id=req.user_id, amount_cents=req.amount_cents))
     session.commit()
     session.close()
'''


@scenario
def pool_exhaustion() -> Path:
    sc = Scenario("pool-exhaustion", now="2026-10-14T09:58:00Z", seed=101)
    baseline(sc)
    deploy = at(sc.now, "09:38")
    rng, n = sc.rng, sc.noise

    sc.change(deploy, "deploy", "payments-svc", "pay-5531", "dev.patel",
              "Record declined charge attempts for fraud scoring (FRAUD-231)",
              commit="a41f9e2", version="2026.10.14-1", diff=POOL_LEAK_DIFF)
    sc.change(at(sc.now, "06:10"), "deploy", "inventory-svc", "inv-1902", "sam.kim",
              "Add OpenTelemetry attributes to reserve spans", commit="77c02bd")

    # Each declined charge leaks a connection; GC reclaims a few. Saturates ~09:43.
    leaked = lambda t: 18 * ramp(t, deploy, 330)  # noqa: E731
    sat = lambda t: ramp(t, deploy + 280, 90)  # noqa: E731  how saturated the pool is (0..1)
    sc.modify("payments-svc", "db_pool_in_use", lambda t, v: min(20, v + round(leaked(t))))
    sc.modify("payments-svc", "db_pool_wait_ms", lambda t, v: v + sat(t) * 780 * n(0.12))
    sc.modify("payments-svc", "latency_p50_ms", lambda t, v: v + sat(t) * 340 * n(0.1))
    sc.modify("payments-svc", "latency_p99_ms", lambda t, v: v + sat(t) * 820 * n(0.05))
    sc.modify("payments-svc", "error_rate", lambda t, v: v + sat(t) * 0.17 * n(0.15))
    sc.modify("payments-svc", "request_rate", lambda t, v: v * (1 + 0.15 * sat(t)))  # checkout retries
    sc.modify("postgres-payments", "active_connections", lambda t, v: max(v, 3 + min(20, v - 3 + round(leaked(t)))))
    sc.modify("postgres-payments", "cpu_util", lambda t, v: v * (1 - 0.35 * sat(t)))
    derive_checkout(sc)

    sc.log("payments-svc", deploy + 4, "info", "payments-svc starting", version="2026.10.14-1", commit="a41f9e2",
           db_pool_size=20, db_max_overflow=0)
    for t in sc.poisson_times(deploy, sc.t_end, 0.25):
        sc.log("payments-svc", t, "info", "declined charge attempt recorded for fraud scoring", route="/charge")
    for t in sc.poisson_times(deploy + 120, sc.t_end, 1.2):
        sc.log("payments-svc", t, "warning",
               "The garbage collector is trying to clean up non-checked-in connection "
               f"<AdaptedConnection <asyncpg.connection.Connection object at 0x7f{rng.randint(10**9, 10**10 - 1):x}>>, "
               "which will be dropped, as it cannot be safely terminated. Please ensure that SQLAlchemy pooled "
               "connections are returned to the pool explicitly",
               logger="sqlalchemy.pool.impl.AsyncAdaptedQueuePool")
    for t in sc.poisson_times(deploy + 260, sc.t_end, 40):
        if rng.random() < sat(t):
            sc.log("payments-svc", t, "error",
                   "QueuePool limit of size 20 overflow 0 reached, connection timed out, timeout 1.00",
                   route="/charge", status=503, exc_type="sqlalchemy.exc.TimeoutError")
    for t in sc.poisson_times(deploy + 270, sc.t_end, 35):
        if rng.random() < sat(t):
            sc.log("checkout-api", t, "error", "upstream payments-svc returned 503", route="/checkout",
                   status=502, upstream="payments-svc", upstream_status=503,
                   upstream_duration_ms=round(1000 + rng.uniform(5, 60)))

    def pool_wait(sc, t, trace):
        if rng.random() < sat(t) * 0.8:
            pay = trace["spans"][2]
            acquire = pay["children"][0]
            acquire["duration_ms"] = round(1000 + rng.uniform(1, 15), 1)
            acquire["status"] = "error"
            acquire["error"] = "sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 0 reached"
            pay["children"] = [acquire]
            pay["duration_ms"] = round(acquire["duration_ms"] + 4, 1)
            pay["status"] = "error"
            pay["http_status"] = 503

    checkout_traces(sc, every_s=60, extra=pool_wait)

    readme = """
# Scenario: pool-exhaustion

## Page received by the on-call engineer (storefront team)

> **[FIRING] SEV2 checkout-api: SLO burn rate high (availability)**
> `checkout-api` 5xx ratio 14.8% over the last 10m (SLO: 99.9% non-5xx). 14.4x burn rate.
> Fired 2026-10-14 09:51 UTC · runbook: checkout-latency · owner: @storefront-oncall

The engineer starts the investigation at 09:58 UTC.
"""
    ground_truth = """
expected_verdict: root_cause
root_cause:
  service: payments-svc
  accept_categories: [code_regression, resource_exhaustion]
  commit: a41f9e2
  summary: >
    Deploy a41f9e2 of payments-svc (09:38) added an early-return path on card decline that
    never closes the DB session, leaking pooled connections. The 20-connection pool saturated
    ~09:43; requests time out acquiring a connection (1s) and return 503, which checkout-api
    surfaces as 502. postgres-payments itself is healthy (23/200 connections).
fix_allowed: true
required_evidence:
  - {tool: query_metrics, service: payments-svc, metric: db_pool_in_use}
  - {tool: search_logs, service: payments-svc}
  - {tool: recent_changes}
"""
    return sc.write(ROOT, readme, ground_truth)


def main(names: list[str]) -> None:
    for name in names or list(SCENARIOS):
        out = SCENARIOS[name]()
        print(f"wrote {out.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main(sys.argv[1:])
