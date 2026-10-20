# Incident report: <one-line title>

**Status:** <Root cause identified | Root cause undetermined> · **Confidence:** <high | medium | n/a>
**Paged service:** <service> · **Onset (T0):** <UTC time> · **Investigated at:** <UTC time>
**Owner of root-cause service:** <team / handle from catalog>

## Summary
<3–5 sentences an incident commander can read in 20 seconds: what broke, why, impact, what to do now.
If abstaining: what we know, what we don't, and the single most useful next data source.>

## Impact
- <SLO / user-facing effect, with numbers and query ids, e.g. "web-api 5xx 3.1% (q003)">

## Timeline (UTC)
| Time | Event | Evidence |
|---|---|---|
| hh:mm | <change / signal> | q00N |

## Root cause
<Mechanism end to end: trigger → resource/mechanism → symptom in suspect → symptom in paged service.
Cite query ids inline. For a code cause, quote the relevant diff lines and the change id/commit.>

## Hypotheses considered
| Hypothesis | Test (query that could disprove it) | Result | Status |
|---|---|---|---|
| <...> | <...> (q00N) | <...> | refuted / survived / untested |

## Mitigation (proposed — a human must execute)
1. **<action>** — why: <evidence>. Risk: <...>. Confirm by: <metric that should recover>.

## Fix
<If a code fix was made: branch name, diff, regression test, and test output before/after.
Otherwise: the fix described in prose and who should own it, and why no code was generated.>

## Missing data / blind spots
- <data that would have raised confidence or that you could not see, and how to get it>

## Follow-ups
- <alert / runbook / instrumentation gaps>
- <proposed org overlay entry, if any — for human review>

## Verification
<Paste the final verify_claims output: N/N claims verified.>

## Machine-readable summary
```yaml
verdict: root_cause            # root_cause | abstain
confidence: high               # high | medium | null when abstaining
root_cause:                    # null when abstaining
  service: example-svc
  category: code_regression    # code_regression | config_change | certificate_or_credential | dependency_failure | resource_exhaustion | capacity_or_traffic | infrastructure | data_issue | unknown
  change_id: ex-123            # null if not caused by a recorded change
  commit: 0abc123              # null if not a code change
  summary: one sentence
fix_proposed: false            # true only if code was actually written (step 7 conditions)
missing_data: []               # required when abstaining
claims:
  - id: c1
    kind: observation          # observation: cites queries + quotes values found in them
    text: example-svc queue_depth reached 5000 at 14:02
    evidence: [q005]
    values: [5000, "14:02"]
  - id: c2
    kind: inference            # inference: reasoning built only on verified observations
    text: the backlog caused the timeouts
    based_on: [c1]
```
