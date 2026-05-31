# PitCrew CI/CD — Self-Healing CI/CD Agent Team

A self-hosted, open-source DevOps **agent team** (Google ADK + local LM Studio via LiteLLM, no
cloud) that runs gated CI/CD for your GitHub repos and **self-heals** failures by proposing fix
PRs a human merges. See `HANDOFF.md` for the build contract and `adk_agent_build_plan.md` for the
design rationale.

## Quick start

Prerequisites: [`uv`](https://docs.astral.sh/uv/), `make`, Python 3.12+, Docker + docker compose,
and LM Studio running with a strong function-calling instruct model loaded (bound to
`0.0.0.0:1234`).

```
make setup            # create the env from uv.lock, pin Python 3.12
cp .env.example .env  # then edit LMSTUDIO_* and generate an EXEC_SERVICE_TOKEN
make check-model      # confirm LM Studio is reachable and the model is loaded
make agent            # chat with the agent in adk web (http://localhost:8000)

make up               # build + start the stack (hardened workload sandbox + Redis 8)
make sandbox          # run the autonomous-goal sandbox demo (Milestone 1)
make cache-demo       # semantic cache: a repeated prompt is served without the model (M2)
make resume           # kill mid-run, restart, resume the same session (M2)
make compaction       # long run summarized so the context window stays bounded (M2)
```

Run `make` with no target to list available targets.

## Status

Built bottom-up per `HANDOFF.md` §3; each milestone has a demonstrated Definition of Done.

- **Milestone 0 — Hello agent** ✅ a single ADK `LlmAgent` answering through your local LM Studio
  model (`make check-model`, `make agent`/`make smoke`).
- **Milestone 1 — Sandbox + loop + native tools** ✅ a single `LlmAgent` worker driven by an ADK
  **dynamic-workflow loop** (the non-deprecated `LoopAgent` replacement) with native tools
  (`run_shell`/`write_file`/`read_file`/`copy_out`/`finish`) that execute inside a **hardened,
  disposable workload container** (non-root, read-only rootfs, `cap_drop ALL`, no model route)
  reached through a narrow token-authed exec service — never a Docker socket. A
  `before_tool_callback` guard enforces step/time budgets, a command denylist, and a kill-switch
  (`make sandbox`, `make test`).
- **Milestone 1.5 — MCP supply + Skills + capability loader** ✅ `capabilities.py` auto-discovers
  `skills/*/SKILL.md` and builds one `tool_filter`-ed `McpToolset` per registry block, so adding a
  capability is a folder or a YAML block — `agent.py` is constant (`make skills`).
- **Milestone 2 — Persistence + cache + compaction** ✅ runs survive a crash and resume the same
  session (`DatabaseSessionService` over SQLite + `ResumabilityConfig`); a repeated prompt is served
  from a **semantic cache** (Redis 8 vector search over LM Studio prompt embeddings, fail-open) so
  the model is skipped; and long sessions are kept inside the small local context window by native
  **event compaction** (`LlmEventSummarizer`). The persistence/cache/compaction runtime lives in
  `runtime.py`, leaving `agent.py` frozen (`make cache-demo`, `make resume`, `make compaction`).
  Note: ADK 2.1 ships no Redis *session* service, so sessions use SQLite (→ Postgres in Phase 9) and
  Redis backs the cache/vector — the consolidated backbone the Phase-7 broker reuses.

Next (per `HANDOFF.md` §3): governance hardening (3), observability (4), eval/sim (5), scale-out
(7), the operator console (8), and finally the CI/CD agent team itself (9) and multi-repo
packaging (10).

## Architecture (so far)

Two-container trust boundary (`CLAUDE.md` invariant #3): a **control plane** (trusted — holds the
model endpoint, guardrails, and secrets) and a **workload container** (untrusted, disposable,
per-run — runs model-directed code/builds). The model never enters the workload container; the
workload reaches the control plane only through the narrow exec service. At this stage the control
plane runs on the host; it is containerized with egress hardening in Milestone 3.

State lives in two trusted stores on the control-plane side: **SQLite** holds resumable agent
sessions (swappable to Postgres in Phase 9), and **Redis 8** is the consolidated backbone for the
semantic cache and vector search (and later cross-run memory + the Phase-7 task broker). Neither is
reachable from the workload container.
