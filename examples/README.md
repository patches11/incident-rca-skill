# Examples

| Example | Adapter | What it shows |
|---|---|---|
| [live-pool-exhaustion](live-pool-exhaustion/) | Prometheus (live sandbox) | End to end on real services: RCA, verified claims, a regression test that fails first, a fix on a local branch, and a second bug it found on its own |
| [fixtures/pool-exhaustion](fixtures/pool-exhaustion/) | fixture | Dependency traversal: page on checkout-api, cause two hops down |
| [fixtures/bad-deploy](fixtures/bad-deploy/) | fixture | Change correlation picking the right one of two nearby deploys |
| [fixtures/red-herring](fixtures/red-herring/) | fixture | Ignoring a loud, pre-existing error stream and a tempting nearby deploy |
| [fixtures/insufficient-data](fixtures/insufficient-data/) | fixture | Abstaining correctly and listing the missing data |
| [fixtures/cert-expiry](fixtures/cert-expiry/) | fixture | Config/credential cause, so no code fix proposed |
| [fixtures/novel-failure](fixtures/novel-failure/) / [-no-overlay](fixtures/novel-failure-no-overlay/) | fixture | A failure class absent from the taxonomy, with and without org knowledge |

Each folder has `input.md` (the page), `transcript.md` (every tool call in order), `incident-report.md`
(the output), `audit.jsonl` (every query and its redacted result) and `score.json` (how the eval scored it).
