"""Adapter over a recorded scenario directory (deterministic; used by evals and demos).

Scenario layout:
    scenario.json          {"now": ISO-8601}   - the moment the engineer was paged
    metrics/<service>.json {"<metric>": [[epoch_s, value], ...], ...}
    logs/<service>.jsonl   one structured log record per line, must include "ts"
    traces/traces.jsonl    one trace per line: {trace_id, start, duration_ms, spans:[...]}
    changes.json           [{ts, type, service, id, author, summary, ...}]

ground_truth.yaml also lives in the scenario dir; this adapter never opens it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .base import AdapterError, FileOrgContext, TelemetryAdapter, parse_time


class FixtureAdapter(FileOrgContext, TelemetryAdapter):
    def __init__(self, scenario_dir: Path, org_context_dir: Path, overlay_path: Path | None):
        self.dir = Path(scenario_dir)
        if not (self.dir / "scenario.json").exists():
            raise AdapterError(f"not a scenario dir: {self.dir}")
        self.org = Path(org_context_dir)
        self.overlay_path = overlay_path
        meta = json.loads((self.dir / "scenario.json").read_text())
        self._now = parse_time(meta["now"], datetime.now(timezone.utc))
        self._metrics: dict[str, dict] = {}
        self._logs: dict[str, list[dict]] = {}
        self._traces: list[dict] | None = None

    def now(self) -> datetime:
        return self._now

    # -- metrics -----------------------------------------------------------
    def _service_metrics(self, service: str) -> dict:
        if service not in self._metrics:
            path = self.dir / "metrics" / f"{service}.json"
            self._metrics[service] = json.loads(path.read_text()) if path.exists() else {}
        return self._metrics[service]

    def list_metrics(self, service: str) -> list[str]:
        self._require_service(service)
        return sorted(self._service_metrics(service))

    def query_metrics(self, service, metric, start, end, step_s):
        self._require_service(service)
        series = self._service_metrics(service).get(metric)
        if series is None:
            raise AdapterError(
                f"no metric {metric!r} for {service}; available: {self.list_metrics(service)}"
            )
        lo, hi = start.timestamp(), end.timestamp()
        buckets: dict[int, list[float]] = {}
        for ts, value in series:
            if lo <= ts <= hi and value is not None:
                buckets.setdefault(int((ts - lo) // step_s), []).append(value)
        return [
            (datetime.fromtimestamp(lo + b * step_s, timezone.utc), sum(v) / len(v))
            for b, v in sorted(buckets.items())
        ]

    # -- logs / traces -------------------------------------------------------
    def search_logs(self, service, start, end):
        self._require_service(service)
        if service not in self._logs:
            path = self.dir / "logs" / f"{service}.jsonl"
            self._logs[service] = (
                [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
                if path.exists()
                else []
            )
        return [r for r in self._logs[service] if start <= _ts(r["ts"]) <= end]

    def get_traces(self, service, start, end):
        self._require_service(service)
        if self._traces is None:
            path = self.dir / "traces" / "traces.jsonl"
            self._traces = (
                [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
                if path.exists()
                else []
            )
        return [
            t
            for t in self._traces
            if start <= _ts(t["start"]) <= end
            and any(s["service"] == service for s in t["spans"])
        ]

    # -- changes / org context ----------------------------------------------
    def recent_changes(self, start, end, service):
        path = self.dir / "changes.json"
        changes = json.loads(path.read_text()) if path.exists() else []
        return [
            c
            for c in changes
            if start <= _ts(c["ts"]) <= end and (service is None or c["service"] == service)
        ]


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
