# PitCrew CI/CD — Self-Healing CI/CD Agent Team

A self-hosted, open-source DevOps **agent team** (Google ADK + local LM Studio via LiteLLM, no
cloud) that runs gated CI/CD for your GitHub repos and **self-heals** failures by proposing fix
PRs a human merges. See `HANDOFF.md` for the build contract and `adk_agent_build_plan.md` for the
design rationale.

## Quick start (Milestone 0)

Prerequisites: [`uv`](https://docs.astral.sh/uv/), `make`, Python 3.12+, and LM Studio running
with a strong function-calling instruct model loaded (bound to `0.0.0.0:1234`).

```
make setup            # create the env from uv.lock, pin Python 3.12
cp .env.example .env  # then edit LMSTUDIO_API_BASE / LMSTUDIO_MODEL
make check-model      # confirm LM Studio is reachable and the model is loaded
make agent            # chat with the agent in adk web (http://localhost:8000)
```

Run `make` with no target to list available targets.

## Status

Milestone 0 — Hello agent: a single ADK `LlmAgent` answering through your local LM Studio model.
Later milestones (per `HANDOFF.md` §3) add the sandbox, MCP/Skills, persistence, governance,
observability, eval, scale-out, the console, and finally the CI/CD agent team itself.
