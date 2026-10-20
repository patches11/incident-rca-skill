# Cache failure / stampede

**Mechanism.** A cache becomes unavailable, is flushed, or a hot key expires; requests fall
through to the backing store, which can't absorb the load.

**Signature**
- `cache_hit_ratio` / `hit_ratio` drops sharply; DB QPS and CPU jump; latency rises.
- Cache errors (timeouts, evictions spike) or a deploy/restart that cleared an in-process cache.

**Confirm**
- Hit ratio and DB load over [T0-30m, now]; `evictions_rate`.

**Refute**
- Hit ratio steady through the symptom.

**Notes.** Short cold-cache periods after deploys are normal; check whether it self-heals.
