"""Query budget and time-range limits, enforced server-side.

The prompt can ask the model to be frugal; only the server can make it so. A
budget breach returns a structured error the model can act on (narrow the query,
or stop and report what it has).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    max_queries: int = int(os.environ.get("RCA_QUERY_BUDGET", "40"))
    max_range_s: int = 6 * 3600
    max_lookback_s: int = 7 * 24 * 3600
    max_points: int = 240
    min_step_s: int = 15
    max_log_lines: int = 200
    used: int = field(default=0)

    def charge(self) -> None:
        if self.used >= self.max_queries:
            raise BudgetExceeded(
                f"query budget of {self.max_queries} exhausted for this investigation. "
                "Conclude with the evidence you have, or abstain and list what is missing. "
                "The budget cannot be raised from inside the investigation."
            )
        self.used += 1

    def check_range(self, start: datetime, end: datetime, now: datetime, max_range_s: int | None = None) -> None:
        cap = max_range_s or self.max_range_s
        if end <= start:
            raise BudgetExceeded("end must be after start")
        if (end - start).total_seconds() > cap:
            raise BudgetExceeded(
                f"time range {(end - start)} exceeds the {cap // 3600}h cap for this tool; "
                "split the query or narrow the window"
            )
        if (now - start).total_seconds() > self.max_lookback_s:
            raise BudgetExceeded("start is more than 7 days before now")

    def fit_step(self, start: datetime, end: datetime, step_s: int) -> int:
        """Raise the step so the series has at most max_points points."""
        step_s = max(step_s, self.min_step_s)
        span = (end - start).total_seconds()
        while span / step_s > self.max_points:
            step_s *= 2
        return step_s

    @property
    def remaining(self) -> int:
        return self.max_queries - self.used
