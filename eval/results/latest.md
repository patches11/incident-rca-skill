# Eval results — 20261021T002907Z

Model: `claude default` · driver: `claude -p` (headless Claude Code) · fixture adapter

| Scenario | Overlay | Pass rate | Verdict ok | Claims verified | Mean queries | Mean wall (s) | Mean cost ($) | Failures |
|---|---|---|---|---|---|---|---|---|
| bad-deploy | default | **3/3** | 3/3 | 35/35 | 12.7 | 96 | 0.75 | — |
| cert-expiry | default | **3/3** | 3/3 | 34/34 | 9.0 | 86 | 0.63 | — |
| insufficient-data | default | **3/3** | 3/3 | 36/36 | 15.7 | 122 | 0.79 | — |
| novel-failure | default | **3/3** | 3/3 | 52/52 | 19.3 | 132 | 1.06 | — |
| novel-failure | empty | **0/1** | 0/1 | 13/13 | 17.0 | 106 | 0.93 | category 'capacity_or_traffic' not in ['resource_exhaustion', 'code_regression'] |

**Overall: 12/13 runs passed.**
8 run(s) excluded for infrastructure errors (usage limit / CLI failure): novel-failure#2, novel-failure#3, pool-exhaustion#1, pool-exhaustion#2, pool-exhaustion#3, red-herring#1, red-herring#2, red-herring#3
