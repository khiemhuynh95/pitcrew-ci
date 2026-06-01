# Build Plan — Self-Healing CI/CD Agent Team (Local, Open-Source, ADK + LM Studio)

**What this is.** A self-hosted, open-source **DevOps agent team** that runs a full CI/CD
cycle for the GitHub repos you point it at — build, scan, test, deploy — and *self-heals*:
when something fails, it triages, proposes a fix as a PR, and re-enters its own pipeline,
with a human gating only the irreversible and the ambiguous. Each user clones it, runs
`docker compose up` on their own machine, lists their repos, and provides their own GitHub
token. Nothing runs in anyone else's cloud.

**The product *is* the CI/CD team.** Everything below — the agent harness, the MCP supply
layer, the Skills packaging layer, the four platform bands (Build / Scale / Govern /
Optimize), the sandbox, governance, observability, evaluation, the remote console — is the
**agentic foundation the CI/CD team stands on**, built bottom-up. Phases 0–8 construct the
substrate; Phase 9 is the agent team itself; Phase 10 makes it clone-and-go for any repo.

Stack is free/open-source and low-code. **LM Studio** is the model provider (free, not OSS —
swappable for Ollama/llama.cpp via the same LiteLLM wiring).

**Conventions**
- *Bands*: Build / Scale / Govern / Optimize.
- *Low-code surface*: what you configure instead of code (YAML, config file, CLI, env var).
- *Gap*: where no free/low-code option exists and you write custom glue (the same boxes a
  paid platform fills — expect them).
- The YAML Agent Config / Visual Builder path is **Gemini-only today**, so with LM Studio the
  *model* is defined in a small Python file; everything else stays config.

---

## Architecture decisions (settled)

Three foundational calls shape every phase. The first two follow from one fact: a local
open-weight model on LM Studio is **weaker at tool-calling than Gemini/Claude/GPT-4**, so the
design favors *deterministic orchestration with bounded LLM decisions inside*. The third
follows from the product being a CI/CD tool other people self-host.

1. **Orchestration backbone = ADK 2.x Workflow Runtime (the graph engine), NOT routing/delegation
   and NOT the deprecated `LoopAgent`.** Each agent in the team (QA, triage, coding) is a single-goal
   worker with one goal and one trace — nothing to route between at the agent level. Routing
   (`sub_agents` transfer / `AgentTool` coordinators / the new Task-API & Collaborative layer) is the
   pattern *most* sensitive to weak tool-calling (every hop is another dice roll; 95% per-call over 8
   steps ≈ 66% success) and hands control *out* of the governed flow. The Skills layer provides the
   specialization routing would — load the right SKILL.md into one worker, without multi-agent's ~15×
   token overhead or split traces.
   → **The worker is a single (non-deprecated) `LlmAgent`** (tools + Skills; its own inner
   reason→tool→observe loop). The **bounded outer iteration** — the old `LoopAgent`'s job (a hard cap
   + clean `finish()`/escalate termination) — is expressed as an **ADK *dynamic workflow*: a plain-
   Python iterative loop** (`while step < budget and not finished: await ctx.run_node(worker)`), which
   is ADK's documented pattern for iterative-refinement loops and gives **automatic checkpointing/
   resume** for free. Push specialization into SKILL.md bundles. Do **not** add `sub_agents`/
   `AgentTool` or adopt the **Task API / Collaborative / inter-agent-routing** layer (that *is* the
   manager pattern). There is **no "manager agent"** — Jenkins coordinates the agents; the agents
   never coordinate each other.
   → *Why not `LoopAgent`:* it is **deprecated in ADK 2.0** and emits a removal warning (the template
   workflow agents Sequential/Parallel/Loop are superseded by the graph engine; the per-class doc
   pages lag the runtime warning, so trust the warning, not the page). Building net-new on it is
   guaranteed migration debt. The graph engine is **model-agnostic** (routers are plain Python
   functions returning `Event(route=...)`, no Gemini/LLM classifier) and a single-turn `LlmAgent` leaf
   is execution-optimized, so we lose nothing by skipping the template wrapper.
   → *When phases branch (Phase 6.5):* graduate the dynamic loop to a **static graph `Workflow`** —
   explicit nodes + code routers (`edges=[("START", worker, router), (router, {...})]`) + `JoinNode`
   for fan-in — only when the phase logic (research→plan→execute→verify) outgrows one loop. Same
   engine, more expressive shape; the worker and tools/skills carry over unchanged. ADK still flags
   stand-alone **Agent Routing as experimental** — keep routing in deterministic code routers.
   → *The one deliberate exception — the coding agent's inner loop is **embedded `mini-swe-agent`**
   (MIT, pinned `==2.2.x`), wrapped as a single node.* QA, triage, and team-lead stay pure ADK
   (single `LlmAgent` in a dynamic-workflow loop); only the coding agent diverges, because harness
   design swings coding success up to ~6× and mini is a battle-tested generate→edit→test→iterate loop
   (>74% SWE-bench Verified) from the SWE-bench team — we inherit the hardest-to-tune part instead of
   hand-building it. **The entire custom surface is one `ExecServiceEnvironment` adapter** (mini's
   `Environment` protocol → our exec service → the workload container) plus a thin wrapping node;
   mini's brain + model calls stay in the control plane and **only its bash `execute()` crosses into
   the workload**, so the trust boundary holds exactly. mini emits a **`git diff`, never a push**; the
   control-plane wrapping node applies the diff allow-list and opens the PR. Because mini runs *outside*
   ADK, the governance Plugin can't see its per-call traffic — its safety is **structural** (see
   Phase 9 and the standalone `mini_swe_agent_integration.md` for the full contract).

2. **ADK version = pin `google-adk~=2.1` now.** 2.0 went GA 2026-05-19; 2.1 followed 4 days later. The
   Workflow Runtime (graph engine) is the headline of 2.0 and where all current hardening is going; it
   is **model-agnostic**, and every piece of this stack — LiteLLM, McpToolset, SkillToolset, Plugins,
   DatabaseSessionService, OpenTelemetry, `adk eval`, `adk optimize` — is supported on 2.x. The
   Gemini-only restrictions (Live API voice, built-in `google_search`, Gemini code-exec) are all
   features this plan doesn't use. We build on the graph engine from day one (see decision 1), so the
   1.x template agents' deprecation never touches us, and there is **no migration later**. The pin
   `~=2.1` also blocks 3.0 (the likely removal point for the deprecated template agents) — a safety
   margin we don't actually need, since we never use them.
   → **Engine hygiene (these are graph-engine requirements, not style):** keep JSON-blob session
   storage (the Event schema gained `node_info`/`output`); **never override `_run_async_impl()`** (the
   graph engine ignores it — use callbacks/nodes); **never append events to the session directly and
   never call `enqueue_event`** — *yield* events from the node so the framework manages persistence/
   routing/streaming (appending circumvents the engine and breaks determinism); and **don't broadly
   `except Exception:` inside tools** — the engine now auto-catches exceptions to drive retries,
   telemetry, and HITL pauses, so swallowing them disables those signals.

3. **Product shape = self-hosted, repo-agnostic engine.** The agent team is a **generic
   engine**; everything repo-specific lives in config the user/repo carries, never in the
   engine's code. *Which* repos = the user's `repos.yml`; *how* each repo builds/tests/deploys
   = that repo's `.agentci.yml` manifest. Build it manifest-driven and keyed on `repo_id` from
   day one (Phase 9), so going multi-repo (Phase 10) is "the engine already reads config," not
   a rewrite. One user per deployment, their own repos — so `repo_id` namespacing is
   organizational hygiene, not a hostile-tenant security wall.

---

## Deployment topology (settled)

The whole system runs in Docker as a **compose stack** — control-plane container, a
per-run workload container, a Squid egress proxy, an observability container, plus the
CI/CD services (Jenkins, SonarQube, Trivy) added in Phase 9. Two boundaries serve two threats:

- **Control-plane container** (trusted): the agent harness(es), the graph-workflow runner + worker,
  governance Plugin, budgets, audit, capabilities loader, and the **model endpoint/secrets**.
  The only container that talks to the model.
- **Workload container** (untrusted, disposable, **per run**): where `run_shell` / `Execute`,
  the headless browser, and — critically for CI/CD — **the repo's own build/test code and the
  coding agent's generated patches** actually land. Runs `EnvironmentToolset(LocalEnvironment())`
  behind a thin **exec service**; the control plane reaches it via a
  `RemoteEnvironment(BaseEnvironment)` adapter over a private compose network.

Why two, not one: containerizing protects the *host* from the app; separating the workload
from the control plane protects the *agent's own guardrails* from the agent. If untrusted
code (a repo's build script, an LLM-generated patch) shared the control-plane container, it
could rewrite the governance Plugin, read the model endpoint/secrets/GitHub token, or delete
the audit log. Keep them apart. For CI/CD this is essential: each repo's build runs in a
fresh, disposable, egress-capped workload container.

Container-to-container exec uses an **exec service** (a narrow network API in the workload
container) — *not* a mounted Docker socket or Docker-in-Docker, both of which hand out
host-root / privilege.

**Model placement:** LM Studio runs on its **own host machine**, outside the compose stack.
The control-plane container reaches it **by IP** (`http://<lm-studio-ip>:1234/v1`), with
LM Studio bound to `0.0.0.0:1234`. Not `localhost`, not `host.docker.internal`. The workload
container has no route to the model at all. (The LiteLLM shim means swapping to a containerized
Ollama/vLLM later is a one-string change.)

---

# Part A — The agentic foundation (Phases 0–8)

These phases build the substrate the CI/CD team runs on. Each is shippable on its own; by the
end of Part A you have a hardened, observable, evaluatable autonomous-agent platform with a
remote console — and *then* Phase 9 turns it into the DevOps team.

## Phase 0 — Hello agent (Build: harness + model)
**Objective:** one ADK agent answering through LM Studio, locally, no sandbox, no autonomy.
This is the seed every team agent (QA, triage, coding) is later grown from.

**Steps**
1. LM Studio → Developer tab → Start Server (port 1234) → load a model.
2. `pip install "google-adk~=2.1" litellm` (pin to the 2.x line — see Architecture decisions).
3. Define the agent in a thin Python file (LiteLlm shim) + `instruction`.
4. Run `adk web` and chat with it in the browser.

**Bands:** Build (harness, model layer)
**Tools (free/OSS):** ADK (Apache-2.0), LiteLLM (MIT). Model: LM Studio (free).
**Low-code surface:** `adk web` UI; instruction as plain text.
**Done when:** the agent replies in `adk web`, served by your local LM Studio model.
**Note:** pin LiteLLM (avoid 1.82.7–1.82.8, a security advisory). Pick an LM Studio model with
strong function-calling (Qwen3 / Llama-3.x-class instruct) — the whole backbone leans on it.
**Networking:** reach LM Studio by IP (`api_base="http://<lm-studio-ip>:1234/v1"`), bound to
`0.0.0.0:1234`; only the control-plane container needs the route.

---

## Phase 1 — Tools + sandbox + autonomy (Build + Scale + basic Govern)
**Objective:** give an agent hands and let it run a goal to completion inside the isolated
workload container. For CI/CD this is the container the coding agent and every repo build
will execute in.

**Steps**
1. Drop in the `SandboxSession` (persistent workload container) + tools:
   `run_shell`, `write_file`, `read_file`, `copy_out`, `finish`.
2. Drive the worker (a single `LlmAgent`) with an **ADK dynamic-workflow loop** — a plain-Python
   iteration (`while step < budget and not finished: await ctx.run_node(worker)`) that terminates on
   `finish()`/escalate or the step budget. This is the **settled orchestration backbone** (decision 1):
   model-agnostic, deterministic, and the documented replacement for the deprecated `LoopAgent` — with
   automatic checkpointing/resume for free. No `sub_agents`/routing/Task-API — specialization comes
   from Skills (Phase 1.5). (Keep the `LlmAgent` in single-turn/task mode so it composes as a node.)
3. Add the `before_tool_callback` guard: step budget, time budget, denylist.
4. Add native tool generators where useful: `OpenAPIToolset` (any OpenAPI 3.x spec) and
   `McpToolset` (any MCP server) — both zero per-tool code.

**Bands:** Build (tools), Scale (sandbox), Govern (first guardrail)
**Tools (free/OSS):** Docker Engine (Apache-2.0); add gVisor `--runtime=runsc` for a harder
boundary. MCP servers (MIT). ADK `OpenAPIToolset`/`McpToolset` (built-in).
**Low-code surface:** OpenAPI spec file; MCP server URL; denylist as a Python tuple.
**Done when:** you give a goal and it autonomously works in the sandbox and saves an artifact,
stopping cleanly at `finish()` or the budget.
**Note:** when guards become the global governance Plugin (Phase 3), verify it fires under
`adk run` / the API server (known issue: Plugin callbacks don't always fire under
`InMemoryRunner`, ADK #4464).
**Execution design (two containers):** run code in a separate **workload container**, never in
the control-plane container — otherwise an agent that can `run_shell`/`write_file` could
rewrite its own guardrails. ADK's prebuilt **Environment Toolset** fits once the container is
the boundary: run `EnvironmentToolset(LocalEnvironment())` **inside the workload container**
(its "runs on the host" danger is neutralized because the host is now disposable and
egress-controlled), reached via a `RemoteEnvironment(BaseEnvironment)` adapter. ADK-standard
exec verbs *with* real isolation. Prefer an **exec service** over a mounted Docker socket /
Docker-in-Docker. Environment Toolset is experimental (≥1.29.0) — keep the isolation yours,
let the thin adapter track ADK.
**Gap:** local code execution — ADK's `BuiltInCodeExecutor` is Gemini-cloud-tied; the custom
workload container is the free local substitute.

---

## Phase 1.5 — MCP supply layer + Skills layer (Build; ties into Scale + Govern)
**Objective:** add many capabilities without bloating context or confusing the model about
which verb to use. This is where the team's *tools* (browser, GitHub, email, docs) and its
*know-how* (skills) live. Two altitudes of the Build band: **MCP = capability supply** (raw
verbs), **Skills = capability packaging** (how/when to use them).

**Mental model**
- *Supply layer* — MCP servers expose raw verbs (Playwright → `browser_navigate`,
  `browser_snapshot`, …; later GitHub → PR/issue verbs). ADK pulls them in via `McpToolset`,
  which auto-discovers and adapts them to native ADK tools. No per-tool code.
- *Packaging layer* — a Skill is a `SKILL.md` bundle (instructions + `references/` +
  `scripts/`) telling the model how to *orchestrate* those verbs. ADK loads them via
  `SkillToolset` and **progressively** (only name/description in context until triggered) —
  the fix for tool-bloat once an agent has browser + GitHub + filesystem verbs at once.
- Both are Python toolsets at the agent, so both are **model-agnostic and work with LM Studio**
  (unlike the Gemini-only YAML Agent Config / Visual Builder).
- *Skills are the alternative to routing.* The specialization a multi-agent design gets from
  routing to specialists, this design gets from loading the right SKILL.md into one worker —
  one trace, one governance surface, one budget. This is *why* each agent stays a single
  worker (one `LlmAgent` in a dynamic-workflow loop), and why the CI/CD "team" is single-purpose
  agents + skills, not a manager hierarchy.

**Steps**
1. Create a small `mcp_registry.py` (or a YAML the loader reads): one entry per MCP server,
   each wrapped in an `McpToolset` with a **`tool_filter` allowlist** (the filter is your first
   governance lever — the model only sees verbs you permit).
2. Add **Playwright MCP** (`npx -y @playwright/mcp@latest --headless --isolated`) via
   `StdioConnectionParams`; filter to read/navigate verbs to start. This becomes the **QA
   agent's** hands in Phase 9.
3. Author skills in `skills/`: `browser-ops/SKILL.md` (low-level: names the Playwright verbs,
   snapshot-driven loop, untrusted-content rule) and `web-research/SKILL.md` (high-level:
   orchestrates the browser). Load via `load_skill_from_dir` + `SkillToolset`. (Auto-discovery:
   any folder containing a `SKILL.md` is loaded — drop in a folder to add a skill, no code.)
4. Add **email send** as a supply entry: a free/OSS **SMTP MCP server** (provider-agnostic —
   `mailer-mcp`/`email-send-mcp`, MIT; Gmail SMTP / SendGrid / self-hosted relay via env).
   Filter to **`send_email` only** — no inbox read/delete; SMTP only (no IMAP). Pair with a
   short `email/SKILL.md` that says *when* to email — only on completion or when instructed.
   This becomes the team's **human-notification channel** in Phase 9.
5. Compose at the agent: `tools=[skills, playwright_mcp, email_mcp, *native_tools]`.

**Settled simplification — every agent integration is a stdio MCP server.** Rather than bespoke
clients per integration, *every* external system an agent touches is wired as a **stdio**
`McpToolset` registry entry (one uniform pattern: `command` + `args` + `env` + `tool_filter`).
Verified stdio servers exist for the whole surface, three of them first-party:
- **GitHub** → `github/github-mcp-server` (**official**; `github-mcp-server stdio` +
  `GITHUB_PERSONAL_ACCESS_TOKEN`). One server serves *two* agents: repos/branches/PRs (coding
  agent) **and** Actions/workflow-run + build-failure analysis + Dependabot alerts (triage agent).
- **Jenkins** → `jenkinsci/mcp-server-plugin` (**official**, MIT — but HTTP endpoint
  `/mcp-server/mcp`); for pure stdio use `hekmon8/jenkins-server-mcp` or `avisangle/jenkins-mcp-server`
  (build status / trigger / console logs).
- **Observability** → `grafana/mcp-grafana` (**official**, stdio): Loki logs, Tempo traces,
  Prometheus metrics — the triage agent's Phase-4 Plane-1/Plane-2 input. (Its health-check/metrics
  endpoints need SSE/HTTP, but the *tools* work over stdio, which is all triage needs.)
- **Browser** → Playwright MCP (above). **Email** → SMTP MCP (above).
Adding/swapping an integration is now editing a registry block, not writing a client.

**Placement rule (settled — drives WHERE each MCP server runs, even under "MCP everywhere"):**
an MCP server goes in the **workload container** if it runs open-ended model-chosen actions *and*
feeds untrusted content back to the model; it goes in the **control plane** if it's a narrow,
outbound action holding a secret. The test: *open-ended + untrusted-inbound → isolate; narrow +
holds-a-secret → protect the secret.* Applying it to the roster:
- **Control plane (trusted):** GitHub MCP (holds the PAT), Jenkins MCP (holds the Jenkins token),
  Grafana MCP (holds the Grafana token), email MCP (holds SMTP creds). All narrow + credentialed.
- **Workload container (untrusted):** Playwright MCP only (open-ended browsing, untrusted web
  content). So "hook an MCP server wherever there's an agent" is true — but the credentialed
  servers attach on the control-plane side where the agents' brains run, not in the sandbox.

**Two guardrails that survive the simplification (uniform wiring ≠ uniform privilege):**
- **`tool_filter` does more work than ever.** The official GitHub server exposes a *broad* verb
  set (repos, issues, PRs, workflows, releases, Dependabot). The coding agent sees only
  PR/branch verbs; the triage agent sees only workflow-read/issue verbs. A broad official server
  makes a *tighter* filter more important, not less.
- **Shared-PAT scope.** The GitHub MCP server authenticates with a single
  `GITHUB_PERSONAL_ACCESS_TOKEN`, so the *same* token backs the coding agent's writes and the
  triage agent's reads. Keep it **fine-grained** (`contents` + `pull-requests`, **no**
  workflow/secrets scope) — the scoping lives on the token since both agents share it (ties into
  Phase 9's minimal-PAT rule).

**Bands:** Build (supply + packaging). **Scale tie-in:** for the autonomous/CI target, flip
Playwright to run *inside the workload container* (official `mcr.microsoft.com/playwright`
image as a sidecar, `StreamableHTTPConnectionParams`). **Govern tie-in:** see Phase 3.
**Tools (free/OSS):** Playwright + Playwright MCP (Apache-2.0); SMTP MCP (MIT); ADK
`McpToolset`/`SkillToolset` (built-in, experimental — Skills need ADK Python ≥1.25.0). Skills
follow the open agentskills.io spec (portable `SKILL.md`).
**Low-code surface:** MCP server entry + `tool_filter` list; `SKILL.md` files.
**Done when:** an agent picks the right skill for a goal, drives the headless browser through
the filtered verbs, only the triggered skill's instructions load, and it can `send_email` a
summary.
**Email credentials + egress (ties into Phase 3):** the SMTP MCP runs in the control-plane
container, so the SMTP password lives in that container's SOPS-encrypted `.env` — the agent
calls `send_email` and never sees the credential. Email is egress, so the relay host must be on
the Squid allowlist.
**Caveat (LM Studio):** MCP + Skills lean hard on tool-calling/instruction-following. Pick a
Qwen/Llama-class instruct model known for tools or skill-triggering and browser/PR orchestration
will be flaky.
**Why now:** a browser is the highest-risk capability — heavyweight, stateful (Scale), and it
feeds untrusted web content back to the model (Govern). Adding it forces both layers together.

---

## Phase 2 — Persistence + memory + cache + compaction (Scale)
**Objective:** runs survive restarts; agents recall past context; repeated model calls are
cached. **All local — no cloud dependency.** For CI/CD this stores run history, per-repo state
(keyed by `repo_id`), and the document index if you add code/doc RAG.

**Steps**
1. Minimal start: `InMemorySessionService` → `DatabaseSessionService` with a **SQLite** URI
   (one line, zero infra).
2. Consolidated backbone (recommended): **Redis 8** (AGPL) or **Valkey** (BSD-3) as one local
   container covering **four boxes** — session store, cross-run memory, **vector search**, and
   a **semantic cache**. ADK ships a Redis tile (low-code), and it's the same broker Phase 7
   uses (no new infra). Prefer this over SQLite-then-pgvector-then-Mem0.
3. The **semantic cache** caches LLM responses by meaning, so repeated/similar prompts skip the
   model call — direct latency + load relief for LM Studio (your slowest component). **Mechanism
   is ADK-native, not custom:** a `before_model_callback` (or a Plugin hook) that returns a cached
   `LlmResponse` short-circuits the model call (ADK's documented "intervene" pattern). What we
   build is only the *store* (Redis keyed by prompt embedding) and the lookup — the skip-the-model
   hook is off-the-shelf.
4. Add semantic memory only when an agent must recall *across* runs; within a run, session state
   carries context. Redis covers it; **Mem0 OSS** / **Graphiti** (Apache-2.0) are alternatives.
5. **Context compaction (do this regardless — small local windows will bite you).** ADK has
   native compaction **plus a prebuilt Context Filter plugin** (reduces context size) — use these
   rather than hand-rolling a trimmer. A long agent loop grows its history until it overruns the
   model window (local models are often 8k–32k) and silently degrades. Not optional polish for a
   many-step local-model agent — and CI/CD loops (read logs → reason → patch → re-read) get long.

**Bands:** Scale (sessions, memory, cache)
**Tools (free/OSS, all local):** `DatabaseSessionService` + SQLite (minimal); **Redis 8 /
Valkey** as the consolidated backbone; Mem0 OSS / Graphiti as alternatives; pgvector if you
already run Postgres. **Note:** Phase 9 adds a **Postgres CI/CD metrics DB** — if you're running
Postgres for that, it's the natural single engine for vectors too (one instance, separate schemas),
so "Postgres for metrics" and "pgvector for memory" consolidate.
**Low-code surface:** a single DB/Redis URI; the prebuilt Redis tile.
**Done when:** kill the process mid-run, restart, resume the same session; a repeated prompt is
served from the semantic cache; a long multi-step run no longer overruns the context window.
**No-cloud note:** the first-class managed option (Vertex Memory Bank) is excluded by the
no-cloud rule anyway; Redis/Valkey + Mem0/Graphiti are the local substitutes.

---

## Phase 3 — Govern hardening (Govern)
**Objective:** make unattended agent runs safe to leave running. For CI/CD this is what makes
it safe for the coding agent to execute model-generated patches and reach GitHub.

**Steps**
1. Write **one ADK Plugin** (global, on the Runner) firing on every model/tool call:
   - **Presidio** (MIT) → PII detection/redaction.
   - **LLM Guard** (MIT) → prompt-injection + output scanning (CPU-only).
   - **NeMo Guardrails** (Apache-2.0) → policy in declarative Colang `.co` files.
   **Critical ordering caution:** Plugin callbacks run *before* agent-level callbacks, and if a
   Plugin callback returns anything other than `None`, the agent-level callback is **skipped**.
   Since this is one global governance Plugin, a hook that returns a value (e.g. to block/redact)
   silently bypasses any agent-level callback — intended for blocking, but know it so you don't
   lose an agent callback you expected to run.
2. Secrets: **SOPS + age** (MPL-2.0/BSD-3) encrypting `.env`, decrypted at container start
   (LM Studio needs no key; tools do — SMTP password, **GitHub token**). Upgrade to **OpenBao**
   for rotation.
3. Egress: declare it in compose — **workload container** on an `internal` network (no direct
   egress) behind a **Squid** allowlist proxy (GPLv2); `HTTP_PROXY` set in the workload. Control
   plane reaches only the model host, the SMTP relay, and (Phase 9) GitHub/Jenkins. Declarative
   compose YAML, not host plumbing.
4. **Browser content screening (Phase 1.5 tie-in):** a web page is *untrusted input that flows
   back to the model* — ADK names indirect prompt injection the top risk. Have the Plugin screen
   **Playwright's returned content** like user input, and bound the browser to the Squid
   allowlist. Keep `tool_filter` to read/navigate verbs unless a skill needs more.
5. Audit: the Plugin emits structured JSON on `after_*_callback` — this is also the CI/CD audit
   trail (who/what/when for every agent action on a repo). **Start from ADK's prebuilt Logging
   plugin** (logs at every workflow callback point) and extend it, rather than hand-rolling audit
   from zero.
6. **Resilience — add the prebuilt Reflect and Retry Plugin** (ADK, "automatically retry tool
   calls that fail"). Near-free uplift for a local model that emits malformed/recoverable tool
   calls. A Plugin, so it stacks on the Runner — verify it fires under your runtime (#4464).
   **Also native:** `on_model_error` and `on_tool_error` plugin callbacks catch model/tool
   exceptions and can return a fallback (suppress + recover) or `None` (re-raise) — use these as
   the graceful-degradation hooks rather than wrapping tools in bespoke try/except.

**Bands:** Govern (policy, secrets, egress, audit)
**Low-code surface:** Colang `.co` + YAML for NeMo; `squid.conf` allowlist; `.sops.yaml`.
**Done when:** a probe injection is blocked (including one planted in a fetched web page), PII
is redacted, the sandbox+browser reach only allowlisted hosts, and every action lands in audit.
**Avoid (not OSS):** Model Armor, Lakera Guard, Llama Guard/ShieldGemma (custom licenses).

---

## Phase 4 — Observe: two planes (Optimize, part 1)
**Objective:** see every decision, tool call, and step — across **two distinct observability
planes** that answer different questions for different audiences. Conflating them is a mistake;
they share infrastructure but stay logically separate, and they meet at exactly one seam (the
triage agent).

**Plane 1 — Agent observability (debug the agents).** OpenTelemetry traces of the *agents' own
behavior*: agent → tool → model spans, which skill fired, steps, tokens, retries, where the LLM
decided. **Audience:** you, the operator/developer of the agent team. **Answers:** "why did the
coding agent do that / why did triage misclassify / is QA burning its budget?" This is the plane
the Phase 5 eval harness mines for metrics.

**Plane 2 — Application/pipeline observability (monitor the software under management).** The
health of what the pipeline *produces and acts on*: the CI/CD pipeline itself (which stage
failed, durations, pass rates) and the **deployed apps** (prod error rate, latency — the Phase 9
post-deploy "smoke checks + monitor"). **Audience:** whoever owns the managed repos (in the
self-hosted model, the user). **Answers:** "is my app healthy in prod / did this deploy regress /
which stage is flaky?" **Note the metric/event split:** Plane 2 holds the *metrics* ("error rate
up"); the actual *error events + stack traces* the triage agent needs live in **Loki**, shipped by
the deploy harness (see Phase 9 "managed-app log capture"). A rising metric *alerts*; the Loki log
event is what gets *diagnosed*.

**Why separate:** different audiences, lifetimes, and trust scope. A user of the OSS CI/CD team
cares about Plane 2 (their app) constantly and Plane 1 only when the *team itself* misbehaves.
Mixing "the coding agent's token count" with "my app's prod error rate" in one view serves
neither. Keep them as separate scopes/dashboards.

**The seam (one handoff):** Plane 2 *detects* a problem (a prod alert, a failed stage); the
**triage agent reads Plane 1 (+ pipeline logs) to diagnose it**, then files the standardized
issue and decides if the self-heal loop engages. So:
`Plane 2 (app/pipeline health) ──alert──► triage agent ──reads──► Plane 1 (agent traces) ──► issue + (fixable?) self-heal`.

**Command center = three views, three audiences — ASSEMBLED from existing tools, NOT custom-built.**
"Watch the agents interact / watch a pipeline run" is real, but it's three different views for
three audiences, each already covered — do **not** build a bespoke dashboard (you'd reinvent live
agent graphs + trace waterfalls and own the maintenance forever; your agents already emit OTel
spans, so the command center is a *deployment choice*, not a build):
- **Dev-debug, one agent** → **ADK web UI** (`adk web`, built-in): live agent execution graph,
  invocation chains, the Trace/Graph tabs, event replay, interactive inputs. This is your
  develop-and-debug-one-agent surface, free, in the box. (Not always-on or multi-agent.)
- **Operator, the whole team live** → a **self-hosted OTel dashboard** — this is the genuine gap,
  and it's just a *richer backend pointed at the Plane-1 spans you already emit*. **Default:
  Arize Phoenix** (source-available ELv2, free self-host, OpenInference/OTel-native, renders
  **agent workflow maps** + trace waterfalls — lightest fit for the simplicity bar). Heavier
  alternatives: **Langfuse** (MIT core, richer — but self-host needs 5+ services incl.
  ClickHouse/Postgres/Redis) or **SigNoz** (one tool for agent traces *and* infra metrics, could
  also cover Plane 2). All ingest your existing OTLP — zero custom code.
- **Director (you, non-technical)** → the **team-lead agent + metrics DB** (Phase 9). You don't
  read waterfalls; you ask "what's the team doing?" and it queries the metrics DB.
**The honest seam on "watch a pipeline running":** agent *interactions* live in the OTel dashboard
(Phoenix), pipeline *stage flow* (lint→build→deploy) lives in **Jenkins' own UI**, and *app/pipeline
health* lives in **Grafana** (Plane 2). So "watch the pipeline" spans three panes (Jenkins +
Phoenix + Grafana), each best-in-class at its slice. A **single unified pane** stitching all three
is a **deferred, demand-driven** nicety — the one place a thin custom dashboard *could* later be
justified — but resist building it until the three-pane reality actually annoys you.

**Steps**
1. Run an OTel backend in Docker — **Jaeger** (Apache-2.0, leanest) for plain tracing, or
   **Phoenix** (ELv2) when you want the **operational command-center view** (agent workflow maps).
   One backend serves *both* planes; split by **scope/tags**, not two stacks. Phoenix is the
   recommended default once you want to *watch the team*, not just store traces.
2. Plane 1: set `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` and `OTEL_SERVICE_NAME`. ADK emits
   OpenTelemetry GenAI spans natively — no code. (The same spans feed both Phoenix and the eval
   harness; ADK web UI reads the live run directly.)
3. Plane 2: the Phase 9 pipeline stages and the post-deploy monitor emit pipeline/app metrics
   through the **same OTel collector**, tagged as a separate scope (e.g. `service=pipeline` /
   per-`repo_id`) so they render in their own view (Grafana/Prometheus for classic dashboards).

**Bands:** Optimize (observability)
**Tools (free/OSS):** Jaeger (Apache-2.0); **Phoenix (ELv2) — the operational command center**;
Langfuse (MIT core) / SigNoz as richer-but-heavier alternatives; all via OTLP. ADK web UI
(built-in) for dev-debug. Prometheus + Grafana for Plane-2 metrics dashboards.
**Low-code surface:** env vars (Plane 1); scope tags + the post-deploy monitor config (Plane 2);
the command center is a compose service, not code.
**Done when:** a full agent run renders as a trace tree (agent → tool → model) in the Plane-1
view, *and* a deploy's pipeline-stage + prod-health metrics render in a separate Plane-2 view —
and a Plane-2 alert can hand off to the triage agent.

---

## Phase 5 — Evaluate + benchmark + simulate (Optimize, part 2)
**Objective:** stop regressions *and* measure which configuration is best — by model, prompt,
and harness — with numbers, not vibes. For CI/CD: benchmark each **team agent** (does the
triage agent classify correctly? does the coding agent's fix rate beat a baseline?) and prove a
model/prompt change doesn't regress the team.

**Steps**
1. **Task suite** — an ADK evalset (`*.evalset.json`, via `adk eval` / `pytest`): fixed goals
   with known-good outcomes (e.g. seeded failing builds with a known correct fix).
2. **Environment Simulation** — mock tool responses so runs are deterministic, fast, and free.
   You measure the model + orchestration, not the flaky real web/shell/GitHub, and can run
   hundreds of trials locally and reproducibly. (User Simulation too, for multi-turn cases.)
3. **Metrics** (free/local where possible): `tool_trajectory_avg_score` (right tools/order —
   *the* local-model metric), `response_match_score` (ROUGE); **task success** via a local LLM
   judge — **DeepEval** (Apache-2.0) / **Promptfoo** (MIT, YAML) pointed at LM Studio; plus from
   Phase 4 traces: **steps, latency, tokens, retry count**.
4. **Variance, not point estimates** — local models are stochastic. Run each task **N times
   (3–5)**, report **mean + spread**. Skipping this is the most common local-model benchmarking
   mistake.
5. **Three-axis sweep** (the custom piece — small code). Sweep **one axis at a time**, holding
   the other two fixed, against the same suite — never the full cross-product:
   - **Model** — swap the LM Studio model behind the same `LiteLlm` string. Uniquely cheap for
     you; directly tests the architecture's core bet (is local tool-calling good enough?).
   - **Prompt** — agent + skill instructions (connects to Phase 6).
   - **Harness** — step/time budget, retry policy, skills enabled, **thinking on/off + budget
     (`PlanReActPlanner`)**, later loop-vs-graph. The axis you most control, so the most actionable.
     The thinking sweep answers whether reasoning actually lifts **triage** classification accuracy
     / **coding** fix-success enough to justify its added latency (the policy enables it for those
     two — see Phase 9); measure, don't assume.
6. **Report** — comparison table (mean ± spread per metric per config); persist via the
   **ADK Artifacts service** as a running leaderboard (versioned automatically); wire into CI to
   fail on regressions. **Caveat (see the Phase 9 Artifacts note):** the only *persistent* built-in
   artifact service is GCS (cloud — excluded), so this rides on a **custom local
   `BaseArtifactService`**.

**Coding-agent eval bonus (free, standardized) — mini-swe-agent *is* the SWE-bench harness.** Since
the coding agent embeds **mini-swe-agent** (Phase 9 / Architecture decision 1), and mini ships batch
SWE-bench evaluation, point its **batch eval at LM Studio** to measure your loaded model's fix-success
on **SWE-bench Verified** directly — a credible, comparable coding number with **zero extra harness to
build**. Pin the mini version + a SWE-bench split so numbers stay comparable across runs; this is the
coding agent's analogue to the triage classification accuracy metric.

**Bands:** Optimize (evaluation, benchmarking, simulation)
**Tools (free/OSS, all local):** ADK eval + Environment/User Simulation; DeepEval / Promptfoo /
Ragas with an **LM Studio judge**; **mini-swe-agent's batch SWE-bench eval** for the coding agent;
Phase 4 OTel traces as the metric source.
**Low-code surface:** JSON evalsets; Promptfoo YAML. **Custom:** the sweep/aggregation harness.
**Done when:** you can answer "model A vs B / prompt v1 vs v2 / budget 40 vs 80" with mean ±
variance from a deterministic simulated suite, and a regression run fails CI on a quality drop.
**Also discovers the Phase-7 concurrency cap:** run the suite at increasing parallelism to find
"how many concurrent agent runs before per-run latency degrades" — that number sizes the global
in-flight cap (which must track *model* capacity, not container count).
**Gap:** ADK's richest judge criteria (rubric/hallucination/safety v2) need paid Vertex Eval API
— DeepEval/Promptfoo with an LM Studio judge replace them for free.

---

## Phase 6 — Optimize / self-improve (advanced)
**Objective:** the system tunes its own agent prompts against the evalset.

**Steps**
1. Try native `adk optimize` (GEPA). Optimizer model defaults to Gemini — attempt to point it
   at LM Studio via LiteLLM; if unsupported in your version, use:
2. **DSPy** (MIT) offline with LM Studio as the LM, compile with `MIPROv2`/`GEPA`, then paste
   optimized prompts back into the agent.

**Bands:** Optimize (optimization)
**Low-code surface:** `adk optimize` CLI + a JSON sampler config; DSPy is light code.
**Done when:** the Phase 5 evalset score measurably improves after an optimization pass.

---

## Phase 6.5 — Static graph workflow (advanced; only when a loop outgrows itself)
**Objective:** graduate an agent from the **dynamic-workflow loop** (the day-one backbone — a Python
iteration over one `LlmAgent` worker, see decision 1 & Phase 1) to a **static graph `Workflow`** once
its logic is too tangled for one loop — i.e. it needs explicit *branching* between distinct phases,
parallel sub-investigations (fan-out/fan-in), or a debuggable phase graph. (A likely candidate: the
**triage** agent, if its diagnose → gather-evidence → classify path ever needs parallel
sub-investigations. **Note:** the coding agent's read → plan → patch → test → revise cycle is *not* a
candidate — that loop is supplied by **embedded mini-swe-agent** (Architecture decision 1), not an ADK
dynamic loop, so it is never "graduated" to an ADK static graph; mini owns that shape.)

**Why its own phase (not earlier):** we already build on the graph engine from Phase 1 — so this is
**not a migration off a deprecated primitive** (we never used `LoopAgent`) and **not an engine swap**.
It's promoting one *shape* of graph workflow (a dynamic Python loop) to another *shape* (a static
node/edge graph) **within the same Workflow Runtime**, when branching makes the static form clearer.
The dynamic loop already gives checkpoint/resume; the static graph adds explicit nodes/edges,
conditional routing via **plain Python router functions**, `JoinNode` fan-in, and per-node
`RetryConfig` — the same deterministic-skeleton-with-LLM-inside shape, just more legible when phases
branch. Stay on the dynamic loop while the logic is still loop-shaped; many agents never need this.

**Steps**
1. Lift the loop into a static graph: each phase a node; the `LlmAgent` worker and all tools/skills
   carry over unchanged (a refactor of the *shape*, not a rewrite). Wire with
   `Workflow(edges=[("START", worker, router), (router, {"ROUTE": next_node, ...})])`.
2. Express transitions as **code routers** (`def router(node_input) -> Event(route=...)`), not
   stand-alone **Agent Routing** (experimental) and not `transfer_to_agent`/Task-API delegation.
3. Add `RetryConfig` per node; add a `JoinNode` only for provably breadth-first work.
4. Use the Web UI **Graph View** to debug visually.
5. **Native model-free HITL.** Graph workflows can include human-in-the-loop nodes via
   `RequestInput(message=..., payload=..., response_schema=...)` (ADK 2.x), which pause the graph for a
   human and **require no model** — matching the "no LLM in control flow" principle. This is the right
   tool for a human checkpoint *internal to an agent's reasoning* (e.g. "I've drafted a risky migration
   — confirm before I continue"). **It is NOT for the Phase-9 pipeline gates** (those stay in
   Jenkins/async — see the Phase-9 HITL-placement note): a `RequestInput` pause holds the agent run
   (model slot + workload container) open while waiting, which the single-LM-Studio concurrency cap
   can't afford. Pairs with **Resume Agents** (checkpoint/resume).

**Bands:** Build (orchestration), with Scale (resume) + Govern (per-node retry/HITL) benefits.
**Low-code surface:** node/edge wiring + Python router functions; `RetryConfig`.
**Done when:** the workflow runs as a static graph with conditional transitions, the governance Plugin
still fires on every node, and a long run can checkpoint/resume.
**Avoid:** stand-alone Agent Routing and LLM-classifier routing until Google drops the experimental
label.
**Optional (durability, demand-driven):** if long runs die mid-task and lose work, **Dapr**
(CNCF, self-hosted sidecar, fully local) adds durable, crash-recoverable execution. Add only if
you hit this; don't take the dependency speculatively. (Temporal/Restate/DBOS are open-core with
paid cloud — prefer Dapr for no-cloud.)

---

## Phase 7 — Scale-out + kill-switch (Scale/Govern)
**Objective:** many concurrent agent runs, with a brake and a sane concurrency model. For CI/CD:
run pipelines for several repos at once (parallel across repos, serialized within a repo), bounded
by the model's real capacity, and halt a runaway agent automatically.

**Steps**
1. Task queue + workers: **Redis 8** (AGPL) or **Valkey** (BSD-3) as broker; a pool of workload
   containers; ADK `Sequential`/`Parallel`/`Loop` workflow agents for fan-out.
2. Multi-instance: expose/consume agents over **A2A** (Apache-2.0) with Agent Cards; register
   tools via the **MCP Registry** (open source, self-hostable).
3. Anomaly + kill-switch: **Falco** (Apache-2.0) for syscall anomalies → Falcosidekick webhook
   → `docker stop`; plus a small sidecar consuming OTel metrics to trip on cost/step-spam
   thresholds. This is the brake the self-heal loop relies on (Phase 9).

**Bands:** Scale (task queue), Govern (anomaly/kill-switch)
**Low-code surface:** A2A Agent Card YAML; Falco rules YAML.
**Done when:** several pipelines run concurrently across different repos; a second run on the
*same* repo queues behind the first (and a same-branch re-push aborts-and-replaces); runs beyond
the global cap queue in the broker; and a threshold breach halts a run.
**Gap:** application-level anomaly detection has no polished OSS product — the watchdog sidecar
is custom code (the last box a paid platform fills for you).

**Concurrency model (settled — state it explicitly; it's currently only implied).** An "agent" is
a **definition** (an `LlmAgent` worker in a dynamic-workflow loop + a skill + a model config), **not a
long-lived worker/singleton**.
Each pipeline run **instantiates** the agent it needs against its **own** disposable workload
container, with its own session/trace/budget. So "can the QA/coding agent work on multiple repos?"
→ yes: repo A's run and repo B's run are *separate instantiations* of the same definition that
don't share state and don't block each other. There is no single "the QA agent" to occupy.
**Admission of a new task is a two-layer check:**
1. **Per-repo serialization (correctness).** If the **same repo** already has a run in flight, the
   new run **queues** behind it — two runs on one repo would collide on the same staging env,
   deploy target, image tag, and branch state (QA couldn't tell which deploy it's testing).
   Implemented with Jenkins per-pipeline concurrency control + a `lock()` on the staging/deploy
   stage. **Refinement:** a newer commit on the **same branch** as the in-flight run **aborts and
   replaces** it (`disableConcurrentBuilds(abortPrevious: true)`) — testing a superseded commit is
   wasted work; only the latest commit matters. So: queue distinct same-repo runs, abort superseded
   same-branch commits.
2. **Global in-flight cap (LLM-resource backpressure).** Every agent instance — any repo, any
   container — calls the **same single LM Studio endpoint** (one model, one GPU), which
   **serializes inference**. So the binding throughput constraint is the *model*, not the container
   pool: 10 busy containers don't give 10× if they're all waiting at one GPU. Enforce a **global cap
   on concurrent agent runs**, sized to LM Studio's *real* capacity (often effectively ~1–2
   concurrent model conversations for a single local model); runs beyond it **queue in the Redis
   broker**. **Critical:** size the cap to *measured model capacity*, NOT to container count — set it
   to 10 because you have 10 containers and you just move the bottleneck inside the model and make
   every run slow instead of queuing cleanly. Discover the right number with the **Phase 5 benchmark**
   ("how many concurrent runs before per-run latency degrades") — and benchmark with **thinking
   enabled on triage + coding** (per the Phase-9 thinking policy), since reasoning lengthens those
   runs and eats model capacity; a cap measured without it will be over-optimistic.
Both layers feed the same broker queue; they're just different admission checks (collision vs.
saturation). **Raise the ceiling by scaling the model** — a second LM Studio instance, or vLLM with
batching — which the LiteLLM shim makes a config change, *not* by adding more workload containers.

---

## Phase 8 — Control surface + remote console (Build/Govern)
**Objective:** drive and watch the system from anywhere — for CI/CD, this is the **operator
console**: see pipeline runs live, get the QA/triage reports, and **give the human-approval for
prod deploy** and escalations from your phone. The app is a thin client; the work is exposing
the control plane as an authenticated service.

**Steps**
1. Expose the control plane: turn on **`adk api_server`** (the Runner wrapped in FastAPI — REST
   for create/run, **SSE** for live events). Bind it **privately** in the control-plane
   container, never public `0.0.0.0`.
2. Access — **Tailscale**: join the control-plane container/host to your **tailnet** and publish
   the API with `tailscale serve` (private to the tailnet) — **never `funnel`** (that exposes it
   publicly). No open port, no public DNS, no TLS cert; not portscan-discoverable.
3. Scope with **Tailscale ACLs**: only your enrolled devices reach the API. Keep a lightweight
   **app-level token** too — Tailscale authenticates the *path*, the token authenticates the
   *app*; defense in depth.
4. Client contract: **submit/trigger**, **watch** (SSE — same data as Phase 4 traces),
   **approve/stop**, **results** (reports + artifacts). For CI/CD the key verbs are *watch a
   run*, *approve a prod deploy*, *review a QA/triage report*, *stop a run*.
5. Wire **stop/approve** to halt or gate server-side via ADK's native **Cancel** runtime feature
   — set escalate/cancel on the active session and tear down the workload container if needed.
   Closing the app's stream must NOT leave an agent running. Build this first. (ADK **Resume** is
   the companion — a stopped/crashed run can be resumed.)
6. Build the client as a standard REST + SSE app. The **email send** toolset (Phase 1.5) doubles
   as the push channel — the team emails you the QA report / an escalation instead of requiring
   an open stream. **(Phase 9 forward-link:** the **team-lead agent** becomes the *conversational*
   front-end of this console — the app evolves from a raw run-viewer into "chat with the lead,"
   which reads this same api_server state and relays your approvals. The console plumbing here is
   what the lead agent later sits on top of.)

**Bands:** Build (API surface), Govern (network access, remote kill-switch/approval)
**Low-code surface:** `adk api_server` (built-in); Tailscale `serve` + ACLs (no nginx, no certs);
the app is conventional client code.
**Done when:** from your phone on the tailnet you watch a pipeline run, approve a prod deploy,
review a report, and stop a run (and it actually halts server-side).
**Security note (non-negotiable):** Tailscale removes the public attack surface and handles
device identity + encryption — but it authenticates the *path*, not intent: keep the app token
and the **server-side kill-switch/approval**. Use `serve`, never `funnel`.
**Why here:** it adds an attack surface on the trusted control plane, so it goes on after
governance (Phase 3) and the kill-switch (Phase 7) are real.

---

# Part B — The CI/CD agent team (Phases 9–10)

Everything above was substrate. These two phases are the product.

## Phase 9 — Self-healing CI/CD agent team (Build/Govern; consumes Phases 1–8)
**Objective:** a DevOps pipeline where deterministic CI/CD stages are gated by a few
single-purpose agents, with a self-healing loop that *proposes* fixes for failures — human in
the loop only at the irreversible and the ambiguous. Built single-repo first, but
**manifest-driven and `repo_id`-keyed from the start** so Phase 10 is config, not a rewrite.

**The design lens (how to decide what becomes an agent):** classify every SDLC step by *who
does it today*, then apply the simplest replacement.
- **Already automated / deterministic → leave it automated.** Lint, tests, Sonar, Trivy, build,
  deploy never needed a human and don't need an agent. They stay plain Jenkins stages; the only
  work is ordering and adding cheap deterministic gates (below). **No agent goes here.**
- **A human does it today *and it needs judgment* → replace with an agent.** Triage a failure,
  write a fix, judge "does it work", decide if a test is flaky, draft a postmortem. This is the
  agent's actual mandate — replace the *judgment* a human SDLC role performs.
- **A human does it today *as a reflex, not a judgment* → replace with a deterministic control,
  not an agent.** Rolling back a bad prod deploy is an SRE reflex (mitigate first, understand
  later); dependency bumps are a mechanical chore. The right replacement is auto-rollback and
  Renovate — an LLM here would be slower and riskier. *The lens is "replace the human with the
  simplest thing that does their job," which is sometimes an agent and sometimes a script.*
- **A human does it today *as the accountable, irreversible call* → the human stays.** Approving
  prod, merging the agent's own code. These get *more* structural protection, not replacement.

**The guardrail corollary (the cost of every replacement):** the human you replace was *also a
guardrail*. A senior reviewer both writes the change and refuses to game the system to ship it.
When an agent takes the writing job, the refusing job doesn't vanish — it must become an
explicit structural control. **So every human→agent replacement pairs with the guardrail that
the human's judgment used to supply implicitly.** This single rule generates most of the
hardening below (diff allow-list, "new test must fail on parent", branch protection).

**The governing principle (the backbone, applied at pipeline scale):** deterministic skeleton,
bounded LLM inside. **Jenkins is the orchestrator** — stage sequencing, gates, retries,
pass/fail edges, zero LLM judgment. **Agents are steps Jenkins invokes** where judgment lives.
Do **not** put an LLM in charge of pipeline control flow; that reintroduces the LLM-driven
orchestration the backbone rejects. Jenkins = team lead that assigns work; agents = specialists
who return a written report. **Why Jenkins, not an agent-orchestrator:** sequencing a CI/CD DAG
has no judgment in it (an LLM can only misjudge a clear exit code), and the coordinator must be
*more* reliable than the fallible agents it coordinates.

**Most steps are NOT agents.** Lint, SonarQube, dependency install, unit tests, Docker build,
image scan, deploy — deterministic, pass/fail on exit codes. Keep them as Jenkins stages.
Agents enter only where judgment lives.

**Four agents (each a single worker + a skill — NOT a manager/worker hierarchy; each reaches its
integrations via stdio MCP servers). Three (QA, triage, team-lead) are a single `LlmAgent` in a
dynamic-workflow loop; the coding agent is the one exception — its inner loop is embedded
mini-swe-agent (Architecture decision 1).** Three do the work;
one is your interface:
- **QA agent** — drives Playwright (Phase 1.5, workload container) against the staging deploy,
  emits a structured pass/fail JSON report. Skill: `qa-automation` (built on `browser-ops`).
- **Triage agent** — reads failure logs/traces via the **Grafana MCP** (Loki/Tempo/Prometheus,
  Phase 4) and build logs via the **GitHub/Jenkins MCP**, classifies: flaky / infra /
  real-fixable-code-bug. Its *only write* tool is `create_issue` (structured). Skill:
  `failure-triage`.
- **Coding agent** — takes a fixable bug, proposes a patch. **The one non-ADK agent: its inner loop
  is embedded `mini-swe-agent`** (MIT, pinned `==2.2.x`), wrapped as a single ADK node — see
  Architecture decision 1 and the standalone `mini_swe_agent_integration.md`. mini's bash-only
  generate→edit→test loop is a strong fit for a weak local model (it never relies on structured
  function-calling). **Execution runs in the untrusted workload container** (it executes
  model-generated code — the highest-risk action in the system), but **mini's brain + model calls
  stay in the control plane; only its bash `execute()` crosses** the boundary via the one custom
  piece we build — an **`ExecServiceEnvironment`** adapter (mini's `Environment` protocol → the exec
  service → workload; **not** mini's `DockerEnvironment`, which would need a Docker socket). mini
  **emits a `git diff`, never a push, and the GitHub token never enters the workload**: the
  control-plane **wrapping node** extracts the diff, applies the diff allow-list (below), and only
  then opens the `agent-fix` branch + PR via the **GitHub MCP** filtered to PR/branch verbs. Bound one
  fix attempt with mini's **`step_limit`** (not `cost_limit` — local cost ≈ 0); the per-issue attempt
  budget (~2) is the separate circuit-breaker layer. Because mini runs *outside* ADK the governance
  Plugin can't see its calls — its safety is **structural**: per-command screening at the exec service
  (denylist + Squid egress), the diff allow-list on its output, full-pipeline re-entry + human merge,
  and **screening the task input for injection before invoking mini** (the one Plugin job that
  relocates here). Capture mini's linear **trajectory JSON** → the artifact store + an OTel span.
  Skill: `code-fix` (= mini's `system_template` + `instance_template`: fix, run tests, meet ≥80%
  coverage, submit `git diff`).
  **Pre-push self-gate (required):** before opening the PR, the agent runs the repo's **unit tests
  + coverage** in its workload container (using the test command the build planner resolved, so it
  matches CI) and **must not push unless all unit tests pass AND project coverage stays ≥ 80%**. If
  it can't clear that within its attempt budget, it **escalates instead of pushing** a broken or
  coverage-degrading fix. This is a *pre-flight* check (fail-fast, saves a wasted PR + pipeline run
  and tightens the fix loop) — **not** the authoritative gate: the same tests + a deterministic
  coverage gate (`--cov-fail-under=80` / Sonar) re-run when the PR enters the pipeline, and *that*
  is the source of truth (a weak local model's self-reported "tests pass" is never trusted; it is
  re-verified). **Coverage gate is project-level ≥ 80% (no regression).** Because the agent **may
  ADD new tests** (append-only — see the diff allow-list below; it may never modify or delete
  existing tests), it can write tests to cover its fix and meet the bar — escalating only if it
  genuinely can't produce adequate tests. The "new test must fail on parent" gate already forces the
  fix itself to be covered by a real regression test, so patch-level coverage of the changed lines
  follows from that gate; the 80% project floor guards the suite as a whole.
- **Team-lead agent — your single conversational surface. A SPOKESPERSON, NOT a manager.** As the
  director you talk to *only* this agent: "what's the team doing?", "how's it performing?", "what
  needs my approval?". Critical distinction that keeps invariant #1 intact: **coordinating the
  work stays with Jenkins** (deterministic — sequences stages, dispatches the other agents);
  the lead only **communicates about** the work (reads state, narrates it, relays your
  approvals). It sits *beside* the team as your interface, never *above* it as their boss. Skill:
  `team-lead`. Specifics:
  - **Reads, doesn't orchestrate.** Its tools are *read* surfaces — the **CI/CD metrics DB via
    parameterized query tools** (`get_pending_approvals`, `repo_performance`, `incident_ranking`,
    … — see the metrics-datastore note below), plus Plane 1 (agent traces) and Plane 2
    (pipeline/app health) for drill-down — and exactly **one write path: relaying your decision
    into a waiting gate**. It **never calls** the QA/triage/coding agents; it reads what they
    produced. No agent-to-agent routing — an agent reading a store, not a coordinator wrapping
    workers.
  - **Relays approvals, never holds authority.** The pipeline pauses at the deterministic human
    gate (Jenkins `input` / Phase-8 Cancel-backed approval). The lead tells you it's waiting and
    shows the QA report; you decide; the lead **records your authenticated decision into the
    gate**. The gate is the control; the approval is cryptographically *yours*. Audit logs show
    *you* approved (via the lead), never "the lead approved." The lead is the messenger carrying
    your signed note, not the signatory.
  - **Pull-only (no autonomous trigger).** It runs *only* when you initiate a query or an
    approval — no background loop, no proactive pings. This is what keeps it from drifting into
    "deciding things": an agent that only wakes on your turn can't quietly orchestrate between
    turns. The **deterministic notifier** (email MCP, triggered by Jenkins) does the *pushing*,
    **framed in the lead's voice** so it feels like one assistant; tapping a ping drops you into a
    pull conversation with the lead. Push notifier (always-fires, no LLM) and pull spokesperson
    (converses, can't self-trigger) are separate components — that separation is the safety.
  - **Narrates fetched facts, doesn't compute them.** A local model can misread traces, so the
    lead's numbers come from *deterministic queries* (eval metrics, pipeline pass-rate) and its
    summaries **link to the underlying artifact** so you can drill to ground truth. It reports
    facts it fetched; it doesn't eyeball-estimate.
  - **Never accrues authority (the standing rule).** It relays human decisions and reads state —
    it never *originates* an approval, dispatches an agent, or modifies the pipeline. The moment
    something would be "decided," that's a human gate or a deterministic rule, not the lead. Do
    not let "just auto-approve the low-risk ones" creep in — that's the slide back to a manager.
  - **This is the conversational front-end of the Phase 8 console** (it reads the same state the
    api_server exposes). The Android app becomes "chat with the lead" rather than a raw run viewer.
- The old "manager that reports to a human" is still **not an agent** — event *pushes* are the
  deterministic notification step (email MCP). The lead is the *pull* interface, distinct from it.
  The working agents hand back **artifacts** (reports, a PR); they don't converse or decide who
  acts — Jenkins decides, by stage. (No-routing rule, preserved.)

**Thinking / extended-reasoning policy (per agent — think where judgment lives, not where it's
execution or lookup).** Reasoning helps a weak local model, but it costs tokens + inference time,
and the **single LM Studio endpoint serializes inference** — so a thinking run holds its model slot
longer and eats into the Phase-7 concurrency cap. Enable it *selectively*:
- **Triage → YES.** Diagnosis/classification *is* reasoning; a misclassification is expensive (flaky
  routed to coding, or a fixable bug escalated), and triage is **off the hot path** (only on
  failures), so the latency cost is affordable. Strongest candidate.
- **Coding → YES.** Fix generation is the most reasoning-heavy generative task; a wrong fix costs a
  **full pipeline cycle**, far more than the thinking tokens. Also off the hot path (self-heal only).
  **Mechanism differs:** the coding agent's reasoning comes from **mini-swe-agent's own prompt-driven
  loop** (optionally a reasoning model loaded in LM Studio — QwQ / Qwen3-thinking / DeepSeek-R1-distill),
  **not** `PlanReActPlanner` (which is for the ADK agents — triage). It's the same "thinking ON"
  intent, supplied by mini's harness rather than an ADK planner.
- **QA → NO.** Execution-bound (browser driving), not reasoning-bound, and already slow — thinking
  adds latency for little judgment gain.
- **Team-lead → NO.** Deliberately low-reasoning by design (parameterized queries, not NL2SQL) and
  it's the **interactive** surface — it must answer fast, not pause to "think."

**Mechanism (LM Studio, not Gemini):** for the **ADK agents (triage)**, use ADK's
**`PlanReActPlanner`** (prompt-driven plan→act→reason; model-agnostic — the docs recommend it
precisely for "models without a built-in thinking feature"); the **coding agent gets its reasoning
from mini's own loop instead** (above), so `PlanReActPlanner` does not apply to it. **Never
`BuiltInPlanner` / `ThinkingConfig`** — those target Gemini's
thinking-tokens API and silently do nothing on a local model. Alternatively, if a *reasoning* model
is loaded in LM Studio (QwQ / Qwen3-thinking / DeepSeek-R1-distill), it thinks natively — then the
policy inverts to **suppressing** thinking for QA + lead. Either way **bound the reasoning length**
(a thinking budget) so it can't run away on the slow local model — it ties into the step/time guard.
Note: `PlanReActPlanner`'s structured plan/reason output is *separate* from the final answer, so
triage's constrained-JSON output lives in the FINAL_ANSWER section, with reasoning in PLANNING/REASONING.
**Eval-gated:** whether thinking actually lifts triage accuracy / coding fix-success enough to pay
for the latency is a Phase-5 sweep axis (prompt/harness) — measure it, and size the Phase-7 cap with
thinking **enabled** on triage + coding (see Phases 5 and 7).

**CI/CD metrics datastore (the structured record the lead queries — a real missing primitive).**
Observability (Phase 4) holds *traces* (Jaeger/Phoenix, ephemeral, span-shaped) and *time-series*
(Prometheus, "error rate now") — neither answers "which repo has the most incidents this quarter?"
or "how is repo A performing?", which are **relational aggregations over historical outcomes**
(`GROUP BY repo`, DORA rollups). So add a dedicated **metrics DB: Postgres** — the **same instance**
Phase 2 reaches for when SQLite/Redis is outgrown (one engine, separate schema; pairs with pgvector
if vectors move there too). It is the **DORA-metrics home** the best-practice review and Phase 5
already assume, doing double duty (eval/benchmark history *and* the lead's reporting source). All
records are **`repo_id`-keyed**. Tables (illustrative): `runs` (repo, commit, trigger, result,
duration, failed-gate), `deploys` (env, outcome, approver, rollback y/n), `incidents` (repo,
severity, triage class, MTTR), `agent_actions` (which agent, fix proposed, merged y/n), `gates`
(the **pending-approvals** queue — operational state, not just history). **Writers:** Jenkins
stages and the agents append records as the pipeline runs (deterministic instrumentation, not the
lead). **Reader:** the team-lead agent, read-only.

**How the lead queries it — parameterized query tools, NOT free-form NL2SQL.** You ask in natural
language ("how's repo A doing?"); the model maps it to a **fixed, reviewed SQL query with bound
parameters**, not arbitrary generated SQL. Rationale: a weak local model writing open-ended SQL
gets joins/columns/aggregations subtly wrong, and the lead's whole job is *accurate* narration —
a confabulated query is a confident wrong answer, its worst failure mode. So the model's task
shrinks from "write correct SQL" to "pick the right tool + fill params" — the bounded tool-calling
local models *can* do (deterministic skeleton, bounded LLM inside, applied to querying). The lead's
read tools (each backed by parameterized SQL, read-only): `get_pending_approvals()`,
`repo_performance(repo, window)` (DORA rollup), `incident_ranking(window)`, `recent_runs(repo, n)`,
`deploy_history(repo, window)`. Answers your example questions directly — *"any approvals needed?"*
→ `get_pending_approvals`; *"how's repo A?"* → `repo_performance`; *"most incidents?"* →
`incident_ranking`. **Constraints:** the metrics DB is exposed to the lead **read-only** (the lead
never writes — Jenkins/agents do); if open-ended exploration is ever truly needed, a **read-only
NL2SQL tool may be added later as a fallback**, but start bounded (safer + more reliable on a local
model). This also retroactively defines the "run history" the lead spec referred to — *this* is it,
made queryable.

**Artifacts — the versioned store for QA reports, coding-agent patches, and the eval leaderboard
(ADK-native interface, custom local backend).** Beyond the relational metrics DB (numbers,
queryable) there's *file-shaped* output that shouldn't live in session state: the **QA agent's
report**, the **coding agent's proposed diff/patch**, and the **Phase-5 eval leaderboard**. ADK
**Artifacts** is the right primitive — named, **automatically versioned** binary/file storage via a
`BaseArtifactService` on the Runner, accessed with `save_artifact` / `load_artifact` /
`list_artifacts`. Its **`user:` namespacing maps onto `repo_id` scoping** (per-repo, persistent
across runs). **The honest limit (a real build item):** the only *persistent* built-in
implementation is **`GcsArtifactService` (Google Cloud Storage) — which violates the no-cloud
rule**; the only other built-in, `InMemoryArtifactService`, is ephemeral. So to get persistent +
local, **write a custom `BaseArtifactService`** backed by our local store (filesystem or Postgres),
implementing the five interface methods (`save` / `load` / `list_keys` / `delete` / `list_versions`).
This is a small, well-scoped custom component — the *interface* is reused; only the *local backend*
is ours. (`LoadArtifactsTool` exists for model-driven artifact loading but is Gemini-flavored; our
agents mostly use direct `load_artifact`.) This belongs in the "code, not config" list.

**Build planner + manifest resolution (runs BEFORE Jenkins — fills the toolchain gap).**
A repo needs the right toolchain to build — Python needs Python+uv, a React/TS repo needs
Node+npm, Go needs the Go toolchain. The toolchain comes from a **Docker image, per build**, via
Jenkins' `agent { docker { image <var> } }` (the image is a *variable*, so one generic
Jenkinsfile serves every repo) — never pre-installed on the Jenkins controller (that's the
fragile mega-image / version-conflict trap). The **build planner** is a deterministic
control-plane step (not an agent) that resolves, for each repo, a `(toolchain_image, commands)`
pair, then hands it to Jenkins. Resolution order:
1. **Repo override** — the repo carries its own `.agentci.yml`? Use it. (User override always wins;
   never *required*.)
2. **Cached manifest** — a manifest cached on *our* system under `repo_id`? Use it. (We do **not**
   force the user to add a file to their repo — the resolved manifest lives in our `repo_id`-keyed
   state, Phase 2/10. The repo stays untouched.)
3. **Generate + cache** — neither exists → generate one, then cache it under `repo_id`:
   a. **Fingerprint the stack deterministically** (the common case, no agent): `package.json` →
      node, `pyproject.toml`/`requirements.txt` → python, `go.mod` → go, `pom.xml` → java → fill
      `language`/`build`/`test`/`toolchain_image` from the **language preset table** (Phase 10).
   b. **Only if fingerprinting fails** (weird monorepo, no recognizable manifest) → an agent drafts
      a candidate manifest from the repo structure. This is the *fallback*, the one place judgment
      is needed; the deterministic path handles ~everything else.
**Resolution is deterministic-first, agent-last** (the backbone rule): a fingerprint is a
file-existence lookup, not an LLM call. **A generated/cached manifest leaves `deploy:` DISABLED**
until a human confirms the deploy target (via the repo override or the Phase-8 console) — inference
can fill build/test from fingerprints but *cannot* know your staging/prod targets, and deploy is
the irreversible action. So a generated manifest runs the full **reversible** pipeline (gates,
tests, ephemeral staging) frictionlessly, but **will not deploy to prod** unconfirmed. **Cached ≠
hidden:** the console surfaces the resolved manifest per repo ("here's what we inferred — edit if
wrong"), and the planner **re-resolves on a stack change** (e.g. a `package.json` appears where
there was none) so a wrong inference isn't silently sticky.

**Image-build without a Docker socket (ties to topology invariant):** the *app's own* image build
(pipeline step 3) uses **Kaniko** (daemonless image builds) rather than a mounted Docker socket or
DinD; toolchain build/test stages run on the resolved image via the **exec service** in the
workload container. So "run `npm ci` in `node:20`" = the exec service starts that run on the
`node:20` image — the planner chooses the image, the existing exec-service boundary does the
spawning, no new socket hole.

**Pipeline order (cheap/deterministic gates first, agents next, human last):**
1. PR opened/updated on GitHub → Jenkins webhook trigger. **The build planner resolves the
   manifest first** (repo override → cached → generate+cache; see above) and hands Jenkins the
   `toolchain_image` + commands; stages run via `agent { docker { image <resolved> } }`.
2. **Fast gates** (fail-fast, cheap — all deterministic, no agent): lint → **gitleaks** (secret
   scan, sub-second) → **Semgrep CE** (SAST, offline) → install deps → **unit tests (emit
   coverage)** → **coverage gate: fail if project coverage < 80%** (`--cov-fail-under=80` or the
   Sonar coverage condition — the *authoritative* coverage check; the coding agent also runs this
   pre-push, but here is the source of truth) → **SonarQube quality gate** (consumes the coverage
   report — so Sonar runs *after* tests, not before) → **Trivy fs/SCA** on the lockfile. If
   `.agentci.yml` declares migrations, **Squawk** lints them here.
3. **Build gates** (expensive): build image → **Trivy image scan** immediately after build →
   **Syft** (SBOM) → **cosign** sign image with a **local key** (not keyless/Fulcio — that needs
   cloud). Reject bad images before a human ever sees them.
4. Deploy to **staging** (ephemeral, re-runnable).
5. **QA agent** runs web-automation tests on staging → structured report. Fail → report on the
   PR, no merge.
6. **Human approval** — the *one* gate, at the single irreversible action (prod deploy). The
   human approves a change that already passed automated browser testing.
7. Deploy to **production** (blue-green: deploy idle color → smoke-test → swap proxy upstream →
   keep old color hot N minutes) → post-deploy smoke checks + monitor.
8. **Auto-rollback FIRST on prod failure** (deterministic, *not* an agent — rollback is an SRE
   reflex): if a post-deploy smoke check or monitor alert trips a burn-rate threshold, swap the
   proxy back to the last-good color immediately. *Then* the triage→fix agent loop runs on the
   now-stable system, off the emergency timeline. (On docker-compose this is a ~50–100-line
   script + a ~10-line Caddy/Traefik config, no new container.)
9. Deterministic **notify human** (summary + artifacts) via the email channel.

**Managed-app log capture (closes the "where does a repo's crash get caught?" gap).** The
self-heal loop's whole premise is "app crashes/errors → issue → fix" — but that needs the
*managed app's runtime error events*, not just a Plane-2 error-rate *metric*. The gap: Grafana
showed "errors up" but no stack trace to triage. Closed as follows, **zero changes to the user's
app** (honors clone-and-go):
- **Capture at the deploy boundary, not in the app.** Because the pipeline *deploys* the app
  (blue-green, in containers the platform controls), the deploy harness ships the **container's
  stdout/stderr to Loki**, tagged by `repo_id` (Promtail/Alloy tailing container output — Loki's
  normal ingestion path). The app instruments nothing. This is the **baseline**.
- **Loki is the sink** — already the triage agent's log source via the **Grafana MCP**
  (Loki/Tempo/Prometheus). Adopting it for managed-app logs adds no new tool; it just means the
  apps' logs land where triage can already read them, `repo_id`-scoped (a label query
  `{repo_id="A", level="error"}` — exactly Loki's sweet spot).
- **A deterministic error-detection alert is the "system error-catch" trigger** (the source that
  previously had no owner). A Grafana/Loki alert rule on the `repo_id`-scoped error logs/rate
  fires the self-heal loop when it trips. **Deterministic (an alert rule), NOT an LLM watching
  logs.**
- **Triage reads the actual logs** via the Grafana MCP (tooling already exists; this just supplies
  the logs). It pulls the error + stack trace around the failure to classify it.
- **Honest limit + opt-in:** container stdout/stderr captures *crashes and logged errors* but not
  source-mapped, breadcrumb-rich error intelligence. A repo wanting deeper context can **opt in**
  via `.agentci.yml` (declare a structured-error/OTel endpoint) — an enhancement, **never
  required**. Baseline stays zero-touch.

**Self-healing loop (safe by construction — auto-propose, human merges):**
- **Issues come from three sources, converging on one path:** (1) the **QA agent** (a functional
  failure on staging), (2) the **system error-catch** (a failed stage, or a **Loki/Grafana
  prod-error alert** — see managed-app log capture above), and (3) a **human** (filed normally).
  Sources (1) and (2) route through the **triage agent**, which adds the classification +
  structured context and files a *standardized* issue — they do not write raw issues themselves. A
  human-filed issue is already well-formed and enters the same fix flow (and an escalation
  *becomes* a human-owned issue — the "human" source and the "escalation" path are one channel
  seen from two sides).
- failure detected → **triage agent** classifies (flaky / infra / real-fixable-bug), emitting
  **constrained JSON** (`{category, confidence, evidence_span, alternative_hypothesis}`); below a
  confidence threshold (e.g. 0.7) it **abstains and escalates** rather than guess.
- **always create a GitHub issue** (even on escalation — audit trail + human entry point).
- **flaky → quarantine, NEVER the coding agent.** A "flaky" classification opens a PR adding the
  test to a `quarantine.txt` skiplist + files a tracked issue assigned to the human owner, and
  **stops there**. (Wire `pytest-rerunfailures` / Playwright `retries` first.) Routing flaky to
  the coding agent guarantees it eventually "fixes" nondeterminism by deleting the test.
- **infra → escalate** (not code-fixable: secrets, a down dependency, runner issues).
- real-fixable + attempt budget left → **coding agent** proposes a fix (workload container) →
  opens a **PR labeled `agent-fix`, NEVER auto-merged** → the PR **re-enters the same validated
  pipeline** from step 1. The fix is the *most* suspect code in the system; it gets every gate.
- **Circuit breakers (not just the per-issue budget):** per-issue attempt budget (e.g. 2); a
  per-repo cap on concurrent open `agent-fix` PRs (e.g. 3); a per-day total cap (e.g. 10); a
  **freeze-mode** when triage events exceed N in 15 min (issues still file, PRs stop); and
  **same-signature dedup** (a failure whose stack-trace fingerprint matches one the agent's last
  merge introduced raises a sticky `agent-regression` issue and disables the agent for that repo
  until a human clears it). A global kill-switch env var, checked at the start of every agent
  action, is the cheap backstop. Because a human must merge, the loop **physically cannot run
  away** regardless.

**Structural guardrails (the cost of replacing the human reviewer — see the design lens):**
- **Branch protection makes "never auto-merge" structural, not a convention.** On `main`: require
  PR + ≥1 approval + **CODEOWNERS** review (humans only, covering `/`, `/.github/`, `/CODEOWNERS`,
  `/tests/`), strict required status checks, dismiss-stale-approvals, `enforce_admins: true`. The
  agent then *cannot* merge its own PR even if its code tried to. **Highest-leverage change in the
  whole phase; near-zero cost.**
- **Diff allow-list (the defense against reward-hacking the green-pipeline signal).** mini emits a
  **`git diff` as its submission** (never a push); the **control-plane wrapping node** — not mini, and
  not anything holding the GitHub token inside the workload — parses that diff (`git diff
  --name-status`) before opening any PR and enforces: **fully rejected** (no touch) — `.github/**`, `Jenkinsfile`, `Dockerfile`, dependency
  manifests, `.gitignore`, `sonar-project.properties`, `.semgrep.yml`, `.trivyignore`, coverage
  configs; **append-only** — under `tests/**`, **added files (status `A`) are allowed but modified,
  deleted, or renamed files (`M`/`D`/`R`) are rejected** (the agent may write new tests to cover its
  fix, but can never weaken or remove an existing test — the dangerous operation). It also greps
  added lines for `# noqa` / `// NOSONAR` / `@pytest.mark.skip` / suppression markers (including in
  new test files). ~100 lines of Python. This is the refusal-to-game-the-system that a human
  reviewer supplied implicitly — and the append-only rule is what lets coverage and the new-test
  gate be satisfiable while still blocking test-deletion attacks.
- **"New test must fail on the parent" gate.** The agent's PR must add/strengthen a test; the gate
  checks out `HEAD~1`, runs the new test, **expects failure**, then runs it on the patch and
  expects pass. Catches the commonest deception — "test passes because it no longer tests
  anything."
- **GitHub PAT is fine-grained and minimal:** `contents: write` + `pull-requests: write`,
  **explicitly no `workflows: write` / `actions: write` / `secrets: write`**; agent branches in an
  `agent/*` namespace running with `pull_request` (not `pull_request_target`) so they never see
  secrets — closes the "agent-opened PR as a GitHub-Actions attack vector" path.
- **Capability containment against indirect prompt injection.** The triage agent reads
  attacker-controllable text (logs, issue bodies). Guard scanning alone is insufficient (log-
  formatted injections evade it). So: the triage agent's only tool is `create_issue(structured
  template)`; the **coding agent never reads raw logs**, only the triage agent's JSON object;
  wrap untrusted text in explicit `<UNTRUSTED>…</UNTRUSTED>` delimiters; force JSON-schema output.

**Communication model — how/when you (the human) are reached.** Two channels, one voice, two
gates:
- **Push = the deterministic notifier** (email/SMTP MCP, triggered by **Jenkins** — *no LLM in the
  path*). It is the *only* thing that proactively reaches you; it always fires on a defined event,
  so it can't forget or hallucinate "all clear." It is **framed in the lead's voice** (e.g. "🔵
  Team lead: repo X passed QA, waiting on your prod approval — reply or open the console") so the
  *experience* is one assistant, but the *trigger* stays dumb-reliable.
- **Pull = the team-lead agent**, which stays **pull-only** (no autonomous trigger). It composes
  nothing proactively; it answers your questions and relays your approvals. You turn to it *after*
  a ping or whenever you want a read; tapping a notification drops you into conversation with it.
- **Safety property:** the pinger and the conversationalist are **different components** — the
  thing that notifies can't fail to notify (deterministic), and the thing you converse with can't
  act on its own (pull-only). The lead's voice on a notification is *formatting*, not a trigger.

**You are NOTIFIED (FYI, no action required) on:** run finished (pass/fail); prod deploy completed
(+ smoke result); **auto-rollback fired** (told after the fact — you don't approve a rollback, it's
a reflex); incident/issue filed; agent-fix PR opened; circuit breaker tripped (freeze-mode /
budget exhausted / agent-regression).

**You must REVIEW + APPROVE — exactly two gates:** (1) **prod-deploy approval** (the one
irreversible action, after QA passes on staging — the pipeline *pauses* here), and (2)
**escalation** (triage can't safely classify/fix, or the attempt budget is exhausted). Everything
else is automated because it's reversible (staging is ephemeral) or deterministic (a failing test
is unambiguous). You do **not** approve: staging deploys, rollbacks, issue creation, gate results,
or which agent runs. Both gates reach you via the push notifier (in the lead's voice); you review
and decide by talking to the lead, which relays your authenticated decision into the deterministic
gate (the approval is cryptographically yours; audit shows *you* approved via the lead).

**HITL placement — why the gates live in Jenkins/async, NOT ADK's `RequestInput`.** ADK 2.x has a
native human-input node (`RequestInput`, model-free), but it's the wrong tool for *these* gates, for
two reasons. **(1) The gates aren't inside an agent run.** Prod-deploy approval and escalation are
*pipeline-level* events that occur in **Jenkins between stages** (after the QA-agent stage, before the
prod-deploy stage) — there is no agent executing at that moment to host a `RequestInput` node;
`RequestInput` pauses *an agent/graph run*, which is the wrong scope. **(2) Even if forced into an
agent run, it would regress the concurrency model:** a `RequestInput` pause holds the *agent run* (its
LM Studio slot + workload container) open while waiting for the human — which, against a single-
endpoint concurrency cap, is exactly the resource you can't tie up for hours. The two gates are
deliberately **pipeline-level and resource-cheap to pause**: prod-deploy approval pauses the
**Jenkins** pipeline (`input` step — no GPU, no model, no container held), and escalation
is an **async GitHub issue** (the agent run completes and releases its slot; the human picks it up
later). So: keep prod approval on Jenkins `input` (Phase-8 Cancel-backed), keep escalation async.
`RequestInput` is reserved for *agent-internal* graph checkpoints in Phase 6.5, not for these gates.

**Bands:** Build (CI/CD agents), Govern (human gates, attempt budgets, no-auto-merge)
**Tools (free/OSS, all local):** Jenkins (orchestrator); **every agent integration is a stdio
MCP server** (uniform registry pattern from Phase 1.5) — `github/github-mcp-server` (official;
serves coding + triage), a stdio Jenkins MCP (`hekmon8`/`avisangle`, or the official
`jenkinsci/mcp-server-plugin` over HTTP), `grafana/mcp-grafana` (official; triage's log/trace
input), Playwright MCP (QA), SMTP MCP (notify) — GitHub/Jenkins/Grafana/email in the **control
plane** (they hold secrets), Playwright in the **workload container**; each behind a tight
`tool_filter`. The **coding agent embeds `mini-swe-agent`** (MIT, pinned `==2.2.x`) behind a custom
`ExecServiceEnvironment` adapter (its bash crosses to the workload; its model calls and the PR-opening
wrapping node stay control-plane). Plus deterministic gates: SonarQube CE, Trivy, gitleaks, Semgrep CE, Syft, cosign
(local key), Squawk; **Renovate self-hosted** (dependency bot, not an agent); a **Postgres CI/CD
metrics DB** (`repo_id`-keyed run/deploy/incident/approval records — the DORA-metrics home, written
by Jenkins+agents, read by the lead via parameterized query tools); **Loki** + a log shipper
(Promtail/Alloy) for **managed-app log capture** at the deploy boundary (`repo_id`-tagged
container stdout/stderr; read by triage via the Grafana MCP) + a Grafana/Loki **error-detection
alert** as the system-catch trigger; four agent skills
(`qa-automation`, `failure-triage`, `code-fix`, `team-lead` — the last being your read-and-relay
conversational surface, the front-end of the Phase 8 console).
**Low-code surface:** Jenkins declarative pipeline (`Jenkinsfile`) generated from the repo's
`.agentci.yml`; MCP registry entries; three `SKILL.md` bundles; gate binaries as one-line stages.
**Done when:** a merged PR flows through the gated pipeline to a human-approved prod deploy with
an automated QA report; **a crash/error in a deployed managed app is captured to Loki
(`repo_id`-tagged, no app changes), trips a deterministic alert, and triggers triage**; an induced
failure produces an issue + an `agent-fix` PR that re-enters the pipeline and stops at the human
merge; a flaky test is quarantined (not code-fixed); a coding-agent attempt to **modify or delete
an existing test is rejected by the diff allow-list while adding a new test is allowed
(append-only)**; a PR that fails tests or drops project coverage below 80% is blocked (pre-push
self-check + authoritative pipeline gate); and the agent cannot merge its own PR (branch protection).
**Consumes lower phases:** workload container (Phase 1) runs the coding agent + repo builds;
GitHub/Playwright tools (Phase 1.5) are the agents' hands; observability (Phase 4) feeds triage;
eval (Phase 5) benchmarks the agents; kill-switch (Phase 7) halts a runaway self-heal; console +
notifications (Phase 8) are the gates and reports.
**Build-for-Phase-10 note:** read all build/test/deploy commands from the repo's `.agentci.yml`
(never hardcode), and key every stored thing on `repo_id` even with one repo. This is the only
"extra" work Phase 9 does for Phase 10, and it's cheap.
**Defer (genuine agent-replaceable judgment, but add later):** **postmortem drafting** as a
triage-agent *skill* (seeded with the Google SRE template, called when a loop closes an issue —
LLMs are genuinely good at this); **mutation testing** (Stryker/mutmut) as an *opt-in nightly*
job on `main` to objectively answer "did the agent fix the bug or lobotomize the test?" — never
per-PR (too slow). **Skip:** cosign keyless/Fulcio (needs cloud — use local key); a second
coverage tool on top of Sonar (duplication); LLM-judge as a *gating* metric (bias — keep it
advisory, gate on deterministic tool-trajectory success).
**Avoid (anti-patterns):** an LLM orchestrating the pipeline; a manager↔worker agent hierarchy;
auto-merging any agent fix; fast-pathing the fix past any gate; routing flaky tests to the coding
agent; letting the coding agent edit tests/gates/CI-config/deps (the diff allow-list forbids it);
**letting mini push or hold the GitHub token** (it emits a `git diff`; the control-plane wrapping
node pushes); **using mini's `DockerEnvironment`** (needs a Docker socket — use the
`ExecServiceEnvironment`); bounding a mini attempt with `cost_limit` instead of `step_limit`;
triaging a prod incident *before* rolling back; **letting the team-lead agent orchestrate the
other agents, originate an approval, or hold deploy authority** (it reads + relays only — Jenkins
coordinates, the human decides, the gate enforces).

---

## Phase 10 — Self-hosted, multi-repo, open-source packaging (Build/Govern)
**Objective:** turn the working single-repo team into a tool **anyone can clone, run on their
own Docker, point at a list of their own GitHub repos, and use** — without editing the engine's
code. This is a *productize* phase, not an engineering-feature phase.

**The decoupling principle:** the agent team is a **generic engine**; everything repo-specific
lives in config — but the user is **never forced to author it**. *Which* repos = the user's
`repos.yml`. *How* each repo builds/tests/deploys = a manifest the **build planner resolves and
caches** (repo override → cached → generate+cache; Phase 9). The engine stays repo-agnostic.
**Adding repo N+1 = add a line to `repos.yml` + grant the token access** — the planner infers and
caches the manifest on first contact; the user only confirms the deploy target. Zero engine
changes, and zero required YAML in their repo.

**Steps**
1. **Repo-list config** — a `repos.yml` in the user's deployment listing the repos to manage.
   Jenkins multibranch/per-repo jobs read it. This is the *only* file the user must write.
2. **Manifest, resolved not required** — the build planner (Phase 9) resolves each repo's
   `(toolchain_image, build, test, image, quality, deploy, qa)` by: repo-carried `.agentci.yml`
   (optional **override**, wins if present) → manifest **cached under `repo_id` on our system** →
   else **generate** (deterministic fingerprint; agent only as fallback) **and cache**. The user
   is not forced to add `.agentci.yml` to their repo; the resolved manifest lives in our state. A
   **generated manifest leaves `deploy:` disabled** until the user confirms the deploy target (in
   the console or via a committed override) — gates/tests/staging run freely, prod deploy waits
   for a human. The console **surfaces** each repo's resolved manifest and the planner
   **re-resolves on a stack change**, so "cached" never means "stale and hidden."
3. **Language presets** — ship `node`/`python`/`go`/`java`/… presets (default `toolchain_image` +
   build/test commands) that the build planner's fingerprint step fills from. A standard app needs
   *zero* config; anything unusual is handled by a committed `.agentci.yml` override. Good defaults
   + full override = the OSS low-code sweet spot.
4. **User-held credentials** — the user creates a **fine-grained GitHub PAT** (or self-registers
   their own GitHub App) scoped to their repos and drops it in their `.env`. *They* own the
   repos, *they* mint the credential, *they* hold it — it never leaves their machine. (A central
   GitHub App is the SaaS model; this is self-hosted, so it's the user's own token. The token
   lives in the control plane per the Phase 1.5 placement rule.)
5. **`repo_id` namespacing** — every stored thing (sessions, vectors, artifacts, secrets, run
   history) carries a `repo_id`; every query filters on it. One user per deployment, so this is
   **organizational hygiene, not a hostile-tenant wall** — but it keeps repo A's data out of repo
   B's reports.
6. **Per-run isolation** — each repo's build/test/coding-agent work runs in a **fresh disposable
   workload container** (already true from Phase 1; here it also keeps repos from contaminating
   each other).
7. **Onboarding polish (the actual product work)** — one `docker compose up`; documented
   `.env.example` and `repos.yml.example`; a "create a GitHub token like this" guide; the
   language presets; and a short "add a repo" walkthrough. OSS tools live or die on
   time-from-clone-to-first-green-run.

**Bands:** Build (generic engine, packaging), Govern (per-repo creds/secrets/isolation)
**Tools (free/OSS):** everything already in the stack; new is config + docs, not new services.
**Low-code surface:** `repos.yml`, per-repo `.agentci.yml`, `.env`, language presets — the whole
product is configured, not coded.
**Done when:** a stranger clones the repo, sets a token in `.env`, lists two of their own repos
in `repos.yml`, runs `docker compose up`, and gets a working gated+self-healing pipeline on both
— the build planner infers and caches each manifest on first contact, so they author **zero
`.agentci.yml` files** and edit zero lines of engine code; they only confirm the deploy target
before the first prod deploy.
**Honest caveat — QA stays semi-generic:** build/test/deploy decouple cleanly via manifest
commands, but "QA-test this app" is app-specific. The engine is generic; each repo supplies its
*own* QA spec/skill (what flows to test, what "working" means) in its manifest or a repo-carried
SKILL.md. You can't auto-generate good end-to-end tests for an arbitrary unseen UI — the repo
declares intent.
**Scoping reality:** making it turnkey for *arbitrary* users (multi-language presets, robust
docs, repos that don't fit assumptions) is real product effort — but for an OSS project that's
the point.

---

## At-a-glance: phase → band → role in the CI/CD product

| Phase | Focus | Bands | Role in the CI/CD team | Low-code? |
|---|---|---|---|---|
| 0 | Hello agent | Build | the seed every team agent grows from | tiny Python shim |
| 1 | Tools + sandbox + loop | Build/Scale/Govern | the container coding agent + repo builds run in | mostly |
| 1.5 | MCP supply + Skills (stdio MCP everywhere) | Build (→Scale/Govern) | stdio MCP per integration: GitHub, Jenkins, Grafana, Playwright, email + SKILL.md | registry block + filter |
| 2 | Persistence + memory + cache + compaction | Scale | run history, per-repo state, long-loop survival | yes (URI) |
| 3 | Guardrails + secrets + egress | Govern | makes executing model-generated patches safe | config files |
| 4 | Observability (2 planes) + command center | Optimize | Phoenix (agent command center) + Jaeger / ADK web UI (dev) + Prometheus/Grafana (app) | env vars + tags |
| 5 | Eval + benchmark + simulation | Optimize | proves the agents work + no regressions | JSON/YAML + sweep |
| 6 | Self-optimization | Optimize | tunes agent prompts | CLI + light code |
| 6.5 | Static graph workflow | Build | when an agent's loop branches beyond itself (e.g. coding agent) | node/edge + Python |
| 7 | Scale + kill-switch + concurrency model | Scale/Govern | parallel across repos / serialize within a repo / global LLM-capacity cap; Falco brake | YAML + watchdog |
| 8 | Control surface + console | Build/Govern | operator console: watch, approve, stop, reports | API + app |
| 9 | **Self-healing CI/CD team** | Build/Govern | **the product: gated pipeline + self-heal loop** (Jenkins, 4 agents incl. team-lead; coding agent = embedded mini-swe-agent; gitleaks/Semgrep/Trivy/Syft/cosign/Squawk, Renovate, Postgres metrics DB + parameterized query tools, branch protection, diff allow-list, auto-rollback) | Jenkinsfile + SKILL.md + config |
| 10 | **Self-hosted multi-repo packaging** | Build/Govern | **clone-and-go for any repo** | repos.yml + .agentci.yml |

## The boxes that stay "code, not config"
Budget real engineering here, in order of when they bite: **(1) local code sandbox** (Phase 1),
**(2) LLM-judge eval** (Phase 5, solved by an LM Studio judge), **(3) semantic memory at scale**
(Phase 2), **(4) anomaly / kill-switch** (Phase 7), **(5) the four skills + Jenkins wiring, plus the
coding agent's `ExecServiceEnvironment` adapter + mini-wrapping node (diff extraction → allow-list →
PR)** (Phase 9), **(6) the generic-engine packaging + QA-spec story** (Phase 10). Everything else is
native ADK or one-line config.

## Suggested cadence
Build the foundation bottom-up: **0–1** first (an agent with hands in a sandbox), then **1.5**
(the browser/GitHub/email tools + skills the team needs), then **2 and 4** together (persistence
+ observability give a debuggable system — and observability is triage's input). Then **3**
(harden before the coding agent executes patches unattended). Then **5–6** (prove and tune the
agents). **6.5** (graduating an agent's dynamic loop to a static branching graph) is optional/demand-
driven (likely first for the coding agent). **7** when one
machine isn't enough or you want the kill-switch backstop. **8** after 3 and 7 (the operator
console needs auth + a real server-side stop/approval before exposure). Then **9** — the CI/CD
team itself, the capstone that consumes 1–8, built manifest-driven and `repo_id`-keyed. Finally
**10** — productize into the clone-and-go open-source tool. Phases 9–10 are the goal; 0–8 are the
foundation that makes them safe, observable, and tunable.
