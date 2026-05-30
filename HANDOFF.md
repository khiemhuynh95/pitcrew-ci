# HANDOFF — Self-Healing CI/CD Agent Team

**Audience:** Claude Code (the implementing agent). **Companion:** `adk_agent_build_plan.md`
(the full design + rationale) and `CLAUDE.md` (always-loaded invariants). This document is the
*executable contract*: what to build first, in what order, the rules you may never break, and
how to verify each step is done. The build plan explains *why*; this explains *how to start*.

> **Read order:** (1) this file, top to bottom; (2) `CLAUDE.md`; (3) the relevant phase section
> of `adk_agent_build_plan.md` before starting that phase. Do **not** start coding a phase
> without reading its plan section — the plan contains constraints not repeated here.

---

## 0. What you are building (one paragraph)

A self-hosted, open-source DevOps **agent team** that runs a full CI/CD cycle for the GitHub
repos a user points it at — build, scan, test, deploy — and **self-heals**: on failure it
triages, proposes a fix as a PR, and re-enters its own pipeline, with a human gating only the
irreversible (prod deploy) and the ambiguous (escalation). Built on Google ADK (`google-adk~=2.1`)
+ a local LM Studio model via LiteLLM, fully local / no cloud, as a Docker-compose stack. The
*product* is the CI/CD team; the lower phases build the agent substrate it runs on.

---

## 1. NON-NEGOTIABLE INVARIANTS (never violate these)

These are settled architecture decisions. If a task seems to require breaking one, **stop and
flag it** — do not silently work around them. They also live in `CLAUDE.md` so they're always in
context.

1. **Orchestration = `LoopAgent` + a single `LlmAgent` + Skills. No routing.** No `sub_agents`,
   no `AgentTool` coordinators, no `transfer_to_agent`, **no "manager agent."** Specialization
   comes from loading a `SKILL.md`, not from agent-to-agent delegation. (Local models are weak at
   tool-calling; every routing hop is a failure multiplier.)

2. **Pin `google-adk~=2.1`.** Don't use 1.x patterns that 2.x supersedes. Migration hygiene:
   JSON-blob session storage; **never** override `_run_async_impl()` (use callbacks); **never**
   broad `except Exception:` inside a tool (it swallows the engine's retry/HITL signals).

3. **Two-container trust boundary.** A **control-plane container** (trusted: harness, governance,
   secrets, model endpoint, all credentialed MCP servers) and a **workload container**
   (untrusted, disposable, per-run: runs `run_shell`/code/browser/coding-agent patches). The
   model never enters the workload container. Container-to-container exec via a **narrow exec
   service**, NOT a mounted Docker socket and NOT Docker-in-Docker.

4. **Model reached by IP, never `localhost`.** LM Studio runs on its own host;
   `api_base="http://<lm-studio-ip>:1234/v1"`, LM Studio bound `0.0.0.0:1234`. Only the
   control-plane container has the route; the workload container has none.

5. **Every external integration is a stdio MCP server** with a **`tool_filter` allowlist**.
   Uniform pattern: `command` + `args` + `env` + `tool_filter`. No bespoke API clients.
   **Placement rule:** credentialed/narrow servers (GitHub, Jenkins, Grafana, email) live in the
   **control plane**; open-ended + untrusted-inbound servers (Playwright) live in the **workload
   container**. Uniform wiring ≠ uniform privilege.

6. **Adding a capability never edits `agent.py`.** A skill = drop a folder with `SKILL.md`
   (auto-discovered). An MCP server = add a registry block. The agent definition is constant.

7. **CI/CD: Jenkins orchestrates; agents are steps it invokes.** No LLM in charge of pipeline
   control flow. Cheap deterministic gates first, agents where judgment lives, human last.

8. **Self-heal is safe by construction:** the coding agent **auto-proposes, a human merges** —
   never auto-merge. Every agent fix re-enters the **full** pipeline (no fast-path). Hard attempt
   budget + circuit breakers. **Flaky tests → quarantine, never the coding agent.** Prod failure
   → **deterministic auto-rollback FIRST**, then triage.

9. **The coding agent cannot edit its own gates, and cannot weaken tests.** A **diff allow-list**
   fully rejects any agent patch touching `.github/**`, `Jenkinsfile`, `Dockerfile`, dependency
   manifests, or gate configs (`sonar-project.properties`, `.semgrep.yml`, `.trivyignore`, coverage
   configs), and rejects added suppression markers (`# noqa`, `// NOSONAR`, `@pytest.mark.skip`, …).
   Under `tests/**` it is **append-only**: ADD new test files is allowed; modify/delete/rename
   existing tests is rejected (`git diff --name-status`: allow `A`, reject `M`/`D`/`R`) — the agent
   can write tests to cover its fix but can never weaken or remove one. Anti-reward-hacking; not
   optional. **Pre-push test+coverage gate:** the coding agent must run unit tests + coverage in its
   workload container before opening a PR and **must not push unless all tests pass AND project
   coverage ≥ 80%**; this is a fail-fast *pre-flight*, NOT authoritative — the same tests + a
   deterministic `--cov-fail-under=80` / Sonar gate re-run in the pipeline and are the source of
   truth (never trust the model's self-reported result).

10. **Secrets via SOPS+age, never plaintext in the repo.** The GitHub PAT is **fine-grained**:
    `contents` + `pull-requests` only, **no** `workflows`/`actions`/`secrets` scope. The same PAT
    backs both the coding and triage agents (shared via the GitHub MCP), so scope lives on the token.

11. **No cloud dependency. Free/OSS only.** No paid SaaS, no Gemini/Vertex-tied features, no
    cosign keyless (needs Fulcio). Everything runs on the user's machine.

12. **All formatting/output discipline of the plan holds:** prose over bullets in docs the user
    reads; minimal formatting; no over-engineering.

---

## 1.5 AGENT & TOOL ROSTER (the who-has-what-and-what-they-can't reference)

**Two meanings of "agent" — do not conflate.** (A) **LLM agents** = ADK LoopAgent + skill + model,
the only things with MCP tools and judgment. (B) **Deterministic components** loosely called agents
but are NOT (no model, no MCP): Jenkins, build planner, notifier, Renovate, auto-rollback. Build (B)
as plain code, never as LLM agents.

### A. The four LLM agents (each a per-run instantiation, NOT a long-lived singleton)

| Agent | Skill | MCP / tools | Container | Writes | Cannot |
|---|---|---|---|---|---|
| **QA** — judges if the deployed app works | `qa-automation` (on `browser-ops`) | **Playwright MCP** (stdio), filter: `browser_navigate`/`browser_snapshot`/`browser_click`/`browser_type` | **workload** (open-ended + untrusted web) | QA report → **Artifacts** (versioned) + metrics DB | reach GitHub, deploy, edit code, read secrets |
| **Triage** — classifies a failure (flaky/infra/real) | `failure-triage` | **Grafana MCP** (stdio, read: Loki/Tempo/Prometheus) + **GitHub MCP** & **Jenkins MCP** filtered to *read* verbs (workflow runs, build logs) | **control plane** | one tool: `create_issue` (structured) + classification → metrics DB | write code, open PRs, deploy; pass *raw* logs to coding agent (hands forward sanitized JSON) |
| **Coding** — proposes a fix PR (highest-risk) | `code-fix` | **GitHub MCP** (stdio), filter: **PR/branch verbs only** | **workload** (runs model-generated code) | branch + `agent-fix` PR; patch/diff → **Artifacts**; `agent_action` → metrics DB | **auto-merge**; modify/delete existing tests, touch `.github/`,`Jenkinsfile`,`Dockerfile`,dep-manifests,gate-configs (diff allow-list); use `workflows`/`actions`/`secrets` PAT scopes. **MAY add new tests (append-only).** **Pre-push:** must run unit tests + coverage in-container and not push unless tests pass AND project coverage ≥ 80% (pre-flight; pipeline re-runs the authoritative gate) |
| **Team-lead** — your conversational surface (spokesperson) | `team-lead` | **parameterized read-only query tools** over the **Postgres metrics DB** (`get_pending_approvals`/`repo_performance`/`incident_ranking`/`recent_runs`/`deploy_history`); read Plane 1/2 for drill-down | **control plane**; **pull-only** | one write path: relay *your* approval into the gate | call the other agents, orchestrate, originate/hold an approval, write SQL |

**Thinking / extended reasoning (per agent):** **Triage = YES, Coding = YES** (judgment-heavy + off the hot path; a misclassification or wrong fix is far costlier than the reasoning tokens); **QA = NO** (execution-bound + already slow); **Team-lead = NO** (deliberately low-reasoning + interactive, must answer fast). **Mechanism:** ADK **`PlanReActPlanner`** (prompt-driven, model-agnostic — for models without a built-in thinking feature); **NEVER `BuiltInPlanner`/`ThinkingConfig`** (Gemini thinking-tokens API — silently no-ops on LM Studio). If a *reasoning* model is loaded in LM Studio instead, the policy inverts to *suppressing* thinking on QA + lead. Bound the reasoning length (thinking budget). Triage's constrained JSON goes in `PlanReActPlanner`'s FINAL_ANSWER section. **Eval-gated** (Phase-5 harness sweep axis), and **size the Phase-7 concurrency cap with thinking enabled on triage + coding**.

### B. The 5 stdio MCP servers (by placement — uniform wiring ≠ uniform privilege)

- **Control plane (credentialed/narrow):** **GitHub MCP** (holds the fine-grained PAT — *shared* by
  coding + triage, so scope lives on the token; per-agent `tool_filter` separates their verbs),
  **Jenkins MCP** (Jenkins token), **Grafana MCP** (Grafana token), **email/SMTP MCP** (`send_email`
  only).
- **Workload container (open-ended + untrusted-inbound):** **Playwright MCP** only.

### C. Deterministic components (NO model, NO MCP — build as code)

Jenkins (orchestrator: sequences stages, dispatches the LLM agents, holds gates) · build planner
(`.agentci.yml` → toolchain image + commands; fingerprint lookup, agent only as fallback) ·
notifier (deterministic email push, Jenkins-triggered, framed in the lead's voice) · Renovate
(dependency-update bot) · auto-rollback (prod-failure reflex) · log shipper (Promtail/Alloy →
Loki) · error-detection alert (Grafana/Loki rule = the "system error-catch" trigger).

### D. Cross-cutting (touches all agents, owned by none)

The **governance Plugin** (Presidio PII + LLM Guard injection + NeMo Guardrails + Reflect-and-Retry)
fires on *every* model/tool call across all four agents — it wraps the Runner, not any one agent.
**Built on prebuilt pieces:** Reflect-and-Retry is a prebuilt ADK plugin; audit logging starts from
the prebuilt **Logging plugin**; resilience uses native `on_model_error`/`on_tool_error` hooks.
**Ordering caution:** Plugin callbacks run *before* agent-level callbacks, and a Plugin hook that
returns non-`None` **skips** the agent-level callback (fine for blocking — know it regardless).

**Read the asymmetry as the safety story:** the agent touching untrusted web (QA) can't reach code
or secrets; the agent writing code (coding) is sealed in the workload container behind the diff
allow-list; the agent you talk to (lead) reads everything but writes almost nothing; triage (reads
attacker-controllable logs) has exactly one narrow write verb. Power is inversely matched to exposure.

### E. Prebuilt vs custom — the ADK audit (check Integrations BEFORE building)

ADK's rule: *check Tools & Integrations for a prebuilt before writing your own.* Applying it:
- **Use prebuilt plugins (don't hand-roll):** **Reflect-and-Retry** (tool-failure retry),
  **Logging** (start audit from this), **Context Filter** + native **context compaction** (Phase 2
  context-window control), **Save Files as Artifacts**.
- **Use native hooks (not bespoke code):** `on_model_error` / `on_tool_error` (graceful
  degradation); `before_model_callback` returning a cached `LlmResponse` = the semantic-cache
  **mechanism** (the Redis *store* is still ours).
- **Config, not code:** all MCP servers via `McpToolset`, any REST via `OpenAPIToolset`, the
  `LoopAgent` backbone, skills as `SKILL.md` folders.
- **Genuinely custom code (the short list):** the lead's **parameterized query tools** (fixed SQL);
  a **custom local `BaseArtifactService`** (only persistent built-in is GCS = cloud = excluded;
  implement `save`/`load`/`list_keys`/`delete`/`list_versions` over filesystem/Postgres); the
  Phase-5 **sweep/aggregation harness**; the Phase-7 **watchdog sidecar**; plus the deterministic
  components in C.
- **Artifacts store (versioned, not session state):** QA reports, coding-agent patches/diffs, the
  Phase-5 eval leaderboard — via the custom local `BaseArtifactService`; `user:` namespacing →
  `repo_id` scoping.
- **Code execution (validated against the Code integrations catalog):** the agents' in-container
  code/test execution uses the prebuilt **Environment Toolset** (local compute environments) — the
  *only* local code-exec primitive in the catalog; the cloud/Gemini executors (**Code Execution**,
  **Code Execution Tool with Agent Runtime**, **GKE Code Executor**, **Computer Use**) are all
  **excluded by no-cloud**. **Daytona** (self-hostable sandbox) is an *alternative* to the custom
  workload+exec-service worth evaluating only if its isolation matches the two-container topology —
  not a default swap. The **GitHub** integration is the GitHub MCP we already use.
- **Non-issue confirmed:** the "one tool per agent" limit applies ONLY to Gemini built-in tools
  (Google Search / built-in Code Execution / Agent Search), none of which we use → does not affect us.

---


## 2. ENVIRONMENT PREREQUISITES (assume nothing is installed)

Before Phase 0, confirm/establish:
- **Docker Engine** + **docker compose** available on the build host.
- **LM Studio** running on a reachable host machine, bound `0.0.0.0:1234`, with (a) a strong
  function-calling instruct model loaded (Qwen2.5/Qwen3 or Llama-3.x class) **and** (b) an
  embedding model loaded if/when RAG is added (separate model load — watch VRAM).
- **`uv`** is the Python package/env manager (NOT bare `pip`/`venv`). **`make`** is the task
  runner — every common operation is a Make target, never a remembered command. See §4.5.
- **Python 3.12+** (managed by `uv` — `uv python pin 3.12`).
- Pin versions in `pyproject.toml`: `google-adk~=2.1`, `litellm` (avoid 1.82.7–1.82.8 — security
  advisory). Commit `uv.lock` for reproducibility — this is what makes a user's clone "just work."
- Network reachability from the build host to the LM Studio host's port 1234 (the single most
  common first-run failure — `make check-model` wraps `curl http://<ip>:1234/v1/models`).

State the resolved `<lm-studio-ip>` in `.env` (see §5), never hardcode it.

---

## 3. BUILD ORDER (milestones, each independently verifiable)

Build **bottom-up**. Each milestone is shippable and has a **Definition of Done (DoD)** you must
demonstrate before moving on. Do not batch phases; land and verify one at a time. Read the
matching plan section before each.

### Milestone 0 — Hello agent
Thin `agent.py`: one `LlmAgent` via `LiteLlm` pointed at LM Studio (by IP), wrapped in nothing yet.
Set up `pyproject.toml` + `uv.lock` + the initial `Makefile` here.
**DoD:** `make check-model` succeeds; `make agent` (`adk web`) chats with the agent, served by the
local model.

### Milestone 1 — Sandbox + loop + native tools
`LoopAgent(max_iterations) + finish()`; native tools `run_shell/write_file/read_file/copy_out/finish`
running in the **workload container** behind the exec service; `before_tool_callback` guard
(step/time budget, denylist). Adopt the Environment Toolset interface (`RemoteEnvironment`) over
the workload container.
**DoD:** give a goal, it works autonomously in the sandbox, saves an artifact, stops cleanly at
`finish()` or budget. The control-plane container cannot be modified by sandbox code.

### Milestone 1.5 — MCP supply + Skills + the capability-loader pattern
The `capabilities.py` loader (auto-discovers `skills/`, builds `McpToolset` per registry block).
Playwright MCP (workload) + email SMTP MCP (control plane), each `tool_filter`-ed. Two skills
(`browser-ops`, `web-research`). **This is where `agent.py` becomes constant** — verify adding a
skill/server needs no `agent.py` edit.
**DoD:** agent triggers the right skill, drives the headless browser via filtered verbs, only the
triggered skill's instructions load, and can `send_email`. Adding a dummy skill folder works with
zero code change.

### Milestone 2 — Persistence + memory + cache + compaction
`DatabaseSessionService` (SQLite first), then **Redis/Valkey** as the consolidated
session+memory+vector+semantic-cache backbone. Turn on **context compaction** (required — local
windows are small).
**DoD:** kill mid-run, restart, resume same session; a repeated prompt is served from the
semantic cache; a long run doesn't overrun the context window.

### Milestone 3 — Govern hardening
One global ADK **Plugin** on the Runner: Presidio (PII) + LLM Guard (injection/output) + NeMo
Guardrails (Colang policy). SOPS+age secrets. Squid egress allowlist (workload on `internal`
network). Browser-content screening. Audit JSON on `after_*`. Add the **Reflect-and-Retry**
Plugin. **Verify the Plugin actually fires under your runtime** (ADK #4464 — not always under
`InMemoryRunner`).
**DoD:** a probe injection is blocked (incl. one planted in fetched web content); PII redacted;
sandbox+browser reach only allowlisted hosts; every action audited.

### Milestone 4 — Observability (two planes) + command center
One OTel backend, split by scope/tags. **Plane 1** = agent traces (native ADK GenAI spans).
**Plane 2** = pipeline/app health (Prometheus/Grafana, fed later by Phase 9's post-deploy
monitor). Ensure the Jenkins build span and agent span share a `traceparent`. **Command center =
three views, ASSEMBLED not built:** (a) **ADK web UI** (`adk web`, built-in) for dev-debugging one
agent — live agent graph, Trace/Graph tabs, event replay; (b) **Arize Phoenix** (self-hosted, ELv2,
the recommended default) as the *operational* view that renders **agent workflow maps** from the
Plane-1 OTLP spans you already emit — the upgrade of "just Jaeger" once you want to *watch the
team* (Langfuse/SigNoz are richer-but-heavier alternatives); (c) the **team-lead agent + metrics
DB** (Phase 9) as the director view. "Watch a pipeline running" spans three panes — Jenkins UI
(stage flow) + Phoenix (agent steps) + Grafana (health); a unified single pane is **deferred,
demand-driven**, not custom-built now.
**DoD:** an agent run renders as a trace tree in Plane 1 (Phoenix); a deploy's stage/app metrics
render separately in Plane 2; a Plane-2 alert can hand off to triage; the ADK web UI shows a live
agent run during dev.

### Milestone 5 — Eval + benchmark + simulation
ADK evalset + **Environment Simulation** (mock tool responses). Metrics: tool-trajectory,
response-match, task-success via a **local** LLM judge (DeepEval/Promptfoo at LM Studio), plus
steps/latency/tokens/retries from Plane 1. **Variance over N=3–5 repeats.** The 3-axis sweep
(model / prompt / harness), one axis at a time.
**DoD:** you can answer "model A vs B / prompt v1 vs v2 / budget 40 vs 80" with mean±variance from
a deterministic simulated suite; a regression run fails CI.

### Milestone 6 — Self-optimization (optional/demand-driven)
`adk optimize` (GEPA) pointed at LM Studio if supported, else DSPy offline. **DoD:** the Phase-5
score measurably improves after a pass.

### Milestone 6.5 — Graph-workflow migration (ONLY if a loop outgrows itself)
Lift a loop into an ADK 2.x graph with **code routers** (not `RoutedAgent`, not
`transfer_to_agent`). Likely first for the coding agent. Graph workflows unlock ADK's native
**model-free HITL node** (`RequestInput`) for *agent-internal* checkpoints — NOT for the Phase-9
pipeline gates (those stay Jenkins/async; see the HITL-placement trap). **DoD:** runs as a graph,
governance Plugin still fires per node, checkpoint/resume works.

### Milestone 7 — Scale-out + kill-switch + concurrency model
Redis/Valkey broker + workload-container pool; A2A if multi-instance; **Falco** + a metrics
watchdog → `docker stop` kill-switch (the self-heal backstop). **Concurrency model — build it
this way, not as long-lived agents:** an agent is a **definition instantiated per run** in its own
disposable container, NOT a singleton that holds a repo. **Different repos run in parallel**
(separate instances). **Same-repo overlap is two-layer:** (a) queue a second run behind the first
(serialize — collision on staging/deploy/branch), but **abort-and-replace** a newer commit on the
*same branch* (`disableConcurrentBuilds(abortPrevious: true)` + stage `lock()`); (b) a **global
in-flight cap** queues runs beyond what the single LM Studio endpoint can serve — sized to
**measured model capacity, NOT container count** (every instance hits one GPU that serializes
inference; the model is the throughput ceiling). Raise the ceiling by scaling the model (2nd LM
Studio / vLLM, a LiteLLM config change), not by adding containers. **DoD:** different-repo
pipelines run concurrently; a same-repo second run queues (and a same-branch re-push
aborts-and-replaces); runs beyond the cap queue in the broker; a threshold breach halts a run.

### Milestone 8 — Control surface (operator console)
`adk api_server` (REST+SSE), bound **privately**, published over **Tailscale `serve`** (NEVER
`funnel`), ACL-scoped, plus an app token. Wire **stop/approve** to ADK **Cancel** server-side.
**DoD:** from a phone on the tailnet: watch a run, approve a prod deploy, stop a run (and it
actually halts server-side), download artifacts.

### Milestone 9 — The CI/CD agent team (THE PRODUCT)
Jenkins orchestrator. A **build planner** (deterministic control-plane step, runs *before*
Jenkins) resolves each repo's `(toolchain_image, commands)`: repo-carried `.agentci.yml` override
→ manifest **cached under `repo_id` on our system** → else **generate + cache** (deterministic
stack fingerprint — `package.json`→node, `pyproject.toml`→python, `go.mod`→go; an agent drafts
only if fingerprinting fails). Stages run via `agent { docker { image <resolved> } }` (toolchain
from the image, never pre-installed on Jenkins); app-image build uses **Kaniko** (no Docker
socket); build/test run on the resolved image via the **exec service**. **A generated/cached
manifest leaves `deploy:` DISABLED until a human confirms the deploy target** (console or
committed override) — gates/tests/staging run freely, prod deploy waits. The console surfaces the
resolved manifest; re-resolve on stack change. The user is **never forced to author `.agentci.yml`**.
Three agents (QA / triage / coding), each LoopAgent+skill, each reaching integrations via stdio
MCP (GitHub/Jenkins/Grafana/email in control plane, Playwright in workload). **Plus a fourth: the
team-lead agent — your single conversational surface, a SPOKESPERSON not a manager.** You (the
director) talk to only this agent for status/performance/approvals. It **reads** state (Plane 1
traces, Plane 2 health, run history, the pending-gates queue) and **relays** your approvals into
the deterministic gate — it **never** calls the other agents (Jenkins coordinates), never
originates an approval, never holds deploy authority (the approval stays cryptographically yours;
audit shows *you* approved via the lead). **Pull-only** (no autonomous trigger — runs only on your
query/approval; the email channel still pushes events). Narrates *fetched* facts (deterministic
metrics + links to artifacts), never eyeball-estimates. **Data source: a `repo_id`-keyed Postgres
CI/CD metrics DB** (`runs`/`deploys`/`incidents`/`agent_actions`/`gates` — DORA-metrics home,
written by Jenkins+agents, the same Postgres Phase 2 can use for pgvector). The lead queries it via
**parameterized query tools, NOT free-form NL2SQL**, read-only (`get_pending_approvals`,
`repo_performance`, `incident_ranking`, `recent_runs`, `deploy_history`) — fixed reviewed SQL,
model fills params. It is the conversational front-end of the
Phase-8 console. Skill: `team-lead`.
**Communication model (who reaches you, when):** PUSH = the **deterministic notifier** (email MCP,
triggered by Jenkins, no LLM in the path) — the only thing that proactively pings you; **framed in
the lead's voice** but dumb-reliable. PULL = the **team-lead agent** (pull-only) — you turn to it
after a ping or to ask anything; tapping a ping opens a conversation with it. They are **separate
components** (the pinger can't fail to ping; the conversationalist can't self-trigger).
*Notified (FYI):* run finished, deploy completed, auto-rollback fired, incident filed, agent-fix
PR opened, circuit breaker tripped. *Review + approve (exactly two gates):* prod-deploy approval
(after QA, pipeline pauses) and escalation (triage can't fix / budget exhausted). You do NOT
approve staging deploys, rollbacks, issue creation, or gate results.
Deterministic gates in order (lint → gitleaks → Semgrep →
deps → unit tests → **Sonar after tests** → Trivy fs → [Squawk if migrations] → build → Trivy
image → Syft → cosign) → staging → QA agent → **human approval** → prod (blue-green) →
**auto-rollback first on failure** → notify. Self-heal loop with all guardrails (§1.8–1.9),
circuit breakers, flaky→quarantine, branch protection + CODEOWNERS, "new test must fail on
parent" gate. Renovate as the dependency bot.
**Managed-app log capture (the self-heal loop's input — currently the easy thing to miss):** the
deploy harness ships each **deployed app's container stdout/stderr to Loki**, tagged by `repo_id`
(Promtail/Alloy; **zero app changes** — honors clone-and-go). A **deterministic Grafana/Loki
error alert** on those logs is the "system error-catch" issue source (NOT an LLM watching logs);
it fires the self-heal loop. The triage agent reads the actual error+stack-trace from Loki via the
**Grafana MCP** (tooling already exists). Plane-2 metrics say "error rate up"; the **Loki log
event** is what triage diagnoses. Richer structured errors are an **opt-in `.agentci.yml`**
enhancement, never required.
**Build manifest-driven and `repo_id`-keyed from the start** (the only Phase-10 prep).
**DoD:** a merged PR flows through gated pipeline → human-approved prod deploy → automated QA
report; **a repo with no `.agentci.yml` gets a manifest inferred + cached (deterministically) and
runs gates/tests, but does NOT deploy until the human confirms the target**; an induced failure →
issue + `agent-fix` PR that re-enters the pipeline and **stops at the human merge**; a flaky test
is quarantined (not code-fixed); **a coding-agent attempt to MODIFY/DELETE an existing test is
rejected by the diff allow-list (but ADDING a new test is allowed), and a PR that fails tests or
drops project coverage below 80% is blocked** (pre-push self-check + authoritative pipeline gate);
the agent cannot merge its own PR; **and you can ask the team-lead agent "what's
the team doing / what needs me?" and approve a pending prod deploy by talking to it — with the
audit log showing the human (you) as approver, not the lead.**

### Milestone 10 — Self-hosted multi-repo OSS packaging
`repos.yml` (the user's repo list — the *only* file they must write) + **language presets** the
build planner infers from + user-held fine-grained PAT + `repo_id` namespacing (hygiene) +
per-run isolation + onboarding polish (`docker compose up`, `.env.example`, `repos.yml.example`,
"create a token like this" doc). The per-repo `.agentci.yml` is an **optional override**, never
required — the planner generates and caches a manifest on first contact.
**DoD:** a fresh clone + a token in `.env` + two repos in `repos.yml` + `docker compose up`
yields a working gated+self-healing pipeline on both, **zero engine-code edits**.

---

## 4. PROPOSED REPO LAYOUT (create as you go; don't scaffold all at once)

```
.
├── CLAUDE.md                      # always-loaded invariants (companion file)
├── HANDOFF.md                     # this file
├── adk_agent_build_plan.md        # full design + rationale
├── README.md                      # user-facing: clone → configure → make up
├── Makefile                       # the task runner — one verb per operation (see §4.5)
├── pyproject.toml                 # uv-managed deps; pins google-adk~=2.1, litellm, etc.
├── uv.lock                        # COMMITTED — reproducible env; what makes a clone "just work"
├── docker-compose.yml             # control-plane, workload, squid, observability, (P9) jenkins/sonar
├── .env.example                   # documented env vars (NEVER commit real .env)
├── repos.yml.example              # P10: user's repo list
│
├── control_plane/
│   ├── agent.py                   # CONSTANT — never edited to add capabilities
│   ├── capabilities.py            # skill auto-discovery + MCP registry loader
│   ├── config/
│   │   └── mcp_servers.yaml        # stdio MCP registry (github/jenkins/grafana/email/playwright)
│   ├── governance/                # the ADK Plugin (Presidio/LLM Guard/NeMo) + diff allow-list
│   ├── skills/                    # one folder per skill (auto-discovered)
│   │   ├── browser-ops/SKILL.md
│   │   ├── web-research/SKILL.md
│   │   ├── qa-automation/SKILL.md      # P9
│   │   ├── failure-triage/SKILL.md     # P9
│   │   ├── code-fix/SKILL.md           # P9
│   │   └── team-lead/SKILL.md          # P9 — read+relay spokesperson; the console's chat front-end
│   └── secrets/                   # .sops.yaml + encrypted env (age)
│
├── workload/
│   ├── exec_service/              # the narrow exec API (NOT a docker socket)
│   └── Dockerfile                 # hardened: non-root, read_only, cap_drop ALL, no-new-privileges
│
├── pipeline/                      # P9
│   ├── build_planner/             # resolve .agentci.yml: override→cache→generate(fingerprint); presets table
│   ├── presets/                   # language presets: node/python/go/java → toolchain_image + commands
│   ├── Jenkinsfile.tmpl           # generic; toolchain image is a variable from the planner
│   ├── gates/                     # gitleaks/semgrep/trivy/syft/cosign/squawk stage scripts; kaniko image build
│   ├── deploy/                    # blue-green swap script + Caddy/Traefik config; ships app container logs → Loki (repo_id-tagged)
│   ├── metrics/                   # Postgres schema (runs/deploys/incidents/agent_actions/gates) + parameterized query tools the lead uses
│   ├── artifact_store/            # custom local BaseArtifactService (filesystem/Postgres) — QA reports, coding patches, eval leaderboard; GCS is the only persistent built-in (excluded)
│   └── self_heal/                 # circuit breakers, quarantine, "new test fails on parent"
│
├── eval/                          # P5: evalsets + the 3-axis sweep harness
└── observability/                 # P4: otel config; Phoenix (agent command center) + Jaeger; Prometheus/Grafana (Plane 2)
```

`.agentci.yml` is an **optional repo override** — most repos never carry one. By default the
build planner infers and **caches** the manifest under `repo_id` in control-plane state (Phase 2
Redis); the cache, not the repo, is the source of truth unless the repo commits an override.
When present in a repo it declares: `language`, `build`, `test`, `image`, `quality` gates,
`deploy` targets, `qa` intent, and optionally `toolchain_image`.

---

## 4.5 TOOLING — `uv` + Makefile (uniform, reproducible ops)

**`uv` manages Python** (no bare `pip`/`venv`/`poetry`). Rationale: fast, lockfile-reproducible,
single tool for envs + deps + Python versions — which is exactly what a *self-hosted clone*
needs to "just work."
- Dependencies live in **`pyproject.toml`**; the resolved **`uv.lock` is committed** (reproducible
  builds — do not gitignore it).
- Common commands: `uv sync` (create/refresh env from the lock), `uv add <pkg>` (add a dep + update
  lock), `uv run <cmd>` (run inside the env without manual activation), `uv python pin 3.12`.
- **In Dockerfiles**, use the official `uv` base/COPY pattern and `uv sync --frozen` for a
  reproducible image build — NOT `pip install --break-system-packages`.
- Control-plane and workload containers each get their own `pyproject.toml`/lock if their deps
  diverge (the workload image stays minimal — it runs untrusted code, so fewer deps = smaller
  attack surface).

**`make` is the task runner** — every common operation is a target, so nobody memorizes compose
or uv incantations. This mirrors the convention-over-configuration spine of the whole project
(skill auto-discovery, MCP registry). Targets to provide (grow per phase; keep names stable):

```
make setup         # uv sync + uv python pin + pre-flight checks
make check-model   # curl http://<lm-studio-ip>:1234/v1/models  (the #1 first-run trap)
make up            # docker compose up -d the whole stack
make down          # docker compose down
make logs          # tail compose logs
make agent         # run `adk web` against the control-plane agent (Milestone 0/1)
make fmt           # uv run ruff format
make lint          # uv run ruff check
make test          # uv run pytest (unit tests)
make eval          # run the ADK evalset / 3-axis sweep (Milestone 5)
make guardrails    # run the guardrail tests (egress-block, diff-allow-list, never-auto-merge, kill-switch)
make secrets-edit  # sops edit the encrypted env
make clean         # remove build artifacts, prune workload containers
```

Rules: a Make target wraps the canonical way to do each operation — if you'd type a multi-part
`docker compose ...` or `uv run ...` command more than once, it becomes a target. The README's
user-facing flow is `make setup` → edit `.env` → `make check-model` → `make up`.

---

## 5. ENV VARS (document every one in `.env.example`; commit no secrets)

Minimum set (expand per phase):
- `LMSTUDIO_API_BASE=http://<lm-studio-ip>:1234/v1` and `LMSTUDIO_MODEL=<model-id>`
- `REDIS_URL=...` (P2)
- `METRICS_DB_URL=postgres://...` (P9 — CI/CD metrics DB; may be the same Postgres as pgvector)
- `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=...`, `OTEL_SERVICE_NAME=...` (P4)
- `GITHUB_PERSONAL_ACCESS_TOKEN=...` (fine-grained; P9) — **SOPS-encrypted, not plaintext**
- `JENKINS_URL/_USER/_TOKEN`, `GRAFANA_URL/_TOKEN`, `SMTP_HOST/_PORT/_USER/_PASSWORD` (P9/P1.5)
- `AGENT_KILL_SWITCH=0` (P7/P9 global brake, checked at the start of every agent action)
- `TAILSCALE_*` / app token (P8)

---

## 6. HOW TO WORK (process for the implementing agent)

- **One milestone at a time.** Land it, demonstrate its DoD, then proceed. Don't scaffold ahead.
- **Read the plan section first.** It carries constraints not duplicated here.
- **When blocked by an invariant**, surface the tension explicitly; don't work around it silently.
- **Verify, don't assume.** Each DoD is a thing you can *show* working, not a claim.
- **Prefer config over code.** New capability → registry block or `SKILL.md`, not new client code
  and never an `agent.py` edit.
- **Use `uv` and the Makefile, not ad-hoc commands.** Manage Python with `uv` (commit `uv.lock`);
  every repeatable operation is a `make` target (§4.5). If you run a command twice, make it a target.
- **Keep the trust boundary sacred.** Anything that runs model-generated code or untrusted content
  goes in the workload container; anything holding a secret stays in the control plane.
- **Test the guardrails as guardrails.** The egress allowlist, the diff allow-list, "never
  auto-merge," and the kill-switch each need a test that proves they *block* the bad case — not
  just that the happy path works (`make guardrails`).

---

## 7. THE TRAPS (things that will bite if forgotten)

- LM Studio `localhost` from inside a container is the container, not the host — use the IP.
- ADK Plugin callbacks may not fire under `InMemoryRunner` (#4464) — verify under the real runtime.
- **Plugin callbacks take precedence over and SKIP agent-level callbacks.** Plugin hooks run
  *before* agent callbacks, and if a Plugin hook returns non-`None`, the matching agent-level
  callback is **not executed**. With one global governance Plugin, a hook that returns a value (to
  block/redact/cache) silently bypasses agent callbacks — intended for blocking, but don't rely on
  an agent callback that a Plugin hook may pre-empt.
- **The only persistent built-in `BaseArtifactService` is `GcsArtifactService` (cloud) — excluded.**
  `InMemoryArtifactService` is ephemeral. To store QA reports / coding patches / the eval
  leaderboard persistently *and* locally, **write a custom `BaseArtifactService`** (filesystem or
  Postgres) implementing `save`/`load`/`list_keys`/`delete`/`list_versions`. Don't reach for
  `GcsArtifactService` — it breaks the no-cloud rule. Use ADK's Artifacts *interface* (versioning,
  `user:`→`repo_id` namespacing) with a local backend.
- The built-in `LlamaIndexRetrieval` returns only `[0]` (drops top-k/scores/citations) — if RAG is
  added, write a ~10-line custom `FunctionTool` instead.
- PyMuPDF/Marker/Unstructured-`hi_res` are AGPL/GPL traps — if document parsing is added, use
  Docling.
- The official GitHub MCP exposes a *broad* verb set — `tool_filter` per agent is mandatory, not
  optional.
- Flaky tests routed to the coding agent → it "fixes" them by deleting them. Route to quarantine.
- Triaging a prod incident before rolling back inverts SRE doctrine — roll back first.
- cosign keyless needs Fulcio (cloud) — use a local key.
- **Don't pre-install language toolchains on Jenkins** (mega-image / version-conflict trap). The
  toolchain comes from the resolved Docker image per build (`agent { docker { image <var> } }`);
  app-image builds use Kaniko, not a Docker socket.
- **A generated/cached manifest must leave `deploy:` disabled** until a human confirms the target.
  Inferring build/test from a fingerprint is safe (reversible); silently enabling a *deploy* is
  not. Cache the manifest, never the deploy decision.
- **Manifest resolution is deterministic-first, agent-last** — fingerprint the stack with a
  file-existence check; only call an agent to draft a manifest when fingerprinting fails. Don't
  reach for the LLM when a lookup works.
- **Cached ≠ hidden** — surface the resolved manifest in the console and re-resolve on a stack
  change, or a wrong inference becomes silently sticky.
- **The team-lead agent reads + relays only.** It must not call the other agents (Jenkins
  coordinates), must not originate or hold approvals (the human decides; the gate enforces; the
  approval is cryptographically the human's), and is **pull-only** (no autonomous trigger). The
  slide to watch for is "let it auto-approve low-risk ones" — that turns it back into the manager
  agent invariant #1 forbids. Its summaries link to artifacts and its numbers come from
  deterministic queries (a local model narrating traces can confabulate).
- **The lead queries the metrics DB via parameterized tools, not free-form NL2SQL.** Fixed reviewed
  SQL with bound params, read-only. A weak local model writing arbitrary SQL gets joins/columns
  wrong and produces confident wrong answers — the lead's worst failure. Model picks a tool + fills
  params; it does not author SQL. (NL2SQL only as an explicit later fallback, still read-only.)
  Observability (traces/time-series) is NOT this DB — "which repo has most incidents" is a
  relational `GROUP BY` over the Postgres metrics records, not a Jaeger/Prometheus query.
- **Push and pull are separate components.** The proactive notifier is the **deterministic email
  step (Jenkins-triggered, no LLM)** — it must NOT be the lead agent (the lead is pull-only so it
  can't self-trigger). The notifier is *framed in the lead's voice* for one-assistant UX, but that
  framing is a template, not an LLM call — don't "let the lead send notifications," which would
  give it a push trigger and erode pull-only. Exactly two events require human action (prod approval,
  escalation); everything else notified is FYI. Never notify-and-wait on a rollback (it already fired).
- **The two human gates live in Jenkins/async, NOT ADK's `RequestInput`.** ADK has a native
  model-free HITL node, but (1) it requires a **graph workflow** — the agents are `LoopAgent`s, so
  it isn't available to them until a Phase-6.5 graph migration; and (2) a `RequestInput` pause holds
  the **agent run open** (its LM Studio slot + workload container) while waiting for the human —
  unaffordable against the single-endpoint concurrency cap. Keep **prod-deploy approval = Jenkins
  `input`** (resource-cheap pause: no GPU/model/container held) and **escalation = async GitHub
  issue** (run completes, releases its slot). `RequestInput` is reserved for *agent-internal* graph
  checkpoints (Phase 6.5), never the pipeline gates. Validated against adk.dev/workflows +
  /graphs/human-input: the LoopAgent is ADK's Template-workflow (correct fit); graph is its
  documented successor; routing is still experimental (no-routing stance holds).
- **Agents are per-run instantiations, NOT long-lived singletons.** Don't build "the QA agent" as
  one process that holds a repo and blocks — build the *definition* and instantiate it per run in
  its own container. Different repos parallelize; same-repo serializes (queue + abort superseded
  same-branch commit). The real concurrency ceiling is the **single LM Studio endpoint** (one GPU,
  serialized inference), so the global in-flight cap tracks **measured model capacity, not
  container count** — and you raise throughput by scaling the model (vLLM / 2nd instance), not by
  adding containers. Sizing the cap to container count just moves the bottleneck inside the model.
- **Don't build a custom command-center dashboard.** Your agents already emit OTel spans — point
  them at **Arize Phoenix** (self-hosted) for the live agent-workflow view; use the **built-in ADK
  web UI** for single-agent dev-debug; the **team-lead agent** is the director view. Building a
  bespoke UI means reinventing live agent graphs + trace waterfalls and owning them forever. A
  single unified pane across Jenkins + Phoenix + Grafana is deferred/demand-driven, not now.
- **Managed-app errors are captured at the DEPLOY BOUNDARY, not in the app.** Ship the deployed
  container's stdout/stderr to Loki tagged by `repo_id` (zero app changes — clone-and-go). The
  self-heal "system error-catch" trigger is a **deterministic Grafana/Loki alert**, not an LLM
  watching logs. Don't require managed apps to add logging code (breaks zero-touch); structured
  error capture is opt-in via `.agentci.yml`. Plane-2 = the *metric*; Loki = the *event triage
  reads*. Without this wired, the coding agent has nothing to react to — it's the loop's input.

---

## 8. WHAT "DONE" MEANS FOR THE WHOLE SYSTEM

A user clones the repo, sets a fine-grained GitHub token in `.env`, lists their repos in
`repos.yml`, runs `docker compose up`, and gets: gated CI/CD on every PR, automated QA on staging,
a single human approval for prod, automatic rollback on prod failure, and a self-healing loop that
proposes fixes as PRs (which they merge) — all on their own hardware, no cloud, having edited zero
engine code. The agent team is observable (two planes), benchmarked (eval suite), and safe (every
guardrail tested as a guardrail).
