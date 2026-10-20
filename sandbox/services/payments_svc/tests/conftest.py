import os
import sys
from pathlib import Path

import pytest

SERVICES = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVICES))


@pytest.fixture(scope="session")
def app_module(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("payments")
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/payments.db"
    os.environ["LOG_DIR"] = str(tmp / "logs")
    os.environ["DB_POOL_SIZE"] = "3"
    import payments_svc.app as app_module

    return app_module


@pytest.fixture
def client(app_module):
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as c:
        yield c
    app_module.engine.dispose()
