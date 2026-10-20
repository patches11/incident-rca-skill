# Handoff: Incident RCA Skill (Take-Home Challenge)

## Context

This is a take-home challenge: **"Build an AI engineering skill usable across an engineering organization throughout the SDLC."** Evaluators care about engineering judgment, context engineering, validation/guardrails, recognizing failure modes, simplicity over complexity, and whether engineers would actually use it. They explicitly said to use AI aggressively, and they will ask what AI generated, what decisions were made by the human, what didn't work, and what we'd improve. **Keep a running `DECISIONS.md` and `WHAT_DIDNT_WORK.md` as you go.**

Required deliverables:
1. A working skill they can run
2. README: problem, how it works, how an engineer uses it, context/data needed, key architectural decisions, limitations and failure modes
3. At least one realistic worked example (input → what the skill did → output)
4. Reflection: "If you deployed this tomorrow for 20 engineers and had to trust the code it helps produce in production, what would you change?"

The skill must demonstrate that it:
1. Understands enough codebase/org context to make useful decisions
2. Gives repeatable results (not a giant one-off prompt)
3. Knows when to generate code vs. ask, investigate, or refuse
4. Validates its own work
5. Avoids obvious security, architectural, or maintainability problems
6. Can evolve as the org and codebase change

## The problem we chose

**Production incident investigation and root cause analysis.** An on-call engineer gets paged. The skill helps them go from "something is wrong" to "here's the root cause, here's the evidence, here's a proposed fix with a regression test," without touching production.

## Key design decisions (already made — don't relitigate without reason)

### 1. Methodology first, knowledge second
The skill must find failures **nobody in the org has seen before**. Relying on a "known failure patterns" file would make it a lookup table that fails on first occurrences and biases toward the familiar. Knowledge is layered:

| Layer | Source | Purpose |
|---|---|---|
| **Methodology** | Built into the skill workflow | RED per service, USE per resource, dependency-graph traversal, change correlation, hypothesis falsification. Finds novel failures. |
| **Generic failure taxonomy** | Ships with the skill, curated from public SRE knowledge (Google SRE books, public postmortem collections, general industry knowledge) | Collective knowledge available day one. Each entry: telemetry signature + confirming/refuting queries. |
| **Org overlay** | Maintained by teams; postmortems append to it | Only what's unknowable from outside: local quirks, noisy alerts, known-benign patterns, vendor behaviors. Reduces false leads; never the primary source of answers. |

Hypotheses are generated from methodology first, then the taxonomy and overlay are consulted. The overlay must not be required for correctness.

### 2. Read-only, propose-don't-act
- All data tools are read-only.
- The skill never executes remediation in production. It may **propose** a rollback or mitigation as text; a human executes it.
- Code fixes are proposed as a branch/PR in the service repo, accompanied by a failing regression test that reproduces the bug.

### 3. Adapter interface over telemetry
Data access goes through one interface with swappable backends:
- `FixtureAdapter` — reads recorded scenario files (deterministic; used by evals and the quick demo)
- `PrometheusAdapter` + file-based logs — used by the live sandbox
- Real orgs would plug in Datadog/Grafana/etc. — document this, don't build it

### 4. Every claim is evidence-backed and verified
Each statement in the RCA must cite the specific tool call/query that supports it. A separate verifier step re-runs cited queries and checks claims against results. Unsupported claims are removed or downgraded. This targets the main failure mode of AI RCA: plausible fabricated evidence.

### 5. Abstention is a valid output
If data is insufficient, the correct output is "I can't determine the root cause; here's what's missing and how to get it." This is scored in evals.

### 6. Scope discipline
The trap is building observability infrastructure instead of the skill. **No Grafana, Jaeger, Kubernetes, or service mesh.** Prometheus + structured JSON logs + git history is enough. Fixtures first, live sandbox second, and the live sandbox only needs one scenario working.

## Architecture

```
incident-rca-skill/
├── README.md
├── REFLECTION.md
├── DECISIONS.md               # human vs AI decisions, tradeoffs (keep updated)
├── WHAT_DIDNT_WORK.md         # keep updated as you go
├── Makefile                   # eval, demo, break, sandbox-up/down
│
├── skill/                     # the Claude Code skill
│   ├── SKILL.md               # workflow, rules, output format
│   ├── taxonomy/              # generic failure taxonomy (collective knowledge)
│   │   └── *.md               # one file per failure class
│   ├── templates/
│   │   └── incident-report.md
│   └── scripts/               # helpers the skill invokes (redaction, verifier)
│
├── mcp-server/                # read-only telemetry tools
│   ├── server.py              # MCP server exposing the tools below
│   ├── adapters/
│   │   ├── base.py            # interface
│   │   ├── fixture.py
│   │   └── prometheus.py
│   ├── redaction.py           # PII/secret scrubbing before results reach the model
│   └── budget.py              # query count + time-range limits
│
├── org-context/               # what a real org would maintain
│   ├── service-catalog.yaml   # services, owners, deps, SLOs, runbook links
│   ├── runbooks/
│   └── overlay.md             # org-specific quirks; postmortems append here
│
├── scenarios/                 # recorded incidents = demo + eval suite
│   └── <scenario-name>/
│       ├── README.md          # page/alert text the engineer would see
│       ├── metrics/           # JSON/CSV time series
│       ├── logs/              # structured JSON logs
│       ├── traces/            # optional, a few representative traces
│       ├── changes.json       # deploys, config changes, git commits in window
│       └── ground_truth.yaml  # root cause, required evidence, expected verdict
│
├── sandbox/                   # live demo (one scenario minimum)
│   ├── docker-compose.yml     # 2–3 tiny services + Postgres + Prometheus
│   ├── services/
│   └── faults/                # scripts that inject each fault
│
├── eval/
│   ├── run_eval.py            # runs skill against every scenario, scores it
│   └── results/               # committed results table(s)
│
└── examples/
    └── pool-exhaustion/       # full worked example for the README
        ├── input.md
        ├── transcript.md      # what the skill did, step by step
        ├── incident-report.md
        └── proposed-fix.diff
```

## MCP tools (all read-only)

| Tool | Purpose |
|---|---|
| `query_metrics(service, metric, start, end, step)` | Time series |
| `search_logs(service, query, start, end, limit)` | Structured log search; output is redacted |
| `get_traces(service, start, end, filter)` | Representative traces |
| `recent_changes(start, end, service?)` | Deploys, config changes, commits |
| `service_catalog(service?)` | Owners, dependencies, SLOs, runbooks |
| `get_runbook(service, name)` | Runbook contents |

Enforce in the server, not the prompt: time-range caps, per-investigation query budget, redaction, and no write capability at all.

## Skill workflow (SKILL.md)

1. **Triage** — establish start time, blast radius, affected services/SLOs, and a timeline. Ask the engineer for the alert/page if not provided.
2. **Hypothesize** — generate 2–4 candidate causes using methodology (RED/USE, dependency graph, change correlation). Then consult taxonomy and overlay for additional candidates or known-benign signals.
3. **Investigate** — for each hypothesis, run the queries most likely to **disprove** it. Record results.
4. **Correlate** — align the surviving hypothesis against changes and dependency health.
5. **Conclude** — RCA with a confidence level (high/medium/low), each claim citing its evidence. Or abstain with a list of missing data.
6. **Verify** — verifier re-runs cited queries and checks each claim. Drop or downgrade unsupported claims.
7. **Hand off** — produce the incident report. If the cause is in code and confidence is high: propose a fix plus a failing regression test. Propose (never execute) mitigations. Suggest an alert/runbook/overlay update.

Rules to encode explicitly:
- When to **ask**: missing alert context, ambiguous service, conflicting signals the data can't resolve.
- When to **refuse**: requests to execute remediation, write to prod, disable alerts, or bypass the query budget.
- When to **generate code**: only after a high-confidence RCA with a reproducing test.

## Scenarios (build in this order)

| # | Scenario | Tests | Expected verdict |
|---|---|---|---|
| 1 | **Connection pool exhaustion** — symptom in service A, cause in service B | Dependency traversal | Root cause in B |
| 2 | **Bad deploy** — commit introduces an N+1 query; latency spikes after deploy | Change correlation | Points to exact commit |
| 3 | **Red herring** — loud, pre-existing, unrelated error rate plus a quiet real cause | Hypothesis falsification | Ignores noise, finds real cause |
| 4 | **Insufficient data** — logs missing for the critical window | Abstention | "Cannot determine" + what's needed |
| 5 | **Config/infra, no code change** — e.g. expired cert or changed env var | Not assuming code is the cause | Config root cause, no code fix proposed |
| 6 | **Novel failure** — deliberately absent from taxonomy and overlay (e.g. slow memory leak that only crosses a threshold under a specific traffic mix) | Methodology alone finds unseen failures | Correct cause, overlay empty |

Scenario 6 should be run **twice** in evals: once with the org overlay empty and once populated, to show the skill works from collective knowledge and methodology alone, and that org knowledge makes it faster/less noisy rather than making it work at all.

Generate fixture data with a script (seeded, reproducible), not by hand. Data should look realistic: baseline noise, diurnal patterns, unrelated low-level errors.

## Evaluation

`make eval` runs every scenario through the fixture adapter and scores:
- **Root cause correct** (vs. `ground_truth.yaml`)
- **Required evidence cited**
- **No fabricated claims** (verifier output + check against fixture data)
- **Correct abstention** on scenario 4
- **No code fix proposed** where inappropriate (scenarios 4, 5)
- Query count and wall time

Commit a results table. Run each scenario several times to show repeatability (report pass rate, not a single run). Document at least one real failure found by the evals and how the skill was changed to fix it — this goes in `WHAT_DIDNT_WORK.md` and the README.

## Build phases

1. **Scaffold** — repo layout, adapter interface, fixture adapter, MCP server with the tools above, redaction and budget enforcement.
2. **Scenario 1 end to end** — fixture generator, ground truth, SKILL.md workflow, incident report template. Get one scenario passing before adding more.
3. **Verifier** — claim-vs-evidence check.
4. **Eval runner** — scoring, results table.
5. **Scenarios 2–6** — iterate on SKILL.md based on eval failures.
6. **Live sandbox** — docker compose with one fault (pool exhaustion), Prometheus adapter, `make break SCENARIO=...`.
7. **Example + docs** — worked example, README, REFLECTION.md, DECISIONS.md.

## Demo plan

- **Quick path (no Docker):** `make demo SCENARIO=pool-exhaustion` runs against fixtures in under a minute.
- **Live path:** `make sandbox-up && make break SCENARIO=pool-exhaustion`, then invoke the skill in Claude Code.
- Record the live path with asciinema and link it from the README.

## README must cover

- Problem chosen and why
- How it works (diagram of the workflow and knowledge layers)
- How an on-call engineer uses it (concrete commands)
- What context/data it needs, and how an org would wire real telemetry via the adapter interface
- Key architectural decisions (summarize from this doc and DECISIONS.md)
- Limitations and failure modes, honestly, including:
  - Fabricated or over-confident evidence (mitigated by the verifier, not eliminated)
  - Anchoring on the first plausible hypothesis
  - Fixtures are cleaner than real telemetry
  - Missing instrumentation means blind spots the skill can't see
  - Cost and latency of many queries during a live incident
  - Redaction is pattern-based and can miss things
  - Overlay can rot if teams don't maintain it
- Eval results table

## Reflection prompts (REFLECTION.md)

Answer: "Deploying tomorrow for 20 engineers and trusting the code it helps produce in production, what would you change?" Points to cover:
- Shadow mode first: run alongside human on-call, compare RCAs, measure accuracy before anyone relies on it
- Real incident replay: build the eval set from the org's past incidents, not synthetic ones
- Scoped, audited read-only credentials per environment; log every query
- Stronger redaction (data classification, not just regex)
- Proposed fixes go through normal code review and CI; nothing merges on the skill's say-so
- Ownership for the taxonomy and overlay; review overlay entries like code
- Feedback capture on each RCA (correct / partially / wrong) feeding back into evals
- Cost and rate limits on observability backends during incidents

## Tech choices

- Python for the MCP server, adapters, fixture generator, and eval runner
- Claude Code skill format for the skill itself
- Docker Compose for the sandbox; tiny services can be Python (FastAPI) or Go, whichever is fastest
- Keep dependencies minimal

## Open questions for the human

- Time budget for the whole challenge? (Determines whether the live sandbox is in scope.)
- Preferred language for sandbox services?
- Should evals call the model via the Claude API directly, or drive Claude Code headlessly?
