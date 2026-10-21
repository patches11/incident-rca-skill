"""Copy one run per scenario from an eval results file into examples/fixtures/<scenario>/
(page text, condensed transcript, report, audit log) for the README.

    uv run python eval/make_examples.py eval/results/<stamp>.json
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def condense(transcript: Path) -> str:
    out = ["# Transcript (condensed)\n\n",
           "Every tool call the skill made, in order, with its short reasoning text in between. "
           "Raw stream-json is not committed (large); the audit log has every query result.\n"]
    n = 0
    for line in transcript.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "assistant":
            for c in e["message"]["content"]:
                if c["type"] == "text" and c["text"].strip():
                    out.append("\n> " + c["text"].strip().replace("\n", "\n> ") + "\n")
                if c["type"] == "tool_use":
                    n += 1
                    name = c["name"].replace("mcp__incident-telemetry__", "")
                    inp = c["input"]
                    if name == "Write":
                        inp = {"file_path": Path(inp["file_path"]).name, "content": "<report, see incident-report.md>"}
                    elif name == "Edit":
                        inp = {"file_path": Path(inp["file_path"]).name, "old_string": inp["old_string"][:100] + "…"}
                    elif name in ("verify_claims",):
                        inp = {"claims_yaml": "<draft claims>"}
                    s = json.dumps(inp)
                    out.append(f"{n}. `{name}` `{s[:260]}{'…' if len(s) > 260 else ''}`\n")
        if e.get("type") == "result":
            out.append(f"\n**Result:** {e.get('num_turns')} turns, {(e.get('duration_ms') or 0) / 1000:.0f}s, "
                       f"${e.get('total_cost_usd') or 0:.2f}\n")
    return "".join(out)


def main(results_file: str) -> None:
    results = json.loads(Path(results_file).read_text())
    chosen: dict[tuple, dict] = {}
    for r in results:
        key = (r["scenario"], r["overlay"])
        if r.get("infra_error"):
            continue
        if key not in chosen or (r["pass"] and not chosen[key]["pass"]):
            chosen[key] = r
    for (scenario, overlay), r in sorted(chosen.items()):
        name = scenario + ("-no-overlay" if overlay == "empty" else "")
        dst = REPO / "examples" / "fixtures" / name
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)
        src = REPO / r["workspace"]
        readme = (REPO / "scenarios" / scenario / "README.md").read_text()
        (dst / "input.md").write_text(readme)
        for f in ("incident-report.md", "audit.jsonl", "score.json"):
            if (src / f).exists():
                shutil.copy(src / f, dst / f)
        if (src / "transcript.jsonl").exists():
            (dst / "transcript.md").write_text(condense(src / "transcript.jsonl"))
        print(f"{name}: {'PASS' if r['pass'] else 'FAIL'} from {r['workspace']}")


if __name__ == "__main__":
    main(sys.argv[1])
