# checkout-api: high latency / 5xx

checkout-api is a thin orchestrator: almost all of its latency is time spent waiting on
payments-svc (/charge) and inventory-svc (/reserve). It has no database of its own.

- If checkout p99 is high, compare `upstream` span durations in traces to see which dependency is slow.
- checkout-api returns 504 when a dependency call exceeds its 2s timeout, and 502 on connection errors.
- checkout-api CPU is rarely the issue; it is I/O bound.
