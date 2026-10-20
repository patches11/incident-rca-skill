# Decisions

Who decided what, and why. "Human" = decided by me (the candidate) before or during the build;
"AI" = proposed by Claude Code and accepted after review. Newest last.

| # | Decision | By | Why / tradeoff |
|---|---|---|---|
| 1 | Problem: production incident RCA | Human | High-stakes, high-frequency, cross-team; on-call quality varies with experience. Hallucination risk is real, which makes guardrails the point rather than an add-on. |
| 2 | Methodology first; taxonomy and org overlay are layers on top | Human | A lookup table of known failures fails on first occurrences. Overlay must not be required for correctness (tested: scenario 6 with empty overlay). |
| 3 | Read-only tools; propose, never act | Human | Blast radius of an agent with prod write access is unacceptable. Enforced by tool surface (no write tools exist), not by prompt. |
| 4 | Adapter interface over telemetry (fixture / Prometheus) | Human | Deterministic evals + a live demo from the same skill; orgs plug in Datadog etc. by implementing one class. |
| 5 | Evals drive Claude Code headlessly (`claude -p`), not a re-implemented API loop | Human | Tests the artifact engineers actually use (skill + MCP server), not a lookalike. Costs: slower, less control over the loop. |
| 6 | Sandbox in Python/FastAPI; 3–5 day budget so the live sandbox is in scope | Human | One language across the repo. |
| 7 | Every tool result gets a `query_id`; server writes a JSONL audit log including the redacted result | AI | Gives claims something concrete to cite, gives the verifier ground truth for "what the model saw", and doubles as the per-query audit trail a real deployment needs. |
| 8 | Verifier lives in the MCP server (`verify_claims` tool) + a CLI, not a skill script | AI (accepted) | Handoff placed it in `skill/scripts/`. Moved because the server owns the audit log and adapter; a skill script would need its own copy of both. The eval re-runs the same verifier independently on the final report, so the model's self-report isn't trusted. |
| 9 | Verifier is deterministic: cited query exists + quoted values appear in its result + re-run reproduces | AI (accepted) | Catches the main failure mode (fabricated numbers / non-existent queries) cheaply and repeatably. Does *not* catch a true number used to support a wrong conclusion — inference claims are only checked structurally. LLM-judge pass noted as future work. |
| 10 | Report ends in a fenced YAML summary (verdict, category, commit, claims) | AI (accepted) | Makes verification and eval scoring deterministic instead of LLM-graded. |
| 11 | Budget counts telemetry queries only; catalog/runbook/overlay/verify are free | AI (accepted) | Budget exists to bound load on observability backends and runaway loops, not to discourage reading context. |
| 12 | Logs return histogram + top message templates + evenly-sampled lines | AI (accepted) | First-N lines hide bursts at the end of a window; templates let the model see "what's loud" without reading thousands of lines. |
| 13 | One fictional org shared by fixtures and sandbox; sandbox code is the "service repo" | AI (accepted) | Proposed fixes and regression tests are real pytest files against real code, not prose. |
| 14 | Coarse root-cause category enum + exact commit match for scoring | AI (accepted) | Free-text root-cause grading would need an LLM judge; categories plus service plus commit are checkable. Categories overlap (a leak is both `code_regression` and `resource_exhaustion`); ground truth accepts a set. |
| 15 | Memory-leak/OOM class deliberately **held out** of the taxonomy | Human | Scenario 6 measures whether methodology alone finds a failure the knowledge base doesn't describe. A real taxonomy would include it; here it's a held-out test class, stated openly. |
| 16 | Report example in the template uses a neutral fictional service | Human (caught in review) | First draft of the template used the pool-exhaustion answer as its example — would have leaked the answer into every run. |
