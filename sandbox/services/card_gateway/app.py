"""Fake external card gateway (vendor). Declines tokens starting with tok_decline_.
/v2/risk-score is deliberately slow (~0.9s p50): it's a vendor ML call."""

from __future__ import annotations

import asyncio
import random

from fastapi import FastAPI

from common.obs import instrument

app = FastAPI()
instrument(app, sample_ok=0.0)


@app.post("/v2/charges")
async def charges(body: dict):
    await asyncio.sleep(random.lognormvariate(-3.2, 0.4))  # ~40ms
    if body["card_token"].startswith("tok_decline_"):
        return {"declined": True, "code": random.choice(["insufficient_funds", "do_not_honor"])}
    return {"declined": False, "code": None}


@app.post("/v2/risk-score")
async def risk_score(body: dict):
    await asyncio.sleep(random.lognormvariate(-0.1, 0.35))  # ~0.9s
    return {"score": random.randint(0, 100)}
