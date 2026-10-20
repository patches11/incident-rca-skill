"""Shared observability for sandbox services: JSON logs to a file + Prometheus RED metrics."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

SERVICE = os.environ.get("SERVICE_NAME", "unknown")
LOG_DIR = os.environ.get("LOG_DIR", "/var/log/acme")

REQUESTS = Counter("http_requests_total", "HTTP requests", ["service", "route", "status"])
LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["service", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5),
)
UPSTREAM = Histogram(
    "upstream_request_duration_seconds", "Outbound call latency", ["service", "upstream", "outcome"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5),
)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname.lower(),
            "service": SERVICE,
            "msg": record.getMessage(),
        }
        out.update(getattr(record, "fields", {}))
        if record.exc_info:
            out["exc_type"] = record.exc_info[0].__module__ + "." + record.exc_info[0].__name__
        return json.dumps(out)


def get_logger() -> logging.Logger:
    log = logging.getLogger(SERVICE)
    if not log.handlers:
        os.makedirs(LOG_DIR, exist_ok=True)
        for h in (logging.FileHandler(f"{LOG_DIR}/{SERVICE}.jsonl"), logging.StreamHandler()):
            h.setFormatter(_JsonFormatter())
            log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def fields(**kw) -> dict:
    return {"extra": {"fields": kw}}


def instrument(app: FastAPI, sample_ok: float = 0.05) -> None:
    """RED metrics for every route + sampled access logs (all non-2xx are logged)."""
    import random

    log = get_logger()

    @app.middleware("http")
    async def _red(request: Request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.url.path
            if route != "/metrics":
                elapsed = time.perf_counter() - start
                REQUESTS.labels(SERVICE, route, str(status)).inc()
                LATENCY.labels(SERVICE, route).observe(elapsed)
                if status >= 400 or random.random() < sample_ok:
                    level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
                    log.log(level, "request", **fields(route=route, status=status, duration_ms=round(elapsed * 1000, 1)))

    @app.get("/metrics")
    def _metrics():
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
