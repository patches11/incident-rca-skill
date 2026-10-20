# Eval results — 20261020T200347Z

Model: `claude default` · driver: `claude -p` (headless Claude Code) · fixture adapter

| Scenario | Overlay | Pass rate | Verdict ok | Claims verified | Mean queries | Mean wall (s) | Mean cost ($) | Failures |
|---|---|---|---|---|---|---|---|---|
| bad-deploy | default | **1/1** | 1/1 | 12/12 | 13.0 | 110 | 0.85 | — |
| cert-expiry | default | **1/1** | 1/1 | 16/16 | 13.0 | 141 | 0.80 | — |
| insufficient-data | default | **1/1** | 1/1 | 13/13 | 17.0 | 103 | 0.82 | — |
| novel-failure | default | **1/1** | 1/1 | 12/12 | 24.0 | 173 | 1.31 | — |
| novel-failure | empty | **0/1** | 1/1 | 0/15 | 22.0 | 151 | 1.07 | 15/15 claims failed verification; required evidence not cited |
| pool-exhaustion | default | **1/1** | 1/1 | 14/14 | 15.0 | 105 | 0.75 | — |
| red-herring | default | **1/1** | 1/1 | 16/16 | 15.0 | 108 | 0.81 | — |

**Overall: 6/7 runs passed.**
