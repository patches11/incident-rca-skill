"""payments-svc: charges cards via card-gateway and records payments in postgres."""

from __future__ import annotations

import os
import time

import httpx
from fastapi import FastAPI, HTTPException
from prometheus_client import Gauge, Histogram
from pydantic import BaseModel
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, insert
from sqlalchemy.orm import sessionmaker

from common.obs import SERVICE, UPSTREAM, fields, get_logger, instrument

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://acme:acme@postgres:5432/payments")
GATEWAY_URL = os.environ.get("CARD_GATEWAY_URL", "http://card-gateway:8080")
POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))

metadata = MetaData()
payments = Table(
    "payments", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String(64)),
    Column("amount_cents", Integer),
    Column("risk_score", Integer, nullable=True),
)

engine = create_engine(DATABASE_URL, pool_size=POOL_SIZE, max_overflow=0, pool_timeout=1.0, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

POOL_IN_USE = Gauge("db_pool_checked_out", "Connections checked out of the pool", ["service"])
POOL_SIZE_G = Gauge("db_pool_size", "Configured pool size", ["service"])
POOL_WAIT = Histogram("db_pool_acquire_seconds", "Time to acquire a pooled connection", ["service"],
                      buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2))
POOL_IN_USE.labels(SERVICE).set_function(lambda: engine.pool.checkedout())
POOL_SIZE_G.labels(SERVICE).set(POOL_SIZE)

log = get_logger()
app = FastAPI()
instrument(app)
http = httpx.Client(timeout=2.5)


class ChargeRequest(BaseModel):
    user_id: str
    card_token: str
    amount_cents: int


class Gateway:
    def charge(self, card_token: str, amount_cents: int) -> dict:
        return self._call("/v2/charges", {"card_token": card_token, "amount_cents": amount_cents})

    def risk_score(self, user_id: str, amount_cents: int) -> int:
        return self._call("/v2/risk-score", {"user_id": user_id, "amount_cents": amount_cents})["score"]

    def _call(self, path: str, body: dict) -> dict:
        start = time.perf_counter()
        outcome = "error"
        try:
            r = http.post(GATEWAY_URL + path, json=body)
            r.raise_for_status()
            outcome = "ok"
            return r.json()
        finally:
            UPSTREAM.labels(SERVICE, "card-gateway", outcome).observe(time.perf_counter() - start)


gateway = Gateway()


@app.on_event("startup")
def _startup() -> None:
    metadata.create_all(engine)
    log.info("payments-svc starting", **fields(db_pool_size=POOL_SIZE, db_max_overflow=0,
                                               commit=os.environ.get("GIT_COMMIT", "dev")))


def _acquire(session) -> None:
    start = time.perf_counter()
    try:
        session.connection()
    finally:
        POOL_WAIT.labels(SERVICE).observe(time.perf_counter() - start)


@app.post("/charge")
def charge(req: ChargeRequest):
    session = SessionLocal()
    try:
        result = gateway.charge(req.card_token, req.amount_cents)
        if result["declined"]:
            raise HTTPException(status_code=402, detail=result["code"])
        _acquire(session)
        session.execute(insert(payments).values(user_id=req.user_id, amount_cents=req.amount_cents))
        session.commit()
        return {"status": "charged"}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        log.error(str(e).splitlines()[0], exc_info=True, **fields(route="/charge"))
        raise HTTPException(status_code=503, detail="temporarily unavailable") from e
    finally:
        session.close()


@app.get("/healthz")
def healthz():
    return {"ok": True}
