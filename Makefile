# Task runner (HANDOFF §4.5). Every repeatable op is a target; names stay stable across phases.
# Targets grow per phase — add a target the moment you'd type a command twice.

.DEFAULT_GOAL := help
.PHONY: help setup check-model agent smoke up down logs build-workload sandbox skills cache-demo resume compaction fmt lint test clean

help:
	@echo Targets:
	@echo "  setup        uv sync + pin Python 3.12 (run this first)"
	@echo "  check-model  verify LM Studio endpoint + configured model (the #1 first-run trap)"
	@echo "  agent        run adk web against the control-plane agent (Milestone 0/1)"
	@echo "  smoke        non-interactive one-turn chat test against LM Studio"
	@echo "  up           build + start the compose stack (workload sandbox)"
	@echo "  down         stop the compose stack"
	@echo "  logs         tail compose logs"
	@echo "  sandbox      run the autonomous-goal sandbox demo (Milestone 1 DoD)"
	@echo "  skills       run the skills + browser MCP demo (Milestone 1.5 DoD)"
	@echo "  cache-demo   semantic cache: a repeated prompt is served without the model (M2 DoD)"
	@echo "  resume       kill mid-run, restart, resume the same session (M2 DoD)"
	@echo "  compaction   long run summarized so the context window stays bounded (M2 DoD)"
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

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

build-workload:
	docker compose build workload

sandbox:
	uv run python scripts/sandbox_demo.py

skills:
	uv run python scripts/skills_demo.py

# --- Milestone 2 DoD demos (need `make up` for Redis; resume/compaction also need the model) ---
cache-demo:
	uv run python scripts/cache_demo.py

resume:
	uv run python scripts/persistence_demo.py phase1
	uv run python scripts/persistence_demo.py phase2

compaction:
	uv run python scripts/compaction_demo.py

fmt:
	uv run ruff format .

lint:
	uv run ruff check .

test:
	uv run pytest

clean:
	uv run python -c "import shutil,glob,os; [shutil.rmtree(p,ignore_errors=True) for p in glob.glob('**/__pycache__',recursive=True)+['.ruff_cache','.pytest_cache']]"
