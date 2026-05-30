# Architecture Diagrams — Self-Healing CI/CD Agent Team

Companion to `adk_agent_build_plan.md`, `HANDOFF.md`, and `CLAUDE.md`. These render in
GitHub, VS Code (Mermaid preview), and most Markdown viewers. Each diagram answers **one**
question — there is deliberately no single master diagram.

All diagrams reflect the architecture **as settled through the latest planning pass** (append-only
tests, the ≥80% coverage gate, HITL-in-Jenkins, the per-agent thinking policy, LoopAgent =
ADK Template-workflow). If a diagram and the prose ever disagree, the prose docs win — open an issue.

**Color legend (shared across all diagrams):**
- 🟪 **agent** (purple) — an LLM agent (a `LoopAgent` + a skill)
- 🟦 **pipeline / MCP** (blue) — deterministic pipeline step, or an MCP server (no LLM judgment)
- 🟩 **gate / scale** (green) — a decision gate or a scale/runtime component
- 🟥 **data / governance** (red) — a datastore, or a governance/secrets control
- 🟨 **human** (yellow) — you, the director
- ⬜ **external** (grey) — an external system (GitHub, LM Studio, the deployed app)

---

## 1. Trust boundary + MCP placement (the foundational topology)

**Answers:** what runs where, and why "uniform wiring ≠ uniform privilege."
**Reflects:** Architecture decision (two-container topology); Phase 1/1.5; HANDOFF §1.5-B.

The key invariant this encodes: **each agent's *brain* and model calls run in the control plane;
only its *untrusted execution* (running patches, driving a browser) runs in the disposable workload
container. The model never enters the workload** — the control plane bridges in via
`RemoteEnvironment` → `LocalEnvironment`. Credentialed MCP servers (GitHub/Jenkins/Grafana/SMTP)
sit in the control plane with the secrets; only open-ended, untrusted-inbound Playwright sits in the
workload.

```mermaid
flowchart TB
    LM["LM Studio - ONE endpoint by IP<br/>reachable ONLY from control plane"]:::ext
    GH["GitHub"]:::ext
    GRAF["Grafana / Loki / Tempo / Prometheus"]:::ext
    JENKHOST["Jenkins engine"]:::ext
    WEB["staging web app (untrusted content)"]:::ext
    MAIL["SMTP relay"]:::ext

    subgraph CP["CONTROL PLANE - trusted; secrets, model, agent BRAINS live here"]
      direction TB
      PLUGIN["governance Plugin (Presidio / LLM Guard / NeMo)<br/>fires on EVERY model + tool call"]:::govern
      TRIAGE["TRIAGE agent brain"]:::agent
      CODER["CODING agent brain"]:::agent
      QABRAIN["QA agent brain"]:::agent
      LEAD["TEAM-LEAD agent (pull-only)"]:::agent
      GHMCP["github-mcp-server stdio - holds PAT"]:::mcp
      JENMCP["jenkins MCP stdio - holds token"]:::mcp
      GRMCP["mcp-grafana stdio - holds token"]:::mcp
      MAILMCP["SMTP MCP stdio - send only"]:::mcp
    end

    subgraph WL["WORKLOAD CONTAINER - untrusted, disposable, per-run, NO model"]
      direction TB
      PWMCP["Playwright MCP stdio - open-ended browsing"]:::mcp
      EXEC["LocalEnvironment / exec service<br/>runs patches + pre-push tests"]:::scale
    end

    PLUGIN -.guards.-> TRIAGE
    PLUGIN -.guards.-> CODER
    PLUGIN -.guards.-> QABRAIN
    TRIAGE --> GRMCP
    TRIAGE --> GHMCP
    TRIAGE --> JENMCP
    CODER --> GHMCP
    LEAD --> MAILMCP
    CODER -->|RemoteEnvironment bridge| EXEC
    QABRAIN -->|drives| PWMCP

    TRIAGE --> LM
    CODER --> LM
    QABRAIN --> LM
    LEAD --> LM
    GHMCP --> GH
    JENMCP --> JENKHOST
    GRMCP --> GRAF
    MAILMCP --> MAIL
    PWMCP --> WEB

    classDef agent fill:#f3e8fd,stroke:#a142f4,color:#1a1a1a
    classDef mcp fill:#e8f0fe,stroke:#4285f4,color:#1a1a1a
    classDef scale fill:#e6f4ea,stroke:#34a853,color:#1a1a1a
    classDef govern fill:#fce8e6,stroke:#ea4335,color:#1a1a1a
    classDef ext fill:#f1f3f4,stroke:#5f6368,color:#1a1a1a
```

---

## 2. CI/CD pipeline (the product's deterministic skeleton)

**Answers:** how a PR flows from open → gates → staging → human-approved prod, with agents as steps.
**Reflects:** Phase 9 (pipeline order, build planner, two observability planes); invariants #7, #9.

Jenkins orchestrates; **agents are steps, never the orchestrator**. A deterministic **build planner**
resolves the toolchain/commands *before* Jenkins. The coverage gate (`--cov-fail-under=80`) sits
right after unit tests, and Sonar runs *after* tests so it can consume the coverage report.

```mermaid
flowchart TB
    PR["PR on GitHub - user's repo (.agentci.yml OPTIONAL)"]:::ext
    HUMAN["HUMAN - approves prod / handles escalation"]:::human
    PLAN["BUILD PLANNER (deterministic, pre-Jenkins)<br/>resolve toolchain+commands: override then cache then generate"]:::pipe

    subgraph JEN["JENKINS - deterministic orchestrator (no LLM in control flow)"]
      direction TB
      FAST["FAST GATES: lint, gitleaks, Semgrep, deps, unit tests,<br/>COVERAGE GATE (fail under 80 pct), Sonar, Trivy fs"]:::pipe
      BUILD["BUILD: image (Kaniko), Trivy image, Syft SBOM, cosign local-key"]:::pipe
      STAGE["deploy to staging"]:::pipe
      PROD["human-approved deploy to prod, monitor, auto-rollback on failure"]:::pipe
      FAST --> BUILD --> STAGE
    end

    subgraph AGENTS["AGENT TEAM - each a LoopAgent + skill (no manager)"]
      direction TB
      QA["QA agent - Playwright on staging"]:::agent
      TRIAGE["TRIAGE agent - classify + standardize issue"]:::agent
      CODER["CODING agent - propose fix (workload) + pre-push tests/coverage gate"]:::agent
    end

    P1["PLANE 1 - agent OTel traces"]:::opt
    P2["PLANE 2 - pipeline + deployed-app health"]:::opt
    ISSUE["GitHub issue - standardized"]:::pipe
    FIXPR["agent-fix PR - NEVER auto-merged (diff allow-list)"]:::pipe

    PR --> PLAN --> FAST
    STAGE --> QA
    QA -->|pass| PROD
    QA -->|functional failure| TRIAGE
    BUILD -.stage failure.-> TRIAGE
    PROD --> P2
    JEN -.stage metrics.-> P2
    AGENTS -.agent traces.-> P1
    P2 -->|deterministic alert| TRIAGE
    P1 -->|reads to diagnose| TRIAGE
    HUMAN -->|files| ISSUE
    TRIAGE --> ISSUE
    ISSUE -->|fixable + budget| CODER
    ISSUE -->|not fixable / budget out| HUMAN
    CODER --> FIXPR
    FIXPR -->|re-enters same pipeline| FAST
    PROD --> HUMAN

    classDef pipe fill:#e8f0fe,stroke:#4285f4,color:#1a1a1a
    classDef agent fill:#f3e8fd,stroke:#a142f4,color:#1a1a1a
    classDef human fill:#fef7e0,stroke:#f9ab00,color:#1a1a1a
    classDef opt fill:#e6f4ea,stroke:#34a853,color:#1a1a1a
    classDef ext fill:#f1f3f4,stroke:#5f6368,color:#1a1a1a
```

---

## 3. Self-heal loop (hardened) — the safety-critical path

**Answers:** how a failure becomes a fix safely, and every reward-hacking / runaway guard on the way.
**Reflects:** Phase 9 self-heal; invariants #8, #9; the append-only-tests + coverage-gate decisions.

This is where most of the hardening lives. Three issue sources converge on triage; triage **always
creates a standardized issue**; the branch is deterministic (flaky → quarantine, *never* the coding
agent; infra → escalate; real+fixable+budget → coding agent). The coding agent is bounded by
**append-only tests + a pre-push ≥80% coverage gate**, its diff passes the **allow-list**, the PR is
**never auto-merged**, it **re-enters the full pipeline**, and **circuit breakers** bound the whole loop.

```mermaid
flowchart TB
    subgraph SRC["3 ISSUE SOURCES - all converge on triage"]
      QAFAIL["QA agent - functional failure on staging"]:::agent
      SYSERR["system error-catch - deterministic Grafana/Loki alert (NO LLM)"]:::pipe
      HUMANFILE["human files an issue"]:::human
    end

    TRIAGE{"TRIAGE agent - classify (thinking ON)<br/>confidence + abstain: under 0.7 escalates"}:::agent
    ISSUE["GitHub issue - one standardized format (ALWAYS created)"]:::pipe

    FLAKY["FLAKY: quarantine (quarantine.txt + pytest-rerunfailures)<br/>NEVER the coding agent"]:::pipe
    INFRA["INFRA: escalate to human (secrets, down dep, runner)"]:::human
    REAL{"REAL + fixable + budget left?"}:::gate

    CODER["CODING agent (workload, thinking ON)<br/>append-only tests; pre-push: tests pass AND coverage >= 80%"]:::agent
    ALLOW{"diff allow-list: reject test modify/delete,<br/>gate/CI/dep edits, suppressions"}:::gate
    HALT["patch rejected - escalate"]:::human
    FIXPR["agent-fix PR - labeled, NEVER auto-merged"]:::pipe
    PIPE["re-enters the FULL gated pipeline"]:::pipe
    MERGE["HUMAN merge via CODEOWNERS - the one human gate"]:::human

    BREAK["CIRCUIT BREAKERS: per-issue budget ~2, concurrent-PR cap,<br/>daily cap, freeze-mode, dup-signature dedup, global kill-switch"]:::govern

    QAFAIL --> TRIAGE
    SYSERR --> TRIAGE
    HUMANFILE --> ISSUE
    TRIAGE --> ISSUE
    ISSUE --> FLAKY
    ISSUE --> INFRA
    ISSUE --> REAL
    REAL -->|no| INFRA
    REAL -->|yes| CODER
    CODER --> ALLOW
    ALLOW -->|reject| HALT
    ALLOW -->|pass| FIXPR
    FIXPR --> PIPE
    PIPE --> MERGE
    PIPE -.fails again.-> TRIAGE
    BREAK -.bounds.-> CODER

    classDef agent fill:#f3e8fd,stroke:#a142f4,color:#1a1a1a
    classDef pipe fill:#e8f0fe,stroke:#4285f4,color:#1a1a1a
    classDef gate fill:#e6f4ea,stroke:#34a853,color:#1a1a1a
    classDef human fill:#fef7e0,stroke:#f9ab00,color:#1a1a1a
    classDef govern fill:#fce8e6,stroke:#ea4335,color:#1a1a1a
```

---

## 4. Communication model — how you and the system reach each other

**Answers:** when you get pinged vs. when you go ask, and the exactly-two things you must approve.
**Reflects:** Phase 9 communication model; the HITL-placement decision (gates in Jenkins, not `RequestInput`).

**Push** is a deterministic notifier (Jenkins-triggered, no LLM, framed in the lead's voice) — it
can't forget to fire. **Pull** is the team-lead agent — it converses and relays, but can't self-trigger.
Separate components = the safety. The **prod-approval gate is a Jenkins `input` step** (resource-cheap
pause), *not* an ADK `RequestInput` (which would hold a model slot + container open — see HANDOFF traps).

```mermaid
flowchart TB
    DIR["DIRECTOR you"]:::human

    NOTIFY["NOTIFIER - deterministic email, Jenkins-triggered, no LLM<br/>framed in lead's voice"]:::pipe
    LEAD["TEAM-LEAD agent - pull-only<br/>you turn to it after a ping"]:::agent

    subgraph DET["DETERMINISTIC BACKBONE"]
      JENKINS["JENKINS - orchestrator"]:::pipe
      GATE{"PROD-APPROVAL gate - Jenkins input, pauses pipeline"}:::gate
      ESC{"ESCALATION - triage cannot fix / budget out"}:::gate
      ROLLBACK["auto-rollback fired - FYI only"]:::pipe
    end

    MDB[("METRICS DB - Postgres")]:::data

    subgraph TEAM["WORKING AGENTS"]
      QA["QA agent"]:::agent
      TRIAGE["TRIAGE agent"]:::agent
      CODER["CODING agent"]:::agent
    end

    JENKINS -->|run done, deploy done, incident, agent-PR, breaker, ROLLBACK| NOTIFY
    GATE -->|approval needed| NOTIFY
    ESC -->|escalation| NOTIFY
    NOTIFY ==>|PUSH: pings you| DIR

    DIR ==>|PULL: ask status / review / approve| LEAD
    LEAD -->|read-only parameterized queries| MDB
    LEAD -->|relays YOUR approval| GATE

    JENKINS --> QA
    JENKINS --> TRIAGE
    JENKINS --> CODER
    JENKINS --> ROLLBACK
    QA -->|report| MDB
    TRIAGE -->|classification| MDB
    CODER -->|action| MDB
    JENKINS -->|run/deploy records| MDB
    QA -->|fail| TRIAGE
    TRIAGE -->|fixable| CODER

    classDef human fill:#fef7e0,stroke:#f9ab00,color:#1a1a1a
    classDef agent fill:#f3e8fd,stroke:#a142f4,color:#1a1a1a
    classDef pipe fill:#e8f0fe,stroke:#4285f4,color:#1a1a1a
    classDef gate fill:#e6f4ea,stroke:#34a853,color:#1a1a1a
    classDef data fill:#fce8e6,stroke:#ea4335,color:#1a1a1a
```

---

## 5. Concurrency model — per-run instances and the real ceiling

**Answers:** how many runs go in parallel, and why adding containers doesn't raise throughput.
**Reflects:** Phase 7 concurrency model; invariant #14; the thinking-policy cap-sizing tie-in.

An agent is a **definition instantiated per run** in its own disposable container — not a long-lived
singleton. Two-layer admission: per-repo serialization (correctness) + a global in-flight cap
(LLM backpressure). The cap is sized to **measured model capacity, not container count** — the single
LM Studio endpoint serializes inference, so it *is* the ceiling. Raise it by scaling the model
(vLLM / 2nd instance), not by adding containers.

```mermaid
flowchart TB
    PRA["repo A - PR"]:::ext
    PRA2["repo A - 2nd PR / re-push"]:::ext
    PRB["repo B - PR"]:::ext
    PRC["repo C - PR"]:::ext

    JEN["JENKINS - admission control"]:::pipe

    SAME{"same repo already running?"}:::gate
    CAP{"global in-flight < model cap?"}:::gate

    QUEUE[("Redis broker QUEUE<br/>holds runs that must wait")]:::data

    subgraph POOL["WORKLOAD CONTAINER POOL - per-run agent INSTANCES (not singletons)"]
      IA["instance: repo A QA - container 1"]:::agent
      IB["instance: repo B QA - container 2"]:::agent
      IC["instance: repo C coding - container 3"]:::agent
    end

    LM["LM Studio - ONE endpoint, ONE GPU<br/>serializes inference = the real ceiling<br/>(cap measured WITH thinking on triage + coding)"]:::ext

    PRA --> JEN
    PRA2 --> JEN
    PRB --> JEN
    PRC --> JEN
    JEN --> SAME

    SAME -->|"same repo, distinct run"| QUEUE
    SAME -->|"same branch, newer commit"| ABORT["abort + replace in-flight run"]:::pipe
    SAME -->|"different repo"| CAP
    CAP -->|"at cap"| QUEUE
    CAP -->|"capacity free"| POOL
    QUEUE -.->|"slot frees"| CAP

    IA --> LM
    IB --> LM
    IC --> LM
    ABORT --> CAP

    classDef ext fill:#f1f3f4,stroke:#5f6368,color:#1a1a1a
    classDef pipe fill:#e8f0fe,stroke:#4285f4,color:#1a1a1a
    classDef gate fill:#e6f4ea,stroke:#34a853,color:#1a1a1a
    classDef agent fill:#f3e8fd,stroke:#a142f4,color:#1a1a1a
    classDef data fill:#fce8e6,stroke:#ea4335,color:#1a1a1a
```

---

## 6. Managed-app error capture — where a deployed app's crash gets caught

**Answers:** how a runtime failure in a *deployed user app* (not the pipeline) triggers self-heal.
**Reflects:** Phase 9 managed-app log capture; closes "where does a repo crash get caught."

The deploy harness ships the deployed app's stdout/stderr to **Loki, tagged by `repo_id`, with zero
app changes** (clone-and-go). A **deterministic** Grafana/Loki alert (not an LLM watching logs) is the
"system error-catch" trigger; triage then reads the actual error + stack via the Grafana MCP.

```mermaid
flowchart TB
    subgraph PROD["DEPLOYED MANAGED APP - prod, zero app changes"]
      APP["repo A app container - emits stdout/stderr"]:::ext
    end

    SHIP["log shipper - Promtail/Alloy at deploy boundary"]:::pipe
    LOKI[("LOKI - logs, repo_id-tagged<br/>label query: repo_id=A, level=error")]:::data
    PROM[("Prometheus / Grafana - Plane 2 metrics<br/>error RATE")]:::opt

    ALERT{"deterministic error alert - Grafana/Loki rule<br/>NOT an LLM watching logs"}:::gate

    subgraph CP["CONTROL PLANE"]
      TRIAGE["TRIAGE agent - reads error+stack via Grafana MCP<br/>classify: flaky / infra / real-fixable"]:::agent
      CODER["CODING agent - propose fix PR (executes in workload)"]:::agent
    end

    ISSUE["GitHub issue - standardized"]:::pipe
    GH["GitHub - agent-fix PR, human merges"]:::ext

    APP -->|container logs| SHIP
    SHIP -->|ship, repo_id tag| LOKI
    APP -.health metric.-> PROM

    LOKI -->|error logs/rate| ALERT
    PROM -->|rate spike| ALERT
    ALERT -->|fires self-heal| TRIAGE
    TRIAGE -->|reads actual logs| LOKI
    TRIAGE --> ISSUE
    ISSUE -->|fixable + budget| CODER
    ISSUE -->|not fixable / budget out| HUMAN["escalate to human"]:::human
    CODER -->|agent-fix PR| GH
    GH -->|re-enters full pipeline| PIPE["gated pipeline"]:::pipe

    classDef ext fill:#f1f3f4,stroke:#5f6368,color:#1a1a1a
    classDef pipe fill:#e8f0fe,stroke:#4285f4,color:#1a1a1a
    classDef data fill:#fce8e6,stroke:#ea4335,color:#1a1a1a
    classDef opt fill:#e6f4ea,stroke:#34a853,color:#1a1a1a
    classDef gate fill:#fef7e0,stroke:#f9ab00,color:#1a1a1a
    classDef agent fill:#f3e8fd,stroke:#a142f4,color:#1a1a1a
    classDef human fill:#fef7e0,stroke:#f9ab00,color:#1a1a1a
```

---

## 7. OSS clone-and-go — what a stranger actually does (Phase 10)

**Answers:** how the product packages so a new user runs it on their own repos with near-zero config.
**Reflects:** Phase 10 packaging; the generic-engine / `repos.yml`-only / `repo_id`-namespacing goals.

`repos.yml` is the **only** file the user must write; `.agentci.yml` is an optional override the build
planner infers when absent. The engine is repo-agnostic; everything repo-specific is config.

```mermaid
flowchart TB
    USER["user clones the OSS project<br/>docker compose up"]:::ext
    CFG["repos.yml + .env (secrets via SOPS+age)<br/>(user's repo list + their OWN GitHub token)"]:::govern
    REPOS["the user's OWN repos<br/>.agentci.yml is OPTIONAL (planner infers if absent)"]:::ext

    subgraph SELFHOST["SELF-HOSTED on the USER'S Docker (one user, their repos)"]
      direction TB
      JENKINS["Jenkins - reads repos.yml + resolved manifest, orchestrates"]:::pipe
      ENGINE["generic agent engine<br/>QA / triage / coding / lead - repo-agnostic, language presets"]:::agent
      STATE["per-repo state namespaced by repo_id<br/>(hygiene, not a security wall - single user)"]:::scale
      WORK["workload container - per run, disposable<br/>(runs the user's own code + coding agent patches)"]:::scale
      JENKINS --> ENGINE --> STATE
      ENGINE --> WORK
    end

    USER --> CFG --> JENKINS
    REPOS -->|webhook + user's token| JENKINS

    classDef pipe fill:#e8f0fe,stroke:#4285f4,color:#1a1a1a
    classDef agent fill:#f3e8fd,stroke:#a142f4,color:#1a1a1a
    classDef govern fill:#fef7e0,stroke:#f9ab00,color:#1a1a1a
    classDef scale fill:#e6f4ea,stroke:#34a853,color:#1a1a1a
    classDef ext fill:#f1f3f4,stroke:#5f6368,color:#1a1a1a
```

---

### Diagram-to-doc cross-reference

| # | Diagram | Primary doc section |
|---|---|---|
| 1 | Trust boundary + MCP placement | Architecture decisions; Phase 1/1.5; HANDOFF §1.5-B |
| 2 | CI/CD pipeline | Phase 9 (pipeline order, build planner) |
| 3 | Self-heal loop (hardened) | Phase 9 (self-heal); invariants #8, #9 |
| 4 | Communication model | Phase 9 (communication); HITL-placement note |
| 5 | Concurrency model | Phase 7; invariant #14 |
| 6 | Managed-app error capture | Phase 9 (managed-app log capture) |
| 7 | OSS clone-and-go | Phase 10 (packaging) |
