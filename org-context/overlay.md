# Org overlay — Acme Shop

Org-specific knowledge an outside SRE couldn't know. Maintained by teams; postmortems append here.
Each entry: what you'll see, why it's (usually) benign or what it means here, owner, date added.
Entries are hints for discarding false leads — never the sole evidence for a root cause.

## notification-worker: SMTP 421 bursts
- **Signal:** notification-worker `error_rate` 5–20% with `SMTP 421 4.7.0 Try again later` warnings, usually 08:00–11:00 UTC.
- **Meaning here:** email-provider throttles our account during EU morning peak. Jobs are retried; customers are not affected. Not in the checkout request path.
- **Owner:** @growth-oncall · added 2026-03-02 (PM-0112)

## inventory-svc: cache warm-up after deploy
- **Signal:** `cache_hit_ratio` drops to ~0.3 and p99 doubles for ~3 minutes after every inventory-svc deploy.
- **Meaning here:** expected cold cache; self-heals. Only investigate if it lasts > 10 minutes.
- **Owner:** @catalog-oncall · added 2026-05-19

## checkout-api: 499s are client disconnects
- **Signal:** `status=499` in checkout-api logs.
- **Meaning here:** mobile clients abandon after 10s. Counted as non-errors by the SLO.
- **Owner:** @storefront-oncall · added 2026-01-11
