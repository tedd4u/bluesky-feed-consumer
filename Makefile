.DEFAULT_GOAL := check

VENV := .venv/bin

.PHONY: check lint typecheck test fmt

## Run all checks (lint + typecheck + tests)
check: lint typecheck test

## Lint with ruff
lint:
	$(VENV)/ruff check src/ tests/

## Type-check with mypy
typecheck:
	$(VENV)/mypy src/

## Run test suite
test:
	$(VENV)/pytest tests/ -v

## Auto-format and fix lint errors
fmt:
	$(VENV)/ruff format src/ tests/
	$(VENV)/ruff check --fix src/ tests/
