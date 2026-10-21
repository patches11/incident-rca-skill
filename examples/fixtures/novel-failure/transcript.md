# Transcript (condensed)

Every tool call the skill made, in order, with its short reasoning text in between. Raw stream-json is not committed (large); the audit log has every query result.
1. `Skill` `{"skill": "incident-rca", "args": "[FIRING] SEV3 checkout-api: error budget burn (slow burn, 1h window). checkout-api 5xx in short bursts over the last hour; 1h error budget burn 3.2x. Fired 2026-10-19 13:31 UTC \u00b7 runbook: checkout-latency \u00b7 owner: @…`
2. `ToolSearch` `{"query": "select:mcp__incident-telemetry__current_time,mcp__incident-telemetry__service_catalog,mcp__incident-telemetry__query_metrics,mcp__incident-telemetry__list_metrics,mcp__incident-telemetry__recent_changes,mcp__incident-telemetry__search_logs,mcp__inci…`
3. `current_time` `{}`
4. `service_catalog` `{}`
5. `get_org_overlay` `{}`
6. `get_runbook` `{"service": "checkout-api", "name": "checkout-latency"}`
7. `query_metrics` `{"service": "checkout-api", "metric": "error_rate", "start": "now-2h", "end": "now"}`
8. `query_metrics` `{"service": "checkout-api", "metric": "latency_p99_ms", "start": "now-2h", "end": "now"}`
9. `query_metrics` `{"service": "checkout-api", "metric": "request_rate", "start": "now-2h", "end": "now"}`
10. `recent_changes` `{"start": "now-8h", "end": "now"}`

> Six 1–2-minute bursts (12:05, 12:19, 12:39, 12:46, 13:13, 13:27), traffic flat. Checking both dependencies.
11. `query_metrics` `{"service": "payments-svc", "metric": "error_rate", "start": "now-2h", "end": "now"}`
12. `query_metrics` `{"service": "inventory-svc", "metric": "error_rate", "start": "now-2h", "end": "now"}`
13. `search_logs` `{"service": "checkout-api", "query": "level=error", "start": "now-2h", "end": "now", "limit": 30}`

> Inventory-svc is the source: 502 "connection reset by peer" from inventory-svc, matching its bursts. Connection resets point to the process dying, so I'm checking restarts and memory next.
14. `query_metrics` `{"service": "inventory-svc", "metric": "container_restarts", "start": "now-2h", "end": "now"}`
15. `query_metrics` `{"service": "inventory-svc", "metric": "memory_rss_mb", "start": "now-2h", "end": "now"}`
16. `search_logs` `{"service": "inventory-svc", "query": "level!=info", "start": "now-2h", "end": "now", "limit": 30}`
17. `query_metrics` `{"service": "inventory-svc", "metric": "memory_limit_mb", "start": "now-6h", "end": "now", "step": "300s"}`
18. `query_metrics` `{"service": "inventory-svc", "metric": "memory_rss_mb", "start": "now-6h", "end": "now", "step": "300s"}`
19. `query_metrics` `{"service": "inventory-svc", "metric": "request_rate", "start": "now-6h", "end": "now", "step": "300s"}`

> Memory grew slowly (about 1.2 MB/min) from about 08:00, then got roughly 10x steeper at about 11:50. Request rate only ramps gradually. Next I'm looking at what changed at 11:50: cache, redis and DB.
20. `query_metrics` `{"service": "inventory-svc", "metric": "cache_hit_ratio", "start": "now-6h", "end": "now", "step": "300s"}`
21. `query_metrics` `{"service": "inventory-svc", "metric": "db_queries_per_request", "start": "now-6h", "end": "now", "step": "300s"}`
22. `search_logs` `{"service": "inventory-svc", "query": "", "start": "2026-10-19T11:40:00Z", "end": "2026-10-19T12:00:00Z", "limit": 40}`
23. `search_logs` `{"service": "inventory-svc", "query": "partner-bulk", "start": "now-6h", "end": "now", "limit": 20}`
24. `search_logs` `{"service": "inventory-svc", "query": "/quote/bulk", "start": "2026-10-19T07:40:00Z", "end": "2026-10-19T11:50:00Z", "limit": 10}`
25. `recent_changes` `{"service": "inventory-svc", "start": "now-7d", "end": "now"}`
26. `Glob` `{"pattern": "sandbox/services/inventory_svc/**/*"}`
27. `Glob` `{"pattern": ".claude/skills/incident-rca/**/*"}`
28. `Read` `{"file_path": "/home/patrick-brown/.cache/incident-rca-eval/20261021T002907Z/novel-failure-2/.claude/skills/incident-rca/templates/incident-report.md"}`
29. `Write` `{"file_path": "incident-report.md", "content": "<report, see incident-report.md>"}`
30. `verify_report` `{}`
31. `Edit` `{"file_path": "incident-report.md", "old_string": "## Verification\n(see below)\u2026"}`
32. `verify_report` `{}`

> **Verdict:** root cause identified, high confidence, `code_regression` in **inventory-svc** (owner: @catalog-oncall). The fault isn't in checkout-api itself.
> 
> **Root cause:** deploy **inv-1890** (commit `e4b7a20`, 2026-10-16) added a cache for bulk quotes (`_quote_cache` in `bulk_quote()`) with no size limit and no eviction. Every bulk-quote request from the client `partner-bulk` (168–400 items each) adds an entry that is never freed.
> 
> **How it reaches the page:**
> 1. inventory-svc memory was flat at about 500 MB until bulk-quote traffic started at about 08:00 today, then grew slowly.
> 2. At about 11:50 bulk-quote traffic rose about 10x, and memory grew about 12x faster.
> 3. Each time memory nears the 1024 MB limit, a worker process is killed for running out of memory. That happened six times (12:05, 12:19, 12:39, 12:46, 13:13, 13:27).
> 4. Each kill drops the worker's open connections, and checkout-api returns 502 "upstream inventory-svc connection reset by peer" for those requests. That's your 1–2 minute bursts, peaking at a 26% error rate.
> 
> **Ruled out:**
> - **payments-svc:** flat, at most 0.28% errors.
> - **checkout-api traffic:** no surge.
> - **inventory-svc cache and database:** cache hit ratio and queries per request both flat.
> - **The two config changes today:** the inventory-svc log sampling change and the payments-svc gateway timeout change line up with neither the memory growth nor the errors.
> 
> **Top mitigation (for you to run; I can't execute it):** roll back inventory-svc to the version before inv-1890, or ship a hotfix that removes the bulk-quote caching. If that can't happen quickly, throttle `partner-bulk` on `/quote/bulk` as a stopgap. Don't raise the memory limit as the fix; it only spaces the kills further apart. Until something changes, expect another burst every 15–25 minutes. The fix has worked when inventory-svc memory levels off and its restart count stops rising.
> 
> **Caveats:**
> - No memory profile was available. Blaming the cache rests on the diff plus the close match between memory growth and bulk traffic. Checking the cache's size on a live worker would confirm it.
> - Why `partner-bulk` volume jumped at 11:50 is unknown.
> - No code fix was generated: the inventory-svc repo isn't in this workspace. The report describes the fix in prose: replace the dict with a cache that has a size limit and expiry, plus a regression test.
> 
> All 18 claims in the report passed `verify_report`. The report also proposes new alerts (inventory-svc restarts, memory near its limit), a runbook addition and an overlay entry.
> 
> Report: `.rca/incident-report.md`

**Result:** 34 turns, 142s, $1.11
