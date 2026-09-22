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

### Step 1 complete — raw outputs recovered; Step 2 classification
- **Prompt:** "do it then" (finish the Step 1 re-run interrupted by session end).
- **Done:** first driver had finished all 20 but Ollama dropped mid-run: 8 captured, 12 `ENVIRONMENT_FAILURE` (ConnectionError). Re-ran those 12 with a scratch driver (retargeted at `ENVIRONMENT_FAILURE`, kept `previous_classification`). Result: 20/20 have `raw_model_calls` (2 calls each). Oracle `sql_validation_start` count 10 before/after → 0 Oracle calls. Classified all 19 remaining failures into fix.md #6 buckets from the `cause` field + raw JSON.
- **Findings:** (1) only 2/19 are pydantic contract failures; 17/19 are `QueryPlanSemanticValidationError` — evaluator wording "did not satisfy the QueryPlan contract" was hiding this. (2) Largest bucket (6): `normalize_recency_sorting` injects `Dimension('date', grouping=False)` for the model's `sorting=[date desc]`, then the date-dimension rule rejects it because a date range is present — a self-conflict in `query_plan_semantic_validator.py`; the model never emitted that dimension. (3) Correction round is a no-op: `call1 == call2` in 19/20; retry uses system prompt `"Return one JSON object only."` not `SYSTEM_PROMPT`. (4) 9/19 are grn/stock/attendance/unknown questions failing semantics before reaching `evaluate_capability`. (5) Model fills `entities[].concept` with the literal value in ≥8 plans.
- **Files:** `fix.md` (#1 ✅, new #6), `progress.md` (Steps 1–2 ✅, commits table, stage table), `CLAUDE.md` (§5 ✅ marks, §6 counts/note, §4 commit ref), `fixlog.md`, `scripts/v1_real_question_eval_results.json` (20 records replaced). No `app/` changes.
- **Tests:** none (evaluation + docs). Step 3 not started.

### Plugin scope brainstorm (discussion only)
- **Prompt:** using the AJSMGPT scope doc, what custom plugins can be created.
- **Done:** answered in chat, grounded against `app/resources/business_schema_catalog.json` (5 domains: purchase, mrs, consumption, supplier_lookup, material_lookup; 24 concepts; 3 relationships) and `app/resources/v1_query_capabilities.json` (supported: purchase_orders, supplier_lookup, material_lookup, mrs, consumption; unsupported: stock, grn). Key point recorded: in the current code an ERP "plugin" = catalog domain + capability family + concepts — the catalog is already the extension point; no runtime plugin framework needed before V1 freeze. No code changes; §5 order of work unchanged.
- **Files:** `fixlog.md`
- **Tests:** none (discussion only)

### Create the post-execution plugins
- **Prompt:** create the custom plugins and tell me each use and when to use.
- **Done:** `app/plugins.py` — plugin contract (`Plugin`, `PluginOutput`, `REGISTRY`, `run_plugin`, `applicable`) and six plugins over the real `NLPExecuteResponse`: `export` (CSV to `reports/`), `share` (share/cumulative/top-N concentration), `anomaly` (z-score outliers, stdlib `statistics`), `trend` (period-over-period), `compare` (outer-join two results, also aggregate vs aggregate), `explain` (plan/grounding/SQL/limits provenance). Decimal arithmetic (Oracle NUMBER arrives as Decimal-as-string). Measure column = last all-numeric column, label = first other, both overridable. CLI `python -m app.plugins list|run`. Not wired into the router (§2 no new endpoints; §5 V1 not frozen). Skipped by design: `ratio`/price-variance (would assert NET/QTY = unit price — unverified business semantics, §3; wait for a verified rate concept) and saved/scheduled reports (needs Oracle re-execution; wait for Step 9).
- **Files:** `app/plugins.py` (new), `scripts/test_plugins.py` (new), `CLAUDE.md` §4, `progress.md` (Post-V1 C, log), `fixlog.md`
- **Tests:** `scripts/test_plugins.py` 15/15; `py_compile app/plugins.py` OK; `git diff --check` clean; no `app/` V1-chain file changed

## 2026-09-22

### Complete today's session in one go (Step 3 + review + measurement)
- **Prompt:** complete the session in one go; use subagents; say which model; SSH server offered.
- **Models:** implementation Opus 5 medium effort (this session); independent review Pass 2 = fresh-context Opus 5 subagent, findings only. SSH/company server **not used** — Step 6 not reached (CLAUDE.md §5 order).
- **Done:** Step 3 fixes in `app/query_plan_semantic_validator.py` (6a declared-dimension guard; `normalize_entity_dimensions`) and `app/query_plan_extractor.py` (capability-first gate ordering; correction round keeps `SYSTEM_PROMPT` + question; prompt: canonical entity concepts, `business_subject`, entity≠dimension). Review found a BLOCKER in the first gate predicate (`domain not in supported_domains()` wider than `evaluate_capability`) → replaced with `not evaluate_capability(plan).supported`; reviewer probes added as tests; tautological prompt test replaced with a resolver-pinned one. Measured twice with Ollama (0 Oracle calls; trace count 13 = unit-test datatype rejections only): 6a+correction alone 19→10 QPF; all fixes 24 re-run → **PASS_PIPELINE 3**, QPF 3, UNSUPPORTED 24, ENTITY 8, CAPABILITY 6, GROUNDING 3. Found fix.md #7 (validator accepts aggregate without GROUP BY; 2 of 3 passing SQLs would hit ORA-00937). Docs updated. Parallel session added `app/plugins.py` docs to CLAUDE.md/progress.md/fixlog.md during this work — kept.
- **Files:** `app/query_plan_semantic_validator.py`, `app/query_plan_extractor.py`, `scripts/test_query_plan_semantic_validator.py`, `scripts/test_query_plan_extractor.py`, `fix.md`, `progress.md`, `CLAUDE.md`, `fixlog.md`, `scripts/v1_real_question_eval_results.json`
- **Tests:** 11 core suites 263/263 (`test_query_plan_semantic_validator` 30, `test_query_plan_extractor` 50, others unchanged); `git diff --check` clean

### Oracle ERP schema study (read-only) from the company server
- **Prompt:** SSH to the AJSMGPT server, study the Oracle ERP database read-only (5 schemas, hyper-detailed), inspect existing Oracle access; understand the whole database, not only V1's families.
- **Done:** key-auth SSH; three read-only dictionary/profile scripts run with the server venv + sourced `.env` (SELECT on `ALL_*` views, `COUNT(*)`, aggregates; no row data, no DML/DDL/PL-SQL execution; PL/SQL *source* read via `ALL_SOURCE` only). Wrote `docs/ORACLE_SCHEMA_STUDY_2026-09-22.md` (environment/access, schema inventory, core tables, conventions, relationships incl. undeclared ones, verified business definitions from ERP functions/triggers, V1 impact, open questions, full table appendix) and `data/oracle_schema_profile_2026-09-22.json` (all 696 tables: columns, PK/UK/FK/indexes/comments/triggers/row counts; 58 profiled tables; conventions; definitions). Drift vs June metadata: 5 new tables, 42 new columns, 2 new FKs; all V1 catalog columns exist.
- **Findings for V1 (recorded, not fixed):** business dates are `VARCHAR2(8)` `YYYYMMDD` and NLS is `DD-MON-RR` → V1's required `ADD_MONTHS(TRUNC(SYSDATE),-N)`/DATE-bind comparisons will raise ORA-01861; Oracle is 11.2 → `FETCH FIRST` is invalid; `INVENTORY` account has DML grants (read-only is code-enforced only); neither server copy runs V1 (live service = legacy `/ask` from `AJSMGPT_git`, branch `feature/master-purchase-analytics`); supplier = `PARTYMASTER.GOODSTYPECODE = 2`; 407 duplicate item names; `PURCHASEORDER.STATUS` constant 0; `rate`/`cost`/GRN/stock/attendance all have real columns.
- **Files:** `docs/ORACLE_SCHEMA_STUDY_2026-09-22.md` (new), `data/oracle_schema_profile_2026-09-22.json` (new), `fixlog.md`. No `app/` changes; no Oracle writes.
- **Tests:** none (read-only study). Oracle statements: SELECT only.

### Complete the remaining plan: P1–P4 verification, independent review, re-run
- **Prompt:** complete the remaining planning task, tell the progress, then say what is possible at full potential with this data.
- **Models:** implementation + supervision Opus 5 (this session); independent review = fresh-context Opus 5 subagent, findings only, read-only, no Oracle, no model runs.
- **Done (verification step 2):** the worktree's eval results were garbage — the pre-shutdown run had died with 47 × `ReadTimeout` on Ollama (every record `ENVIRONMENT_FAILURE`). Smoke-tested Ollama (`qwen3:8b`, 9.4 s), re-ran the full 47 offline with the fixture-backed resolver and stub runner: **PASS 2 · UNSUPPORTED 23 · ENTITY 10 · CAPABILITY 5 · QPF 4 · GROUNDING 2 · SQL_VALIDATION 1 · ENVIRONMENT 0**, 0 Oracle calls. Count 3 → 2 but both passes are now executable on 11.2 (the old ones were not). Baseline for the comparison taken from `git show HEAD:`.
- **Done (verification step 4):** independent review of `088b908`/`0a11c17`/`7816149`/`8d83c74`. It confirmed P1's core claim (the runner receives the validated SQL wrapped only by `SELECT * FROM (...) WHERE ROWNUM <= n`), `_add_months` = Oracle `ADD_MONTHS`, the supplier scope predicate cannot carry model text, dedupe-by-code only increases ambiguity, and every catalog change matches the real column types. Six findings reproduced and fixed: `ROUND(SUM(x))` defeated the whole ORA-00937 rule (anchored regex); `GROUP BY` was checked one way only, so an extra key silently changed the answer's grain; `relative_date_spec` read "6 months" out of "the 6 months ending March 2025"; `oracle_entity_lookup` projected the identifier twice (ORA-00918 inside the ROWNUM inline view — every code lookup would have failed live); the bind-datatype scan never ran on fully qualified columns; `LIKE` was allowed on `DATE_TEXT`. Eight more recorded in fix.md #9 rather than guessed at.
- **Found (new):** fix.md #10 — `how much cost consumed last month?` fails grounding on a cross-domain alias collision (`"date"` on both `purchase_date` and `consumption_date`), not on a missing cost column. Fail-closed and correct; the fix touches grounding scoring for every family, so it is recorded, not patched here.
- **Files:** `app/grounded_sql_validator.py`, `app/nlp_execution.py`, `app/entity_resolution.py`, `app/sql_datatype_validator.py`, `app/grounded_sql_generator.py`, `scripts/test_grounded_sql_validator.py`, `scripts/test_nlp_execution.py`, `scripts/test_entity_resolution.py`, `scripts/test_sql_datatype_validator.py`, `scripts/v1_real_question_eval_results.json`, `fix.md`, `progress.md`, `CLAUDE.md`, `docs/ORACLE_READONLY_ACCOUNT.md` (new), `docs/ORACLE_SCHEMA_STUDY_2026-09-22.md`, `fixlog.md`
- **Tests:** 11 core suites **278/278** (`test_grounded_sql_validator` 46, `test_nlp_execution` 30, `test_entity_resolution` 23, `test_sql_datatype_validator` 20, others unchanged); `py_compile` on every changed module; `git diff --check` clean. No Oracle, no `unittest discover`.
