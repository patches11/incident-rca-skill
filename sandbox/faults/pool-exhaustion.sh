#!/usr/bin/env bash
# "Deploy" FRAUD-231 to payments-svc: it calls the (slow) vendor risk-score API while
# holding a pooled DB connection. Under normal load the 10-connection pool saturates.
set -euo pipefail
cd "$(dirname "$0")/../.."
git apply sandbox/faults/pool-exhaustion.patch
commit=$(git hash-object sandbox/services/payments_svc/app.py | cut -c1-7)
PAYMENTS_COMMIT=$commit docker compose -f sandbox/docker-compose.yml up -d --build payments-svc
python3 - "$commit" <<'PY'
import json, sys, datetime, pathlib
diff = pathlib.Path("sandbox/faults/pool-exhaustion.patch").read_text()
rec = {"ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "type": "deploy",
       "service": "payments-svc", "id": "pay-live-1", "author": "dev.patel", "commit": sys.argv[1],
       "summary": "Score every charge with card-gateway risk API and store it (FRAUD-231)", "diff": diff}
p = pathlib.Path("sandbox/data/changes.jsonl"); p.parent.mkdir(parents=True, exist_ok=True)
with p.open("a") as f: f.write(json.dumps(rec) + "\n")
PY
echo "Fault injected (commit $commit). Symptoms appear on checkout-api within ~1-2 minutes."
echo "Undo with: make unbreak"
