# AJSMGPT — Claude Code Harness Policy
## Claude-only Model Routing, V1 Context, and Long-Term Target
**Date:** 2026-09-21
**Project:** AJSMGPT
**Scope:** Claude Code with Sonnet 5 and Opus 5 only.
**Purpose:** Define how Claude Code is used to build AJSMGPT V1 and beyond without sacrificing architectural correctness, Oracle safety, or development efficiency.

---

# 0. Executive decision

```text
                        TARUN
                          |
                          v
                AJSMGPT engineering spec
                          |
                          v
                     Claude Code
                      2.1.278+
                          |
              +-----------+-----------+
              |                       |
              v                       v
          Sonnet 5                 Opus 5
        routine work           complex work
        / tests / docs         / debugging
                               / architecture
                               / review
              |                       |
              +-----------+-----------+
                          |
                          v
                   Git + pytest + evals
                          |
                          v
                       AJSMGPT
                          |
        +-----------------+-----------------+
        |                 |                 |
        v                 v                 v
      spaCy             Qwen          deterministic
                       runtime          validators
        |                 |                 |
        +-----------------+-----------------+
                          |
                          v
                   read-only Oracle
```

## Default routing

| Work type | Primary | Escalation |
|---|---|---|
| Routine implementation | Sonnet 5 | Opus 5 |
| Complex implementation | Opus 5 | Opus 5 max effort |
| Architecture | Opus 5 max effort | Human decision |
| Deep debugging | Opus 5 | Opus 5 max effort |
| Repository-wide refactor | Opus 5 | Opus 5 max effort |
| Security / Oracle safety review | Opus 5 max effort, fresh session | Human approval |
| Test / eval construction | Opus 5 | — |
| Documentation | Sonnet 5 | Opus 5 |
| Final high-risk review | **Two independent Opus 5 passes** (separate sessions, no shared context) | Human decision |
| Runtime NLP/SQL model | **Qwen** | Do not replace with a coding LLM |

The critical optimization is **routing + effort**, not loyalty to one model.

---

# 1. AJSMGPT V1 — project context

## 1.1 What AJSMGPT is

AJSMGPT is an Oracle ERP natural-language query assistant. A layman asks a business question in plain English; the system produces a verified, read-only Oracle SQL query and a deterministic business report.

It is not a generic CRUD application. The LLM is never the source of truth.

## 1.2 V1 architecture

```text
raw question
→ safe typo correction
→ spaCy NLP signals
→ Qwen structured QueryPlan
→ semantic validation
→ deterministic GroundedSchemaPlan
→ Qwen grounded Oracle SQL
→ strict static SQL validation
→ safe read-only Oracle execution
→ deterministic business report
```

Core principle:

> **Qwen proposes. Deterministic code verifies. Oracle executes only verified SQL.**

The verified schema/business catalog is the source of truth, not the LLM.

## 1.3 V1 entry points

The V1 API is `/v1/nlp/*`:

```text
/v1/nlp/analyze
/v1/nlp/understand
/v1/nlp/sql-preview
/v1/nlp/execute
```

Do not invent additional endpoints.

## 1.4 Legacy pipeline (NOT V1)

```text
/ask
app/query_engine.py
app/purchase_analytics_router.py
app/sql_generator_v2.py
app/schema_search.py
```

- `/ask` remains registered; do not remove or modify unless a task explicitly requests migration/deprecation.
- Legacy tests may remain as regression/reference material.
- Never use legacy modules as the basis for new V1 implementation.

## 1.5 Unwired files

`app/query_planner.py` is unwired and must NOT be assumed part of the V1 execution chain.

## 1.6 Non-negotiable architecture rules

- Prefer deterministic validation over LLM assumptions.
- Never infer unverified Oracle business semantics.
- Fail closed when schema/business meaning is not verified.
- V1 SQL must be grounded in the verified business schema catalog.
- Never bypass SQL safety validation.
- Oracle access is read-only.
- No live metadata lookups where offline verified metadata is required.
- Do not use the old SQL builder as the production architecture.
- Do not introduce a multi-agent framework into the runtime.

## 1.7 Current V1 baseline

```text
Targeted V1 suites:        105/105 passing
Earlier authoritative suite: 293/293 passing
```

Current 47-question evaluation:

```text
QUERY_PLAN_FAILURE            20
UNSUPPORTED_EXPECTED          13
ENTITY_RESOLUTION_REJECTION    9
CAPABILITY_FAILURE             3
GROUNDING_FAILURE              2
SQL_GENERATION_FAILURE         0
SQL_VALIDATION_FAILURE         0
ENVIRONMENT_FAILURE            0
PASS_PIPELINE                  0
```

Key conclusion:

> **Do not interpret the 20 QueryPlan failures as proof that Qwen3:8b is inadequate until the raw model outputs are recovered.**

Raw outputs were not saved. Therefore:

```text
20 failures
    ↓
recover raw outputs
    ↓
classify root cause (model vs prompt vs contract vs ontology)
    ↓
only then compare Qwen3:8b vs Qwen3:14b
```

Increasing model size cannot fix a bad ontology, bad contract, bad semantic validator, missing capability, or incorrect catalog.

## 1.8 V1 immediate workflow

```text
DO NOT:
- switch immediately to 30B
- add RAG
- rewrite QueryPlan
- connect Oracle
- redesign architecture
```

Instead:

```text
1. Recover the 20 raw Qwen3:8b outputs.
2. Classify every failure.
3. Determine root causes.
4. Run controlled 8B vs 14B comparison.
5. Fix only the proven bottleneck.
6. Re-run acceptance.
7. Connect company server.
8. Validate real Oracle results.
9. Freeze V1.
10. Add RAG/Qdrant afterward.
```

---

# 2. Long-term achievement

## 2.1 Definition of "V1 done"

V1 is achieved when:

```text
- every /v1/nlp/* endpoint is grounded, validated, and read-only
- the 47-question evaluation has zero unexplained failures
  (every remaining failure is classified as UNSUPPORTED_EXPECTED
   with a documented reason, or fixed)
- targeted and authoritative suites stay green
- real Oracle results are validated against the company server
- the runtime model choice (Qwen3:8b vs 14b) is evidence-based
- V1 is frozen and tagged
```

## 2.2 Beyond V1

Only after the V1 freeze:

```text
RAG / Qdrant entity and value retrieval
    ↓
broader business capability coverage
    ↓
question bank + eval automation (AutomateQuery)
    ↓
continuous evaluation against real questions
```

Each stage keeps the same control layer: **deterministic grounding, deterministic SQL validation, read-only Oracle**.

## 2.3 Long-term end state

```text
                 DEVELOPMENT
                      |
                 Claude Code
                      |
                Opus 5 / Sonnet 5
                      |
                      v
                 AJSMGPT code
                      |
                 DEPLOYMENT
                      |
                      v
                    USER
                      |
                      v
                    spaCy
                      |
                      v
                     Qwen
                      |
                      v
             deterministic grounding
                      |
                      v
               Qwen SQL generation
                      |
                      v
             deterministic SQL validator
                      |
                      v
             read-only Oracle execution
```

The coding-model stack stays separate from the application-model stack. Coding agents operate outside the runtime.

## 2.4 What NOT to optimize for

```text
highest public benchmark score
lowest token cost
largest model
most agents
most plugins
largest context window
most autonomous behavior
```

Optimize for:

```text
correct AJSMGPT changes
+ safe Oracle behavior
+ architecture preservation
+ regression resistance
+ long-term maintainability
```

---

# 3. Claude Code baseline

Latest verified release as of 2026-09-21:

```text
Claude Code v2.1.278   (released 2026-09-19)
```

https://github.com/anthropics/claude-code/releases/tag/v2.1.278

Recent changes relevant to AJSMGPT:

- server-side Auto Mode classification for supported API/enterprise/gateway configurations;
- AGENTS.md support (v2.1.277) when CLAUDE.md is absent;
- `claude plugin eval` for reproducible plugin evaluation;
- effort controls;
- subagent model forcing;
- gateway request-class / agent-type / compaction hints;
- improved MCP startup controls;
- memory-pressure warnings;
- Remote Control improvements.

Most relevant features:

```text
effort control
subagent model control
plugin evaluation
gateway hints
AGENTS.md support
```

Do not upgrade in the middle of a critical validation run. Pin/record the version used for important benchmark/evaluation runs.

Recommended configuration:

```text
CLAUDE.md
+ AGENTS.md where cross-agent portability is useful
+ Git
+ pytest
+ AJSMGPT evaluator
+ controlled SSH
```

---

# 4. Claude models

## 4.1 Claude Opus 5

```text
claude-opus-5
```

Anthropic positions Opus 5 as approaching Fable 5's frontier intelligence at approximately half the price, with state-of-the-art Frontier-Bench results and near-Fable CursorBench 3.2 performance at max effort.

Use for:

- architecture audits and V1 freeze review;
- long-horizon repository reasoning;
- difficult implementation;
- deep debugging;
- multi-file refactoring;
- SQL-generation architecture;
- QueryPlan failures;
- semantic-validator problems;
- complex test/evaluation failures;
- security and Oracle safety review;
- code review.

**Opus 5 is the escalation and architecture model for AJSMGPT.** Use max effort for architecture, security, and repository-wide work; medium/high effort for everyday complex engineering.

## 4.2 Claude Sonnet 5

```text
claude-sonnet-5
$2 / MTok input, $10 / MTok output (permanent introductory pricing)
```

Substantially more agentic than Sonnet 4.6 and close to Opus 4.8 on important agentic tasks.

Use for:

- tests;
- documentation;
- bounded endpoint implementation;
- straightforward bug fixes;
- small refactors;
- repetitive implementation;
- test fixture construction;
- low-risk code cleanup.

Do not use it as the default for architectural redesign.

---

# 5. Tiered routing

## Tier A — Routine → Sonnet 5

Adding tests, implementing already-designed endpoints, documentation, fixtures, simple bug fixes, deterministic utility functions, formatting, focused refactors.

Selection rule — use Sonnet 5 when the task has:

```text
clear specification
small change surface
low architectural risk
good existing test coverage
```

## Tier B — Complex engineering → Opus 5 (medium/high effort)

QueryPlan debugging, semantic validation, schema grounding, entity resolution, SQL generation, SQL validator changes, FastAPI execution flow, Oracle-specific logic, evaluation infrastructure, cross-module debugging, large test failures.

## Tier C — Architecture / long-horizon → Opus 5 (max effort)

Architectural review, large-scale refactor planning, V1 freeze review, RAG architecture, security architecture, Oracle execution boundary review, long-term repository strategy.

For high-risk architectural decisions:

```text
Opus 5 max effort (session 1)
      +
Opus 5 max effort (session 2, fresh context, different framing)
      ↓
independent analyses
      ↓
human decision
```

Do not ask one session "Decide the architecture." Ask both:

> "Find contradictions, risks, alternatives, and evidence."

The project owner makes the architectural decision.

## Decision tree

```text
TASK
 |
 +-- low risk / bounded?
 |       |
 |      YES
 |       ↓
 |   Sonnet 5
 |
 +-- complex engineering?
 |       |
 |      YES
 |       ↓
 |   Opus 5 (medium/high effort)
 |
 +-- architecture / huge context / high risk?
         |
        YES
         ↓
   Opus 5 (max effort)
```

Subagents may use Sonnet 5 where appropriate. For high-risk work, explicitly force Opus 5 rather than relying on accidental routing.

---

# 6. Effort routing

Model choice is only half the optimization.

| Effort | Use for |
|---|---|
| Low | simple test, documentation, small deterministic change, obvious bug |
| Medium / High | cross-file implementation, debugging, SQL generation, QueryPlan, entity resolution, semantic validation |
| Maximum | architecture, security, major refactor, long-horizon debugging, ambiguous repository-wide failures |

Do not use maximum effort for every request.

---

# 7. Independent-review protocol

For a major AJSMGPT change:

### Pass 1 — implementation (Opus 5 or Sonnet 5)

```text
Inspect.
Plan.
Implement.
Run focused tests.
Report changed files.
```

### Pass 2 — independent review (Opus 5 max effort, fresh session)

```text
Do not modify files.

Review the diff.

Check:
- architecture
- Oracle safety
- deterministic boundaries
- entity-resolution safety
- validator bypasses
- regression risk
- test adequacy

Return findings only.
```

`/code-review` in Claude Code is the tool for this pass; for the highest-risk changes, `/code-review ultra` runs a multi-agent cloud review of the branch.

### Pass 3 — correction (same model as Pass 1)

```text
Address only validated findings.
Do not broaden scope.
Run tests.
```

### Pass 4 — final verification

```text
pytest
git diff --check
AJSMGPT evaluator
```

Never let two sessions edit the same working tree concurrently.

---

# 8. The repository is the memory

Do not depend on model memory as the architectural source of truth.

```text
verified code + tests
        ↓
architecture documents
        ↓
contracts
        ↓
ADRs
        ↓
CLAUDE.md / AGENTS.md
        ↓
agent memory
```

Agent memory (claude-mem, `~/.claude/projects/.../memory/`) is useful for conventions, commands, and preferences. It is not the authority.

```text
repository docs > agent memory
```

---

# 9. Coding and testing discipline (applies to every model)

- Work only within the explicitly requested task; no unrelated refactors; no automatic scope expansion.
- Preserve unrelated dirty worktree changes. Never `git add .`.
- Do not modify `.env`.
- Never expose credentials, DSNs, or ERP result rows.
- Do not reset, restore, clean, or delete unrelated work.
- Prefer targeted tests for the changed component.
- Do not run Oracle/network tests on the laptop.
- Do not run Ollama unless explicitly requested.
- Report exactly which tests were run and their result.
- Every task has a bounded scope and explicit acceptance criteria; stop when they are satisfied.

---

# 10. Internal benchmark for model selection

Public benchmarks (SWE-bench etc.) are incomplete for AJSMGPT. AJSMGPT failure modes include:

```text
wrong architectural assumption
wrong domain interpretation
wrong schema relationship
unsafe SQL
incorrect entity resolution
incorrect QueryPlan contract
wrong Oracle date semantics
incorrect deterministic validation
regression in existing capabilities
```

Benchmark hierarchy:

```text
1. AJSMGPT internal acceptance/evals
2. Terminal/agentic coding benchmarks
3. Long-horizon coding benchmarks
4. Repository-level coding benchmarks
5. General reasoning benchmarks
6. SWE-bench
```

## Model shootout

Before committing long-term, run the same task set / repository snapshot / CLAUDE.md / tools / test environment against:

```text
Claude Sonnet 5
Claude Opus 5 (medium effort)
Claude Opus 5 (max effort)
```

Score:

```text
correctness
tests passed
regressions
architectural violations
security violations
tokens
time
human corrections
```

Use quality-adjusted cost, not raw token price.

## Scorecard

```text
AJSMGPT Agent Score =
    30% correctness
  + 20% architectural compliance
  + 15% test success
  + 15% security
  + 10% regression avoidance
  + 5% token efficiency
  + 5% time efficiency
```

A cheap model that produces a dangerous Oracle change is not cheaper.

## Hard-fail conditions

Regardless of model or test count, any of these is a FAIL:

```text
Oracle DML introduced
Oracle DDL introduced
PL/SQL execution introduced
validator bypass
raw entity interpolation
unverified join
destructive git operation
unrelated architecture rewrite
production .env modification without approval
tests deleted to make suite green
legacy architecture silently reinstated
```

---

# 11. Plugins and memory

Plugins are optional. Prioritize:

```text
Claude Code + repository documentation + tests + evaluation harness
```

Evaluate any plugin by:

```text
does it reduce real AJSMGPT work?
does it preserve deterministic control?
does it create another source of hidden state?
does it consume substantial context?
does it modify files autonomously?
can it be reproduced?
```

Avoid plugins that obscure the engineering state.

---

# 12. Bottom line

```text
                 DEVELOPMENT (Claude Code)

        Opus 5 max effort  ← architecture / security / high-risk review
             |
        Opus 5             ← complex engineering / debugging / review
             |
        Sonnet 5           ← routine implementation / tests / docs


                 APPLICATION RUNTIME

        Qwen3:8b / Qwen3:14b
                  |
        deterministic grounding
                  |
        deterministic SQL validation
                  |
          READ-ONLY Oracle
```

For high-risk AJSMGPT changes, use **independent review in a fresh Opus 5 session** rather than more autonomous agents.

The repository's deterministic architecture remains the ultimate control layer.

---

# 13. Sources

Claude Code releases: https://github.com/anthropics/claude-code/releases
Claude Opus 5: https://www.anthropic.com/news/claude-opus-5
Claude Sonnet 5: https://www.anthropic.com/news/claude-sonnet-5
Model lifecycle: https://docs.anthropic.com/en/docs/about-claude/model-deprecations

Benchmark figures are vendor-published evidence, not a universal ranking. The authoritative comparison is AJSMGPT's own controlled shootout.
