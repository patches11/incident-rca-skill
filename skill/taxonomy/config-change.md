# Config change

**Mechanism.** A change to env vars, feature flags, timeouts, pool sizes, endpoints, rate limits
or infra config alters behavior without any code change.

**Signature**
- Abrupt change aligned with a `config` / `flag` / `infra` entry in `recent_changes`.
- Limits behaving differently (pool max, timeout values, rate-limit ceilings) visible in metrics or logs.
- No deploy of the affected service near T0 — or a deploy that is clearly unrelated.

**Confirm**
- `recent_changes` → the change, its diff, and author; compare the changed value with the observed limit.
- Startup/reload logs showing the new value.

**Refute**
- Metrics behave identically before and after the change time.

**Fix.** Revert or correct the configuration through the config repo. **Not a code fix.**
