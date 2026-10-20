"""checkout-api: thin orchestrator over inventory-svc and payments-svc."""

from __future__ import annotations

import os
import time

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common.obs import SERVICE, UPSTREAM, fields, get_logger, instrument

PAYMENTS = os.environ.get("PAYMENTS_URL", "http://payments-svc:8080")
INVENTORY = os.environ.get("INVENTORY_URL", "http://inventory-svc:8080")
log = get_logger()
app = FastAPI()
instrument(app)
http = httpx.Client(timeout=2.0)


class Checkout(BaseModel):
    user_id: str
    card_token: str
    items: list[dict]


def _call(upstream: str, url: str, body: dict) -> httpx.Response:
    start = time.perf_counter()
    try:
        r = http.post(url, json=body)
        UPSTREAM.labels(SERVICE, upstream, "ok" if r.status_code < 500 else "error").observe(time.perf_counter() - start)
        return r
    except httpx.TimeoutException:
        UPSTREAM.labels(SERVICE, upstream, "timeout").observe(time.perf_counter() - start)
        log.error(f"timeout calling {upstream} after 2000ms", **fields(route="/checkout", upstream=upstream))
        raise HTTPException(504, f"{upstream} timeout")
    except httpx.TransportError as e:
        UPSTREAM.labels(SERVICE, upstream, "error").observe(time.perf_counter() - start)
        log.error(f"upstream connect error ({upstream}): {e}", **fields(route="/checkout", upstream=upstream))
        raise HTTPException(502, f"{upstream} unreachable")


@app.post("/checkout")
def checkout(req: Checkout):
    r = _call("inventory-svc", f"{INVENTORY}/reserve", {"items": req.items})
    if r.status_code != 200:
        raise HTTPException(409 if r.status_code == 409 else 502, "reservation failed")
    amount = sum(i.get("qty", 1) for i in req.items) * 1999
    r = _call("payments-svc", f"{PAYMENTS}/charge",
              {"user_id": req.user_id, "card_token": req.card_token, "amount_cents": amount})
    if r.status_code == 402:
        raise HTTPException(402, "card declined")
    if r.status_code >= 500:
        log.error(f"upstream payments-svc returned {r.status_code}",
                  **fields(route="/checkout", upstream="payments-svc", upstream_status=r.status_code))
        raise HTTPException(502, "payment failed")
    return {"status": "ok"}
