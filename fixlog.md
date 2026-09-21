# AJSMGPT — Claude Session Log

Append-only. One entry per prompt. Newest at the bottom.
Format: date · prompt (short) · what was done · files touched · tests run.

---

## 2026-09-21

### Create Claude-only harness doc
- **Prompt:** split the ChatGPT harness doc into a Claude-only version, add V1 context and long-term goal.
- **Done:** wrote Claude-only routing policy (Sonnet 5 / Opus 5), V1 architecture context, "V1 done" definition, post-V1 roadmap. Fable removed on follow-up. ChatGPT "CLAUDE.md structure" section removed on follow-up.
- **Files:** `/home/tarun/AJSMGPT_Claude_Code_Harness_2026-09-21.md`, copied to `docs/AJSMGPT_Claude_Code_Harness_2026-09-21.md`
- **Tests:** none (docs only)

### Rewrite CLAUDE.md
- **Prompt:** create CLAUDE.md in the 12-section structure; goal = user prompt → database report; currently V1.
- **Done:** replaced project `CLAUDE.md`. Filled from repo: module→pipeline-stage map, `/v1/nlp/*` endpoints, legacy + unwired files, branch, 47-question failure table, unittest invocation (`python scripts/test_<component>.py`), eval command.
- **Files:** `CLAUDE.md`
- **Tests:** none (docs only)
- **Open:** confirm `entity_resolution.py` / `entity_resolver.py` / `full_value_resolver.py` are truly unwired; confirm "293/293 authoritative suite" still exists.

### Create progress.md and fix.md
- **Prompt:** create progress.md and fix.md.
- **Done:** `progress.md` — done milestones from git log, uncommitted work, ordered next steps, deferred list. `fix.md` — 5 items from `scripts/v1_real_question_eval_results.json`.
- **Finding:** 0 of the 20 `QUERY_PLAN_FAILURE` records have `raw_model_calls` saved → evaluator must be changed before raw outputs can be recovered (fix.md #1).
- **Files:** `progress.md`, `fix.md`
- **Tests:** none (docs only)

### Create fixlog.md
- **Prompt:** session log that records every prompt's output, kept updated.
- **Done:** this file; back-filled today's entries; added append rule to `CLAUDE.md` §12.
- **Files:** `fixlog.md`, `CLAUDE.md`
- **Tests:** none

### Reference tracking files in CLAUDE.md
- **Prompt:** mention progress.md / fix.md / fixlog.md in CLAUDE.md.
- **Done:** listed in §4 with update rules; linked from §5 and §6; §12 requires fixlog entry + progress/fix updates.
- **Files:** `CLAUDE.md`
- **Tests:** none

### Expand progress.md
- **Prompt:** hyper-detailed future needs + current V1 completion.
- **Done:** per-stage status table with unit-test counts (105 total across suites), endpoint table, capability-family coverage (5 supported / 2 unsupported), 47-question eval, ~55% completion estimate, 9-step remaining V1 plan with exit criteria, post-V1 needs A–I.
- **Finding:** `POST /v1/nlp/ground` exists in `nlp_router.py` but was missing from CLAUDE.md → added.
- **Files:** `progress.md`, `CLAUDE.md`
- **Tests:** none (static inspection only)

### Read project context and summarize understanding
- **Prompt:** read the AJSMGPT context (CLAUDE.md, progress.md, fix.md, fixlog.md, docs/harness) and state what was understood.
- **Done:** read-only review; summary given in chat.
- **Finding:** `app/entity_resolution.py` is NOT unwired — `app/nlp_execution.py:18` imports it and `_default_resolve_entities` (line 158) calls `resolve_plan_entities(plan, oracle_entity_lookup)` from `execute_nlp_query`; it is what produced the 9 `ENTITY_RESOLUTION_REJECTION` results. Only `entity_resolver.py` and `full_value_resolver.py` are unwired. CLAUDE.md §4, progress.md "In progress", and fix.md #2 say otherwise → need correction.
- **Finding:** fix.md #1 root cause located: `scripts/evaluate_v1_real_questions.py` `_run_one` drops `raw_model_calls` when `classification == "QUERY_PLAN_FAILURE"` (explicit `if` near the end); `QueryPlanValidationError` is raised `from` the pydantic error so field detail exists in `__cause__` but is never persisted.
- **Finding:** 4 of the 9 entity rejections used non-canonical concept names (`item`, `vendor`) that `resolvable_concepts()` will never accept, even against real Oracle — model vocabulary issue, not a fixture/data gap.
- **Files:** `fixlog.md` only
- **Tests:** none (read-only)

### Fix doc/code mismatches
- **Prompt:** fix the doc mismatch.
- **Done:** `CLAUDE.md` §4 — moved `app/entity_resolution.py` from "Unwired" to a new "Wired" line. `progress.md` — "In progress" now states the resolver is wired, lists the older resolvers as the unwired ones, and adds this session's uncommitted fixes (QueryFilter bypass gate, `:ITEM_NAME` validator fix, evaluator offline resolver + reclassification); Step 4 rewritten around the 4 non-canonical-concept / 5 fixture-gap split instead of a wire-or-not decision. `fix.md` #1 — root cause located (`!= "QUERY_PLAN_FAILURE"` guard in `_run_one`; pydantic detail lost in `__cause__`); #2 — wiring corrected, 4/5 split recorded, fix direction set (prompt/ontology, not `_VERIFIED_SOURCES` aliases).
- **Verified:** every line/function/test name cited in the docs grep-checked against the code.
- **Files:** `CLAUDE.md`, `progress.md`, `fix.md`, `fixlog.md`
- **Tests:** none (docs only); `git diff --check` clean

### Plan next steps, checkpoint commits, Step 1 evaluator fix
- **Prompt:** what is the next plan; ask questions; which model per the md files.
- **Decisions (user):** two workstream-level commits; Step 1 = fix evaluator and re-run only the 20 `QUERY_PLAN_FAILURE`; Ollama approved; stay on Opus 5 medium effort for Steps 1–3; Opus 5 max fresh session for the review pass.
- **Done:** core V1 suites 252/252 (11 suites). Commit `ab77443` — 26 V1 production/resource/test files (hunk-level split was not self-consistent: validator depends on uncommitted `compound_conditions`, execution on untracked `entity_resolution.py`). Excluded legacy (`query_engine.py`, `purchase_analytics_router.py`), `.gitignore`, `AJSMquery.sql`, `requirements.txt` (Jinja2 for AutomateQuery), `question_logger.py`, AutomateQuery/*, index/qdrant/benchmark scripts, unwired resolvers, `test_oracle_connection.py`, `test_real_user_log_fixes.py` (legacy), `test_historical_question_understanding.py` (live Ollama script). Step 1 evaluator change: removed the `!= "QUERY_PLAN_FAILURE"` guard in `_run_one`; every record now stores `raw_model_calls` / `full_*`; new `cause` field persists `exc.__cause__` (pydantic field errors). Checked: raw outputs for the 20 exist nowhere on disk (no logging in extractor/execution; `logs/user_questions.jsonl` predates).
- **Files:** `scripts/evaluate_v1_real_questions.py`, `fix.md`, `fixlog.md` (+ commit 1 files above)
- **Tests:** `test_text_correction`, `test_spacy_nlp`, `test_query_plan_extractor`, `test_query_plan_semantic_validator`, `test_schema_grounding`, `test_grounded_sql_generator`, `test_grounded_sql_validator`, `test_sql_datatype_validator`, `test_nlp_execution`, `test_entity_resolution`, `test_v1_acceptance_matrix` — 252/252; `py_compile` evaluator OK; `git diff --cached --check` clean
