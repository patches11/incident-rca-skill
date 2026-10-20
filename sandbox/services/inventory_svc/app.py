"""inventory-svc: stock reservations (kept minimal in the sandbox)."""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, select

from common.obs import get_logger, instrument

engine = create_engine(os.environ.get("DATABASE_URL", "postgresql+psycopg://acme:acme@postgres:5432/inventory"),
                       pool_size=10, max_overflow=0, pool_timeout=1.0)
metadata = MetaData()
stock = Table("stock", metadata, Column("sku", String(32), primary_key=True), Column("available", Integer))
log = get_logger()
app = FastAPI()
instrument(app)


class Item(BaseModel):
    sku: str
    qty: int


class ReserveRequest(BaseModel):
    items: list[Item]


@app.on_event("startup")
def _startup() -> None:
    metadata.create_all(engine)
    with engine.begin() as conn:
        if conn.execute(select(stock).limit(1)).first() is None:
            conn.execute(stock.insert(), [{"sku": f"SKU-{i:03d}", "available": 10**9} for i in range(200)])
    log.info("inventory-svc starting")


@app.post("/reserve")
def reserve(req: ReserveRequest):
    with engine.connect() as conn:
        rows = conn.execute(select(stock).where(stock.c.sku.in_([i.sku for i in req.items]))).all()
    by_sku = {r.sku: r for r in rows}
    for item in req.items:
        if item.sku not in by_sku or by_sku[item.sku].available < item.qty:
            raise HTTPException(409, f"insufficient stock for {item.sku}")
    return {"reserved": len(req.items)}
