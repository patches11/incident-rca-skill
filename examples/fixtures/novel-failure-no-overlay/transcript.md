# Transcript (condensed)

Every tool call the skill made, in order, with its short reasoning text in between. Raw stream-json is not committed (large); the audit log has every query result.
1. `Skill` `{"skill": "incident-rca", "args": "[FIRING] SEV3 checkout-api: error budget burn (slow burn, 1h window). checkout-api 5xx in short bursts over the last hour; 1h error budget burn 3.2x. Fired 2026-10-19 13:31 UTC \u00b7 runbook: checkout-latency \u00b7 owner: @…`
2. `ToolSearch` `{"query": "select:mcp__incident-telemetry__current_time,mcp__incident-telemetry__service_catalog,mcp__incident-telemetry__query_metrics,mcp__incident-telemetry__recent_changes,mcp__incident-telemetry__search_logs,mcp__incident-telemetry__get_traces,mcp__incide…`
3. `current_time` `{}`
4. `service_catalog` `{}`
5. `get_org_overlay` `{}`
6. `get_runbook` `{"service": "checkout-api", "name": "checkout-latency"}`
7. `query_metrics` `{"service": "checkout-api", "metric": "error_rate", "start": "now-2h", "end": "now"}`
8. `query_metrics` `{"service": "checkout-api", "metric": "latency_p99_ms", "start": "now-2h", "end": "now"}`
9. `query_metrics` `{"service": "checkout-api", "metric": "request_rate", "start": "now-2h", "end": "now"}`

> Six bursts of 1–2 min each, at 12:05, 12:19, 12:39, 12:46, 13:13 and 13:27. Traffic is flat. Next I'll check the dependencies and checkout-api's own restarts.
10. `query_metrics` `{"service": "payments-svc", "metric": "error_rate", "start": "now-2h", "end": "now"}`
11. `query_metrics` `{"service": "inventory-svc", "metric": "error_rate", "start": "now-2h", "end": "now"}`
12. `query_metrics` `{"service": "checkout-api", "metric": "container_restarts", "start": "now-2h", "end": "now"}`
13. `search_logs` `{"service": "checkout-api", "query": "level=error", "start": "now-2h", "end": "now", "limit": 30}`

> Every checkout error is `upstream inventory-svc connection reset by peer` (502), and inventory-svc's errors spike in the same minutes. Next I'll look at inventory-svc's own resources.
14. `query_metrics` `{"service": "inventory-svc", "metric": "container_restarts", "start": "now-2h", "end": "now"}`
15. `query_metrics` `{"service": "inventory-svc", "metric": "memory_rss_mb", "start": "now-2h", "end": "now"}`
16. `search_logs` `{"service": "inventory-svc", "query": "level!=info", "start": "now-2h", "end": "now", "limit": 30}`
17. `recent_changes` `{"start": "now-7d", "end": "now"}`
18. `query_metrics` `{"service": "inventory-svc", "metric": "memory_limit_mb", "start": "now-2h", "end": "now", "step": "600s"}`
19. `query_metrics` `{"service": "inventory-svc", "metric": "request_rate", "start": "now-2h", "end": "now", "step": "300s"}`
20. `search_logs` `{"service": "inventory-svc", "query": "quote", "start": "now-3h", "end": "now", "limit": 20}`
21. `query_metrics` `{"service": "inventory-svc", "metric": "memory_rss_mb", "start": "now-6h", "end": "now-2h", "step": "600s"}`
22. `search_logs` `{"service": "inventory-svc", "query": "route=/quote/bulk", "start": "now-6h", "end": "now", "limit": 5}`
23. `Glob` `{"pattern": "sandbox/services/inventory_svc/**"}`
24. `Glob` `{"pattern": ".claude/skills/incident-rca/**"}`

> The inventory-svc repo isn't in this workspace, so I'll describe the code fix in prose instead of writing it. Next I'll read the report template.
25. `Read` `{"file_path": "/home/patrick-brown/.cache/incident-rca-eval/20261021T052223Z/novel-failure-no-overlay-1/.claude/skills/incident-rca/templates/incident-report.md"}`
26. `Write` `{"file_path": "incident-report.md", "content": "<report, see incident-report.md>"}`
27. `verify_report` `{}`
28. `Edit` `{"file_path": "incident-report.md", "old_string": "See the final verify_report output below.\u2026"}`
29. `verify_report` `{}`

> **Root cause (medium confidence):** checkout-api is returning 502s because inventory-svc keeps running out of memory. Its gunicorn workers get killed, which cuts off checkout-api's in-flight requests. The memory growth most likely comes from a cache added three days ago that never empties. The report checks out: 14/14 claims verified.
> 
> **What's happening:**
> - **The cause in code:** deploy `inv-1890` (commit `e4b7a20`, 2026-10-16 14:40 UTC) added a process-wide `_quote_cache` dict that memoizes every partner bulk quote. It has no size limit, expiry or eviction.
> - **Memory only goes up:** inventory-svc memory rose from 502.9 MB at 07:40 to 776.4 MB at 11:40 while request rate stayed flat at about 41–49 rps. It only drops when a worker is killed.
> - **What sped it up:** at about 11:50, `/quote/bulk` traffic from `partner-bulk` rose roughly 8–10x in the sampled logs. Memory growth went from about 1 MB/min to about 14 MB/min.
> - **The kills:** each burst follows memory reaching about 1010–1020 MB against a 1024 MB limit. Then the log shows `Worker ... was sent SIGKILL! Perhaps out of memory?` and the restart count goes up by one. That happened six times: 12:05, 12:19, 12:39, 12:46, 13:13 and 13:27.
> - **What checkout-api sees:** all 199 of its error logs are `upstream inventory-svc connection reset by peer` (502). p99 latency hit 1197.5 ms at 13:13, against the 800 ms SLO.
> - **Ruled out:** payments-svc (errors flat, including after its 11:10 timeout config change), checkout-api restarting itself (none), a traffic surge (rates flat), redis (a few scattered timeouts that don't line up with the bursts), and the other recent changes.
> 
> **Why medium, not high:** I had no heap profile, so the claim that `_quote_cache` is what fills the memory is inferred. It rests on the diff, the steady growth at flat load, and growth tracking bulk-quote volume. A `tracemalloc` or py-spy dump from one worker would confirm it.
> 
> **Top mitigation (for @catalog-oncall to run; I can't execute it):** roll back inventory-svc past `inv-1890`, or hotfix the cache with a size limit and expiry (e.g. a TTL cache). It worked if inventory-svc memory levels off, the restart count stops climbing, and checkout-api errors return to the ~0.003 baseline. Stopgap: a rolling restart or rate-limiting `partner-bulk`. A restart buys only about 15–25 minutes at current rates. Raising the memory limit won't help because the growth has no ceiling.
> 
> **No code written:** confidence is medium and the inventory-svc repo isn't in this workspace. The fix is described in the report instead.
> 
> Investigation used 16 of the 40-query budget. The report also lists gaps found along the way: no OOM alert on inventory-svc, and the checkout-latency runbook doesn't cover 502 "connection reset" bursts.
> 
> Report: `.rca/incident-report.md`

**Result:** 31 turns, 138s, $0.92
