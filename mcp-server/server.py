"""Read-only incident telemetry MCP server.

One server process = one investigation (one query budget, one audit log).
There is deliberately no tool that writes, restarts, scales, rolls back or
silences anything.

Config (env):
    RCA_ADAPTER        fixture | prometheus           (default fixture)
    RCA_SCENARIO_DIR   scenario dir for the fixture adapter
    RCA_ORG_CONTEXT    dir with service-catalog.yaml + runbooks/ (default ./org-context)
    RCA_OVERLAY_PATH   org overlay markdown (default $RCA_ORG_CONTEXT/overlay.md)
    RCA_AUDIT_LOG      JSONL audit log of every query (default ./.rca/audit.jsonl)
    RCA_QUERY_BUDGET   max telemetry queries per investigation (default 40)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp_types import ToolAnnotations  # noqa: E402

from pipeline import Investigation, make_adapter  # noqa: E402
from verify import extract_summary, format_verdicts, load_audit  # noqa: E402
from verify import verify_claims as check_claims  # noqa: E402

AUDIT = Path(os.environ.get("RCA_AUDIT_LOG", ".rca/audit.jsonl"))
inv = Investigation(make_adapter(), AUDIT)
server = MCPServer("incident-telemetry")
RO = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)


@server.tool(annotations=RO)
def current_time() -> str:
    """The current time as seen by the telemetry backend. Call this first; use it to anchor all windows."""
    return _out(inv.run("current_time", {}))


@server.tool(annotations=RO)
def service_catalog(service: str | None = None) -> str:
    """Service catalog: owners, team, tier, depends_on (downstream), dependents (upstream),
    SLOs, runbooks and metric names. Omit `service` for the whole org. Free (no budget)."""
    return _out(inv.run("service_catalog", {"service": service}))


@server.tool(annotations=RO)
def list_metrics(service: str) -> str:
    """Logical metric names available for a service. Free (no budget)."""
    return _out(inv.run("list_metrics", {"service": service}))


@server.tool(annotations=RO)
def query_metrics(service: str, metric: str, start: str, end: str, step: str = "60s") -> str:
    """Time series for one metric of one service, with a min/max/mean/first/last summary.
    Step is auto-raised to keep <=240 points. Costs 1 budget unit. 
    Times: ISO-8601 UTC ('2026-10-14T09:30:00Z') or relative to now ('now', 'now-90m', 'now-2h'). Max 6h per query."""
    return _out(inv.run("query_metrics", {"service": service, "metric": metric, "start": start, "end": end, "step": step}))


@server.tool(annotations=RO)
def search_logs(service: str, query: str, start: str, end: str, limit: int = 50) -> str:
    """Search a service's structured JSON logs. Returns total matches, a 12-bucket
    histogram over the window, the top message templates, and up to `limit` (<=200)
    lines sampled evenly across the window. Output is redacted.
    Query: space-separated terms, all must match. `level=error` / `field!=value` exact field
    match; `word` substring anywhere; `-word` must not contain. Empty query = all records.
    Costs 1 budget unit. 
    Times: ISO-8601 UTC ('2026-10-14T09:30:00Z') or relative to now ('now', 'now-90m', 'now-2h'). Max 6h per query."""
    return _out(inv.run("search_logs", {"service": service, "query": query, "start": start, "end": end, "limit": limit}))


@server.tool(annotations=RO)
def get_traces(service: str, start: str, end: str, filter: str = "", limit: int = 5) -> str:
    """Traces touching a service: total matches, duration p50/p99/max, and the `limit`
    (<=10) slowest traces with per-span timings. Filter terms: `status=error`,
    `min_duration_ms=500`, `op=<span op>`. Costs 1 budget unit. 
    Times: ISO-8601 UTC ('2026-10-14T09:30:00Z') or relative to now ('now', 'now-90m', 'now-2h'). Max 6h per query."""
    return _out(inv.run("get_traces", {"service": service, "start": start, "end": end, "filter": filter, "limit": limit}))


@server.tool(annotations=RO)
def recent_changes(start: str, end: str, service: str | None = None) -> str:
    """Deploys, config/env changes, feature flags and infra changes in the window, oldest
    first, with commit SHAs, authors and diffs where available. Omit `service` for all
    services. Costs 1 budget unit.
    Times: ISO-8601 UTC ('2026-10-14T09:30:00Z') or relative to now ('now', 'now-90m', 'now-2d'). Up to 7 days per query."""
    return _out(inv.run("recent_changes", {"start": start, "end": end, "service": service}))


@server.tool(annotations=RO)
def get_runbook(service: str, name: str) -> str:
    """Runbook text by name (names are listed in the service catalog). Free (no budget)."""
    return _out(inv.run("get_runbook", {"service": service, "name": name}))


@server.tool(annotations=RO)
def get_org_overlay() -> str:
    """Org-maintained notes: known-noisy alerts, known-benign log patterns, vendor quirks.
    Use it to discard false leads, never as the sole evidence for a root cause. Free."""
    return _out(inv.run("get_org_overlay", {}))


@server.tool(annotations=RO)
def verify_claims(claims_yaml: str) -> str:
    """Verify the RCA's claims against this investigation's audit log. Pass the `claims:`
    list from the report's YAML summary (or the whole summary block). Each observation
    must cite query_ids and quote values that appear in those results; each inference
    must be based_on passing claims. Cited queries are re-executed to confirm they
    reproduce. Returns PASS/FAIL per claim with reasons. Free (no budget)."""
    data = yaml.safe_load(claims_yaml)
    claims = data.get("claims", []) if isinstance(data, dict) else data
    if not isinstance(claims, list):
        return "could not parse claims: expected a YAML list of claims or a mapping with `claims:`"
    verdicts = check_claims(claims, load_audit(AUDIT), lambda tool, a: inv.run(tool, a, record=False))
    return format_verdicts(verdicts)


@server.tool(annotations=RO)
def verify_report() -> str:
    """Verify the claims in the written report (.rca/incident-report.md, next to the audit
    log) - the final artifact, not a draft. Run this after writing the report and after
    every edit to it; the report is done only when every claim PASSes. Free (no budget)."""
    report = AUDIT.parent / "incident-report.md"
    if not report.exists():
        return f"no report at {report}; write it first"
    try:
        summary = extract_summary(report.read_text())
    except ValueError as e:
        return str(e)
    claims = summary.get("claims") or []
    if not claims and summary.get("verdict") == "root_cause":
        return "FAIL: a root_cause verdict with no claims"
    verdicts = check_claims(claims, load_audit(AUDIT), lambda tool, a: inv.run(tool, a, record=False))
    return format_verdicts(verdicts)


def _out(result: dict) -> str:
    # Compact single-line JSON: indented output roughly triples the tokens for a time series.
    return json.dumps(result, separators=(",", ":"))


def _main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    _main()
