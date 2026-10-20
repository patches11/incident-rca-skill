from verify import extract_summary, load_audit, verify_claims


def _setup(inv):
    q1 = inv.run("query_metrics", {"service": "payments-svc", "metric": "db_pool_in_use", "start": "now-1h", "end": "now"})["query_id"]
    q2 = inv.run("recent_changes", {"start": "now-2h", "end": "now", "service": "payments-svc"})["query_id"]
    return q1, q2, load_audit(inv.audit_path), lambda tool, a: inv.run(tool, a, record=False)


def test_supported_claims_pass(inv):
    q1, q2, audit, rerun = _setup(inv)
    claims = [
        {"id": "c1", "kind": "observation", "text": "pool pinned at 20", "evidence": [q1], "values": [20, "09:43"]},
        {"id": "c2", "kind": "observation", "text": "deploy a41f9e2", "evidence": [q2], "values": ["a41f9e2"]},
        {"id": "c3", "kind": "inference", "text": "deploy leaked connections", "based_on": ["c1", "c2"]},
    ]
    assert [v["status"] for v in verify_claims(claims, audit, rerun)] == ["PASS", "PASS", "PASS"]


def test_fabricated_claims_fail(inv):
    q1, q2, audit, rerun = _setup(inv)
    claims = [
        {"id": "c1", "kind": "observation", "text": "pool max is 50", "evidence": [q1], "values": [50]},
        {"id": "c2", "kind": "observation", "text": "made-up query", "evidence": ["q999"], "values": [1]},
        {"id": "c3", "kind": "observation", "text": "no values", "evidence": [q2], "values": []},
        {"id": "c4", "kind": "observation", "text": "wrong sha", "evidence": [q2], "values": ["deadbee"]},
        {"id": "c5", "kind": "inference", "text": "built on sand", "based_on": ["c1"]},
    ]
    assert all(v["status"] == "FAIL" for v in verify_claims(claims, audit, rerun))


def test_tampered_audit_detected_on_rerun(inv):
    q1, _, audit, rerun = _setup(inv)
    audit[q1]["result"]["summary"]["max"] = 50  # model "saw" 50 — but re-running disagrees
    v = verify_claims([{"id": "c1", "evidence": [q1], "values": [50]}], audit, rerun)
    assert v[0]["status"] == "FAIL" and "different result" in v[0]["reasons"][0]


def test_timestamps_do_not_satisfy_small_numbers(inv):
    _, q2, audit, _ = _setup(inv)
    # changes contain "2026-10-14T..." — that must not count as evidence for the value 14
    v = verify_claims([{"id": "c1", "evidence": [q2], "values": [14]}], audit)
    assert v[0]["status"] == "FAIL"


def test_extract_summary_takes_last_verdict_block():
    md = "x\n```yaml\nfoo: 1\n```\n## Summary\n```yaml\nverdict: abstain\nclaims: []\n```\n"
    assert extract_summary(md)["verdict"] == "abstain"
