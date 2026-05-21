.DEFAULT_GOAL := check

# Use .venv/bin/ prefix if it exists (local dev), otherwise bare commands (CI)
BIN := $(shell [ -d .venv/bin ] && echo ".venv/bin/" || echo "")

.PHONY: check lint typecheck test fmt

## Run all checks (lint + typecheck + tests)
check: lint typecheck test

## Lint with ruff
lint:
	$(BIN)ruff check src/ tests/

## Type-check with mypy
typecheck:
	$(BIN)mypy src/

## Run test suite
test:
	$(BIN)pytest tests/ -v

## Auto-format and fix lint errors
fmt:
	$(BIN)ruff format src/ tests/
	$(BIN)ruff check --fix src/ tests/
