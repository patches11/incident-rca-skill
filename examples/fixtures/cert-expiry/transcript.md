# Transcript (condensed)

Every tool call the skill made, in order, with its short reasoning text in between. Raw stream-json is not committed (large); the audit log has every query result.
1. `Skill` `{"skill": "incident-rca", "args": "[FIRING] SEV1 checkout-api: SLO burn rate high (availability). checkout-api 5xx ratio 91% over the last 10m (SLO: 99.9% non-5xx). 910x burn rate. Fired 2026-10-18 09:06 UTC \u00b7 runbook: checkout-latency \u00b7 owner: @stor…`
2. `ToolSearch` `{"query": "select:mcp__incident-telemetry__current_time,mcp__incident-telemetry__service_catalog,mcp__incident-telemetry__query_metrics,mcp__incident-telemetry__search_logs,mcp__incident-telemetry__get_traces,mcp__incident-telemetry__recent_changes,mcp__incide…`
3. `current_time` `{}`
4. `service_catalog` `{}`
5. `get_org_overlay` `{}`
6. `Read` `{"file_path": "/home/patrick-brown/.cache/incident-rca-eval/20261021T002907Z/cert-expiry-3/.claude/skills/incident-rca/templates/incident-report.md"}`
7. `query_metrics` `{"service": "checkout-api", "metric": "error_rate", "start": "now-2h", "end": "now"}`
8. `query_metrics` `{"service": "payments-svc", "metric": "error_rate", "start": "now-2h", "end": "now"}`
9. `query_metrics` `{"service": "inventory-svc", "metric": "error_rate", "start": "now-2h", "end": "now"}`
10. `recent_changes` `{"start": "now-12h", "end": "now"}`

> Onset at 09:00 UTC in both checkout-api and payments-svc, and inventory-svc looks clean. Next I'm checking payments-svc's logs and its card-gateway upstream.
11. `search_logs` `{"service": "payments-svc", "query": "level=error", "start": "2026-10-18T08:30:00Z", "end": "2026-10-18T09:24:00Z", "limit": 15}`
12. `query_metrics` `{"service": "payments-svc", "metric": "upstream_error_rate", "start": "2026-10-18T08:00:00Z", "end": "now"}`
13. `query_metrics` `{"service": "payments-svc", "metric": "db_pool_in_use", "start": "2026-10-18T08:00:00Z", "end": "now"}`
14. `Glob` `{"pattern": ".claude/skills/incident-rca/taxonomy/*"}`
15. `search_logs` `{"service": "checkout-api", "query": "level=error", "start": "2026-10-18T08:30:00Z", "end": "2026-10-18T09:24:00Z", "limit": 5}`
16. `search_logs` `{"service": "payments-svc", "query": "cert", "start": "2026-10-18T03:24:00Z", "end": "2026-10-18T09:00:00Z", "limit": 10}`
17. `Read` `{"file_path": "/home/patrick-brown/.cache/incident-rca-eval/20261021T002907Z/cert-expiry-3/.claude/skills/incident-rca/taxonomy/certificate-credential-expiry.md"}`
18. `get_runbook` `{"service": "payments-svc", "name": "general-oncall"}`

> The evidence is decisive. Writing the report now.
19. `Write` `{"file_path": "incident-report.md", "content": "<report, see incident-report.md>"}`
20. `verify_report` `{}`
21. `Edit` `{"file_path": "incident-report.md", "old_string": "(see below \u2014 populated after verify_report)\u2026"}`
22. `verify_report` `{}`

> **Root cause found, high confidence:** payments-svc's client certificate for card-gateway expired at exactly 09:00:00 UTC. The category is an expired certificate or credential. checkout-api isn't broken itself; it's passing through payments-svc's failures.
> 
> **What happened:**
> - payments-svc uses a client certificate (`CN=payments-svc.acme.internal`) to connect to card-gateway. It expired at `2026-10-18T09:00:00Z`.
> - From 09:00, every card-gateway request fails with `TLSV1_ALERT_CERTIFICATE_EXPIRED` (all 1291 errors, q008). That message is card-gateway rejecting *our* certificate, not its own having expired. payments-svc then returns 502 on `/charge`.
> - payments-svc error rate is about 95% (q005). checkout-api's only error is "upstream payments-svc returned 502" (q011), giving the 91% 5xx rate you were paged for (q004).
> - payments-svc warned about this before it happened: "mTLS client certificate … expires soon", `days_left: 0`, logged at 06:00 and 08:20 (q012). Nothing alerted on it.
> 
> **Ruled out:**
> - **payments-svc deploy pay-5562 at 08:20 (structlog bump):** there were zero errors for the ~40 minutes after it. The restart just reloaded the same old certificate.
> - **inventory-svc:** healthy throughout.
> - **payments-svc database connection pool:** usage flat.
> - **Other changes earlier in the night:** both hours before onset, and neither affects checkout.
> 
> **Top mitigation (for @payments-oncall to run; I made no changes):**
> 1. Renew the certificate through secrets management/PKI and roll it out to every payments-svc instance. If card-gateway keeps a list of approved certificates, the vendor may need to register the new one.
> 2. **Don't roll back pay-5562.** It wouldn't help, because the restarted service would still have the expired certificate.
> 3. You'll know it worked when payments-svc's card-gateway error rate drops back to about 0.1% and checkout-api 5xx falls below 0.5%.
> 
> No code fix: this needs a certificate renewal, not a code change. The report also lists follow-ups:
> - Page on certificates that are close to expiry.
> - Block deploys while a certificate is about to expire.
> - Fix the page's runbook link, which points at `checkout-latency` though this is an availability failure.
> - A proposed note for the org's known-issues list ("overlay") saying this warning is not harmless.
> 
> The report passed verification (12/12 claims).
> 
> Report: `.rca/incident-report.md`

**Result:** 24 turns, 83s, $0.65
