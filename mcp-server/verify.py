"""Claim verifier: checks every claim in an RCA against the queries it cites.

Deterministic on purpose. For each `observation` claim:
  1. every cited query_id must exist in this investigation's audit log and have succeeded;
  2. every value the claim quotes must actually appear in a cited result
     (numbers within 5% relative tolerance; strings case-insensitive substring);
  3. re-running the cited query must reproduce the same result (hash match).
An `inference` claim passes only if every claim it is `based_on` passes.

Usage (CLI, used by the eval runner):
    python mcp-server/verify.py --report incident-report.md --audit .rca/audit.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REL_TOL = 0.05
_NUM_IN_TEXT = re.compile(r"-?\d+(?:\.\d+)?")
# Timestamps and ids contain digits that would make almost any small number "match".
_NOT_A_QUANTITY = re.compile(r"\d+(?:\.\d+){2,}(?:-\d+)?|\d{4}-\d{2}-\d{2}T[\d:.]+Z?|\b[0-9a-f]{7,40}\b|\b[a-z]+[-_]?\d+\b", re.I)
_YAML_BLOCK = re.compile(r"```yaml\s*\n(.*?)```", re.S)


def load_audit(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    entries = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    return {e["query_id"]: e for e in entries}


def extract_summary(report_md: str) -> dict:
    """The machine-readable block at the end of the report (last ```yaml block with a verdict)."""
    for block in reversed(_YAML_BLOCK.findall(report_md)):
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and "verdict" in data:
            return data
    raise ValueError("report has no ```yaml block containing a `verdict:` key")


def _numbers(obj: Any) -> list[float]:
    out: list[float] = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        out.extend(float(n) for n in _NUM_IN_TEXT.findall(_NOT_A_QUANTITY.sub(" ", obj)))
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_numbers(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_numbers(v))
    return out


def _value_found(value: Any, results: list[dict]) -> bool:
    if isinstance(value, bool):
        value = str(value).lower()
    if isinstance(value, (int, float)):
        target = float(value)
        candidates = [target] + ([target / 100] if abs(target) > 1 else [])  # "12%" vs 0.12
        nums = [n for r in results for n in _numbers(r)]
        for c in candidates:
            for n in nums:
                if c == n or (c != 0 and abs(n - c) <= REL_TOL * abs(c)):
                    return True
        return False
    needle = str(value).strip().lower()
    if not needle:
        return False
    if _NUM_IN_TEXT.fullmatch(needle.rstrip("%msx ")):  # "2400ms", "12%" -> numeric check
        return _value_found(float(_NUM_IN_TEXT.match(needle).group(0)), results)
    return any(needle in json.dumps(r).lower() for r in results)


def verify_claims(claims: list[dict], audit: dict[str, dict], rerun=None) -> list[dict]:
    """Returns one verdict per claim: {id, status: PASS|FAIL, reasons: [...]}.

    `rerun(tool, args) -> result` re-executes a query without charging budget or
    writing to the audit log; pass None to skip the reproducibility check."""
    verdicts: dict[str, dict] = {}
    rerun_cache: dict[str, bool] = {}

    for i, claim in enumerate(claims):
        cid = str(claim.get("id") or f"c{i + 1}")
        kind = claim.get("kind", "observation")
        reasons: list[str] = []
        if kind == "observation":
            evidence = claim.get("evidence") or []
            values = claim.get("values") or []
            if not evidence:
                reasons.append("observation cites no query_id")
            if not values:
                reasons.append("observation quotes no checkable value")
            results = []
            for qid in evidence:
                entry = audit.get(str(qid))
                if entry is None:
                    reasons.append(f"{qid}: no such query in this investigation's audit log")
                    continue
                if not entry["ok"]:
                    reasons.append(f"{qid}: cited query returned an error")
                    continue
                results.append(entry["result"])
                if rerun is not None:
                    if qid not in rerun_cache:
                        from pipeline import result_hash

                        fresh = result_hash(rerun(entry["tool"], entry["args"]))
                        rerun_cache[qid] = fresh == entry["sha256"] == result_hash(entry["result"])
                    if not rerun_cache[qid]:
                        reasons.append(f"{qid}: re-running the query gave a different result")
            if results:
                for v in values:
                    if not _value_found(v, results):
                        reasons.append(f"value {v!r} not found in cited results {evidence}")
        elif kind == "inference":
            based_on = claim.get("based_on") or []
            if not based_on:
                reasons.append("inference is not based_on any claim")
            for ref in based_on:
                ref_v = verdicts.get(str(ref))
                if ref_v is None:
                    reasons.append(f"based_on {ref}: unknown or later claim")
                elif ref_v["status"] != "PASS":
                    reasons.append(f"based_on {ref}: that claim failed verification")
        else:
            reasons.append(f"unknown claim kind {kind!r}")
        verdicts[cid] = {
            "id": cid,
            "text": claim.get("text", ""),
            "status": "FAIL" if reasons else "PASS",
            "reasons": reasons,
        }
    return list(verdicts.values())


def format_verdicts(verdicts: list[dict]) -> str:
    lines = []
    for v in verdicts:
        lines.append(f"[{v['status']}] {v['id']}: {v['text']}")
        lines.extend(f"    - {r}" for r in v["reasons"])
    passed = sum(v["status"] == "PASS" for v in verdicts)
    lines.append(f"{passed}/{len(verdicts)} claims verified")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--audit", required=True, type=Path)
    ap.add_argument("--rerun", action="store_true", help="re-execute cited queries via the configured adapter")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    summary = extract_summary(args.report.read_text())
    audit = load_audit(args.audit)
    rerun = None
    if args.rerun:
        from pipeline import Investigation, make_adapter

        inv = Investigation(make_adapter(), audit_path=None)
        rerun = lambda tool, a: inv.run(tool, a, record=False)  # noqa: E731
    verdicts = verify_claims(summary.get("claims") or [], audit, rerun)
    print(json.dumps(verdicts, indent=2) if args.json else format_verdicts(verdicts))
    return 0 if all(v["status"] == "PASS" for v in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
