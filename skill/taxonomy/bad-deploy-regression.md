# Bad deploy / code regression

**Mechanism.** New code changes behavior: more work per request (N+1 queries, removed cache,
sync call added), new error path, resource leak, or incompatible contract with a dependency.

**Signature**
- Step change in latency, errors or resource use within minutes of a deploy of that service.
- Work-per-request metrics change (e.g. `db_queries_per_request`, payload size, CPU per request).
- New log message templates that did not exist before the deploy; version/commit in startup logs.

**Confirm**
- `recent_changes(T0-6h, now)` → deploy at or shortly before T0 on the suspect service.
- Before/after comparison of the same metric across the deploy time.
- Read the diff: the mechanism must be visible in it (e.g. a query inside a loop).

**Refute**
- Symptom started before the deploy, or the suspect is not in the request path.
- Metric unchanged across the deploy boundary.
- Same symptom on services that weren't deployed, with a common dependency.

**Notes.** "Deployed recently" is correlation. Require a mechanism in the diff that predicts the
observed signal. N+1 signature: queries-per-request scales with items-per-request; DB QPS and
CPU up while request rate is flat.
