# payments-svc: database connection pool

payments-svc uses a SQLAlchemy connection pool to postgres-payments.
Pool size is set by env `DB_POOL_SIZE` (default 20), overflow by `DB_MAX_OVERFLOW` (default 0).
Pool checkout timeout is 1s; a timeout surfaces as `QueuePool limit ... connection timed out` and a 503.

Signals of pool exhaustion: `db_pool_in_use` == `db_pool_max` for sustained periods, rising
`db_pool_wait_ms`, while postgres-payments `active_connections` stays well below `max_connections`.

Mitigation (human executes): raise `DB_POOL_SIZE` via config repo, or roll back the change that
reduced it / that holds connections longer.
