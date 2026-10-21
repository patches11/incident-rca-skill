"""The single path every tool call takes: budget -> adapter -> shape -> redact -> audit.

Used by the MCP server, the claim verifier and the eval runner, so all three see
exactly the same data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adapters.base import AdapterError, TelemetryAdapter, iso, parse_duration, parse_time
from budget import Budget, BudgetExceeded
from redaction import redact

REPO_ROOT = Path(__file__).resolve().parent.parent


def make_adapter() -> TelemetryAdapter:
    """Build the adapter selected by environment variables."""
    kind = os.environ.get("RCA_ADAPTER", "fixture")
    org = Path(os.environ.get("RCA_ORG_CONTEXT", REPO_ROOT / "org-context"))
    overlay_env = os.environ.get("RCA_OVERLAY_PATH")
    overlay = Path(overlay_env) if overlay_env else org / "overlay.md"
    if kind == "fixture":
        from adapters.fixture import FixtureAdapter

        scenario = os.environ.get("RCA_SCENARIO_DIR")
        if not scenario:
            raise SystemExit("RCA_SCENARIO_DIR must point at a scenario directory")
        return FixtureAdapter(Path(scenario), org, overlay)
    if kind == "prometheus":
        from adapters.prometheus import PrometheusAdapter

        return PrometheusAdapter(org_context_dir=org, overlay_path=overlay)
    raise SystemExit(f"unknown RCA_ADAPTER {kind!r}")


class Investigation:
    """One investigation = one budget = one audit log."""

    def __init__(self, adapter: TelemetryAdapter, audit_path: Path | None, budget: Budget | None = None):
        self.adapter = adapter
        self.budget = budget or Budget()
        self.audit_path = Path(audit_path) if audit_path else None
        self._seq = 0
        if self.audit_path:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            if self.audit_path.exists():  # continue numbering after a server restart
                self._seq = sum(1 for _ in self.audit_path.open())

    # Tools that read telemetry cost budget; catalog/runbook/overlay lookups don't.
    CHARGED = {"query_metrics", "search_logs", "get_traces", "recent_changes"}

    def run(self, tool: str, args: dict[str, Any], record: bool = True) -> dict:
        # Pin relative windows ("now-2h") to absolute times so the audit log - and any
        # later re-verification - replays exactly the query the model saw.
        now = self.adapter.now()
        args = {k: iso(parse_time(v, now)) if k in ("start", "end") and isinstance(v, str) and v.startswith("now") else v
                for k, v in args.items()}
        try:
            if tool in self.CHARGED and record:
                self.budget.charge()
            result = getattr(self, f"_{tool}")(**args)
            ok = True
        except (AdapterError, BudgetExceeded, ValueError, TypeError) as e:
            result = {"error": str(e)}
            ok = False
        result = redact(result)
        if not record:
            return result
        self._seq += 1
        query_id = f"q{self._seq:03d}"
        if self.audit_path:
            entry = {
                "query_id": query_id,
                "tool": tool,
                "args": args,
                "ok": ok,
                "sha256": result_hash(result),
                "at": iso(datetime.now(timezone.utc)),
                "result": result,
            }
            with self.audit_path.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        out = {"query_id": query_id, **result}
        if tool in self.CHARGED:
            out["budget_remaining"] = self.budget.remaining
        return out

    # -- helpers --------------------------------------------------------------
    def _window(self, start: str, end: str, max_range_s: int | None = None) -> tuple[datetime, datetime]:
        now = self.adapter.now()
        s, e = parse_time(start, now), parse_time(end, now)
        self.budget.check_range(s, e, now, max_range_s)
        return s, e

    # -- tools ----------------------------------------------------------------
    def _current_time(self) -> dict:
        return {"now": iso(self.adapter.now())}

    def _list_metrics(self, service: str) -> dict:
        return {"service": service, "metrics": self.adapter.list_metrics(service)}

    def _query_metrics(self, service: str, metric: str, start: str, end: str, step: str = "60s") -> dict:
        s, e = self._window(start, end)
        step_s = self.budget.fit_step(s, e, parse_duration(step))
        points = self.adapter.query_metrics(service, metric, s, e, step_s)
        values = [v for _, v in points]
        summary = None
        if values:
            peak = max(points, key=lambda p: p[1])
            summary = {
                "min": _r(min(values)),
                "max": _r(max(values)),
                "mean": _r(sum(values) / len(values)),
                "first": _r(values[0]),
                "last": _r(values[-1]),
                "max_at": iso(peak[0]),
            }
        return {
            "service": service,
            "metric": metric,
            "start": iso(s),
            "end": iso(e),
            "step_s": step_s,
            "summary": summary,
            "points": [[iso(t), _r(v)] for t, v in points],
            "note": None if values else "no data points in window (missing instrumentation or gap?)",
        }

    def _search_logs(self, service: str, query: str, start: str, end: str, limit: int = 50) -> dict:
        s, e = self._window(start, end)
        limit = max(1, min(limit, self.budget.max_log_lines))
        records = self.adapter.search_logs(service, s, e)
        match = _log_matcher(query)
        hits = [r for r in records if match(r)]
        hits.sort(key=lambda r: r["ts"])
        # Evenly sample across the window so a burst at the end isn't hidden by the limit.
        sampled = len(hits) > limit
        lines = [hits[int(i * len(hits) / limit)] for i in range(limit)] if sampled else hits
        templates = Counter(_template(r.get("msg", "")) for r in hits)
        return {
            "service": service,
            "query": query,
            "start": iso(s),
            "end": iso(e),
            "total_records_in_window": len(records),
            "total_matches": len(hits),
            "histogram": _histogram([r["ts"] for r in hits], s, e),
            "top_messages": [{"template": t, "count": c} for t, c in templates.most_common(10)],
            "sampled": sampled,
            "lines": lines,
        }

    def _get_traces(self, service: str, start: str, end: str, filter: str = "", limit: int = 5) -> dict:
        s, e = self._window(start, end)
        traces = [t for t in self.adapter.get_traces(service, s, e) if _trace_matches(t, filter)]
        durations = sorted(t["duration_ms"] for t in traces)
        traces.sort(key=lambda t: -t["duration_ms"])
        return {
            "service": service,
            "filter": filter,
            "total_matches": len(traces),
            "duration_ms": {
                "p50": _pct(durations, 0.5),
                "p99": _pct(durations, 0.99),
                "max": durations[-1] if durations else None,
            },
            "slowest": traces[: max(1, min(limit, 10))],
        }

    def _recent_changes(self, start: str, end: str, service: str | None = None) -> dict:
        # Change records are small and cheap; allow the full 7-day lookback in one query.
        s, e = self._window(start, end, max_range_s=self.budget.max_lookback_s)
        changes = sorted(self.adapter.recent_changes(s, e, service), key=lambda c: c["ts"])
        return {"start": iso(s), "end": iso(e), "service": service, "count": len(changes), "changes": changes}

    def _service_catalog(self, service: str | None = None) -> dict:
        catalog = self.adapter.service_catalog()
        services = catalog.get("services", {})
        for name, svc in services.items():
            svc["dependents"] = sorted(
                n for n, other in services.items() if name in other.get("depends_on", [])
            )
        if service is None:
            return catalog
        if service not in services:
            raise AdapterError(f"unknown service {service!r}; known: {sorted(services)}")
        return {"service": service, **services[service]}

    def _get_runbook(self, service: str, name: str) -> dict:
        return {"service": service, "name": name, "text": self.adapter.get_runbook(service, name)}

    def _get_org_overlay(self) -> dict:
        text = self.adapter.org_overlay().strip()
        return {"overlay": text or None, "note": None if text else "org overlay is empty"}


def result_hash(result: dict) -> str:
    return hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()


def _r(v: float) -> float:
    return round(v, 4) if abs(v) < 10 else round(v, 1)


def _pct(sorted_vals: list[float], q: float):
    if not sorted_vals:
        return None
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def _histogram(timestamps: list[str], s: datetime, e: datetime, buckets: int = 12) -> list[dict]:
    width = (e - s).total_seconds() / buckets
    counts = [0] * buckets
    for ts in timestamps:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        counts[min(buckets - 1, int((t - s).total_seconds() // width))] += 1
    return [
        {"from": iso(datetime.fromtimestamp(s.timestamp() + i * width, timezone.utc)), "count": c}
        for i, c in enumerate(counts)
    ]


_NUMERIC = re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{8}-[0-9a-f-]{27}\b|\d+(?:\.\d+)?")


def _template(msg: str) -> str:
    return _NUMERIC.sub("<n>", msg)


def _log_matcher(query: str):
    """Query language: whitespace-separated terms, all must match.
    field=value / field!=value  exact (case-insensitive) match on a top-level field
    -word                        record must NOT contain word
    word                         record must contain word (case-insensitive)
    Empty query matches everything."""
    terms = shlex.split(query or "")

    def match(rec: dict) -> bool:
        blob = None
        for term in terms:
            if "!=" in term:
                k, v = term.split("!=", 1)
                if str(rec.get(k, "")).lower() == v.lower():
                    return False
            elif "=" in term:
                k, v = term.split("=", 1)
                if str(rec.get(k, "")).lower() != v.lower():
                    return False
            else:
                blob = blob or json.dumps(rec).lower()
                if term.startswith("-"):
                    if term[1:].lower() in blob:
                        return False
                elif term.lower() not in blob:
                    return False
        return True

    return match


def _trace_matches(trace: dict, flt: str) -> bool:
    for term in shlex.split(flt or ""):
        k, _, v = term.partition("=")
        if k == "status" and trace.get("status") != v:
            return False
        if k == "min_duration_ms" and trace["duration_ms"] < float(v):
            return False
        if k == "op" and not any(sp.get("op") == v for sp in trace["spans"]):
            return False
    return True
