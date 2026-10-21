# Eval results summary

Every valid run so far, grouped by skill version. Runs that hit infrastructure errors (usage limits) are excluded. Each run is one headless `claude -p` session (default model, fixture adapter, isolated workspace). Sample sizes are small: read pass counts, not percentages.

| Version | Change |
|---|---|
| v0 | initial skill (verifies drafts via `verify_claims`) |
| v1 | + `verify_report` (verify the written file), pinned time windows |
| v2 | + level-vs-accumulation / trigger-vs-cause rules |

| Scenario | Overlay | Version | Passed | Queries (mean) | Wall s (mean) | Cost $ (mean) | Failure |
|---|---|---|---|---|---|---|---|
| bad-deploy | default | v0 | **1/1** | 13.0 | 110 | 0.85 | — |
| bad-deploy | default | v1 | **3/3** | 12.7 | 96 | 0.75 | — |
| cert-expiry | default | v0 | **1/1** | 13.0 | 141 | 0.80 | — |
| cert-expiry | default | v1 | **3/3** | 9.0 | 86 | 0.63 | — |
| insufficient-data | default | v0 | **1/1** | 17.0 | 103 | 0.82 | — |
| insufficient-data | default | v1 | **3/3** | 15.7 | 122 | 0.79 | — |
| novel-failure | default | v0 | **1/1** | 24.0 | 173 | 1.31 | — |
| novel-failure | default | v1 | **3/3** | 19.3 | 132 | 1.06 | — |
| novel-failure | empty | v0 | **0/1** | 22.0 | 151 | 1.07 | 15/15 claims failed verification; required evidence not cited |
| novel-failure | empty | v1 | **1/2** | 19.5 | 132 | 1.07 | category 'capacity_or_traffic' not in ['resource_exhaustion', 'code_regression'] |
| novel-failure | empty | v2 | **1/1** | 16.0 | 139 | 0.92 | — |
| pool-exhaustion | default | v0 | **2/2** | 15.5 | 105 | 0.76 | — |
| pool-exhaustion | default | v2 | **1/1** | 15.0 | 106 | 0.80 | — |
| red-herring | default | v0 | **1/1** | 15.0 | 108 | 0.81 | — |
| red-herring | default | v2 | **1/1** | 19.0 | 107 | 0.88 | — |

- **v0:** 7/8 runs passed
- **v1:** 13/14 runs passed
- **v2:** 3/3 runs passed
