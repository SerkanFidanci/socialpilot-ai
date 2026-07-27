PYTHON ?= python
API_DIR := services/api
COMPOSE ?= docker compose

.PHONY: lint format-check typecheck test-backend verify migrate migrate-down compose-config compose-up compose-ps generate-docs check-openapi

lint:
	cd $(API_DIR) && $(PYTHON) -m ruff check app tests migrations

format-check:
	cd $(API_DIR) && $(PYTHON) -m ruff format --check app tests migrations

test-backend:
	cd $(API_DIR) && $(PYTHON) -m pytest

typecheck:
	cd $(API_DIR) && $(PYTHON) -m mypy .

verify: lint format-check typecheck test-backend check-openapi

generate-docs:
	$(PYTHON) services/api/scripts/generate_openapi.py

check-openapi: generate-docs
	git diff --exit-code -- docs/generated/openapi.json

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
