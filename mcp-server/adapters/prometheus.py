"""Live adapter: Prometheus for metrics, JSON log files for logs, a JSONL change log.

Logical metric names (the ones in the service catalog) map to PromQL templates in
$RCA_PROMQL_MAP (default sandbox/promql.yaml). A real org would do the same mapping
for Datadog/Grafana/etc.; the skill only ever sees logical names.

Env:
    RCA_PROMETHEUS_URL   default http://localhost:19090
    RCA_LOG_DIR          default sandbox/data/logs
    RCA_CHANGES_FILE     default sandbox/data/changes.jsonl
    RCA_PROMQL_MAP       default sandbox/promql.yaml
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

from .base import AdapterError, FileOrgContext, TelemetryAdapter

REPO = Path(__file__).resolve().parents[2]


class PrometheusAdapter(FileOrgContext, TelemetryAdapter):
    def __init__(self, org_context_dir: Path, overlay_path: Path | None):
        self.org = Path(org_context_dir)
        self.overlay_path = overlay_path
        self.url = os.environ.get("RCA_PROMETHEUS_URL", "http://localhost:19090")
        self.log_dir = Path(os.environ.get("RCA_LOG_DIR", REPO / "sandbox" / "data" / "logs"))
        self.changes_file = Path(os.environ.get("RCA_CHANGES_FILE", REPO / "sandbox" / "data" / "changes.jsonl"))
        self.promql: dict[str, str] = yaml.safe_load(
            Path(os.environ.get("RCA_PROMQL_MAP", REPO / "sandbox" / "promql.yaml")).read_text()
        )
        self.http = httpx.Client(timeout=10)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def list_metrics(self, service):
        self._require_service(service)
        declared = self.service_catalog()["services"][service].get("metrics", [])
        return [m for m in declared if m in self.promql]

    def query_metrics(self, service, metric, start, end, step_s):
        if metric not in self.list_metrics(service):
            raise AdapterError(f"no metric {metric!r} for {service}; available: {self.list_metrics(service)}")
        query = self.promql[metric].replace("$service", service)
        try:
            r = self.http.get(f"{self.url}/api/v1/query_range", params={
                "query": query, "start": start.timestamp(), "end": end.timestamp(), "step": step_s})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise AdapterError(f"prometheus query failed: {e}") from e
        result = r.json()["data"]["result"]
        if not result:
            return []
        return [
            (datetime.fromtimestamp(float(ts), timezone.utc), float(v))
            for ts, v in result[0]["values"]
            if v not in ("NaN", "+Inf", "-Inf")
        ]

    def search_logs(self, service, start, end):
        self._require_service(service)
        path = self.log_dir / f"{service}.jsonl"
        if not path.exists():
            return []
        out = []
        with path.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if start <= _ts(rec["ts"]) <= end:
                    out.append(rec)
        return out

    def get_traces(self, service, start, end):
        raise AdapterError("distributed tracing is not configured in this environment (no trace backend)")

    def recent_changes(self, start, end, service):
        if not self.changes_file.exists():
            return []
        changes = [json.loads(l) for l in self.changes_file.read_text().splitlines() if l.strip()]
        return [c for c in changes if start <= _ts(c["ts"]) <= end and (service is None or c["service"] == service)]


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
