# Transcript (condensed)
Every tool call the skill made against the **live** sandbox (Prometheus adapter), in order, with the model's short reasoning text between calls. Raw stream: `transcript.jsonl`.
1. `Skill` `{"skill": "incident-rca", "args": "[FIRING] SEV2 checkout-api: SLO burn rate high (availability) \u2014 checkout-api 5xx ratio elevated over the last 10m, p99 latency over 2s. Owner @storefront-oncall. Live sandbox environment; service code is in this repo."}`
2. `ToolSearch` `{"query": "select:mcp__incident-telemetry__current_time,mcp__incident-telemetry__service_catalog,mcp__incident-telemetry__query_metrics,mcp__incident-telemetry__recent_changes,mcp__incident-telemetry__search_logs,mcp__incident-telemetry__get_traces,mcp__incident-telemetry__get_org_overlay,mcp__incid…`
3. `current_time` `{}`
4. `service_catalog` `{}`
5. `get_org_overlay` `{}`
6. `query_metrics` `{"service": "checkout-api", "metric": "error_rate", "start": "now-2h", "end": "now"}`
7. `query_metrics` `{"service": "checkout-api", "metric": "latency_p99_ms", "start": "now-2h", "end": "now"}`
8. `query_metrics` `{"service": "checkout-api", "metric": "request_rate", "start": "now-2h", "end": "now"}`
9. `recent_changes` `{"start": "now-6h", "end": "now"}`
10. `query_metrics` `{"service": "checkout-api", "metric": "error_rate", "start": "2026-10-20T19:30:00Z", "end": "2026-10-20T21:00:00Z"}`
11. `query_metrics` `{"service": "payments-svc", "metric": "error_rate", "start": "2026-10-20T19:30:00Z", "end": "2026-10-20T21:00:00Z"}`
12. `query_metrics` `{"service": "payments-svc", "metric": "db_pool_in_use", "start": "2026-10-20T19:30:00Z", "end": "2026-10-20T21:00:00Z"}`
13. `query_metrics` `{"service": "inventory-svc", "metric": "error_rate", "start": "2026-10-20T19:30:00Z", "end": "2026-10-20T21:00:00Z"}`
14. `search_logs` `{"service": "payments-svc", "query": "level=error", "start": "now-6h", "end": "now", "limit": 10}`
15. `get_traces` `{"service": "checkout-api", "start": "now-1h", "end": "now", "filter": "min_duration_ms=2000", "limit": 3}`
16. `query_metrics` `{"service": "payments-svc", "metric": "upstream_latency_p99_ms", "start": "2026-10-20T19:30:00Z", "end": "2026-10-20T21:00:00Z"}`
17. `search_logs` `{"service": "checkout-api", "query": "level=error", "start": "now-6h", "end": "now", "limit": 5}`
18. `Bash` `{"command": "ls sandbox/services/payments_svc sandbox/services/payments_svc/tests; cat sandbox/services/payments_svc/app.py", "description": "Show payments-svc code and tests"}`
19. `search_logs` `{"service": "payments-svc", "query": "level!=info -QueuePool", "start": "now-6h", "end": "now", "limit": 8}`
20. `query_metrics` `{"service": "payments-svc", "metric": "db_pool_wait_ms", "start": "2026-10-20T19:30:00Z", "end": "2026-10-20T21:00:00Z"}`
21. `query_metrics` `{"service": "postgres-payments", "metric": "lock_wait_ms", "start": "2026-10-20T19:30:00Z", "end": "2026-10-20T21:00:00Z"}`
22. `search_logs` `{"service": "payments-svc", "query": "starting", "start": "now-6h", "end": "now", "limit": 5}`
23. `search_logs` `{"service": "payments-svc", "query": "status=402", "start": "2026-10-20T20:09:00Z", "end": "now", "limit": 10}`
24. `search_logs` `{"service": "payments-svc", "query": "status=200", "start": "2026-10-20T20:09:00Z", "end": "now", "limit": 8}`
25. `Bash` `{"command": "git log --oneline -6 --all; git cat-file -t 863745e 2>&1; cat sandbox/services/payments_svc/tests/test_charge.py sandbox/services/payments_svc/tests/conftest.py", "description": "Check commits and existing payments tests"}`
26. `Bash` `{"command": "git checkout -b rca/payments-pool-held-across-risk-score && ls .claude/skills/incident-rca/ .claude/skills/incident-rca/templates && cat .claude/skills/incident-rca/templates/incident-report.md", "description": "Create fix branch and read report template"}`

> Now the regression test (should fail on current code):
27. `Bash` `{"command": "cat >> sandbox/services/payments_svc/tests/test_charge.py <<'EOF'\n\n\ndef test_risk_score_call_does_not_hold_a_db_connection(app_module, client, monkeypatch):\n    # RCA: ed3be52 called the slow card-gateway risk API while holding a pooled connection,\n    # pinning the pool at its lim…`
28. `Bash` `{"command": "uv run --with-requirements sandbox/services/requirements-dev.txt pytest -q sandbox/services/payments_svc/tests 2>&1 | grep -E \"^E |assert\" | head -10", "description": "Show failure assertions"}`

> Both fail for the expected reasons. Applying the minimal fix:
29. `Edit` `{"file_path": "/home/patrick-brown/.cache/incident-rca-live/repo/sandbox/services/payments_svc/app.py", "old_string": "        _acquire(session)\n        session.execute(insert(payments).values(user_id=req.user_id, amount_cents=req.amount_c\u2026"}`
30. `Bash` `{"command": "uv run --with-requirements sandbox/services/requirements-dev.txt pytest -q sandbox/services/payments_svc/tests 2>&1 | tail -2; git diff --stat", "description": "Run payments tests after fix"}`

> All 4 tests pass. Writing the report.
31. `Write` `{"file_path": "/home/patrick-brown/.cache/incident-rca-live/repo/.rca/incident-report.md", "content": "<report, see incident-report.md>"}`
32. `verify_report` `{}`

> Verification failed only on claims citing queries with relative windows (`now-…`): time moved, so re-runs differ. I'll re-run those with absolute windows.
33. `recent_changes` `{"start": "2026-10-20T18:25:00Z", "end": "2026-10-21T00:25:00Z"}`
34. `search_logs` `{"service": "payments-svc", "query": "level=error", "start": "2026-10-20T20:00:00Z", "end": "2026-10-21T00:25:00Z", "limit": 5}`
35. `query_metrics` `{"service": "checkout-api", "metric": "error_rate", "start": "2026-10-20T22:25:00Z", "end": "2026-10-21T00:25:00Z"}`
36. `query_metrics` `{"service": "checkout-api", "metric": "latency_p99_ms", "start": "2026-10-20T22:25:00Z", "end": "2026-10-21T00:25:00Z"}`
37. `query_metrics` `{"service": "checkout-api", "metric": "request_rate", "start": "2026-10-20T22:25:00Z", "end": "2026-10-21T00:25:00Z"}`
38. `search_logs` `{"service": "checkout-api", "query": "level=error", "start": "2026-10-20T20:00:00Z", "end": "2026-10-21T00:25:00Z", "limit": 3}`
39. `search_logs` `{"service": "payments-svc", "query": "status=402", "start": "2026-10-20T20:09:00Z", "end": "2026-10-21T00:25:00Z", "limit": 10}`
40. `search_logs` `{"service": "payments-svc", "query": "status=200", "start": "2026-10-20T20:09:00Z", "end": "2026-10-21T00:25:00Z", "limit": 8}`
41. `search_logs` `{"service": "payments-svc", "query": "starting", "start": "2026-10-20T20:00:00Z", "end": "2026-10-20T20:30:00Z", "limit": 5}`
42. `Bash` `{"command": "cd .rca && python3 -I - <<'EOF'\nimport re\np = \"incident-report.md\"\ns = open(p).read()\nfor a, b in [\n    (\"q007\", \"q022\"), (\"q013\", \"q023\"), (\"q004\", \"q024\"), (\"q005\", \"q025\"), (\"q006\", \"q026\"),\n    (\"q015\", \"q027\"), (\"q020\", \"q028\"), (\"q021\", \"q029…`
43. `verify_report` `{}`
44. `Edit` `{"file_path": "/home/patrick-brown/.cache/incident-rca-live/repo/.rca/incident-report.md", "old_string": "(see below \u2014 final verify_report output)\u2026"}`
45. `verify_report` `{}`

> **The cause is a code regression in payments-svc, not in checkout-api. Confidence is high, and all 16 claims in the report pass `verify_report`.**
> 
> **What happened:** at 20:08:09Z, deploy `pay-live-1` (commit `ed3be52`, FRAUD-231) added a call to card-gateway's risk-score API in `/charge`. The call runs after a database connection is taken from the pool and before the commit. Each successful charge now holds one of the pool's 10 connections for about 1–2s.
> 
> - **Within one minute of the deploy:**
>   - the pool went from 0 to 10/10 in use and stayed there;
>   - the wait to get a connection rose from about 5ms to about 1.9s;
>   - card-gateway p99 went from 121ms to 1963ms.
> - **payments-svc errors:** other `/charge` requests can't get a connection within the 1s pool timeout (`QueuePool limit of size 10 overflow 0 reached`) and return 503.
> - **checkout-api:** those 503s and payments timeouts after 2000ms surface as roughly 28% 5xx, with p99 around 2.94s.
> - **What I ruled out:** inventory-svc had no errors, traffic was flat at about 12 rps, and card-gateway's charge endpoint is healthy (declined charges, which call only that endpoint, still take 30–103ms).
> 
> **Second bug in the same change:** the new `UPDATE ... WHERE user_id = ...` overwrites the risk score on every past payment for that user, not just the new one. Risk scores written since 20:08 are probably wrong and need recomputing.
> 
> **Mitigation (for you to run; I can't execute it):** roll back payments-svc to the build before `pay-live-1`. You'll know it worked when `db_pool_in_use` drops below 10, `db_pool_wait_ms` falls back to single-digit ms, and checkout-api 5xx goes to about 0 with p99 under 800ms. Don't treat a bigger pool as the fix; it only moves the limit.
> 
> **Code fix:** on local branch `rca/payments-pool-held-across-risk-score`, not committed or pushed. It calls the risk API before taking a connection and writes the score as part of the insert, so only the new row gets it. I added two regression tests: before the fix they failed (`[1] == [0]` connections held, `[22, 22] == [11, 22]` scores); after it, all 4 payments-svc tests pass.
> 
> **Caveats:**
> - The pre-deploy baseline is only about 40 seconds long: the sandbox started at 20:07:32.
> - There's no tracing in this environment, so "the time is spent in the risk-score call" is inferred from fast declines versus slow successful charges, not seen in spans.
> - The change record says commit `ed3be52`, but the restarted process logs `commit: 863745e`, which isn't a commit in this repo. Someone should confirm which build is actually running.
> - The alert fired about 4 hours after onset. The report's follow-ups suggest an alert on the pool being full.
> 
> The full report, with timeline, hypotheses, the diff and follow-ups, is at `.rca/incident-report.md`.

**Result:** 47 turns, 208s, $1.25
