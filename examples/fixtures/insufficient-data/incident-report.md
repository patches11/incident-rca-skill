# Incident report: checkout-api 5xx at ~94% because payments-svc is unreachable; why payments-svc went dark is unknown

**Status:** Root cause undetermined · **Confidence:** n/a
**Paged service:** checkout-api · **Onset (T0):** 2026-10-17 09:30 UTC · **Investigated at:** 2026-10-17 09:47 UTC
**Owner of suspect service:** payments team / @payments-oncall (payments-svc)

## Summary
Starting at 09:30 UTC, checkout-api's error rate jumped from about 0.3% to about 94%, and p99 latency went from about 330ms to about 1.8s (q004, q006). Every sampled error is checkout-api failing to reach payments-svc: either `connection refused` (502) or `connect timeout after 2000ms` (504) (q010). At 09:28–09:30, payments-svc stopped sending all telemetry: metrics, logs and traces (q008, q011, q014). At the same moment, connections to postgres-payments dropped from ~7.5 to 3 (q015). So payments-svc's processes are down or can't be reached. **Why** they went down can't be seen in the available telemetry. No change was recorded (q007, q017), restarts were 0 and memory was flat up to the last sample (q012, q013), and nothing was logged before it went silent (q016). **Next step:** @payments-oncall should check pod/node state and orchestrator events for payments-svc from 09:25 to 09:35.

## Impact
- checkout-api 5xx ratio was 0.9176–0.9534 per minute from 09:31 onward, against a baseline of about 0.003 (q004). Nearly all checkouts are failing.
- checkout-api p99 rose to 1718.9–2014.3ms, up from about 300ms. That matches the 2000ms connect timeout (q006, q010).
- Incoming traffic was normal at 14.7–17.6 rps, so this is not a load spike (q005).

## Timeline (UTC)
| Time | Event | Evidence |
|---|---|---|
| 02:12 | notification-worker deploy nw-2214 (off-path, email copy) | q007 |
| 03:47 | inventory-svc config cfg-8812 (LOG_SAMPLING_RATE 0.01→0.02) | q007 |
| 09:27:29 | Last sampled payments-svc log line ("charge ok", 200) | q011 |
| 09:28 | Last payments-svc metric sample (error_rate 0.0011, memory_rss 312.8MB, restarts 0) | q008, q013, q012 |
| 09:28:58 | Last payments-svc trace, healthy (POST /charge 115.6ms) | q014 |
| 09:30 | postgres-payments active_connections 7.5 → 3.0; checkout-api error_rate 0.2405, first 504 "upstream connect timeout after 2000ms (payments-svc:8080)" | q015, q004, q010 |
| 09:31 | checkout-api error_rate 0.939; "connection refused (payments-svc:8080)" errors begin | q004, q010 |
| 09:33 | Page fires | alert |
| 09:31–09:47 | payments-svc: zero log records, no metric samples | q011, q008 |

## Root cause
**Undetermined.** Here is the chain I can support with evidence:

1. checkout-api's 5xx errors all come from calls to payments-svc. The top error messages are `upstream connect error: connection refused (payments-svc:<n>)` (379) and `upstream connect timeout after <n>ms (payments-svc:<n>)` (326) (q010). inventory-svc, the other dependency, held an error rate of about 0.001 throughout (q009).
2. payments-svc stopped running or became unreachable at about 09:28–09:30. Its metrics end at 09:28 (q008, q012, q013). Its logs drop to 0 records per bucket after 09:31:20 (q011). Its last trace is at 09:28:58 (q014). Its DB connections disappear: postgres-payments drops to a flat 3.0 from 09:30 (q015).
3. Before going silent, payments-svc looked healthy. Error rate was ≤0.0033 (q008), memory was steady at 305–312.8MB (q013), the DB pool acquire took under 1ms in traces (q014), and the only non-info logs were ordinary card declines (q016).

The missing piece is the **trigger** that took payments-svc down. It went from healthy to completely gone in one sample, with no warning and no recorded change. Possible explanations include node/host failure, a crash or kill that left no log, a network/service-mesh partition, or an infra action that isn't in the change log. The available telemetry can't tell these apart.

## Hypotheses considered
| Hypothesis | Test (query that could disprove it) | Result | Status |
|---|---|---|---|
| Recent deploy/config change caused it | recent_changes all services T0-8h (q007), payments-svc 3d (q017) | Only off-path/unrelated changes hours earlier; none for payments-svc | refuted (for recorded changes) |
| Traffic surge overloaded checkout | checkout-api request_rate (q005) | 14.7–17.6 rps, same as baseline | refuted |
| inventory-svc failure | inventory-svc error_rate (q009) | ~0.001, flat through T0 | refuted |
| payments-svc DB pool exhaustion / postgres issue | traces db.pool.acquire (q014), postgres connections (q015) | Pool acquire <1ms right before T0; connections *dropped*, not saturated | refuted as trigger |
| payments-svc memory leak → OOM kill | memory_rss_mb (q013), container_restarts (q012) | Flat at ~309MB, restarts 0. But the series stops, so a later kill can't be ruled out | not supported; untestable after 09:28 |
| payments-svc host/node/network failure or silent crash | — no telemetry after 09:28 | Consistent with the data, but nothing directly confirms it | untested (data missing) |
| card-gateway (external) outage | No metrics exist for card-gateway (q002) | A gateway outage wouldn't cause connection refused *to payments-svc*; healthy 69.4ms span at 09:28:58 (q014) | unlikely, not directly testable |

## Mitigation (proposed — a human must execute)
1. **Restore payments-svc capacity.** @payments-oncall should check pod/instance status and reschedule or restart unhealthy payments-svc instances, or move them onto healthy nodes. Why: checkout-api gets connection refused and timeouts on payments-svc:8080 (q010), and payments-svc has no telemetry or DB connections since 09:30 (q011, q015). Risk: low, since the service is already serving nothing. Before restarting, capture pod/node events and any crash state so the trigger can still be investigated afterwards. Confirm by: payments-svc metrics and logs resume, postgres-payments active_connections returns to about 7–8, and checkout-api error_rate drops back to about 0.003.
2. **If it's a zone or node failure**, fail over or scale payments-svc in a healthy zone. Confirm with the same metrics.
3. Don't change checkout-api. It is a victim, and its timeout/502 behaviour is working as designed (q018).

## Fix
No code fix was produced. The verdict is abstain, and nothing points to a code regression. The fix depends on whatever took payments-svc offline, so @payments-oncall and platform should decide it once the trigger is known.

## Missing data / blind spots
- **payments-svc pod/container state and orchestrator events (09:25–09:40):** shows whether the pods were killed (OOMKilled, evicted, failed liveness) or the node went away. Get it with `kubectl describe pod` / events for payments-svc, or the deploy platform's history (a human runs this).
- **Node/host logs and status for the payments-svc hosts (09:25–09:40):** shows whether there was a host, kernel or hypervisor failure. Get it from cloud console instance status checks and node syslog/kubelet logs.
- **Service mesh / load balancer endpoint health for payments-svc:8080:** separates "process down" from "network partition". `connection refused` suggests nothing is listening, while the timeouts suggest some packets are being dropped.
- **Infra/platform changes not in the change log:** node pool upgrades, autoscaler actions, spot preemptions, security-group/network policy edits around 09:28. Get these from the cloud audit log and platform team.
- **payments-svc stdout/stderr outside the structured log pipeline:** a hard crash, such as a segfault or panic before the logger flushes, wouldn't show up in q011/q016.

## Follow-ups
- Add an alert on payments-svc **absence of telemetry / zero healthy endpoints**. The page came from checkout-api burn rate, and nothing on payments-svc fired.
- The runbook `checkout-latency` should say that 502 "connection refused" to payments-svc means payments-svc is down, and to page @payments-oncall right away.
- Make sure orchestrator/node events are exported to the telemetry backend, so a future RCA can see pod kills and node loss.

## Verification
verify_report: c1–c13 all PASS — **13/13 claims verified**.

## Machine-readable summary
```yaml
verdict: abstain
confidence: null
root_cause: null
fix_proposed: false
missing_data:
  - payments-svc pod/container state and orchestrator events 09:25-09:40 UTC
  - node/host status and system logs for payments-svc hosts 09:25-09:40 UTC
  - service mesh / load balancer endpoint health for payments-svc:8080
  - infra/platform changes not in recent_changes (node pool, autoscaler, network policy) around 09:28 UTC
  - payments-svc raw stdout/stderr (crash output) around 09:28 UTC
claims:
  - id: c1
    kind: observation
    text: checkout-api error_rate rose from baseline ~0.003 to 0.2405 at 09:30 and 0.939 at 09:31, peaking at 0.9534
    evidence: [q004]
    values: [0.2405, 0.939, 0.9534, "2026-10-17T09:30:00Z"]
  - id: c2
    kind: observation
    text: checkout-api latency_p99_ms rose from ~338.5 at 09:29 to 1810.5 at 09:31, max 2014.3
    evidence: [q006]
    values: [338.5, 1810.5, 2014.3]
  - id: c3
    kind: observation
    text: checkout-api request_rate stayed within 14.7-17.6 rps through the incident
    evidence: [q005]
    values: [14.7, 17.6]
  - id: c4
    kind: observation
    text: checkout-api errors are all upstream failures to payments-svc - connection refused (379) and connect timeout (326)
    evidence: [q010]
    values: ["upstream connect error: connection refused (payments-svc:<n>)", 379, "upstream connect timeout after <n>ms (payments-svc:<n>)", 326]
  - id: c5
    kind: observation
    text: inventory-svc error_rate stayed low (max 0.0016) through T0
    evidence: [q009]
    values: [0.0016]
  - id: c6
    kind: observation
    text: payments-svc metrics end at 09:28, with error_rate 0.0011, memory_rss_mb 312.8 and container_restarts 0 at the last sample
    evidence: [q008, q013, q012]
    values: ["2026-10-17T09:28:00Z", 0.0011, 312.8]
  - id: c7
    kind: observation
    text: payments-svc logs drop to 0 records in every bucket from 09:31:20 onward; top messages before that were charge ok and card declined
    evidence: [q011]
    values: ["2026-10-17T09:31:20Z", "charge ok", 86]
  - id: c8
    kind: observation
    text: last payments-svc trace at 09:28:58 was healthy, POST /charge 115.6ms with db.pool.acquire 0.44ms
    evidence: [q014]
    values: ["2026-10-17T09:28:58.691Z", 115.6, 0.44]
  - id: c9
    kind: observation
    text: postgres-payments active_connections fell from 7.5 at 09:29 to a flat 3.0 from 09:30
    evidence: [q015]
    values: [7.5, 3.0, "2026-10-17T09:30:00Z"]
  - id: c10
    kind: observation
    text: no changes recorded for payments-svc in 3 days; only changes org-wide in T0-8h were nw-2214 and cfg-8812
    evidence: [q017, q007]
    values: ["nw-2214", "cfg-8812"]
  - id: c11
    kind: observation
    text: payments-svc non-info logs before T0 were only card declined by issuer warnings (12)
    evidence: [q016]
    values: ["card declined by issuer", 12]
  - id: c12
    kind: inference
    text: checkout-api 5xx is caused by payments-svc being down or unreachable since about 09:28-09:30
    based_on: [c1, c4, c5, c6, c7, c9]
  - id: c13
    kind: inference
    text: the trigger for payments-svc going down is not visible - no recorded change, no pre-failure degradation, no error logs - so the root cause is undetermined
    based_on: [c6, c8, c10, c11]
```
