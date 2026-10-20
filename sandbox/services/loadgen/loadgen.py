"""Steady synthetic checkout traffic (~RPS req/s, ~5% declined cards)."""

import asyncio
import os
import random

import httpx

URL = os.environ.get("CHECKOUT_URL", "http://checkout-api:8080/checkout")
RPS = float(os.environ.get("RPS", "12"))


async def one(client: httpx.AsyncClient) -> None:
    token = "tok_decline_x" if random.random() < 0.05 else f"tok_visa_{random.randint(1000, 9999)}"
    body = {"user_id": f"u_{random.randint(10000, 99999)}", "card_token": token,
            "items": [{"sku": f"SKU-{random.randint(0, 199):03d}", "qty": random.randint(1, 3)}
                      for _ in range(random.randint(1, 4))]}
    try:
        await client.post(URL, json=body)
    except httpx.HTTPError:
        pass


async def main() -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        while True:
            asyncio.create_task(one(client))
            await asyncio.sleep(random.expovariate(RPS))


asyncio.run(main())
