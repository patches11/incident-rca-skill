"""Generate every fixture scenario. Seeded and reproducible:

    uv run python scenarios/_generator/generate.py            # all scenarios
    uv run python scenarios/_generator/generate.py pool-exhaustion
"""

from __future__ import annotations

import sys
from pathlib import Path

from common import Scenario, at, baseline, checkout_traces, derive_checkout, iso_ms, ramp

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


# -----------------------------------------------------------------------------
# 2. Bad deploy: N+1 queries in inventory-svc; an unrelated payments deploy nearby
# -----------------------------------------------------------------------------

N_PLUS_ONE_DIFF = '''\
--- a/sandbox/services/inventory_svc/app.py
+++ b/sandbox/services/inventory_svc/app.py
@@ def reserve(req: ReserveRequest):
-    rows = session.execute(
-        select(stock).where(stock.c.sku.in_([i.sku for i in req.items]))
-    ).all()
-    by_sku = {r.sku: r for r in rows}
+    # CAT-877: return per-item warehouse availability to the storefront
+    by_sku = {}
+    for item in req.items:
+        row = session.execute(select(stock).where(stock.c.sku == item.sku)).one()
+        row.warehouses = session.execute(
+            select(warehouse_stock).where(warehouse_stock.c.sku == item.sku)
+        ).all()
+        by_sku[item.sku] = row
     for item in req.items:
         if by_sku[item.sku].available < item.qty:
             raise HTTPException(409, f"insufficient stock for {item.sku}")
'''


@scenario
def bad_deploy() -> Path:
    sc = Scenario("bad-deploy", now="2026-10-15T15:20:00Z", seed=202)
    baseline(sc)
    rng, n = sc.rng, sc.noise
    deploy = at(sc.now, "14:52")
    sc.change(at(sc.now, "14:40"), "deploy", "payments-svc", "pay-5540", "lee.wong",
              "Bump httpx 0.27.2 -> 0.28.1", commit="5f0e8d1", version="2026.10.15-2",
              diff="-httpx==0.27.2\n+httpx==0.28.1")
    sc.change(deploy, "deploy", "inventory-svc", "inv-1931", "maria.ortiz",
              "Show per-item warehouse availability in reserve response (CAT-877)",
              commit="c9d3f17", version="2026.10.15-1", diff=N_PLUS_ONE_DIFF)

    on = lambda t: ramp(t, deploy + 60, 60)  # noqa: E731  new pods take traffic over a minute
    warm = lambda t: 1.0 if deploy + 30 <= t < deploy + 210 else 0.0  # noqa: E731  normal cold cache
    sc.modify("inventory-svc", "db_queries_per_request", lambda t, v: v + on(t) * 11.6 * n(0.06))
    sc.modify("inventory-svc", "latency_p50_ms", lambda t, v: v + on(t) * 115 * n(0.08))
    sc.modify("inventory-svc", "latency_p99_ms", lambda t, v: v + on(t) * 1050 * n(0.08) + warm(t) * 90)
    sc.modify("inventory-svc", "cache_hit_ratio", lambda t, v: 0.31 * n(0.05) if warm(t) else v)
    sc.modify("inventory-svc", "db_pool_in_use", lambda t, v: min(30, v + round(on(t) * 14 * n(0.15))))
    sc.modify("inventory-svc", "db_pool_wait_ms", lambda t, v: v + on(t) * 6 * n(0.4))
    sc.modify("inventory-svc", "cpu_util", lambda t, v: min(1, v + on(t) * 0.12))
    sc.modify("inventory-svc", "error_rate", lambda t, v: v + on(t) * 0.002 * n(0.4))
    sc.modify("postgres-inventory", "cpu_util", lambda t, v: min(0.97, v + on(t) * 0.5 * n(0.05)))
    sc.modify("postgres-inventory", "slow_queries_rate", lambda t, v: v + on(t) * 7 * n(0.2))
    sc.modify("postgres-inventory", "active_connections", lambda t, v: v + round(on(t) * 14 * n(0.15)))
    derive_checkout(sc)

    sc.log("inventory-svc", deploy + 6, "info", "inventory-svc starting", version="2026.10.15-1", commit="c9d3f17")
    sc.log("payments-svc", at(sc.now, "14:40") + 5, "info", "payments-svc starting", version="2026.10.15-2",
           commit="5f0e8d1", db_pool_size=20, db_max_overflow=0)
    for t in sc.poisson_times(deploy + 60, sc.t_end, 30):
        items = rng.randint(1, 9)
        ms = round((80 + 95 * items) * n(0.2))
        sc.log("inventory-svc", t, "warning", f"slow request: POST /reserve took {ms}ms",
               route="/reserve", items=items, db_queries=2 * items + 1, duration_ms=ms)
    for t in sc.poisson_times(deploy + 60, sc.t_end, 12):
        sc.log("postgres-inventory", t, "warning",
               f"duration: {round(rng.uniform(100, 400), 1)} ms  statement: SELECT * FROM warehouse_stock WHERE sku = $1",
               component="postgres")
    for t in sc.poisson_times(deploy + 30, deploy + 210, 6):
        sc.log("inventory-svc", t, "warning", "cache miss storm: rebuilding stock cache", key_prefix="stock:")

    def n_plus_one(sc, t, trace):
        inv = trace["spans"][1]
        if on(t) > 0.5:
            items = rng.randint(1, 9)
            kids = []
            for _ in range(items):
                kids.append({"service": "postgres-inventory", "op": "SELECT stock WHERE sku = $1", "duration_ms": round(rng.uniform(3, 9), 1)})
                kids.append({"service": "postgres-inventory", "op": "SELECT warehouse_stock WHERE sku = $1", "duration_ms": round(rng.uniform(6, 40), 1)})
            inv["children"] = kids
            inv["duration_ms"] = round(sum(k["duration_ms"] for k in kids) + 8, 1)
            inv["attrs"] = {"items": items, "db.query_count": len(kids)}

    checkout_traces(sc, every_s=60, extra=n_plus_one)

    readme = """
# Scenario: bad-deploy

## Page received by the on-call engineer (storefront team)

> **[FIRING] SEV3 checkout-api: latency SLO burn rate high**
> `checkout-api` p99 latency 1.01s over the last 15m (SLO: 99% of POST /checkout < 800ms). 6.1x burn rate.
> Fired 2026-10-15 15:11 UTC · runbook: checkout-latency · owner: @storefront-oncall
"""
    ground_truth = """
expected_verdict: root_cause
root_cause:
  service: inventory-svc
  accept_categories: [code_regression]
  commit: c9d3f17
  summary: >
    Deploy c9d3f17 of inventory-svc (14:52) replaced a single batched stock query with two queries
    per cart item (N+1). Queries per request went ~2 -> ~14, postgres-inventory CPU and slow queries
    rose, inventory p99 ~1.1s, and checkout-api p99 breached its SLO. The payments-svc httpx bump at
    14:40 and the post-deploy cache warm-up are red herrings.
fix_allowed: true
required_evidence:
  - {tool: recent_changes}
  - {tool: query_metrics, service: inventory-svc}
"""
    return sc.write(ROOT, readme, ground_truth)


# -----------------------------------------------------------------------------
# 3. Red herring: loud notification-worker errors + a nearby deploy; quiet vendor degradation
# -----------------------------------------------------------------------------

@scenario
def red_herring() -> Path:
    sc = Scenario("red-herring", now="2026-10-16T10:12:00Z", seed=303)
    baseline(sc)
    rng, n = sc.rng, sc.noise
    t0 = at(sc.now, "10:01")
    smtp = lambda t: ramp(t, at(sc.now, "08:02"), 600)  # noqa: E731  started 2h before the page
    gw = lambda t: ramp(t, t0, 90)  # noqa: E731

    # Loud and irrelevant: SMTP throttling (pre-existing) and a deploy 11 minutes before onset.
    sc.change(at(sc.now, "09:50"), "deploy", "notification-worker", "nw-2231", "ana.lopez",
              "Retry SMTP 421 with exponential backoff (GROW-388)", commit="8d21b4e",
              diff="-    raise\n+    raise Retry(countdown=min(300, 2 ** attempt * 5))")
    sc.modify("notification-worker", "error_rate", lambda t, v: v + smtp(t) * 0.16 * n(0.15))
    sc.modify("notification-worker", "queue_depth", lambda t, v: v + smtp(t) * 900 * n(0.1))
    for t in sc.poisson_times(at(sc.now, "08:02"), sc.t_end, 90):
        if rng.random() < smtp(t):
            sc.log("notification-worker", t, "error", "SMTP send failed: 421 4.7.0 Try again later",
                   provider="email-provider", attempt=rng.randint(1, 5), template="order_confirmed")
    sc.log("notification-worker", at(sc.now, "09:50") + 8, "info", "notification-worker starting", commit="8d21b4e")

    # Quiet and real: card-gateway slows down and starts failing at 10:01.
    sc.modify("payments-svc", "upstream_latency_p99_ms", lambda t, v: v + gw(t) * 2350 * n(0.06))
    sc.modify("payments-svc", "upstream_error_rate", lambda t, v: v + gw(t) * 0.11 * n(0.15))
    sc.modify("payments-svc", "latency_p50_ms", lambda t, v: v + gw(t) * 900 * n(0.1))
    sc.modify("payments-svc", "latency_p99_ms", lambda t, v: v + gw(t) * 2450 * n(0.05))
    sc.modify("payments-svc", "error_rate", lambda t, v: v + gw(t) * 0.11 * n(0.15))
    sc.modify("payments-svc", "db_pool_in_use", lambda t, v: min(20, v + round(gw(t) * 9 * n(0.2))))
    sc.modify("payments-svc", "db_pool_wait_ms", lambda t, v: v + gw(t) * 3 * n(0.5))
    derive_checkout(sc)

    for t in sc.poisson_times(t0 + 20, sc.t_end, 14):
        if rng.random() < gw(t):
            if rng.random() < 0.6:
                sc.log("payments-svc", t, "error", "card-gateway request timed out after 2500ms",
                       route="/charge", status=504, upstream="card-gateway", exc_type="httpx.ReadTimeout")
            else:
                sc.log("payments-svc", t, "error", "card-gateway returned 503 Service Unavailable",
                       route="/charge", status=502, upstream="card-gateway", upstream_status=503)
    for t in sc.poisson_times(t0 + 30, sc.t_end, 16):
        if rng.random() < gw(t):
            sc.log("checkout-api", t, "error", "timeout calling payments-svc after 2000ms", route="/checkout",
                   status=504, upstream="payments-svc")

    def slow_gateway(sc, t, trace):
        pay = trace["spans"][2]
        if rng.random() < gw(t) * 0.7:
            gwspan = pay["children"][2]
            gwspan["duration_ms"] = round(rng.uniform(1800, 2500), 1)
            if gwspan["duration_ms"] > 2000 or rng.random() < 0.3:
                gwspan["status"] = "error"
                gwspan["error"] = rng.choice(["httpx.ReadTimeout", "HTTP 503 from card-gateway"])
                pay["status"] = "error"
            pay["duration_ms"] = round(sum(c["duration_ms"] for c in pay["children"]) + 5, 1)

    checkout_traces(sc, every_s=60, extra=slow_gateway)

    readme = """
# Scenario: red-herring

## Page received by the on-call engineer (storefront team)

> **[FIRING] SEV2 checkout-api: SLO burn rate high (availability)**
> `checkout-api` 5xx ratio 26.4% over the last 10m (SLO: 99.9% non-5xx). 264x burn rate.
> Fired 2026-10-16 10:07 UTC · runbook: checkout-latency · owner: @storefront-oncall
"""
    ground_truth = """
expected_verdict: root_cause
root_cause:
  service: card-gateway
  accept_categories: [dependency_failure]
  summary: >
    The external card-gateway began timing out / returning 503 at 10:01. payments-svc upstream p99
    went ~180ms -> ~2.5s and upstream errors ~11%, so /charge failed and checkout-api returned 5xx.
    The loud notification-worker SMTP 421 errors started two hours earlier, are off the checkout
    path, and the notification-worker deploy at 09:50 is unrelated.
fix_allowed: false
required_evidence:
  - {tool: search_logs, service: payments-svc}
  - {tool: query_metrics, service: payments-svc}
"""
    return sc.write(ROOT, readme, ground_truth)


# -----------------------------------------------------------------------------
# 4. Insufficient data: payments-svc goes dark (metrics + logs gap) at onset
# -----------------------------------------------------------------------------

@scenario
def insufficient_data() -> Path:
    sc = Scenario("insufficient-data", now="2026-10-17T09:47:00Z", seed=404)
    baseline(sc)
    rng, n = sc.rng, sc.noise
    dark = at(sc.now, "09:29")
    derive_checkout(sc)
    sc.drop_metrics("payments-svc", dark)
    gone = lambda t: ramp(t, dark + 60, 60)  # noqa: E731
    sc.modify("checkout-api", "error_rate", lambda t, v: max(v, gone(t) * 0.94 * n(0.02)))
    sc.modify("checkout-api", "latency_p50_ms", lambda t, v: v * (1 - 0.6 * gone(t)) + gone(t) * 30)
    sc.modify("checkout-api", "latency_p99_ms", lambda t, v: v + gone(t) * 1500 * n(0.1))
    sc.modify("postgres-payments", "active_connections", lambda t, v: 3 if t > dark + 45 else v)
    sc.modify("postgres-payments", "cpu_util", lambda t, v: v * (0.35 if t > dark + 45 else 1))

    sc.logs["payments-svc"] = [r for r in sc.logs["payments-svc"] if r["ts"] < iso_ms(dark)]
    for t in sc.poisson_times(dark + 60, sc.t_end, 40):
        if rng.random() < 0.55:
            sc.log("checkout-api", t, "error", "upstream connect error: connection refused (payments-svc:8080)",
                   route="/checkout", status=502, upstream="payments-svc")
        else:
            sc.log("checkout-api", t, "error", "upstream connect timeout after 2000ms (payments-svc:8080)",
                   route="/checkout", status=504, upstream="payments-svc")

    def payments_dark(sc, t, trace):
        if t > dark + 60:
            trace["spans"][2] = {"service": "checkout-api", "op": "HTTP POST payments-svc/charge",
                                 "duration_ms": rng.choice([round(rng.uniform(1, 4), 1), 2000.0]),
                                 "status": "error", "error": "connect failed: payments-svc:8080"}

    checkout_traces(sc, every_s=60, extra=payments_dark)
    sc.traces = [tr for tr in sc.traces if not (tr["start"] >= iso_ms(dark) and
                 any(s.get("service") == "payments-svc" and s.get("op") == "POST /charge" for s in tr["spans"]))]

    readme = """
# Scenario: insufficient-data

## Page received by the on-call engineer (storefront team)

> **[FIRING] SEV1 checkout-api: SLO burn rate high (availability)**
> `checkout-api` 5xx ratio 93% over the last 10m (SLO: 99.9% non-5xx). 930x burn rate.
> Fired 2026-10-17 09:33 UTC · runbook: checkout-latency · owner: @storefront-oncall
"""
    ground_truth = """
expected_verdict: abstain
summary: >
  payments-svc stopped emitting metrics and logs at 09:29 and checkout-api gets connection
  refused/timeouts from it. Whether payments-svc crashed, its hosts died, or the network
  partitioned cannot be determined from the available telemetry (no payments-svc logs, metrics,
  deploy or infra events for the window). Correct output: abstain, name the missing data
  (host/node/orchestrator events, payments-svc stdout from the hosts, scrape target health),
  and still propose safe mitigations.
fix_allowed: false
required_evidence: []
"""
    return sc.write(ROOT, readme, ground_truth)


# -----------------------------------------------------------------------------
# 5. Config/infra, no code change: expired mTLS client certificate
# -----------------------------------------------------------------------------

@scenario
def cert_expiry() -> Path:
    sc = Scenario("cert-expiry", now="2026-10-18T09:24:00Z", seed=505)
    baseline(sc)
    rng, n = sc.rng, sc.noise
    exp = at(sc.now, "09:00")
    bad = lambda t: ramp(t, exp, 240)  # noqa: E731  keep-alive connections recycle over ~4 min
    sc.change(at(sc.now, "08:20"), "deploy", "payments-svc", "pay-5562", "dev.patel",
              "Bump structlog 24.1 -> 24.4; tidy log field names", commit="5be81aa", version="2026.10.18-1",
              diff="-structlog==24.1.0\n+structlog==24.4.0\n-    log.bind(req=req_id)\n+    log.bind(request_id=req_id)")
    sc.log("payments-svc", at(sc.now, "08:20") + 5, "info", "payments-svc starting", version="2026.10.18-1",
           commit="5be81aa", db_pool_size=20, db_max_overflow=0)
    for hh in ("03:00", "06:00", "08:20"):
        sc.log("payments-svc", at(sc.now, hh) + 7, "warning",
               "mTLS client certificate for card-gateway expires soon",
               cert_subject="CN=payments-svc.acme.internal", not_after="2026-10-18T09:00:00Z", days_left=0)
    sc.modify("payments-svc", "upstream_error_rate", lambda t, v: v + bad(t) * 0.97)
    sc.modify("payments-svc", "error_rate", lambda t, v: v + bad(t) * 0.95 * n(0.01))
    sc.modify("payments-svc", "latency_p50_ms", lambda t, v: v * (1 - 0.6 * bad(t)))
    sc.modify("payments-svc", "upstream_latency_p99_ms", lambda t, v: v * (1 - 0.5 * bad(t)))
    derive_checkout(sc)

    for t in sc.poisson_times(exp, sc.t_end, 60):
        if rng.random() < bad(t):
            sc.log("payments-svc", t, "error",
                   "card-gateway request failed: [SSL: TLSV1_ALERT_CERTIFICATE_EXPIRED] tlsv1 alert certificate expired (_ssl.c:2580)",
                   route="/charge", status=502, upstream="card-gateway", exc_type="ssl.SSLError")
    for t in sc.poisson_times(exp + 10, sc.t_end, 50):
        if rng.random() < bad(t):
            sc.log("checkout-api", t, "error", "upstream payments-svc returned 502", route="/checkout",
                   status=502, upstream="payments-svc", upstream_status=502)

    def tls_fail(sc, t, trace):
        if rng.random() < bad(t):
            pay = trace["spans"][2]
            gwspan = pay["children"][2]
            gwspan.update(duration_ms=round(rng.uniform(4, 12), 1), status="error",
                          error="ssl.SSLError: TLSV1_ALERT_CERTIFICATE_EXPIRED")
            pay["status"] = "error"
            pay["http_status"] = 502
            pay["duration_ms"] = round(sum(c["duration_ms"] for c in pay["children"]) + 3, 1)

    checkout_traces(sc, every_s=60, extra=tls_fail)

    readme = """
# Scenario: cert-expiry

## Page received by the on-call engineer (storefront team)

> **[FIRING] SEV1 checkout-api: SLO burn rate high (availability)**
> `checkout-api` 5xx ratio 91% over the last 10m (SLO: 99.9% non-5xx). 910x burn rate.
> Fired 2026-10-18 09:06 UTC · runbook: checkout-latency · owner: @storefront-oncall
"""
    ground_truth = """
expected_verdict: root_cause
root_cause:
  service: payments-svc
  accept_categories: [certificate_or_credential]
  summary: >
    payments-svc's mTLS client certificate for card-gateway (CN=payments-svc.acme.internal) expired
    at 09:00:00Z; the gateway rejects the handshake (TLSV1_ALERT_CERTIFICATE_EXPIRED), so ~95% of
    charges fail and checkout-api returns 502. The 08:20 structlog deploy is unrelated (40 healthy
    minutes after it). Fix is certificate rotation by a human - no code change.
fix_allowed: false
required_evidence:
  - {tool: search_logs, service: payments-svc}
"""
    return sc.write(ROOT, readme, ground_truth)


# -----------------------------------------------------------------------------
# 6. Novel failure (held out of the taxonomy): memory leak that only bites under
#    a new partner's bulk traffic, causing staggered OOM kills.
# -----------------------------------------------------------------------------

MEMO_DIFF = '''\
--- a/sandbox/services/inventory_svc/pricing.py
+++ b/sandbox/services/inventory_svc/pricing.py
@@
+_quote_cache: dict[tuple, Quote] = {}
+
 def bulk_quote(client_id: str, items: list[Item]) -> Quote:
-    return _compute_quote(items)
+    # CAT-902: partners re-request the same bulk quote repeatedly; memoize it
+    key = (client_id, tuple(sorted((i.sku, i.qty) for i in items)))
+    if key not in _quote_cache:
+        _quote_cache[key] = _compute_quote(items)
+    return _quote_cache[key]
'''


@scenario
def novel_failure() -> Path:
    sc = Scenario("novel-failure", now="2026-10-19T13:40:00Z", seed=606)
    baseline(sc)
    rng, n = sc.rng, sc.noise
    trial, full = at(sc.now, "08:00"), at(sc.now, "11:50")
    sc.change(sc.now.timestamp() - 3 * 86400 + 3600, "deploy", "inventory-svc", "inv-1890", "omar.haddad",
              "Memoize bulk quotes for partner API (CAT-902)", commit="e4b7a20", version="2026.10.16-1",
              diff=MEMO_DIFF)
    sc.change(at(sc.now, "11:10"), "config", "payments-svc", "cfg-8840", "lee.wong",
              "Lower card-gateway connect timeout 1500ms -> 1200ms",
              diff="-CARD_GATEWAY_CONNECT_TIMEOUT_MS=1500\n+CARD_GATEWAY_CONNECT_TIMEOUT_MS=1200")
    # Pre-existing SMTP noise (the populated overlay explains it; the empty one doesn't).
    smtp = lambda t: ramp(t, at(sc.now, "08:05"), 900)  # noqa: E731
    sc.modify("notification-worker", "error_rate", lambda t, v: v + smtp(t) * 0.12 * n(0.2))
    for t in sc.poisson_times(at(sc.now, "08:05"), sc.t_end, 60):
        if rng.random() < smtp(t):
            sc.log("notification-worker", t, "error", "SMTP send failed: 421 4.7.0 Try again later",
                   provider="email-provider", attempt=rng.randint(1, 5))

    def partner_rpm(t):  # partner-bulk requests per minute
        if t < trial:
            return 0.0
        return 6.0 if t < full else 70.0

    # Three pods; each leaks in proportion to the partner traffic it serves; OOM-killed at 1024MB.
    # Uneven load balancing (long-lived partner connections) staggers the OOMs.
    pods = [{"name": f"inventory-svc-7c9f{c}", "mem": m, "share": w, "restarts": 0}
            for c, m, w in (("a", 418, 0.24), ("b", 455, 0.33), ("c", 497, 0.43))]
    mem_max, restarts, oom_events = [], [], []
    for t in sc.times:
        for p in pods:
            p["mem"] += partner_rpm(t) * p["share"] * (0.5 / 60) * 30 * rng.uniform(0.85, 1.15) + rng.gauss(0, 0.4)
            if p["mem"] >= 1024:
                oom_events.append((t, p["name"]))
                p["restarts"] += 1
                p["mem"] = 405 + rng.uniform(0, 15)
        mem_max.append(max(p["mem"] for p in pods))
        restarts.append(sum(p["restarts"] for p in pods))
    sc.metrics["inventory-svc"]["memory_rss_mb"] = mem_max
    sc.metrics["inventory-svc"]["container_restarts"] = restarts
    burst = lambda t: any(0 <= t - e < 45 for e, _ in oom_events)  # noqa: E731
    sc.modify("inventory-svc", "error_rate", lambda t, v: v + (0.31 * n(0.1) if burst(t) else 0))
    sc.modify("inventory-svc", "latency_p99_ms", lambda t, v: v + (1400 if burst(t) else 0) + partner_rpm(t) * 1.5)
    sc.modify("inventory-svc", "request_rate", lambda t, v: v + partner_rpm(t) / 60)
    sc.modify("inventory-svc", "cpu_util", lambda t, v: min(1, v + partner_rpm(t) * 0.002))
    derive_checkout(sc)

    for t in sc.poisson_times(trial, sc.t_end, 1):
        if rng.random() < partner_rpm(t) / 70:
            sc.log("inventory-svc", t, "info", "bulk quote ok", route="/quote/bulk", status=200,
                   client="partner-bulk", items=rng.randint(150, 400), duration_ms=round(rng.uniform(300, 900)))
    for t, pod in oom_events:
        sc.log("inventory-svc", t, "error", f"[gunicorn] Worker (pid:{rng.randint(20, 90)}) was sent SIGKILL! Perhaps out of memory?",
               pod=pod)
        sc.log("inventory-svc", t + 9, "info", "inventory-svc starting", version="2026.10.16-1", commit="e4b7a20", pod=pod)
        for k in range(rng.randint(25, 40)):
            sc.log("checkout-api", t + rng.uniform(0, 40), "error", "upstream inventory-svc connection reset by peer",
                   route="/checkout", status=502, upstream="inventory-svc")

    checkout_traces(sc, every_s=60)

    readme = """
# Scenario: novel-failure

## Page received by the on-call engineer (storefront team)

> **[FIRING] SEV3 checkout-api: error budget burn (slow burn, 1h window)**
> `checkout-api` 5xx in short bursts over the last hour; 1h error budget burn 3.2x.
> Fired 2026-10-19 13:31 UTC · runbook: checkout-latency · owner: @storefront-oncall
"""
    ground_truth = """
expected_verdict: root_cause
root_cause:
  service: inventory-svc
  accept_categories: [resource_exhaustion, code_regression]
  summary: >
    inventory-svc pods leak memory in proportion to partner-bulk bulk-quote traffic (unbounded
    memo cache from e4b7a20, deployed 3 days earlier but harmless until the partner ramped to full
    volume at 11:50). Pods hit the 1024MB limit and are OOM-killed in turn; each kill resets
    in-flight connections and checkout-api returns 502 bursts. Failure class is deliberately absent
    from the taxonomy and overlay.
fix_allowed: true
eval_overlays: [default, empty]
required_evidence:
  - {tool: query_metrics, service: inventory-svc, metric: memory_rss_mb}
  - {tool: search_logs, service: inventory-svc}
"""
    return sc.write(ROOT, readme, ground_truth)


def main(names: list[str]) -> None:
    for name in names or list(SCENARIOS):
        out = SCENARIOS[name]()
        print(f"wrote {out.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main(sys.argv[1:])
