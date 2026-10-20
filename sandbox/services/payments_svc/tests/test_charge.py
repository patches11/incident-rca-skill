def _fake_gateway(app_module, monkeypatch, declined=False, score=10):
    monkeypatch.setattr(app_module.gateway, "charge",
                        lambda token, amount: {"declined": declined, "code": "do_not_honor" if declined else None})
    monkeypatch.setattr(app_module.gateway, "risk_score", lambda user, amount: score)


def test_successful_charge(app_module, client, monkeypatch):
    _fake_gateway(app_module, monkeypatch)
    r = client.post("/charge", json={"user_id": "u1", "card_token": "tok_visa", "amount_cents": 500})
    assert r.status_code == 200
    assert app_module.engine.pool.checkedout() == 0


def test_declined_charge_returns_402_and_releases_connection(app_module, client, monkeypatch):
    _fake_gateway(app_module, monkeypatch, declined=True)
    for _ in range(10):  # more declines than the pool has connections
        r = client.post("/charge", json={"user_id": "u1", "card_token": "tok_decline_x", "amount_cents": 500})
        assert r.status_code == 402
    assert app_module.engine.pool.checkedout() == 0
