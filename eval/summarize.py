"""Aggregate every valid run in eval/runs/ into eval/results/summary.md, grouped by skill version.

Versions are labelled by eval stamp (add a line here when the skill changes).
"""

import collections
import glob
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERSIONS = {
    "20261020T195848Z": "v0", "20261020T200347Z": "v0",
    "20261021T002158Z": "v1", "20261021T002907Z": "v1",
    "20261021T052223Z": "v2", "20261021T052511Z": "v2",
}
DESC = {
    "v0": "initial skill (verifies drafts via `verify_claims`)",
    "v1": "+ `verify_report` (verify the written file), pinned time windows",
    "v2": "+ level-vs-accumulation / trigger-vs-cause rules",
}


def main() -> None:
    groups = collections.defaultdict(list)
    for f in glob.glob(str(REPO / "eval/runs/*/*/score.json")):
        stamp = Path(f).parts[-3]
        d = json.load(open(f))
        if stamp in VERSIONS and not d.get("infra_error"):
            groups[(d["scenario"], d["overlay"], VERSIONS[stamp])].append(d)
    lines = ["# Eval results summary", "",
             "Every valid run so far, grouped by skill version. Runs that hit infrastructure errors (usage limits) "
             "are excluded. Each run is one headless `claude -p` session (default model, fixture adapter, isolated "
             "workspace). Sample sizes are small: read pass counts, not percentages.", "",
             "| Version | Change |", "|---|---|", *(f"| {v} | {d} |" for v, d in DESC.items()), "",
             "| Scenario | Overlay | Version | Passed | Queries (mean) | Wall s (mean) | Cost $ (mean) | Failure |",
             "|---|---|---|---|---|---|---|---|"]
    tot = collections.Counter()
    for (sc, ov, v), rs in sorted(groups.items()):
        n, p = len(rs), sum(r["pass"] for r in rs)
        tot[v, "n"] += n
        tot[v, "p"] += p
        mean = lambda k: sum(r.get(k) or 0 for r in rs) / n  # noqa: E731
        fails = "; ".join(sorted({f.split(":")[0] for r in rs for f in r["failures"]})) or "—"
        lines.append(f"| {sc} | {ov} | {v} | **{p}/{n}** | {mean('queries'):.1f} | {mean('wall_s'):.0f} | "
                     f"{mean('cost_usd'):.2f} | {fails} |")
    lines += ["", *(f"- **{v}:** {tot[v, 'p']}/{tot[v, 'n']} runs passed" for v in DESC), ""]
    (REPO / "eval/results/summary.md").write_text("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
