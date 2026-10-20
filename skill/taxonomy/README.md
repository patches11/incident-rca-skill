# Generic failure taxonomy

Collective, org-independent knowledge about how production systems fail, curated from public
SRE material (Google SRE book ch. 12–13 & 22 "Addressing Cascading Failures", the USE and RED
methods, public postmortem collections such as danluu/post-mortems). Each class lists a
telemetry **signature**, queries that **confirm** it and queries that **refute** it.

Use it *after* generating hypotheses from the data, to add candidates you may have missed and
to pick decisive queries. Not exhaustive: absence from this list says nothing about whether a
failure is real.

| Class | Signature (one line) | File |
|---|---|---|
| Connection pool exhaustion | pool in-use pinned at max, rising acquire wait, DB itself under-loaded | [connection-pool-exhaustion.md](connection-pool-exhaustion.md) |
| Bad deploy / code regression | step change in latency/errors/work-per-request aligned with a deploy | [bad-deploy-regression.md](bad-deploy-regression.md) |
| Config change | step change aligned with a config/env/flag change, no code deploy | [config-change.md](config-change.md) |
| Certificate / credential expiry | abrupt TLS/auth failures at a wall-clock instant, no change | [certificate-credential-expiry.md](certificate-credential-expiry.md) |
| Dependency degradation | our upstream-call latency/errors rise first; our own resources fine | [dependency-degradation.md](dependency-degradation.md) |
| Retry storm / cascading failure | request rate *rises* as success falls; load amplifies across tiers | [retry-storm-cascade.md](retry-storm-cascade.md) |
| Capacity / traffic surge | request rate well above normal for time of day; saturation follows | [capacity-traffic-surge.md](capacity-traffic-surge.md) |
| Database lock contention | lock wait and slow queries rise; CPU not saturated | [db-lock-contention.md](db-lock-contention.md) |
| Cache failure / stampede | hit ratio drops, backing DB load and latency jump | [cache-failure-stampede.md](cache-failure-stampede.md) |

> Maintainers: one file per class, same headings. Add a class when ≥2 independent public
> incidents share a signature. Org-specific knowledge goes in the org overlay, not here.
