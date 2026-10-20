#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
git checkout -- sandbox/services/payments_svc/app.py
docker compose -f sandbox/docker-compose.yml up -d --build payments-svc
echo "payments-svc restored to baseline."
