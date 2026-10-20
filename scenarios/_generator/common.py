"""Building blocks for synthetic-but-realistic incident fixtures.

Every scenario starts from the same healthy baseline (diurnal traffic, noise,
background low-level errors, PII in a few log lines so redaction is exercised),
then applies scenario-specific effects. All randomness comes from one seeded RNG
so `make fixtures` is reproducible byte-for-byte.
"""

from __future__ import annotations

import json
import math
import random
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

STEP = 30  # seconds between metric samples

LEAF_SERVICES = ["payments-svc", "inventory-svc", "notification-worker"]
DATASTORES = ["postgres-payments", "postgres-inventory", "redis-cache"]

# Fake PII/secrets planted in logs. The eval asserts none of these reach a report.
PII_CANARIES = [
    "jane.doe@example.com",
    "4111 1111 1111 1111",
    "sk_live_9f8e7d6c5b4a3f2e1d0c",
    "AKIAIOSFODNN7EXAMPLE",
]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_ms(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def at(now: datetime, hh_mm: str) -> float:
    """Epoch seconds for HH:MM on the same day as `now`."""
    h, m = map(int, hh_mm.split(":"))
    return now.replace(hour=h, minute=m, second=0).timestamp()


def ramp(t: float, start: float, duration: float) -> float:
    """0 before start, rising linearly to 1 over duration."""
    if t < start:
        return 0.0
    return min(1.0, (t - start) / duration) if duration > 0 else 1.0


class Scenario:
    def __init__(self, name: str, now: str, seed: int, hours: float = 8):
        self.name = name
        self.now = datetime.fromisoformat(now.replace("Z", "+00:00"))
        self.rng = random.Random(seed)
        self.t_end = self.now.timestamp() + 5 * 60  # a few minutes past the page
        self.t_start = self.now.timestamp() - hours * 3600
        self.times = [self.t_start + i * STEP for i in range(int((self.t_end - self.t_start) // STEP) + 1)]
        self.metrics: dict[str, dict[str, list[float | None]]] = {}
        self.logs: dict[str, list[dict]] = {}
        self.traces: list[dict] = []
        self.changes: list[dict] = []
        self._trace_seq = 0

    # -- primitives -----------------------------------------------------------
    def traffic(self, t: float) -> float:
        """Diurnal multiplier: trough ~0.35 at 03:00 UTC, peak ~1.0 at 15:00 UTC."""
        hour = (t % 86400) / 3600
        return 0.675 + 0.325 * math.sin((hour - 9) / 24 * 2 * math.pi)

    def noise(self, sigma: float = 0.05) -> float:
        return 1 + self.rng.gauss(0, sigma)

    def series(self, service: str, metric: str, fn) -> None:
        self.metrics.setdefault(service, {})[metric] = [fn(t) for t in self.times]

    def modify(self, service: str, metric: str, fn) -> None:
        """fn(t, value) -> new value."""
        s = self.metrics[service][metric]
        for i, t in enumerate(self.times):
            if s[i] is not None:
                s[i] = fn(t, s[i])

    def value(self, service: str, metric: str, t: float) -> float:
        idx = max(0, min(len(self.times) - 1, int((t - self.t_start) // STEP)))
        v = self.metrics[service][metric][idx]
        return v if v is not None else 0.0

    def drop_metrics(self, service: str, after: float) -> None:
        """Simulate a scrape gap: no samples after `after`."""
        for s in self.metrics[service].values():
            for i, t in enumerate(self.times):
                if t >= after:
                    s[i] = None

    def log(self, service: str, t: float, level: str, msg: str, **fields) -> None:
        self.logs.setdefault(service, []).append(
            {"ts": iso_ms(t), "level": level, "service": service, "msg": msg, **fields}
        )

    def poisson_times(self, start: float, end: float, per_minute: float):
        """Event timestamps from a Poisson process."""
        t = start
        if per_minute <= 0:
            return
        while True:
            t += self.rng.expovariate(per_minute / 60)
            if t >= end:
                return
            yield t

    def change(self, t: float, type_: str, service: str, id_: str, author: str, summary: str, **extra) -> None:
        self.changes.append(
            {"ts": iso(t), "type": type_, "service": service, "id": id_, "author": author, "summary": summary, **extra}
        )

    def trace_id(self) -> str:
        self._trace_seq += 1
        return f"{self.rng.getrandbits(64):016x}"

    # -- output ---------------------------------------------------------------
    def write(self, root: Path, readme: str, ground_truth: str) -> Path:
        out = root / self.name
        if out.exists():
            shutil.rmtree(out)
        (out / "metrics").mkdir(parents=True)
        (out / "logs").mkdir()
        (out / "traces").mkdir()
        (out / "scenario.json").write_text(json.dumps({"now": iso(self.now.timestamp())}, indent=2) + "\n")
        for service, metrics in self.metrics.items():
            data = {
                m: [[int(t), round(v, 4)] for t, v in zip(self.times, vals) if v is not None]
                for m, vals in metrics.items()
            }
            (out / "metrics" / f"{service}.json").write_text(json.dumps(data, separators=(",", ":")) + "\n")
        for service, records in self.logs.items():
            records.sort(key=lambda r: r["ts"])
            (out / "logs" / f"{service}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
        self.traces.sort(key=lambda tr: tr["start"])
        (out / "traces" / "traces.jsonl").write_text("".join(json.dumps(tr) + "\n" for tr in self.traces))
        self.changes.sort(key=lambda c: c["ts"])
        (out / "changes.json").write_text(json.dumps(self.changes, indent=2) + "\n")
        (out / "README.md").write_text(readme.strip() + "\n")
        (out / "ground_truth.yaml").write_text(ground_truth.strip() + "\n")
        return out


# -----------------------------------------------------------------------------
# Healthy baseline shared by every scenario
# -----------------------------------------------------------------------------

def baseline(sc: Scenario) -> None:
    rng, tr, n = sc.rng, sc.traffic, sc.noise

    # payments-svc
    sc.series("payments-svc", "request_rate", lambda t: 22 * tr(t) * n(0.06))
    sc.series("payments-svc", "error_rate", lambda t: max(0.0, 0.0015 * n(0.5)))
    sc.series("payments-svc", "latency_p50_ms", lambda t: 85 * n(0.05))
    sc.series("payments-svc", "latency_p99_ms", lambda t: 240 * n(0.1))
    sc.series("payments-svc", "cpu_util", lambda t: 0.18 + 0.2 * tr(t) * n(0.08))
    sc.series("payments-svc", "memory_rss_mb", lambda t: 310 * n(0.01))
    sc.series("payments-svc", "db_pool_max", lambda t: 20)
    sc.series("payments-svc", "db_pool_in_use", lambda t: max(1, round(6 * tr(t) * n(0.25))))
    sc.series("payments-svc", "db_pool_wait_ms", lambda t: max(0.0, 0.4 * n(0.6)))
    sc.series("payments-svc", "upstream_latency_p99_ms", lambda t: 180 * n(0.12))
    sc.series("payments-svc", "upstream_error_rate", lambda t: max(0.0, 0.001 * n(0.6)))

    # inventory-svc
    sc.series("inventory-svc", "request_rate", lambda t: 48 * tr(t) * n(0.06))
    sc.series("inventory-svc", "error_rate", lambda t: max(0.0, 0.0008 * n(0.6)))
    sc.series("inventory-svc", "latency_p50_ms", lambda t: 18 * n(0.06))
    sc.series("inventory-svc", "latency_p99_ms", lambda t: 95 * n(0.12))
    sc.series("inventory-svc", "cpu_util", lambda t: 0.22 + 0.25 * tr(t) * n(0.08))
    sc.series("inventory-svc", "memory_rss_mb", lambda t: 420 * n(0.01))
    sc.series("inventory-svc", "db_pool_max", lambda t: 30)
    sc.series("inventory-svc", "db_pool_in_use", lambda t: max(1, round(5 * tr(t) * n(0.3))))
    sc.series("inventory-svc", "db_pool_wait_ms", lambda t: max(0.0, 0.3 * n(0.6)))
    sc.series("inventory-svc", "db_queries_per_request", lambda t: 2.1 * n(0.03))
    sc.series("inventory-svc", "cache_hit_ratio", lambda t: min(0.99, 0.93 * n(0.01)))

    # notification-worker (async, off the request path)
    sc.series("notification-worker", "jobs_processed_rate", lambda t: 9 * tr(t) * n(0.1))
    sc.series("notification-worker", "error_rate", lambda t: max(0.0, 0.004 * n(0.5)))
    sc.series("notification-worker", "queue_depth", lambda t: max(0, 15 * n(0.4)))
    sc.series("notification-worker", "cpu_util", lambda t: 0.08 * n(0.15))
    sc.series("notification-worker", "memory_rss_mb", lambda t: 180 * n(0.02))

    # datastores
    for db, base in (("postgres-payments", 0.15), ("postgres-inventory", 0.2)):
        sc.series(db, "max_connections", lambda t: 200)
        sc.series(db, "cpu_util", lambda t, b=base: b + 0.15 * tr(t) * n(0.08))
        sc.series(db, "slow_queries_rate", lambda t: max(0.0, 0.05 * n(0.8)))
        sc.series(db, "lock_wait_ms", lambda t: max(0.0, 2 * n(0.5)))
    sc.series("postgres-payments", "active_connections", lambda t: max(1, round(6 * tr(t) * n(0.25))) + 3)
    sc.series("postgres-inventory", "active_connections", lambda t: max(1, round(5 * tr(t) * n(0.3))) + 3)
    sc.series("redis-cache", "memory_used_mb", lambda t: 900 * n(0.01))
    sc.series("redis-cache", "evictions_rate", lambda t: max(0.0, 0.2 * n(0.5)))
    sc.series("redis-cache", "hit_ratio", lambda t: min(0.99, 0.93 * n(0.01)))
    sc.series("redis-cache", "cpu_util", lambda t: 0.1 * n(0.1))

    # Background logs: sampled access logs + the normal low-level junk every system has.
    users = [f"u_{rng.randint(10000, 99999)}" for _ in range(40)]
    for t in sc.poisson_times(sc.t_start, sc.t_end, 3):
        sc.log("payments-svc", t, "info", "charge ok", route="/charge", status=200,
               duration_ms=round(85 * n(0.3)), user_id=rng.choice(users))
    for t in sc.poisson_times(sc.t_start, sc.t_end, 0.25):
        sc.log("payments-svc", t, "warning", "card declined by issuer", route="/charge", status=402,
               decline_code=rng.choice(["insufficient_funds", "do_not_honor", "expired_card"]))
    for t in sc.poisson_times(sc.t_start, sc.t_end, 4):
        sc.log("inventory-svc", t, "info", "reserve ok", route="/reserve", status=200,
               duration_ms=round(18 * n(0.4)), items=rng.randint(1, 6))
    for t in sc.poisson_times(sc.t_start, sc.t_end, 0.1):
        sc.log("inventory-svc", t, "warning", "redis GET timed out after 50ms, falling back to postgres",
               key=f"stock:{rng.randint(1000, 9999)}")
    for t in sc.poisson_times(sc.t_start, sc.t_end, 3):
        sc.log("checkout-api", t, "info", "checkout complete", route="/checkout", status=200,
               duration_ms=round(150 * n(0.3)), user_id=rng.choice(users))
    for t in sc.poisson_times(sc.t_start, sc.t_end, 0.15):
        sc.log("checkout-api", t, "info", "client closed request", route="/checkout", status=499)
    for t in sc.poisson_times(sc.t_start, sc.t_end, 1.5):
        sc.log("notification-worker", t, "info", "order confirmation sent", template="order_confirmed")

    # Planted PII / secrets (redaction must catch all of these).
    t = sc.t_start + 1800
    sc.log("payments-svc", t, "error", f"card {PII_CANARIES[1]} rejected by validation for {PII_CANARIES[0]}",
           route="/charge", status=400)
    sc.log("payments-svc", t + 600, "debug", f"gateway client init api_key={PII_CANARIES[2]}")
    sc.log("checkout-api", t + 1200, "warning", "slow request", route="/checkout", status=200,
           duration_ms=640, customer_email=PII_CANARIES[0], authorization="Bearer abcdefghijklmnop1234")
    sc.log("notification-worker", t + 900, "error", f"S3 upload failed using key {PII_CANARIES[3]}: AccessDenied")

    # Routine, harmless changes so `recent_changes` is never conveniently empty.
    sc.change(sc.t_start + 1500, "deploy", "notification-worker", "nw-2214", "ana.lopez",
              "Update order-confirmation email copy", commit="3e1a9c0")
    sc.change(sc.t_start + 7200, "config", "inventory-svc", "cfg-8812", "sam.kim",
              "Raise LOG_SAMPLING_RATE 0.01 -> 0.02 for reserve endpoint",
              diff="-LOG_SAMPLING_RATE=0.01\n+LOG_SAMPLING_RATE=0.02")


def derive_checkout(sc: Scenario, timeout_ms: float = 2000) -> None:
    """checkout-api is a thin orchestrator: its RED metrics follow its dependencies."""
    n = sc.noise
    rate = [sc.value("inventory-svc", "request_rate", t) / 2.2 * n(0.03) for t in sc.times]
    sc.metrics.setdefault("checkout-api", {})["request_rate"] = rate

    def lat99(t):
        v = 70 + 0.8 * sc.value("payments-svc", "latency_p99_ms", t) + 0.6 * sc.value("inventory-svc", "latency_p99_ms", t)
        return min(timeout_ms + 40, v * n(0.06))

    def lat50(t):
        v = 25 + sc.value("payments-svc", "latency_p50_ms", t) + sc.value("inventory-svc", "latency_p50_ms", t)
        return min(timeout_ms, v * n(0.04))

    def err(t):
        pay = sc.value("payments-svc", "error_rate", t)
        inv = sc.value("inventory-svc", "error_rate", t)
        slow = max(0.0, (sc.value("payments-svc", "latency_p99_ms", t) - timeout_ms) / 4000)
        return min(1.0, max(0.0, 0.001 * n(0.5) + 0.95 * pay + 0.9 * inv + slow))

    sc.series("checkout-api", "latency_p99_ms", lat99)
    sc.series("checkout-api", "latency_p50_ms", lat50)
    sc.series("checkout-api", "error_rate", err)
    sc.series("checkout-api", "cpu_util", lambda t: 0.12 + 0.15 * sc.traffic(t) * n(0.08))
    sc.series("checkout-api", "memory_rss_mb", lambda t: 260 * n(0.01))


def checkout_traces(sc: Scenario, every_s: float = 120, extra=None) -> None:
    """One representative checkout trace every `every_s` seconds, span timings drawn
    from the current metric values. `extra(sc, t, trace)` may mutate a trace."""
    rng, n = sc.rng, sc.noise
    t = sc.t_start + rng.uniform(0, every_s)
    while t < sc.t_end:
        inv_ms = sc.value("inventory-svc", "latency_p50_ms", t) * max(0.3, n(0.4))
        pay_ms = sc.value("payments-svc", "latency_p50_ms", t) * max(0.3, n(0.4))
        db_ms = pay_ms * 0.25
        spans = [
            {"service": "checkout-api", "op": "POST /checkout", "duration_ms": 0, "status": "ok"},
            {"service": "inventory-svc", "op": "POST /reserve", "duration_ms": round(inv_ms, 1), "status": "ok",
             "children": [{"service": "postgres-inventory", "op": "SELECT stock", "duration_ms": round(inv_ms * 0.4, 1)}]},
            {"service": "payments-svc", "op": "POST /charge", "duration_ms": round(pay_ms, 1), "status": "ok",
             "children": [
                 {"service": "payments-svc", "op": "db.pool.acquire", "duration_ms": round(0.3 * n(0.5), 2)},
                 {"service": "postgres-payments", "op": "INSERT payment", "duration_ms": round(db_ms, 1)},
                 {"service": "card-gateway", "op": "POST /v2/charges", "duration_ms": round(pay_ms * 0.6, 1)},
             ]},
        ]
        trace = {"trace_id": sc.trace_id(), "start": iso_ms(t), "status": "ok", "spans": spans}
        if extra:
            extra(sc, t, trace)
        root = trace["spans"][0]
        root["duration_ms"] = round(20 + sum(s["duration_ms"] for s in trace["spans"][1:]), 1)
        if any(s["status"] != "ok" for s in trace["spans"][1:]):
            trace["status"] = root["status"] = "error"
        trace["duration_ms"] = root["duration_ms"]
        sc.traces.append(trace)
        t += every_s * rng.uniform(0.6, 1.4)
