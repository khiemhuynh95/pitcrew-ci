# Coding Agent ← mini-swe-agent Integration Spec

Self-contained reference for the **coding agent's inner loop**. Extracted from the master build
plan so it can live in the repo next to the code. Where this and the prose docs disagree, the
prose docs win — open an issue.

Two kinds of statement below are labelled deliberately:
- **[FACT]** — verified from mini-swe-agent source/docs (the interface you must conform to).
- **[OURS]** — our architecture decision (what we choose to build and the constraints we impose).

---

## 1. Decision

**[OURS]** The coding agent's inner loop is **embedded `mini-swe-agent`**, wrapped as a single node
in our ADK graph workflow. It is the **one deliberate exception** to "every agent is an `LlmAgent`
in a dynamic-workflow loop" — the other three agents (QA, triage, team-lead) stay pure ADK.

**Why:** harness design swings coding success by up to ~6×, and mini is a battle-tested
generate→edit→test→iterate loop (>74% SWE-bench Verified) from the SWE-bench team. We inherit the
hardest-to-tune part (the loop + prompts), already validated, instead of hand-building it.

**Cost to us:** one custom `Environment` adapter class + a thin wrapping node. Everything else is
inherited or config.

---

## 2. What mini-swe-agent is (the interface you conform to)

- **[FACT] License: MIT.** PyPI package `mini-swe-agent`. Safe to embed/redistribute in our OSS
  project with attribution.
- **[FACT] It is v2.** Latest at time of writing `v2.2.6` (2026-03-02); there is a v1→v2 migration
  guide. **[OURS] Pin `mini-swe-agent==2.2.x`; do NOT track `main`.**
- **[FACT] Model-agnostic via LiteLLM.** `get_model_class()` routes names containing
  anthropic/sonnet/opus/claude → `AnthropicModel`, everything else → `LitellmModel`. So a local
  model name routes to `LitellmModel`, which talks to **LM Studio** over its OpenAI-compatible API.
- **[FACT] Bash-only.** It does not use the LM tool-calling interface at all — the model emits bash
  in text. This is why it runs with *any* model, and is the key fit for a weak local model that is
  poor at structured function-calling.
- **[FACT] Stateless execution.** Every action runs via `subprocess.run` (or equivalent); no
  persistent shell session. Their documented sandboxing path is literally "swap `subprocess.run`
  for `docker exec`."
- **[FACT] Linear history.** The whole agent state is one `self.messages` list; serializable to a
  trajectory JSON.
- **[FACT] Three duck-typed `Protocol`s** (structural typing, no inheritance needed), exported from
  `minisweagent`: `Agent`, `Model`, `Environment`.

### 2a. Control loop [FACT]
- `run(task)` seeds two messages from `system_template` + `instance_template` (Jinja2,
  `StrictUndefined` → crashes on a missing var like `{{task}}`), then loops `step()` until a message
  with `role: "exit"` appears. Returns a dict with **`exit_status`** + **`submission`**.
- `step()` = `query()` then `execute_actions()`.
- `query()` checks limits, calls `model.query()`, appends response to history.
- `execute_actions()` runs each action via `self.env.execute()` and appends observations.
- **Termination is exception-driven:** `LimitsExceeded` (on `step_limit`/`cost_limit`), and
  `Submitted` — triggered when the **first line of a command's output is the magic string
  `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`**.

### 2b. Environment protocol [FACT] — this is the seam we implement
| Method | Returns | Purpose |
|---|---|---|
| `execute(action, cwd, timeout)` | `dict` | Run a bash command; returns merged stdout+stderr and exit code |
| `get_template_vars(**kwargs)` | `dict` | Variables for Jinja2 prompt rendering |
| `serialize()` | `dict` | Config metadata for the trajectory log |
| `cleanup()` | `None` | Release resources |

Shipped environments: `LocalEnvironment` (host `subprocess.run`), `DockerEnvironment`
(`docker run -d` + `sleep` + `docker exec`; **assumes Docker access/socket**),
`SwerexDockerEnvironment`, `SingularityEnvironment`, `BubblewrapEnvironment`.

### 2c. Config & env vars [FACT]
- `AgentConfig` (Pydantic): `step_limit` (max model calls), `cost_limit` (default 3.0),
  `system_template`, `instance_template`. Driven from YAML.
- Env vars: `MSWEA_MODEL_NAME`, `MSWEA_MODEL_API_KEY`, `MSWEA_GLOBAL_COST_LIMIT`,
  `MSWEA_GLOBAL_CALL_LIMIT`, `MSWEA_SILENT_STARTUP`, `MSWEA_GLOBAL_CONFIG_DIR`.
- Python bindings: `DefaultAgent(LitellmModel(...), LocalEnvironment()).run(task)`.

---

## 3. What we build (the integration design) [OURS]

**Install:** `uv add mini-swe-agent==2.2.x` (embed as a library; do not shell out to the CLI).

**The entire custom surface = one `Environment` adapter.** Implement a class conforming to the
4-method protocol whose `execute()` routes each bash command **through our exec service into the
disposable workload container** — NOT mini's `DockerEnvironment` (it wants a Docker socket, which
our two-container topology forbids).

```
# Illustrative SHAPE only — not an implementation (no code yet).
class ExecServiceEnvironment:               # conforms to mini's Environment protocol
    def execute(self, action, cwd=None, timeout=...):
        # send `action` over the narrow exec-service RPC -> workload container
        # return {"output": <stdout+stderr>, "returncode": <int>}
        ...
    def get_template_vars(self): ...
    def serialize(self): ...
    def cleanup(self): ...                   # tear down the disposable workload container
```

**Driving call** (from the `code-fix` skill / coding-agent node, running in the **control plane**):

```
# LitellmModel points at LM Studio by IP; ExecServiceEnvironment bridges to the workload.
agent = DefaultAgent(
    LitellmModel(model_name="openai/<lm-studio-model>",
                 api_base="http://<lm-studio-ip>:1234/v1"),
    ExecServiceEnvironment(workload=<this run's container>),
    step_limit=<N>,            # NOT cost_limit (local cost ~= 0)
)
result = agent.run(task)       # task = issue + triage classification
diff = result["submission"]    # configured to be `git diff` (see Section 4)
```

**Where things run:** mini's `DefaultAgent` + `LitellmModel` execute in the **control plane** (model
calls go control-plane → LM Studio; the model never enters the workload). **Only `execute()` crosses
into the workload.** This preserves the trust boundary exactly — the one thing that needs the
workload (bash) is the one method that crosses.

This adapter **supersedes the generic ADK `RemoteEnvironment`/Environment-Toolset path for the
coding agent only.** The Phase-1 generic sandbox agent still uses the ADK loop; the coding agent
diverges to mini.

---

## 4. Trust-boundary rules (SAFETY-CRITICAL) [OURS]

1. **mini produces a DIFF, never a push.** Configure the `instance_template` so the agent: makes
   the fix, runs the repo's tests, and **terminates with `git diff` as its submission**. It does not
   commit/push.
2. **The GitHub token NEVER enters the workload.** mini's README suggests "want a PR? just have the
   model run `gh`" — **do NOT do this here.** Running `gh`/`git push` inside the untrusted workload
   would put a credentialed token in it. `git diff` is unprivileged and needs no token.
3. **The push is control-plane and post-allow-list.** Flow: mini emits `git diff` → the **control-
   plane** wrapping node extracts it → **diff allow-list** → only then the control-plane **GitHub
   MCP** (token here, filtered to PR/branch verbs) opens the `agent-fix` branch + PR.
4. So the coding agent does **not** call the GitHub MCP from inside mini; the **wrapping node** does,
   after the allow-list passes.

---

## 5. Governance (structural, not Plugin-based) [OURS]

Because mini's loop runs **outside** ADK, the global governance Plugin does **not** see mini's
individual bash/model calls. Acceptable here only because the coding agent's safety was always
structural:

- **Per-command screening → the exec service.** Every bash command mini emits is inspected at the
  exec service: command **denylist** + **Squid egress allowlist** (no arbitrary network).
- **Output screening → the diff allow-list.** Reject modify/delete/rename of existing tests
  (**append-only tests**: allow added test files only); reject edits to `.github/**`, `Jenkinsfile`,
  `Dockerfile`, dep manifests, gate configs; reject suppression markers (`# noqa`, `// NOSONAR`,
  `@pytest.mark.skip`).
- **Real safety net → full-pipeline re-entry + human merge** (PR labeled `agent-fix`, never
  auto-merged; CODEOWNERS humans-only).
- **Screen the task input** handed to mini for injection **before** invoking (the one Plugin
  function that relocates here).

The Plugin still governs triage / QA / lead (pure ADK) normally.

---

## 6. Operating parameters [OURS]

- **Two budgets, do not conflate.** mini's **`step_limit`** bounds ONE fix attempt's inner loop
  (use `step_limit`, **not** `cost_limit` — local-model cost ≈ 0). The circuit-breaker **per-issue
  attempt budget** (~2) bounds how many times the whole coding node is invoked for one issue.
- **Pre-push self-gate (inside mini).** Before submitting the diff, mini runs the repo's **unit
  tests + coverage** in the workload (using the build-planner-resolved test command, matching CI)
  and **must not submit unless all tests pass AND project coverage ≥ 80%**. If it can't within
  `step_limit`, the attempt yields no PR → escalate (or burn one attempt). This is a **pre-flight**;
  the pipeline re-runs the same tests + a deterministic `--cov-fail-under=80` / Sonar gate, and
  **that is the source of truth** (the model's self-report is never trusted).
- **Thinking/reasoning.** The coding agent's reasoning comes from **mini's own prompt-driven loop**
  (optionally a reasoning model in LM Studio — QwQ / Qwen3-thinking / DeepSeek-R1-distill). It does
  **NOT** use ADK's `PlanReActPlanner` (that's for triage, which is an ADK loop).
- **Concurrency.** Each run instantiates mini against its **own disposable workload container**.
  mini's stateless model makes this trivial. The single LM Studio endpoint is still the throughput
  ceiling; mini's model calls go through the same endpoint and count against the same global cap.
- **Observability.** Capture mini's linear **trajectory JSON** into the artifact store + an OTel
  span per coding run.

---

## 7. End-to-end coding-agent flow [OURS]

1. Coding-agent node receives the task (the issue + triage classification).
2. Screen task input for prompt injection.
3. Spin up a disposable workload container; clone the repo at the target commit into it.
4. Invoke mini: `DefaultAgent(LitellmModel(<LM Studio>), ExecServiceEnvironment()).run(task)`.
   mini loops edit→test inside the workload; the pre-push self-gate runs here.
5. mini terminates; `submission` = `git diff`.
6. Control plane extracts the diff; apply the **diff allow-list** (reject test modify/delete, gate/
   CI/dep edits, suppressions; allow added tests).
7. If it passes: control-plane **GitHub MCP** opens the `agent-fix` branch + PR.
8. PR re-enters the **full gated pipeline** (authoritative gates); a **human merges**.
9. Capture mini's trajectory → artifact store + OTel span; write an `agent_action` row to the
   metrics DB.

---

## 8. Build-vs-inherit checklist [OURS]

**Build (ours):**
- [ ] One `ExecServiceEnvironment` class (mini's `Environment` protocol → exec-service RPC → workload).
- [ ] The wrapping ADK coding-agent node (invoke mini, await, parse `submission`).
- [ ] Diff extraction + diff-allow-list hookup.
- [ ] Control-plane GitHub MCP call to open the PR (post-allow-list).
- [ ] Trajectory capture → artifacts + OTel span.
- [ ] `code-fix` skill = the `system_template` + `instance_template` (instruct: fix, run tests,
      meet ≥80% coverage, submit `git diff`).

**Inherit (mini):**
- [x] The generate→edit→test→iterate loop, prompts, linear history, trajectory format, termination.

**Config:**
- [ ] mini YAML / kwargs: `model_name` → LM Studio, `api_base`, `step_limit`, templates.
- [ ] Pin `mini-swe-agent==2.2.x` in `pyproject.toml`; commit `uv.lock`.

---

## 9. Traps specific to mini [OURS]

- **Do NOT use mini's `DockerEnvironment`** — it assumes a Docker socket; our topology forbids one.
  Use the custom exec-service adapter.
- **Do NOT let mini push or hold the GitHub token** (despite its README). It produces a `git diff`;
  the control plane pushes.
- **Pin `==2.2.x`; don't track `main`.** Read the v1→v2 migration guide before bumping.
- **Use `step_limit`, not `cost_limit`** (local-model cost ≈ 0; `cost_limit` won't bound anything).
- **`StrictUndefined`**: a missing template variable crashes — ensure `{{task}}` (and any custom
  vars) are always provided. (This is a feature: loud failure beats silent prompt corruption.)
- mini's global cost/call limit env vars assume priced APIs — irrelevant locally; bound via
  `step_limit` and the circuit breakers instead.

---

## 10. Eval bonus (Phase 5) [OURS/FACT]

**[FACT]** mini-swe-agent *is* the reference SWE-bench harness (it ships batch evaluation).
**[OURS]** Point its batch eval at **LM Studio** to measure your model's fix-success on **SWE-bench
Verified** directly — a credible, standardized coding number with zero extra harness to build. Pin
the mini version + a SWE-bench split so numbers stay comparable across runs.

---

## 11. Source references (re-verify before building)

- Repo + license + v2 notice: https://github.com/SWE-agent/mini-swe-agent
- Architecture (protocols, control loop): https://deepwiki.com/SWE-agent/mini-swe-agent/1.1-architecture-overview
- Environment protocol + shipped envs: https://deepwiki.com/SWE-agent/mini-swe-agent/4.4-execution-environments
- Python API (bindings, model selection): https://deepwiki.com/SWE-agent/mini-swe-agent/7.4-python-api
- Docs home: https://mini-swe-agent.com/latest/
