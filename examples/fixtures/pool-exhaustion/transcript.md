# Transcript (condensed)

Every tool call the skill made, in order, with its short reasoning text in between. Raw stream-json is not committed (large); the audit log has every query result.
1. `Skill` `{"skill": "incident-rca", "args": "SEV2 checkout-api SLO burn rate high (availability). 5xx ratio 14.8% over last 10m, 14.4x burn. Fired 2026-10-14 09:51 UTC. runbook: checkout-latency. owner @storefront-oncall. Investigation starts 09:58 UTC."}`
2. `ToolSearch` `{"query": "select:mcp__incident-telemetry__current_time,mcp__incident-telemetry__get_org_overlay,mcp__incident-telemetry__get_runbook,mcp__incident-telemetry__get_traces,mcp__incident-telemetry__list_metrics,mcp__incident-telemetry__query_metrics,mcp__incident…`
3. `current_time` `{}`
4. `service_catalog` `{}`
5. `get_org_overlay` `{}`
6. `get_runbook` `{"service": "checkout-api", "name": "checkout-latency"}`
7. `query_metrics` `{"service": "checkout-api", "metric": "error_rate", "start": "now-2h", "end": "now"}`
8. `query_metrics` `{"service": "checkout-api", "metric": "request_rate", "start": "now-2h", "end": "now"}`
9. `query_metrics` `{"service": "payments-svc", "metric": "error_rate", "start": "now-2h", "end": "now"}`
10. `query_metrics` `{"service": "inventory-svc", "metric": "error_rate", "start": "now-2h", "end": "now"}`

> T0 = 09:43 (abrupt step), payments-svc errors track checkout exactly; inventory-svc is flat. Descending into payments-svc.
11. `query_metrics` `{"service": "payments-svc", "metric": "db_pool_in_use", "start": "now-2h", "end": "now"}`
12. `query_metrics` `{"service": "payments-svc", "metric": "db_pool_max", "start": "now-2h", "end": "now"}`
13. `recent_changes` `{"start": "now-6h", "end": "now"}`
14. `search_logs` `{"service": "payments-svc", "query": "level=error", "start": "now-2h", "end": "now", "limit": 10}`
15. `query_metrics` `{"service": "postgres-payments", "metric": "active_connections", "start": "now-2h", "end": "now", "step": "120s"}`
16. `query_metrics` `{"service": "postgres-payments", "metric": "lock_wait_ms", "start": "now-2h", "end": "now", "step": "120s"}`
17. `query_metrics` `{"service": "payments-svc", "metric": "upstream_latency_p99_ms", "start": "now-2h", "end": "now", "step": "120s"}`
18. `query_metrics` `{"service": "payments-svc", "metric": "upstream_error_rate", "start": "now-2h", "end": "now", "step": "120s"}`
19. `search_logs` `{"service": "payments-svc", "query": "declined", "start": "now-2h", "end": "now", "limit": 5}`
20. `Read` `{"file_path": "/home/patrick-brown/.cache/incident-rca-eval/20261021T052511Z/pool-exhaustion-1/.claude/skills/incident-rca/taxonomy/README.md"}`
21. `Read` `{"file_path": "/home/patrick-brown/.cache/incident-rca-eval/20261021T052511Z/pool-exhaustion-1/.claude/skills/incident-rca/taxonomy/connection-pool-exhaustion.md"}`
22. `Glob` `{"pattern": "sandbox/services/**/*"}`
23. `Read` `{"file_path": "/home/patrick-brown/.cache/incident-rca-eval/20261021T052511Z/pool-exhaustion-1/.claude/skills/incident-rca/templates/incident-report.md"}`

> Mechanism looks strong. I'll pull traces and pool wait to confirm where the time goes.
24. `get_traces` `{"service": "checkout-api", "start": "2026-10-14T09:43:00Z", "end": "now", "filter": "status=error", "limit": 3}`
25. `query_metrics` `{"service": "payments-svc", "metric": "db_pool_wait_ms", "start": "now-1h", "end": "now", "step": "120s"}`

> Evidence is conclusive. Writing the report now.
26. `Write` `{"file_path": "incident-report.md", "content": "<report, see incident-report.md>"}`
27. `verify_report` `{}`
28. `Edit` `{"file_path": "incident-report.md", "old_string": "_See below; to be filled after verify_report._\u2026"}`
29. `verify_report` `{}`

> **Root cause found, high confidence.** The checkout-api errors are caused by a bad payments-svc deploy that leaks database connections. It's a code bug, and the payments team (@payments-oncall) owns it.
> 
> **What happened:** At 09:38 UTC, payments-svc deploy **pay-5531** (commit `a41f9e2`, FRAUD-231) started saving a record to the database whenever a card is declined. The declined path then returns an error (HTTP 402) without releasing its database connection. Every declined card keeps one connection forever.
> - The pool of 20 connections started filling at 09:39 and was completely full from 09:43, the incident start (q009/q010).
> - After that, payments `/charge` requests wait 1s for a connection, give up, and return 503 (`QueuePool limit of size 20 overflow 0 reached`). That error appears 610 times in the logs, and never before the deploy (q012).
> - checkout-api passes those failures on, so about 16–18% of checkouts fail (q005). In the failing traces, about 1001 ms of a 1063 ms checkout is spent waiting for that connection (q018).
> 
> **What I ruled out:**
> - **inventory-svc:** its error rate stayed flat and its calls are fast.
> - **card-gateway (the card vendor):** its latency and error rate are unchanged.
> - **The payments database (postgres-payments):** lock waits are normal.
> - **A traffic spike:** request rate stayed around 14–18 per second.
> 
> **Recommended action (for you to run; I can't change production):**
> 1. **Roll back payments-svc to the version before pay-5531.** You'll know it worked when payments-svc `db_pool_in_use` drops back to about 3–6 and checkout-api `error_rate` returns to about 0.3%.
> 2. If the rollback is delayed, a restart only helps for about 5 minutes, because declined cards fill the pool again.
> 3. Don't increase the pool size; that only delays the problem.
> 
> **Fix:** I didn't write any code because the payments-svc repo isn't in this workspace. The fix for the payments team is to always release the connection, by wrapping the request handler in `with SessionLocal() as session:` or `try/finally: session.close()`. They should also add a regression test that sends at least 20 declined charges and then checks that a normal charge still succeeds.
> 
> The report also includes a proposed pool-saturation alert, runbook updates, and a suggested overlay entry. I used 15 of the 40 telemetry queries, and all 14 claims in the report passed `verify_report`.
> 
> **Report:** `.rca/incident-report.md`

**Result:** 31 turns, 105s, $0.80
