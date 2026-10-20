"""Pattern-based scrubbing of secrets and PII before any result reaches the model.

Known limitation: regexes miss anything that doesn't look like a known pattern
(free-text names, addresses, novel token formats). See README "Limitations".
"""

from __future__ import annotations

import re
from typing import Any

# Order matters: more specific patterns first.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("AWS_KEY", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("BEARER", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("API_KEY", re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{12,}\b")),
    ("CONN_PASSWORD", re.compile(r"(?<=://)[^:/@\s]+:[^@/\s]+(?=@)")),
    ("SECRET_KV", re.compile(r"(?i)\b(password|passwd|secret|token|api_key)=([^\s&,;\"']+)")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
]

# Keys whose values are always dropped, whatever they contain.
SENSITIVE_KEYS = {"password", "authorization", "cookie", "set-cookie", "card_number", "ssn", "api_key", "token"}


def redact_text(text: str) -> str:
    for label, pattern in PATTERNS:
        if label == "SECRET_KV":
            text = pattern.sub(lambda m: f"{m.group(1)}=[REDACTED:SECRET]", text)
        elif label == "CARD":
            text = pattern.sub(lambda m: _card(m.group(0)), text)
        else:
            text = pattern.sub(f"[REDACTED:{label}]", text)
    return text


def _card(candidate: str) -> str:
    digits = re.sub(r"\D", "", candidate)
    return "[REDACTED:CARD]" if 13 <= len(digits) <= 16 and _luhn(digits) else candidate


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def redact(value: Any) -> Any:
    """Recursively redact a JSON-like structure."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            k: "[REDACTED:SECRET]" if k.lower() in SENSITIVE_KEYS else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value
