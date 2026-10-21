import json

from budget import Budget
from pipeline import Investigation
from scenarios_canaries import PII_CANARIES


def test_every_query_is_audited_with_id(inv):
    r = inv.run("query_metrics", {"service": "payments-svc", "metric": "db_pool_in_use", "start": "now-1h", "end": "now"})
    assert r["query_id"] == "q001"
    entry = json.loads(inv.audit_path.read_text().splitlines()[0])
    assert entry["tool"] == "query_metrics" and entry["ok"] and entry["result"]["summary"]["max"] == 20


def test_budget_enforced_server_side(scenario_dir, tmp_path):
    from conftest import REPO
    from adapters.fixture import FixtureAdapter

    inv = Investigation(FixtureAdapter(scenario_dir, REPO / "org-context", None), None, Budget(max_queries=2))
    args = {"service": "payments-svc", "metric": "error_rate", "start": "now-1h", "end": "now"}
    assert "error" not in inv.run("query_metrics", args)
    assert "error" not in inv.run("query_metrics", args)
    assert "budget" in inv.run("query_metrics", args)["error"]
    # catalog lookups are free
    assert "error" not in inv.run("service_catalog", {"service": "payments-svc"})


def test_time_range_cap(inv):
    r = inv.run("search_logs", {"service": "payments-svc", "query": "", "start": "now-7h", "end": "now"})
    assert "6h cap" in r["error"]


def test_no_pii_reaches_model(inv):
    # Planted canaries live ~6.5h before the page; query that window explicitly.
    for service in ["payments-svc", "checkout-api", "notification-worker"]:
        r = inv.run("search_logs", {"service": service, "query": "", "start": "now-8h", "end": "now-5h", "limit": 200})
        r2 = inv.run("search_logs", {"service": service, "query": "level!=info", "start": "now-8h", "end": "now-5h", "limit": 200})
        blob = json.dumps([r, r2])
        for canary in PII_CANARIES:
            assert canary not in blob, (service, canary)


def test_log_query_language(inv):
    r = inv.run("search_logs", {"service": "payments-svc", "query": "level=error QueuePool", "start": "now-1h", "end": "now", "limit": 5})
    assert r["total_matches"] > 100 and len(r["lines"]) == 5 and r["sampled"]
    r = inv.run("search_logs", {"service": "payments-svc", "query": "level=error -QueuePool", "start": "now-1h", "end": "now"})
    assert all("QueuePool" not in l["msg"] for l in r["lines"])


def test_unknown_service_is_an_error_not_empty_data(inv):
    assert "unknown service" in inv.run("list_metrics", {"service": "paymnts"})["error"]


def test_relative_windows_are_pinned_in_audit(inv):
    inv.run("query_metrics", {"service": "payments-svc", "metric": "error_rate", "start": "now-1h", "end": "now"})
    entry = json.loads(inv.audit_path.read_text().splitlines()[0])
    assert entry["args"]["start"] == "2026-10-14T08:58:00Z" and entry["args"]["end"] == "2026-10-14T09:58:00Z"
