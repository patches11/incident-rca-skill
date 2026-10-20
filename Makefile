SCENARIO ?= pool-exhaustion
RUNS ?= 3
PY = uv run --quiet python

.PHONY: help fixtures test demo eval sandbox-up sandbox-down break

help:
	@echo "make fixtures                  regenerate all scenario fixtures (seeded)"
	@echo "make test                      unit tests (server, redaction, budget, verifier)"
	@echo "make demo SCENARIO=<name>      run the skill headlessly against one recorded scenario"
	@echo "make eval [RUNS=3]             run every scenario RUNS times and score"
	@echo "make sandbox-up / sandbox-down live docker sandbox"
	@echo "make break SCENARIO=<name>     inject a fault into the live sandbox"

fixtures:
	$(PY) scenarios/_generator/generate.py

test:
	uv run --quiet pytest -q

demo:
	$(PY) eval/run_eval.py --scenario $(SCENARIO) --runs 1 --show

eval:
	$(PY) eval/run_eval.py --runs $(RUNS)

sandbox-up:
	docker compose -f sandbox/docker-compose.yml up -d --build

sandbox-down:
	docker compose -f sandbox/docker-compose.yml down -v

break:
	sandbox/faults/$(SCENARIO).sh
