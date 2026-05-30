# Task runner (HANDOFF §4.5). Every repeatable op is a target; names stay stable across phases.
# Targets grow per phase — add a target the moment you'd type a command twice.

.DEFAULT_GOAL := help
.PHONY: help setup check-model agent smoke fmt lint test clean

help:
	@echo Targets:
	@echo "  setup        uv sync + pin Python 3.12 (run this first)"
	@echo "  check-model  verify LM Studio endpoint + configured model (the #1 first-run trap)"
	@echo "  agent        run adk web against the control-plane agent (Milestone 0/1)"
	@echo "  smoke        non-interactive one-turn chat test against LM Studio"
	@echo "  fmt          format with ruff"
	@echo "  lint         lint with ruff"
	@echo "  test         run unit tests (pytest)"
	@echo "  clean        remove python/tool caches"

setup:
	uv python pin 3.12
	uv sync

check-model:
	uv run python scripts/check_model.py

agent:
	uv run adk web .

smoke:
	uv run python scripts/smoke_chat.py

fmt:
	uv run ruff format .

lint:
	uv run ruff check .

test:
	uv run pytest

clean:
	uv run python -c "import shutil,glob,os; [shutil.rmtree(p,ignore_errors=True) for p in glob.glob('**/__pycache__',recursive=True)+['.ruff_cache','.pytest_cache']]"
