# Incident report: payments-svc mTLS client certificate for card-gateway expired at 09:00 UTC → checkout-api 5xx

**Status:** Root cause identified · **Confidence:** high
**Paged service:** checkout-api · **Onset (T0):** 2026-10-18 09:00 UTC · **Investigated at:** 2026-10-18 09:24 UTC
**Owner of root-cause service:** payments / @payments-oncall (payments-svc)

## Summary
payments-svc's mTLS client certificate for card-gateway (`CN=payments-svc.acme.internal`) had `not_after` 2026-10-18T09:00:00Z.
From 09:00, card-gateway rejects the TLS handshake with `TLSV1_ALERT_CERTIFICATE_EXPIRED`, so payments-svc `/charge` returns 502.
checkout-api passes those 502s through: about 91% of checkouts are failing. inventory-svc is healthy.
**What to do now:** have @payments-oncall issue and roll out a renewed client certificate through secrets management. No rollback is needed, because the 08:20 deploy did not cause this.

## Impact
- checkout-api error_rate went from about 0.3% baseline to 0.9073 at 09:04 and peaked at 0.9157 at 09:17. This is the SEV1 availability burn (q004).
- payments-svc error_rate reached 0.9527 at 09:04 (q005). Card charges are failing for about 95% of attempts.

## Timeline (UTC)
| Time | Event | Evidence |
|---|---|---|
| 06:00:07 | payments-svc warns "mTLS client certificate for card-gateway expires soon", not_after 2026-10-18T09:00:00Z, days_left 0 | q012 |
| 08:20 | payments-svc deploy pay-5562 (structlog bump, commit 5be81aa). The same expiry warning is logged again at 08:20:07 on restart | q007, q012 |
| 08:20–08:57 | payments-svc logs no errors after the deploy | q008 |
| 09:00 | payments-svc upstream_error_rate jumps from 0.0004 to 0.0622 and ramps to 0.971 by 09:04 | q009 |
| 09:00 | checkout-api error_rate rises from 0.0037 to 0.0594, reaching 0.9073 by 09:04 | q004 |
| 09:06 | Page fires | alert |

## Root cause
1. **Trigger:** the mTLS client certificate payments-svc presents to card-gateway (`CN=payments-svc.acme.internal`) expired at `not_after` 2026-10-18T09:00:00Z. payments-svc had been warning about it, with `days_left: 0` at 06:00:07 and again at 08:20:07 (q012).
2. **Mechanism:** after 09:00, card-gateway rejects the handshake. payments-svc logs `card-gateway request failed: [SSL: TLSV1_ALERT_CERTIFICATE_EXPIRED] tlsv1 alert certificate expired` with status 502. That is the only error template in the window: 1291 of 1291 errors (q008).
   - The message is a TLS *alert received from the peer*, which means card-gateway rejected **our** client certificate. It does not mean card-gateway's own certificate expired.
   - payments-svc upstream_error_rate went from about 0.001 to 0.971 (q009).
   - The errors ramped over 09:00–09:04 instead of stepping straight up. This fits existing keep-alive TLS connections staying up until they were recycled.
3. **Symptom in the dependency:** payments-svc error_rate reached 0.9527 (q005).
4. **Symptom in the paged service:** checkout-api's only error template is `upstream payments-svc returned <n>`, with 1119 matches (q011). Its error rate follows payments-svc minute by minute (q004 vs q005).

## Hypotheses considered
| Hypothesis | Test (query that could disprove it) | Result | Status |
|---|---|---|---|
| Expired mTLS client cert from payments-svc to card-gateway | Error text names a cert expiry, and onset equals not_after (q008, q012) | not_after 09:00:00Z; errors start 09:00; every error is TLSV1_ALERT_CERTIFICATE_EXPIRED | **survived** |
| Bad deploy pay-5562 (payments-svc, 08:20) | A deploy cause would show errors soon after 08:20 (q008) | Zero errors in every bucket from 08:30 to 08:57; error_rate stays at baseline until 09:00 (q005) | refuted (the deploy restarted with the same old cert, but did not cause the failure) |
| inventory-svc degradation | inventory-svc RED at T0 (q006) | error_rate max 0.0015, flat through 09:00 | refuted |
| payments-svc DB pool exhaustion | db_pool_in_use pinned at a limit (q010) | 2.0–5.5 throughout, no change at T0 | refuted |
| inventory-svc config cfg-8812 (03:24) / notification-worker deploy nw-2214 (01:49) | Path and timing checks (q007, q006) | Hours before T0; inventory is healthy; notification-worker is off the checkout path | refuted |

## Mitigation (proposed — a human must execute)
1. **Renew or rotate payments-svc's mTLS client certificate for card-gateway (`CN=payments-svc.acme.internal`) and roll it out to all payments-svc instances.** Use the normal secrets-management path, then restart or reload so new handshakes use the new cert. Owner: @payments-oncall, with platform/PKI if needed.
   - **Why:** q008 and q012 show the cert expired at 09:00:00Z and every error is a cert-expired TLS alert.
   - **Risk:** low. If card-gateway pins or allowlists the client cert, the vendor may need to register the new cert or CA. Check before rollout and have the vendor contact (status.cardgateway.example) ready.
   - **Confirm by:** payments-svc upstream_error_rate drops back to about 0.001, payments-svc error_rate to about 0.002, and checkout-api error_rate back below 0.005. The TLSV1_ALERT_CERTIFICATE_EXPIRED log template should stop appearing.
2. **Do NOT roll back pay-5562.** It was not the cause: there were no errors for about 40 minutes after it (q008). A rollback would restart the service with the same expired cert.
3. Optional, while waiting for the cert: put up a degraded-mode banner on checkout so users stop retrying failed payments. This is a product decision for the IC.

## Fix
No code generated. The category is `certificate_or_credential`, so the fix is credential rotation in secrets/PKI, which is a human operational decision owned by @payments-oncall.
Longer term, payments-svc's client cert should be auto-renewed well before expiry (for example cert-manager or a scheduled rotation at 2/3 of its lifetime).

## Missing data / blind spots
- The cert's issuance and renewal pipeline is not visible in telemetry. Someone needs to check the secrets-manager / PKI audit trail to find out why auto-renewal (if it exists) did not happen.
- No card-gateway-side telemetry (external vendor). The reading that the alert means *our* client cert expired comes from the TLS alert semantics plus our own not_after log. Nothing from the vendor confirms it, but nothing contradicts it.

## Follow-ups
- **Alerting gap:** the "expires soon" warning was logged at 06:00 and 08:20 with `days_left: 0` (q012), but nothing paged. Add an alert on cert expiry < 14 days and a page at < 72h for every mTLS/client cert.
- **Process gap:** the 08:20 deploy restarted payments-svc 40 minutes before expiry and logged the warning, but nothing gated the deploy on it. Consider a deploy check that fails when a mounted cert expires within N days.
- **Runbook gap:** the page pointed at `checkout-latency`, but this was an availability failure caused by a dependency. Add a "checkout 5xx → check payments-svc upstream errors / TLS" section.
- **Proposed overlay entry (for human review):** "payments-svc: `mTLS client certificate for card-gateway expires soon` warnings are NOT benign — card-gateway hard-fails all charges at not_after. Owner @payments-oncall."

## Verification
```
[PASS] c1 … [PASS] c12 — all observations and inferences verified against the audit log
12/12 claims verified
```

## Machine-readable summary
```yaml
verdict: root_cause
confidence: high
root_cause:
  service: payments-svc
  category: certificate_or_credential
  change_id: null
  commit: null
  summary: payments-svc's mTLS client certificate for card-gateway expired at 2026-10-18T09:00:00Z, so card-gateway rejects handshakes, /charge returns 502 and checkout-api returns 5xx for ~91% of checkouts.
fix_proposed: false
missing_data: []
claims:
  - id: c1
    kind: observation
    text: checkout-api error_rate rose from 0.0037 at 08:59 to 0.0594 at 09:00 and 0.9073 at 09:04, peaking at 0.9157 at 09:17
    evidence: [q004]
    values: [0.0037, 0.0594, 0.9073, 0.9157, "2026-10-18T09:17:00Z"]
  - id: c2
    kind: observation
    text: payments-svc error_rate rose to 0.0606 at 09:00 and 0.9527 at 09:04
    evidence: [q005]
    values: [0.0606, 0.9527, "2026-10-18T09:04:00Z"]
  - id: c3
    kind: observation
    text: payments-svc upstream_error_rate (card-gateway) jumped from 0.0004 at 08:59 to 0.0622 at 09:00 and 0.971 at 09:04
    evidence: [q009]
    values: [0.0004, 0.0622, 0.971]
  - id: c4
    kind: observation
    text: all 1291 payments-svc errors after 08:30 are "card-gateway request failed - TLSV1_ALERT_CERTIFICATE_EXPIRED" with status 502, and no errors occurred in buckets from 08:30 to 08:52:30
    evidence: [q008]
    values: [1291, "TLSV1_ALERT_CERTIFICATE_EXPIRED", 502]
  - id: c5
    kind: observation
    text: payments-svc logged "mTLS client certificate for card-gateway expires soon" for CN=payments-svc.acme.internal with not_after 2026-10-18T09:00:00Z at 06:00:07 and 08:20:07
    evidence: [q012]
    values: ["mTLS client certificate for card-gateway expires soon", "CN=payments-svc.acme.internal", "2026-10-18T09:00:00Z", "2026-10-18T06:00:07.000Z", "2026-10-18T08:20:07.000Z"]
  - id: c6
    kind: observation
    text: all 1119 checkout-api errors are "upstream payments-svc returned <n>" (502)
    evidence: [q011]
    values: [1119, "upstream payments-svc returned <n>"]
  - id: c7
    kind: observation
    text: inventory-svc error_rate stayed at baseline with max 0.0015
    evidence: [q006]
    values: [0.0015]
  - id: c8
    kind: observation
    text: payments-svc db_pool_in_use stayed between 2.0 and 5.5 with no change at T0
    evidence: [q010]
    values: [2.0, 5.5]
  - id: c9
    kind: observation
    text: payments-svc deploy pay-5562 (commit 5be81aa, structlog bump) occurred at 08:20, 40 minutes before onset
    evidence: [q007]
    values: ["pay-5562", "5be81aa", "2026-10-18T08:20:00Z"]
  - id: c10
    kind: inference
    text: the expiry of payments-svc's mTLS client certificate at 09:00:00Z caused card-gateway to reject handshakes, failing /charge with 502
    based_on: [c3, c4, c5]
  - id: c11
    kind: inference
    text: checkout-api 5xx is a propagation of payments-svc 502s; inventory-svc and DB pool are not involved
    based_on: [c1, c2, c6, c7, c8]
  - id: c12
    kind: inference
    text: deploy pay-5562 did not cause the incident since payments-svc had no errors for ~40 minutes after it and onset matches cert not_after
    based_on: [c4, c5, c9]
```
