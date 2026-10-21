"""Run the incident-rca skill headlessly (Claude Code `claude -p`) against recorded
scenarios and score each run deterministically against ground_truth.yaml.

    uv run python eval/run_eval.py                       # all scenarios x 3 runs
    uv run python eval/run_eval.py --scenario pool-exhaustion --runs 1 --show

Each run gets a fresh workspace containing only the skill, an MCP config and a
settings file that denies reading the scenarios/ and eval/ dirs, so the model can
see telemetry only through the MCP tools and can never read the answer key.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SCENARIOS = REPO / "scenarios"
# Workspaces live outside the repo so Claude Code doesn't pick up the repo's own
# .mcp.json / CLAUDE.md / skill; artifacts are copied back to eval/runs/ afterwards.
WORK_ROOT = Path(os.environ.get("RCA_EVAL_WORKDIR", Path.home() / ".cache" / "incident-rca-eval"))
RUNS_DIR = REPO / "eval" / "runs"
RESULTS = REPO / "eval" / "results"
sys.path.insert(0, str(REPO / "mcp-server"))
sys.path.insert(0, str(SCENARIOS / "_generator"))

from adapters.fixture import FixtureAdapter  # noqa: E402
from common import PII_CANARIES  # noqa: E402
from pipeline import Investigation  # noqa: E402
from verify import extract_summary, load_audit, verify_claims  # noqa: E402

TOOLS = [
    "mcp__incident-telemetry__*",
    "Skill",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
]


def all_scenarios() -> list[str]:
    return sorted(p.parent.name for p in SCENARIOS.glob("*/ground_truth.yaml"))


def page_text(scenario: str) -> str:
    return (SCENARIOS / scenario / "README.md").read_text().split("## Page received", 1)[-1].split("\n", 1)[1].strip()


def make_workspace(scenario: str, overlay: str, run_id: str) -> Path:
    ws = WORK_ROOT / run_id
    ws.mkdir(parents=True)
    shutil.copytree(REPO / "skill", ws / ".claude" / "skills" / "incident-rca")
    # Snapshot org context so edits in the repo during an eval can't change a run's inputs.
    shutil.copytree(REPO / "org-context", ws / ".org-context")
    overlay_path = ws / ".org-context" / "overlay.md"
    if overlay == "empty":
        overlay_path = ws / ".rca-empty-overlay.md"
        overlay_path.write_text("")
    (ws / ".rca").mkdir()
    mcp = {
        "mcpServers": {
            "incident-telemetry": {
                "command": "uv",
                "args": ["run", "--quiet", "--project", str(REPO), "python", str(REPO / "mcp-server" / "server.py")],
                "env": {
                    "RCA_ADAPTER": "fixture",
                    "RCA_SCENARIO_DIR": str(SCENARIOS / scenario),
                    "RCA_ORG_CONTEXT": str(ws / ".org-context"),
                    "RCA_OVERLAY_PATH": str(overlay_path),
                    "RCA_AUDIT_LOG": str(ws / ".rca" / "audit.jsonl"),
                },
            }
        }
    }
    (ws / ".mcp.json").write_text(json.dumps(mcp, indent=2))
    # The answer keys, past results and worked examples must be invisible to the model.
    hidden = ["/" + str(p) for p in (SCENARIOS, REPO / "eval", REPO / "examples")]
    deny = [f"{tool}({p}/**)" for tool in ("Read", "Grep", "Glob") for p in hidden]
    settings = {"permissions": {"deny": deny + ["Bash", "WebFetch", "WebSearch"]}}
    (ws / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2))
    return ws


def run_claude(ws: Path, prompt: str, model: str | None, timeout: int) -> dict:
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--mcp-config", str(ws / ".mcp.json"), "--strict-mcp-config",
        "--permission-mode", "dontAsk",
        "--allowedTools", *TOOLS,
    ]
    if model:
        cmd += ["--model", model]
    t0 = time.time()
    env = {k: v for k, v in os.environ.items() if not k.startswith("RCA_")}
    try:
        proc = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, timeout=timeout, env=env,
                              stdin=subprocess.DEVNULL)
        out, err, timed_out = proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as e:
        out, err, timed_out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""), "", True
    wall = time.time() - t0
    (ws / "transcript.jsonl").write_text(out)
    (ws / "stderr.txt").write_text(err or "")
    events = []
    for line in out.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    final = next((e for e in reversed(events) if e.get("type") == "result"), {})
    tool_calls = [
        c["name"]
        for e in events
        if e.get("type") == "assistant"
        for c in e.get("message", {}).get("content", [])
        if c.get("type") == "tool_use"
    ]
    text = final.get("result", "") or ""
    # Usage/rate limits, auth problems or a crashed CLI say nothing about the skill:
    # report them as ERROR and keep them out of the pass rate.
    # (Only short texts: a real report summary can legitimately mention "rate limit".)
    infra_error = timed_out is False and (
        not final or final.get("is_error")
        or (len(text) < 300 and any(k in text.lower() for k in ("session limit", "usage limit", "rate limit", "api error")))
    )
    return {
        "infra_error": bool(infra_error),
        "wall_s": round(wall, 1),
        "timed_out": timed_out,
        "cost_usd": final.get("total_cost_usd"),
        "turns": final.get("num_turns"),
        "final_text": final.get("result", ""),
        "tool_calls": tool_calls,
        "skill_used": "Skill" in tool_calls,
    }


def _evidence_matches(entry: dict, spec: dict) -> bool:
    if entry["tool"] != spec["tool"]:
        return False
    return all(entry["args"].get(k) == v for k, v in spec.items() if k != "tool")


def score(scenario: str, ws: Path, overlay_path: Path) -> dict:
    """Must run before the workspace is deleted (uses its org-context snapshot)."""
    gt = yaml.safe_load((SCENARIOS / scenario / "ground_truth.yaml").read_text())
    report_path = ws / ".rca" / "incident-report.md"
    audit = load_audit(ws / ".rca" / "audit.jsonl")
    queries = sum(1 for e in audit.values() if e["tool"] in Investigation.CHARGED)
    s: dict = {"queries": queries, "failures": []}
    if not report_path.exists():
        s["failures"].append("no report written")
        s["pass"] = False
        return s
    report = report_path.read_text()
    try:
        summary = extract_summary(report)
    except Exception as e:  # noqa: BLE001
        s["failures"].append(f"no parseable summary: {e}")
        s["pass"] = False
        return s

    s["verdict"] = summary.get("verdict")
    rc = summary.get("root_cause") or {}
    s["predicted"] = {k: rc.get(k) for k in ("service", "category", "commit")}
    s["confidence"] = summary.get("confidence")

    if summary.get("verdict") != gt["expected_verdict"]:
        s["failures"].append(f"verdict {summary.get('verdict')!r} != expected {gt['expected_verdict']!r}")
    elif gt["expected_verdict"] == "root_cause":
        exp = gt["root_cause"]
        if rc.get("service") != exp["service"]:
            s["failures"].append(f"root cause service {rc.get('service')!r} != {exp['service']!r}")
        if rc.get("category") not in exp["accept_categories"]:
            s["failures"].append(f"category {rc.get('category')!r} not in {exp['accept_categories']}")
        if exp.get("commit") and not str(rc.get("commit") or "").startswith(exp["commit"][:7]):
            s["failures"].append(f"commit {rc.get('commit')!r} != {exp['commit']!r}")

    # Independent re-verification: don't trust the model's own verify_claims output.
    adapter = FixtureAdapter(SCENARIOS / scenario, ws / ".org-context", overlay_path)
    inv = Investigation(adapter, None)
    claims = summary.get("claims") or []
    verdicts = verify_claims(claims, audit, lambda tool, a: inv.run(tool, a, record=False))
    failed = [v for v in verdicts if v["status"] != "PASS"]
    s["claims"] = len(verdicts)
    s["claims_failed"] = len(failed)
    if failed:
        s["failures"].append(f"{len(failed)}/{len(verdicts)} claims failed verification: " + "; ".join(
            f"{v['id']}: {v['reasons'][0]}" for v in failed[:3]))
    if summary.get("verdict") == "root_cause" and not claims:
        s["failures"].append("root cause asserted with no claims")

    passing = {v["id"] for v in verdicts if v["status"] == "PASS"}
    cited = {str(q) for c in claims if str(c.get("id")) in passing for q in (c.get("evidence") or [])}
    for spec in gt.get("required_evidence", []):
        if not any(_evidence_matches(audit[q], spec) for q in cited if q in audit):
            s["failures"].append(f"required evidence not cited: {spec}")

    s["fix_proposed"] = bool(summary.get("fix_proposed"))
    if s["fix_proposed"] and not gt.get("fix_allowed", False):
        s["failures"].append("code fix proposed where none is appropriate")

    leaked = [c for c in PII_CANARIES if c in report]
    if leaked:
        s["failures"].append(f"PII/secret in report: {leaked}")

    s["pass"] = not s["failures"]
    return s


def one_run(scenario: str, overlay: str, i: int, stamp: str, model: str | None, timeout: int) -> dict:
    run_id = f"{stamp}/{scenario}{'-no-overlay' if overlay == 'empty' else ''}-{i}"
    ws = make_workspace(scenario, overlay, run_id)
    prompt = (
        "I just got paged. Use the incident-rca skill to investigate. Here is the page:\n\n"
        + page_text(scenario)
    )
    r = run_claude(ws, prompt, model, timeout)
    overlay_path = ws / ".rca-empty-overlay.md" if overlay == "empty" else ws / ".org-context" / "overlay.md"
    s = score(scenario, ws, overlay_path)
    if not r["skill_used"]:
        s["failures"].append("skill was not invoked")
        s["pass"] = False
    keep = RUNS_DIR / run_id
    keep.mkdir(parents=True, exist_ok=True)
    for f in [".rca/incident-report.md", ".rca/audit.jsonl", "transcript.jsonl", "stderr.txt"]:
        if (ws / f).exists():
            shutil.copy(ws / f, keep / Path(f).name)
    res = {"scenario": scenario, "overlay": overlay, "run": i, "workspace": str(keep.relative_to(REPO)), **r, **s}
    res.pop("final_text")
    (keep / "score.json").write_text(json.dumps(res, indent=2))
    shutil.rmtree(ws)  # finished reports must not be readable by later runs
    status = "ERROR" if res["infra_error"] else "PASS" if res["pass"] else "FAIL"
    print(f"[{status}] {scenario} ({overlay} overlay) run {i}: {res['queries']} queries, {res['wall_s']}s, "
          f"${res['cost_usd'] or 0:.2f}" + ("" if res["pass"] else f" — {res['failures']}"), flush=True)
    return res


def write_table(results: list[dict], stamp: str, model: str | None) -> Path:
    errored = [r for r in results if r.get("infra_error")]
    results = [r for r in results if not r.get("infra_error")]
    groups: dict[tuple, list[dict]] = {}
    for r in results:
        groups.setdefault((r["scenario"], r["overlay"]), []).append(r)
    lines = [
        f"# Eval results — {stamp}",
        "",
        f"Model: `{model or 'claude default'}` · driver: `claude -p` (headless Claude Code) · fixture adapter",
        "",
        "| Scenario | Overlay | Pass rate | Verdict ok | Claims verified | Mean queries | Mean wall (s) | Mean cost ($) | Failures |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for (sc, ov), rs in sorted(groups.items()):
        n = len(rs)
        passed = sum(r["pass"] for r in rs)
        verdict_ok = sum(not any(f.startswith(("verdict", "root cause", "category", "commit")) for f in r["failures"]) and "verdict" in r for r in rs)
        claims = sum(r.get("claims", 0) for r in rs)
        claims_ok = claims - sum(r.get("claims_failed", 0) for r in rs)
        fails = "; ".join(sorted({f.split(":")[0] for r in rs for f in r["failures"]})) or "—"
        mean = lambda k: sum((r.get(k) or 0) for r in rs) / n  # noqa: E731
        lines.append(
            f"| {sc} | {ov} | **{passed}/{n}** | {verdict_ok}/{n} | {claims_ok}/{claims} | {mean('queries'):.1f} | "
            f"{mean('wall_s'):.0f} | {mean('cost_usd'):.2f} | {fails} |"
        )
    total = sum(r["pass"] for r in results)
    lines += ["", f"**Overall: {total}/{len(results)} runs passed.**"]
    if errored:
        lines.append(f"{len(errored)} run(s) excluded for infrastructure errors (usage limit / CLI failure): "
                     + ", ".join(f"{r['scenario']}#{r['run']}" for r in errored))
    lines.append("")
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{stamp}.md"
    out.write_text("\n".join(lines))
    (RESULTS / f"{stamp}.json").write_text(json.dumps(results, indent=2))
    shutil.copy(out, RESULTS / "latest.md")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", action="append", help="repeatable; default all")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=1, help="parallel claude sessions; >1 burns usage limits fast")
    ap.add_argument("--model", default=None)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--show", action="store_true", help="print the report of each run")
    ap.add_argument("--no-table", action="store_true")
    args = ap.parse_args()

    scenarios = args.scenario or all_scenarios()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    jobs = []
    for sc in scenarios:
        gt = yaml.safe_load((SCENARIOS / sc / "ground_truth.yaml").read_text())
        overlays = gt.get("eval_overlays", ["default"])
        for ov in overlays:
            for i in range(1, args.runs + 1):
                jobs.append((sc, ov, i))
    print(f"{len(jobs)} runs: {scenarios} x {args.runs}", flush=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda j: one_run(*j, stamp, args.model, args.timeout), jobs))
    if args.show:
        for r in results:
            rp = REPO / r["workspace"] / "incident-report.md"
            print(f"\n{'=' * 80}\n{r['workspace']}\n{'=' * 80}")
            print(rp.read_text() if rp.exists() else "(no report)")
    if all(r["infra_error"] for r in results):
        print("every run hit an infrastructure error; not writing a results table")
        return 2
    if not args.no_table and len(results) > 1:
        print(f"\nresults: {write_table(results, stamp, args.model).relative_to(REPO)}")
    return 0 if all(r["pass"] or r["infra_error"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
