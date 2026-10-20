"""Telemetry adapter interface.

Every backend (recorded fixtures, Prometheus + log files, or a real org's
Datadog/Grafana/etc.) implements this interface. Adapters are read-only by
construction: there is no method that writes anything.

Adapters return plain JSON-serialisable dicts. Budget enforcement, redaction and
audit logging happen in pipeline.py, not here, so a new adapter cannot forget them.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

_REL = re.compile(r"^now(?:-(\d+)([smhd]))?$")
_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_time(value: str, now: datetime) -> datetime:
    """Accept ISO-8601 ('2026-10-14T09:30:00Z') or relative ('now', 'now-90m')."""
    value = value.strip()
    m = _REL.match(value)
    if m:
        if not m.group(1):
            return now
        return now - timedelta(**{_UNITS[m.group(2)]: int(m.group(1))})
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_duration(value: str) -> int:
    """'60s' / '5m' / '1h' -> seconds."""
    m = re.match(r"^(\d+)([smh])$", value.strip())
    if not m:
        raise ValueError(f"bad duration {value!r}; use e.g. '30s', '5m', '1h'")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AdapterError(Exception):
    """A query the backend can't answer (unknown service, missing data source...)."""


class TelemetryAdapter(ABC):
    """Read-only access to an org's telemetry, change history and service catalog."""

    @abstractmethod
    def now(self) -> datetime:
        """Current time from the adapter's point of view (fixed for recorded scenarios)."""

    @abstractmethod
    def list_metrics(self, service: str) -> list[str]:
        """Logical metric names available for a service."""

    @abstractmethod
    def query_metrics(
        self, service: str, metric: str, start: datetime, end: datetime, step_s: int
    ) -> list[tuple[datetime, float]]:
        """Time series for one logical metric, aggregated to `step_s` buckets."""

    @abstractmethod
    def search_logs(
        self, service: str, start: datetime, end: datetime
    ) -> list[dict]:
        """All structured log records for a service in the window (filtering is done
        by the pipeline so query semantics are identical across backends)."""

    @abstractmethod
    def get_traces(self, service: str, start: datetime, end: datetime) -> list[dict]:
        """Traces touching a service in the window."""

    @abstractmethod
    def recent_changes(
        self, start: datetime, end: datetime, service: str | None
    ) -> list[dict]:
        """Deploys, config changes and commits in the window."""

    @abstractmethod
    def service_catalog(self) -> dict:
        """The org's service catalog (owners, dependencies, SLOs, runbooks)."""

    @abstractmethod
    def get_runbook(self, service: str, name: str) -> str:
        """Runbook text."""

    @abstractmethod
    def org_overlay(self) -> str:
        """Org-specific quirks / known-benign patterns. May be empty."""
