# Dependency degradation (internal or external)

**Mechanism.** A service we call (database, internal service, vendor API) gets slow or fails.
Our service's latency/errors follow, while its own CPU/memory/pools look normal (or pools
saturate only *because* calls are slower).

**Signature**
- Upstream-call metrics (`upstream_latency_p99_ms`, `upstream_error_rate`) or dependency spans
  rise first, at or before T0.
- Errors naming the dependency: timeouts, connection refused, 5xx from the dependency.
- No change on our side near T0.

**Confirm**
- Traces: where is the time spent? (`get_traces(..., filter="min_duration_ms=...")`).
- Dependency's own RED (if internal) or vendor status page (if external — ask the human to check).

**Refute**
- Dependency spans normal while our own spans are slow → the problem is local.

**Fix.** Usually mitigation not code: fail over, degrade gracefully, open circuit breaker, contact
vendor. A code change (timeouts, retries, breakers) may be a follow-up, not the incident fix.
