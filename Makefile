PYTHON ?= python
API_DIR := services/api
COMPOSE ?= docker compose

.PHONY: lint format-check typecheck test-backend verify migrate migrate-down compose-config compose-up compose-ps generate-docs check-openapi backup restore-check

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

# Off-server encrypted PostgreSQL backup (ADR-XXX). Needs pg_dump + openssl on the runner and the
# BACKUP_* environment (see docs/runbooks/operations.md and .env.example). Prints a structured
# success or db_backup_failed log; exits non-zero on failure so a scheduler surfaces it.
backup:
	cd $(API_DIR) && $(PYTHON) -m scripts.backup_db

# Restore rehearsal: pull the latest backup, load it into RESTORE_CHECK_DATABASE_URL (a throwaway
# database), and assert the Alembic head and core row counts. Needs psql + openssl on the runner.
restore-check:
	cd $(API_DIR) && $(PYTHON) -m scripts.restore_check
