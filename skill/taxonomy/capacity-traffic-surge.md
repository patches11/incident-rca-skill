# Capacity / traffic surge

**Mechanism.** More (or heavier) traffic than provisioned: launch, batch job, bot, a new client.

**Signature**
- `request_rate` well above the same time on a normal day; CPU/pool/queue saturation follows.
- Latency rises smoothly with load (no step aligned with a change).
- A new or dominant client/route/tenant in logs.

**Confirm**
- Compare rate against earlier hours/days; break down logs by client/route field.

**Refute**
- Rate normal for time of day; or work-per-request changed instead (see bad-deploy-regression).
