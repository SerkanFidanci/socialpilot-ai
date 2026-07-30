PYTHON ?= python
API_DIR := services/api
COMPOSE ?= docker compose

.PHONY: lint format-check typecheck test-backend verify migrate migrate-down compose-config compose-up compose-ps generate-docs check-openapi benchmark

lint:
	cd $(API_DIR) && $(PYTHON) -m ruff check app tests migrations scripts

format-check:
	cd $(API_DIR) && $(PYTHON) -m ruff format --check app tests migrations scripts

test-backend:
	cd $(API_DIR) && $(PYTHON) -m pytest

typecheck:
	cd $(API_DIR) && $(PYTHON) -m mypy .

verify: lint format-check typecheck test-backend check-openapi

# Regenerates both the OpenAPI contract and the readable endpoint inventory
# (docs/api/endpoints.md) from the same in-memory schema, so the table cannot drift from code.
generate-docs:
	$(PYTHON) services/api/scripts/generate_openapi.py

# Fails when either generated artifact is stale relative to the code.
check-openapi: generate-docs
	git diff --exit-code -- docs/generated/openapi.json docs/api/endpoints.md

# Provider benchmark (W08). Default: fake providers, no credentials, no DB — safe in CI.
# Pass BENCHMARK_ARGS to add a cost cap or write output, e.g.
#   make benchmark BENCHMARK_ARGS="--runs 5 --cost-cap-minor 40 --out results.json"
benchmark:
	cd $(API_DIR) && $(PYTHON) -m scripts.run_benchmark $(BENCHMARK_ARGS)

migrate:
	cd $(API_DIR) && $(PYTHON) -m alembic upgrade head

migrate-down:
	cd $(API_DIR) && $(PYTHON) -m alembic downgrade base

compose-config:
	$(COMPOSE) config

compose-up:
	$(COMPOSE) up -d --build

compose-ps:
	$(COMPOSE) ps
