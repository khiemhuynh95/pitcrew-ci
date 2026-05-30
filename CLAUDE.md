# CLAUDE.md — Self-Healing CI/CD Agent Team

This file is loaded into context on every session. It is the **condensed invariants + orientation**.
For the full contract see `HANDOFF.md`; for design rationale see `adk_agent_build_plan.md`. Read
the relevant plan section before building any phase.

## What this is
A self-hosted, open-source DevOps **agent team** (Google ADK `~=2.1` + local LM Studio via
LiteLLM, no cloud, Docker-compose) that runs gated CI/CD for a user's GitHub repos and
**self-heals** failures by proposing fix PRs a human merges. The CI/CD team is the product; lower
phases are its substrate.

## Hard invariants (NEVER violate; if a task seems to require it, STOP and flag)
1. **`LoopAgent` + single `LlmAgent` + Skills. No routing** — no `sub_agents`/`AgentTool`/
   `transfer_to_agent`, no manager agent. Specialization = load a `SKILL.md`. (The **team-lead
   agent** is allowed *only* as a read-and-relay **spokesperson** — it reads state + relays the
   human's approvals; it does NOT coordinate the other agents or hold authority. Jenkins
   coordinates; the human decides. A lead that orchestrates or auto-approves = the banned manager.)
2. **`google-adk~=2.1`.** No `_run_async_impl()` overrides; no broad `except Exception:` in tools;
   JSON-blob sessions.
3. **Two containers:** control-plane (trusted: harness, governance, secrets, model endpoint,
   credentialed MCP servers) vs workload (untrusted, disposable, per-run: code/browser/patches).
   Exec via a narrow exec service — NOT a Docker socket, NOT DinD. The model never enters the
   workload container.
4. **Model by IP:** `http://<lm-studio-ip>:1234/v1`, never `localhost`/`host.docker.internal`.
   Only the control plane has the route.
5. **Every integration = a stdio MCP server + `tool_filter`** (`command`+`args`+`env`+`tool_filter`),
   no bespoke clients. **Placement:** GitHub/Jenkins/Grafana/email (credentialed) → control plane;
   Playwright (open-ended + untrusted web) → workload. Uniform wiring ≠ uniform privilege.
6. **Adding a capability never edits `agent.py`.** Skill = drop a `SKILL.md` folder (auto-discovered);
   MCP server = add a registry block. `agent.py` is constant.
7. **CI/CD: Jenkins orchestrates; agents are steps.** No LLM in pipeline control flow. Cheap
   deterministic gates first → agents where judgment lives → human last.
8. **Self-heal safe by construction:** coding agent **auto-proposes, human merges** (never
   auto-merge); every fix re-enters the FULL pipeline; attempt budget + circuit breakers; **flaky
   → quarantine, never the coding agent**; prod failure → **deterministic auto-rollback FIRST**,
   then triage.
9. **Diff allow-list:** the coding agent's patch may NOT touch `.github/**`, `Jenkinsfile`,
   `Dockerfile`, dep manifests, gate configs; nor add suppressions (`# noqa`, `// NOSONAR`,
   `@pytest.mark.skip`). Under `tests/**` it is **append-only**: may ADD new test files, may NEVER
   modify/delete/rename existing ones (enforce via `git diff --name-status`: allow `A`, reject
   `M`/`D`/`R`). Anti-reward-hacking; not optional. **Coding agent also runs unit tests + coverage
   in its workload container PRE-PUSH and must not open a PR unless tests pass AND project coverage
   ≥ 80%** — but that self-check is a fail-fast pre-flight, NOT authoritative: the same tests + a
   deterministic `--cov-fail-under=80` / Sonar gate re-run in the pipeline and are the source of
   truth (never trust the model's self-report).
10. **Secrets via SOPS+age** (never plaintext). GitHub PAT is **fine-grained**: `contents` +
    `pull-requests` only, no `workflows`/`actions`/`secrets`. Same PAT for coding+triage (shared
    via GitHub MCP) → scope lives on the token.
11. **No cloud. Free/OSS only.** No Gemini/Vertex features, no paid SaaS, no cosign keyless (use a
    local key).
12. **Prefer config over code.** Verify guardrails *as guardrails* (prove they block the bad case).
13. **Tooling: `uv` + Makefile.** Manage Python with `uv` (commit `uv.lock`; `uv sync`/`uv add`/
    `uv run`; `uv sync --frozen` in Dockerfiles — never bare `pip`/`venv`). Every repeatable op is a
    `make` target (`setup`, `check-model`, `up`, `test`, `eval`, `guardrails`, …); if you run a
    command twice, make it a target.
14. **Agents are per-run instantiations, not long-lived singletons.** An agent = a definition
    (LoopAgent + skill + model config) instantiated per run in its own disposable container.
    **Different repos parallelize; same-repo serializes** (queue behind the in-flight run; abort +
    replace a newer commit on the same branch). The throughput ceiling is the **single LM Studio
    endpoint** (one GPU, serialized inference) — the global in-flight cap tracks **measured model
    capacity, not container count**; raise throughput by scaling the model (vLLM/2nd instance via
    the LiteLLM shim), not by adding containers.
15. **Thinking/reasoning is selective: Triage YES, Coding YES, QA NO, Team-lead NO** (think where
    judgment lives, not execution/lookup). Use ADK **`PlanReActPlanner`** (model-agnostic);
    **never `BuiltInPlanner`/`ThinkingConfig`** (Gemini-only, no-ops on LM Studio). If a reasoning
    model is loaded, instead *suppress* thinking on QA + lead. Bound the reasoning budget. Eval-gate
    it (Phase-5 sweep) and size the Phase-7 concurrency cap with thinking enabled on triage + coding.

## Trust-boundary rule of thumb
Runs model-generated code or untrusted content → **workload container**.
Holds a secret / narrow outbound action → **control plane**.

## Build order (one milestone at a time; demonstrate its DoD before proceeding)
0 hello agent → 1 sandbox+loop+native tools → 1.5 MCP+Skills+capability loader → 2 persistence
(Redis)+compaction → 3 governance Plugin+secrets+egress → 4 observability (2 planes) → 5
eval+sim+sweep → 6 self-optimize (opt) → 6.5 graph migration (only if a loop outgrows itself) →
7 scale+kill-switch → 8 console (api_server + Tailscale `serve` not `funnel`) → **9 CI/CD agent
team (the product)** → 10 multi-repo OSS packaging.

## Top traps
- LM Studio `localhost` in a container = the container; use the IP.
- ADK Plugins may not fire under `InMemoryRunner` (#4464) — verify under the real runtime.
- Built-in `LlamaIndexRetrieval` drops top-k/scores/citations — write a custom `FunctionTool` if RAG.
- PyMuPDF/Marker/Unstructured-`hi_res` are AGPL/GPL — use Docling if parsing.
- Official GitHub MCP is broad — `tool_filter` per agent is mandatory.
- Flaky → quarantine (never coding agent). Prod failure → rollback before triage.

## Process
One milestone at a time. Read the plan section first. Surface invariant tensions; don't work
around them. Each "done" is something you can *show* working.
