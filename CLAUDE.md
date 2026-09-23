# AJSMGPT

## 1. Product goal
A user types a plain-English business question. AJSMGPT returns a report built from the company's Oracle ERP database — no SQL knowledge, no schema knowledge required from the user.

We are building **V1** now: a grounded, validated, read-only question → SQL → report pipeline. Nothing beyond V1 (RAG, Qdrant retrieval, bigger runtime models) starts until V1 is frozen.

## 2. Architecture
```text
raw question
→ safe typo correction              app/text_correction.py
→ spaCy NLP signals                 app/spacy_nlp.py
→ Qwen structured QueryPlan         app/query_plan_extractor.py, app/query_plan.py
→ semantic validation               app/query_plan_semantic_validator.py
→ deterministic GroundedSchemaPlan  app/schema_grounding.py
→ Qwen grounded Oracle SQL          app/grounded_sql_generator.py
→ strict static SQL validation      app/grounded_sql_validator.py, app/sql_safety.py, app/sql_datatype_validator.py
→ safe read-only Oracle execution   app/nlp_execution.py, app/oracle_client.py
→ deterministic business report     app/answer_formatter.py
```
Principle: **Qwen proposes. Deterministic code verifies. Oracle executes only verified SQL.**
The verified catalog (`app/resources/business_schema_catalog.json`) is the source of truth, not the LLM.

V1 API (`app/nlp_router.py`, mounted in `app/api.py`):
```text
/v1/nlp/analyze
/v1/nlp/understand
/v1/nlp/ground
/v1/nlp/sql-preview
/v1/nlp/execute
```
Do not add endpoints.

## 3. Non-negotiable safety rules
- Oracle access is read-only. Never introduce DML, DDL, or PL/SQL execution.
- Never bypass SQL safety validation.
- Fail closed when schema or business meaning is not verified.
- Never infer unverified Oracle business semantics.
- V1 SQL must be grounded in the verified schema catalog; no live metadata lookups where offline verified metadata is required.
- Never interpolate raw entity strings into SQL.
- Never expose credentials, DSNs, or ERP result rows in code, logs, tests, or chat.
- Do not modify `.env`.

## 4. Repository structure
```text
app/                    V1 pipeline modules (see §2) + FastAPI app
app/resources/          verified catalog, ontology, capabilities, typo dictionary
data/                   schema metadata, entity/value indexes, evaluation questions
scripts/                test_*.py (unittest), evaluation and index-building scripts
AutomateQuery/          question-bank / eval review tooling (not runtime)
reports/, logs/         generated output
docs/                   harness policy and reference docs
progress.md             milestones done / next steps — update when a step lands
fix.md                  open failures with root cause + fix — update status as fixed
fixlog.md               append-only session log — one entry per prompt
```
Legacy — NOT V1, do not build on it, do not remove without an explicit task:
```text
/ask   app/query_engine.py   app/purchase_analytics_router.py
app/sql_generator_v2.py      app/schema_search.py
```
Unwired — not in the V1 chain: `app/query_planner.py`, `app/entity_resolver.py`, `app/full_value_resolver.py`.
Post-V1, unwired — `app/plugins.py`: deterministic post-execution plugins over `NLPExecuteResponse` (CLI `python -m app.plugins`, tests `scripts/test_plugins.py`). Never wire into SQL generation or execution; wiring into the response is a Step 9+ decision.
Wired — `app/entity_resolution.py` IS in the V1 chain: `nlp_execution.py` calls `resolve_plan_entities(plan, oracle_entity_lookup)` before the execution gate (deterministic, fail-closed, Oracle-backed lookup; commit `ab77443`).

## 5. Current active work
Branch: `feature/v1-query-execution`. Live status: `progress.md`.
Order of work — do not skip ahead:
```text
1. Recover the 20 raw Qwen3:8b QueryPlan outputs        ✅
2. Classify every failure (model / prompt / contract / ontology)  ✅ fix.md #6
3. Fix only the proven bottleneck                       ✅
   3b. Close fix.md #7 (aggregate without GROUP BY)      ✅ commit 088b908
   P1. Row limits: 11g ROWNUM wrapper, model writes none ✅ commit 088b908
   P2. Dates: deterministic 'YYYYMMDD' half-open binds   ✅ commit 088b908
   P3. Catalog: purchase/consumption rate, cost aliases  ✅ commit 0a11c17
   P4. Entity resolution on real master data             ✅ commit 7816149
4. Controlled Qwen3:8b vs Qwen3:14b comparison           ✅ docs/STEP4_MODEL_COMPARISON.md, fix.md #11 — 14b adopted as dev/eval model
5. Re-run acceptance                                     ← next
6. Connect company Oracle server, validate real results
   (needs: branch pushed to origin + DBA SELECT-only account — docs/ORACLE_READONLY_ACCOUNT.md)
7. Freeze V1
```
Not now: RAG, Qdrant in runtime, 30B models, QueryPlan rewrite, architecture redesign.

## 6. Current known failures
Detail and fix plan per item: `fix.md`.
47-question real evaluation (`scripts/v1_real_question_eval_results.json`):
```text
UNSUPPORTED_EXPECTED         23   ← by design
ENTITY_RESOLUTION_REJECTION  10   ← values absent from the offline fixture (recheck on real master data)
CAPABILITY_FAILURE            5   ← mrs lookup/unknown operation
QUERY_PLAN_FAILURE            4   ← all model behaviour (low confidence, undeclared sort field)
PASS_PIPELINE                 2   ← both now executable on Oracle 11.2 (ROWNUM wrapper, 'YYYYMMDD' binds)
GROUNDING_FAILURE             2   ← order-pending (P6), cross-domain date alias collision (fix.md #10)
SQL_VALIDATION_FAILURE        1   ← model omitted the entity filter; correct rejection
SQL_GENERATION / ENVIRONMENT  0
```
Re-run 2026-09-22 after the Oracle-executability fixes (offline, stub runner, 0 Oracle calls). Count moved 3 → 2 but
quality went up: before, 2 of the 3 passing SQLs would have raised ORA-00937 and all 3 would have failed on
`FETCH FIRST` and DATE binds. Both current passes are executable as written. The QueryPlan failures are model
behaviour and are the real input to Step 4.

## 7. Test commands
Tests are `unittest` scripts. Run the one for the component you changed:
```bash
python scripts/test_<component>.py
```
Core V1 suites: `test_text_correction`, `test_spacy_nlp`, `test_query_plan_extractor`, `test_query_plan_semantic_validator`, `test_schema_grounding`, `test_grounded_sql_generator`, `test_grounded_sql_validator`, `test_sql_datatype_validator`, `test_nlp_execution`, `test_v1_acceptance_matrix`.
Baseline: 11 core suites 278/278 (2026-09-22).

## 8. Evaluation commands
```bash
python scripts/evaluate_v1_real_questions.py
```
Requires Ollama. Results land in `scripts/v1_real_question_eval_results.json` and `scripts/v1_real_question_eval.log`.

## 9. Oracle restrictions
- Do not run Oracle/network tests on the laptop (`test_oracle_connection.py`, `check_connections.py`, `check_schema_access.py`, `evaluate_v1_real_questions.py` against a live DB).
- Do not run Ollama unless explicitly asked.
- Oracle execution goes only through `app/nlp_execution.py` after validation passes.

## 10. Git restrictions
- Never `git add .`; stage only files you changed.
- Preserve unrelated dirty worktree changes.
- Never reset, restore, clean, or delete unrelated work.
- Commit or push only when asked.

## 11. Change discipline
- Work only within the requested task; no unrelated refactors, no scope expansion.
- Prefer deterministic validation over LLM assumptions.
- Do not use legacy modules as the basis for new V1 code.
- Do not introduce a multi-agent framework into the runtime.
- Stop when acceptance criteria are met; do not continue to the next task.

## 12. Definition of done
- Requested acceptance criteria met.
- Targeted tests for the changed component run and reported by name with pass/fail counts.
- No safety rule in §3 violated; no legacy path reinstated.
- Changed files listed.
- `fixlog.md` entry appended (prompt · done · files · tests). Every prompt, no exceptions.
- `progress.md` / `fix.md` updated if a step or fix landed.
