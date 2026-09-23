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

### fix.md #10 (true root cause) + Step 4 model comparison + new finding #12
- **Prompt:** today's session task — Step 4 protocol, run 8b vs 14b using the server for 14b (`ssh -p 5555
  ajsmgpt@103.171.13.142`), fix.md #10 with tests.
- **Done — fix.md #10:** re-investigated before fixing. The originally recorded symptom (a date-filter tie between
  `ORDERDATE`/`ISSUEDATE`) was two steps downstream of the real bug: `"value"` was a bare alias on `purchase_value`
  only, so a **consumption**-domain plan's generic `"value"` measure had exactly one catalog match and grounded
  successfully — `is_grounded=True`, zero ambiguity — to `INVENTORY.PURCHASEORDER.NET` instead of
  `INVENTORY.ISSUE.ISSUEVALUE`. Silent wrong-table grounding, not a rejection. The same asymmetry existed for
  `"rate"`. Fixed with a 2-line catalog change (added the bare aliases to `consumption_value`/`consumption_rate`,
  matching the already-correct `quantity`/`qty` pattern shared by all three domains) — no grounding-code change, so
  no risk to the legitimate cross-table joins (supplier/material lookups) that same scoring code also serves.
  Verified: consumption now grounds correctly for both "value" and "rate"; purchase is unaffected (regression
  test); the original date-tie symptom is also gone as a consequence, not a separate fix.
- **Done — Step 4:** protocol in `docs/STEP4_MODEL_COMPARISON.md`. Server has `qwen3:14b` only (confirmed via a 404
  before adjusting scope rather than pulling an unneeded second `qwen3:8b`); compared fresh 14b (SSH-tunnelled
  server run) against the already-committed 8b result. In-scope (23/47): `PASS_PIPELINE` 2→5, `QUERY_PLAN_FAILURE`
  4→1, `SQL_VALIDATION_FAILURE` 1→0. Decision: 14b adopted as the dev/eval model (fix.md #11); does not change the
  production runtime default, which stays a Step 6 decision on the real server.
- **Found (new, not fixed) — fix.md #12:** checking one Step 4 per-question move surfaced that
  `app/v1_capabilities.py:_family_name` and `app/schema_grounding.py:_domain` both special-case
  `operation=="lookup" and subject in {material,supplier,...}` *before* ever consulting `plan.domain`. Confirmed
  end-to-end with a resolved entity: a plan tagged `domain="grn"` (0-concept, explicitly unsupported family) passes
  capability (`supported=True`) and grounds successfully as a plain `material_lookup`, silently dropping the
  "received" part of the question with no rejection and no low-confidence signal. Not fixed today — the shortcut is
  also load-bearing for genuine identity lookups and the correct fix needs its own scoped task and regression pass
  across supplier_lookup/material_lookup, per CLAUDE.md §11 (no scope expansion mid-task).
- **Files:** `app/resources/business_schema_catalog.json`, `scripts/test_schema_grounding.py`, `docs/STEP4_MODEL_COMPARISON.md`
  (new), `fix.md`, `progress.md`, `CLAUDE.md`, `fixlog.md`. No `app/` grounding/capability code changed (fix.md #12
  is recorded, not patched). No Oracle access; the server was reached only for its Ollama instance via an SSH
  tunnel, never for its filesystem or `.env`.
- **Tests:** 11 core suites **282/282** (`test_schema_grounding` 29, up from 25; others unchanged); `py_compile`
  clean; catalog JSON re-validated; `git diff --check` clean.

### Step 5 — re-run and extend the acceptance matrix
- **Prompt:** "start" (Step 5, next after Step 4).
- **Done:** ran `test_v1_acceptance_matrix.py` — still 15/15 supported + 3/3 rejection cases passing after today's
  fix.md #10 catalog change. Extended it with 2 cases: (1) the real, verbatim `qwen3:14b` plan+SQL for "last
  purchase rate of barcode scanner in 2026" from the Step 4 run (a live proof point, not a hand-idealized one —
  first acceptance case using a bare generic measure concept, "rate", confirming the purchase-domain path fix.md
  #10 says must keep working); (2) a consumption-domain "how much value consumed?" case, exercising the exact
  fix.md #10 bug shape through the full `evaluate_capability → ground_query_plan → validate_grounded_sql` chain
  this file tests (schema_grounding's own unit tests already covered the bug at the grounding level; this pins it
  at the acceptance level too). Both pass immediately, no further code changes.
- **Files:** `scripts/test_v1_acceptance_matrix.py`, `progress.md`, `CLAUDE.md`, `fixlog.md`.
- **Tests:** 11 core suites 282/282 (unchanged test-method count; `test_v1_acceptance_matrix` subtests 15→17
  supported + 3 rejection); `py_compile` OK; `git diff --check` clean.

### fix.md #12 fix + #3 re-diagnosis + freeze-criteria proposal
- **Prompt:** "let's do it then" (continue autonomously on the "what's left" list after the push was denied by the
  harness's own permission classifier).
- **Done — fix.md #12 (fixed):** `app/v1_capabilities.py:_family_name` and `app/schema_grounding.py:_domain` both
  now exclude domains naming a different, explicitly unsupported family (`stock`/`inventory`, `grn`/`goods
  receipt`/`goods receipt note`) from the `operation=lookup` + material/supplier shortcut. Verified directly: the
  three probes from the earlier finding now correctly reject with the real family reason; five legitimate lookup
  shapes (unknown/empty/purchase-tagged/already-lookup-domain) are unaffected — checked explicitly, not just by
  absence of a test failure. One existing regression test
  (`test_capability_supported_plan_is_validated_regardless_of_domain_label`) had pinned the *old* behaviour using
  `domain="grn"` as its example of "domain label doesn't matter"; updated to `domain="unknown"` for the same point,
  with a new explicit assertion that `domain="grn"` is now correctly capability-unsupported. New tests:
  `test_lookup_shortcut_does_not_override_a_different_unsupported_domain`,
  `test_lookup_shortcut_still_applies_when_domain_is_generic` (`test_schema_grounding.py`), a new `RejectionCase`
  (`test_v1_acceptance_matrix.py`, 3→4 rejection cases).
- **Done — fix.md #3 re-diagnosed (not fixed):** checked the live evidence before touching anything. 3 of 4 current
  `CAPABILITY_FAILURE` (mrs) cases are `operation="unknown"` (the model choosing no valid operation at all), not a
  missing `lookup` capability; the 4th (`Mrs rejected reason?`) already has a catalogued concept
  (`mrs_rejection_reason`) reachable via the mrs family's existing `detail` operation. The original fix.md #3 text
  ("add a lookup capability for mrs") would not have fixed 3 of the 4 cases and was arguably wrong for the 4th.
  Corrected the recorded diagnosis; the real fix is a `query_plan_extractor.py` prompt/ontology task needing its
  own measured eval pass (fix.md #6-shaped), not attempted today.
- **Done — freeze criteria drafted:** `docs/V1_FREEZE_CRITERIA.md`, a proposal (not a decision) splitting "how do
  we know V1 is done" into coverage (which families ship in v1.0 — proposes adding stock+GRN, already verified,
  covering 10 of the 24 currently-refused questions) and depth (pass rate on a held-out question set, proposed
  ≥75%, plus a non-negotiable zero-wrong-answers condition). Explicitly awaiting Tarun's sign-off.
- **Attempted, blocked by the harness, not by choice:** `git push -u origin feature/v1-query-execution` — denied by
  the auto-mode permission classifier as an "Out-of-Place Publication." No workaround attempted (would defeat the
  point of the check); reported to Tarun with the exact command to run himself.
- **Files:** `app/v1_capabilities.py`, `app/schema_grounding.py`, `scripts/test_schema_grounding.py`,
  `scripts/test_v1_acceptance_matrix.py`, `scripts/test_query_plan_extractor.py`, `docs/V1_FREEZE_CRITERIA.md`
  (new), `fix.md`, `progress.md`, `CLAUDE.md`, `fixlog.md`. No Oracle access; nothing pushed.
- **Tests:** 11 core suites **284/284** (`test_query_plan_extractor` unchanged count but one test's assertions
  rewritten; `test_schema_grounding` 29→31; `test_v1_acceptance_matrix` rejection cases 3→4); `py_compile` clean;
  `git diff --check` clean.

### Step 6 deployment: read-only account verification, PUBLIC-grant finding
- **Prompt:** DBA account created by Tarun (SQL Developer, `system` connection, 15/15 grants succeeded);
  two rounds of `.env` credential troubleshooting; then "check now" once fixed.
- **Done:** deployed V1 to `/home/ajsmgpt/AJSMGPT_v1` on the server (cloned, venv, deps, spaCy model, 284/284
  offline core suite), beside the untouched legacy `ajsmgpt-api.service`. `check_schema_access.py` — which had
  only ever checked table *visibility*, never the account's actual privileges — extended to check
  `USER_TAB_PRIVS`/`USER_SYS_PRIVS`: confirmed `ajsmgpt_ro` itself is genuinely `SELECT`-only + `CREATE SESSION`-
  only.
- **Found while verifying that, not assumed:** `USER_TAB_PRIVS` doesn't see `GRANT ... TO PUBLIC`. Checked
  directly and found this Oracle instance has **~28,700 pre-existing PUBLIC object grants database-wide, ~26,500
  beyond SELECT** (old cross-database migration tooling, by the look of it — the same
  `MICROSOFTDTPROPERTIES`/`MICROSOFTSEQDTPROPERTIES` pair recurs across dozens of schemas AJSMGPT has never
  referenced). First version of the check printed the raw unscoped list (useless noise at that scale) and
  asserted "does not affect V1" without checking it against the specific tables — corrected: narrowed to a
  per-schema summary for AJSMGPT's 5 schemas, plus a direct check against the 14 tables AJSMGPT actually reads.
  Verdict, checked not assumed: **none of them carry a PUBLIC grant beyond SELECT.** This is a real database-
  hygiene item for whoever owns the instance, entirely pre-existing, entirely outside AJSMGPT's ability or remit
  to fix (it never runs GRANT/REVOKE) — recorded in `docs/ORACLE_READONLY_ACCOUNT.md`, not resolved.
- **Files:** `scripts/check_schema_access.py` (3 commits: `USER_TAB_PRIVS`/`USER_SYS_PRIVS` check, PUBLIC-grants
  report, then scoping it after seeing the real scale), `docs/ORACLE_READONLY_ACCOUNT.md`, `fix.md` (#13, new),
  `progress.md`, `CLAUDE.md`. No Oracle DDL/DML; every query used was SELECT against a read-only dictionary view.
- **Tests:** `py_compile` on every script revision; 11 core suites 284/284 unaffected (deployment/scripting
  only, no `app/` changes this round); server-side offline suite also 284/284 after each pull.

### Task 1 (Step 6 live eval, continued) + Task 2 (stock/GRN catalog) — both complete
- **Prompt:** "continue with task after this also do one big task also / i will be not be available for the
  task / and before starting tell me what is the task" — described both tasks explicitly, user confirmed
  "complete both task" and stepped away; continued unsupervised through 4 live-Oracle eval rounds, a bonus
  business-logic recording (Tarun supplied a real MRS-pending production query mid-turn), and every fix each
  live round surfaced.
- **Task 1 done:** `scripts/evaluate_v1_real_questions_live.py` (new) — the same real V1 pipeline as the offline
  evaluator, entity resolution and execution both taking their real, Oracle-backed defaults (no stubs). Never
  records `rows`/`columns`/`summary` anywhere, only `row_count` (an aggregate integer) — verified after every one
  of the 4 rounds run this session. First-ever live `PASS_PIPELINE` against real Oracle (run 3,
  `how much cost consumed last month?`, `row_count=1`). Full before/after numbers and every fix found: fix.md #13.
- **Task 2 done:** cataloged `stock` (`ITEMSTOCK.STOCK`, aggregate-only by design — the table holds 1-31 rows per
  item, matching the ERP's own `GETTOTALSTOCK`) and `grn` (`GRNQTY`/`PENDING`/`REJQTY`/`GRNDATE`, full
  detail/aggregate/ranking, reusing the already-verified `FK_GRN`/`FK_GRN_SUPCODE`/`FK_ITEMSTOCK` joins — no new
  join was invented). Deliberately excluded: the PO-pending SO/IA/JMD ladder and the MRS-pending anti-join
  (both need a new grounding capability this catalog-only work does not attempt). 10 of the 47-question set's
  24 by-design-refused questions are now genuinely reachable, not just refused.
- **Bonus, recorded not implemented:** Tarun supplied his own production "pending MRS" query mid-session —
  recorded verbatim as a verified business definition (`docs/ORACLE_SCHEMA_STUDY_2026-09-22.md` §6.1, fix.md #4)
  rather than wired in; it needs a `LEFT JOIN ... NVL(OrderNo,0)=0` anti-join pattern V1's grounding has no
  mechanism for today.
- **Every live round found one real, previously-invisible gap — each reproduced by isolated test before being
  fixed, each re-verified after:** (1) the evaluator's own `KNOWN_UNSUPPORTED_DOMAINS` was stale (still called
  stock/grn by-design-refused after they became real families — would have hidden genuine
  `CAPABILITY_FAILURE`s as false "working as intended"); (2) `qwen3:14b` used the bare word `"cost"` for both
  `business_subject` and `measure`, matching nothing; (3) the same model used the bare word `"quantity"` for a
  GRN question, which — before the fix — silently grounded to `INVENTORY.PURCHASEORDER.QTY` with no warning
  (the fix.md #10 bug pattern, now found a 4th time); (4) `business_subject="quantity received"` (reverse word
  order) matched no domain alias. None of these were guessed at — each was confirmed against the actual live
  captured plan before any catalog edit.
- **Files:** `app/resources/business_schema_catalog.json`, `app/resources/v1_query_capabilities.json`,
  `scripts/evaluate_v1_real_questions_live.py` (new), `scripts/evaluate_v1_real_questions.py`,
  `scripts/test_schema_grounding.py`, `scripts/test_query_plan_extractor.py`,
  `scripts/test_query_plan_semantic_validator.py`, `scripts/test_v1_acceptance_matrix.py`, `fix.md`,
  `progress.md`, `CLAUDE.md`, `docs/ORACLE_SCHEMA_STUDY_2026-09-22.md`, `docs/V1_FREEZE_CRITERIA.md`. Every
  Oracle interaction was SELECT via the existing safe-select path with the read-only `ajsmgpt_ro` account; no
  DDL/DML; no row data ever left the server.
- **Tests:** 11 core suites **290/290** (was 284 at the start of this block: `test_schema_grounding` 31→37,
  `test_v1_acceptance_matrix` 17→20 supported cases / 4→3 rejection cases, `test_query_plan_extractor` and
  `test_query_plan_semantic_validator` updated to a genuinely-still-uncatalogued domain, "attendance"); `py_compile`
  clean at every commit; `git diff --check` clean; `git push` succeeded on every commit (the earlier session's
  publish-permission denial did not recur).

### PO-pending ladder + MRS-pending anti-join (fix.md #4), plus their independent review
- **Prompt:** asked what the next big task should be; I recommended the PO-pending SO/IA/JMD approval ladder
  and the MRS-pending anti-join — the two items `docs/V1_FREEZE_CRITERIA.md` had, that same morning, recorded
  as deferred to v1.1+. Flagged that contradiction plainly rather than silently overriding my own doc. User
  confirmed: "yes start that use sub-agents also think and finish."
- **Done:** built two new, genuinely new grounding/validation mechanisms rather than approximating with what
  already existed — value-pinned and value-or-null compound conditions, and a catalog-declared anti-join not
  backed by a database FK (none exists for `MRS_TEMP`→`MRS` in `data/schema_relationships.json`; verified
  instead from the ERP's own business logic — `FUNCTION GETORDERPENDINGSTATUS` for the PO ladder, Tarun's own
  production query for MRS-pending). 6 new catalog concepts: `po_pending_at_so`, `po_pending_at_ia` (deliberately
  pins only 2 of 3 flags — the source doc never restates the third for that row, and asserting it would be an
  unverified inference), `po_pending_at_jmd`, `po_approved`, `po_pending` (any stage, by negation), `mrs_pending`
  (5 pinned flags + a value-or-null flag + the anti-join, all on one concept). Deliberately avoided a bare
  "pending"/"approved" alias on the new concepts — this catalog's alias lookup is first-match-wins with no
  domain scoring (the fix.md #10 bug class), so a shared bare alias between two domains would either misground
  or falsely refuse; used domain-qualified aliases instead and added a test proving no collision.
- **Independent review (fresh agent, adversarial construction against the real validator, not just reasoning)
  found 2 real gaps, both fixed same day:** (1) a duplicate of an already-satisfied compound-condition fragment
  appended as a trailing `OR (<already-true thing>)` was invisible to every check — nothing verified what lay
  outside the tracked required positions, so Oracle's own operator precedence read the whole WHERE as "the real
  condition OR that other thing." Reproduced against the pre-existing `mrs_approved` too, so this predated
  today's work; not a regression from the new mechanisms, but they widened its blast radius. Closed by
  rejecting any WHERE-clause `OR` outside a required OR-combinator gap or a value-or-null clause's own span.
  (2) the anti-join's `NVL(...)=0` check resolved its column via *any* alias of the physical table, so a second,
  wrongly-shaped join (e.g. a plain `JOIN` on a partial key) could supply the alias the NULL check reads from
  while an unrelated, correctly-shaped `LEFT JOIN` under a different alias satisfied the composite-key check —
  passing validation on SQL that never actually used the verified join. Closed by requiring the NULL check to
  use specifically the verified join's own alias. Both had concrete adversarial SQL that passed validation
  before the fix; both now have named regression tests, reproduced first, fixed after.
- **Also:** a docs-sync agent updated `fix.md`/`progress.md`/`docs/V1_FREEZE_CRITERIA.md`/the schema-study doc
  to match (I verified its diffs myself before trusting them, corrected one stale test count and one file
  omission it couldn't have known about from a later small fix). `docs/V1_FREEZE_CRITERIA.md`'s coverage section
  now explains the reprioritization plainly rather than leaving a same-day contradiction. Explicitly not done:
  the offline 47-question eval was not re-run against this (would need Ollama, not asked for); the goods-receipt
  reading of "PO pending" (`GRN` absent / `MRS_TEMP`/`MRS` state, §8 of the schema study) is a different,
  still-open question, noted as such so it isn't mistaken for resolved.
- **Files:** `app/schema_grounding.py`, `app/grounded_sql_validator.py`, `app/grounded_sql_generator.py`,
  `app/sql_datatype_validator.py`, `app/resources/business_schema_catalog.json`,
  `app/resources/v1_query_capabilities.json`, `scripts/test_schema_grounding.py`,
  `scripts/test_grounded_sql_validator.py`, `scripts/test_sql_datatype_validator.py`, `fix.md`, `progress.md`,
  `CLAUDE.md`, `docs/V1_FREEZE_CRITERIA.md`, `docs/ORACLE_SCHEMA_STUDY_2026-09-22.md`. No Oracle/Ollama access;
  every check offline.
- **Tests:** 10 core suites **293/293** (was 289 immediately after the new mechanism, before the review; +4
  from the review's regression tests and one hardening test). `py_compile` clean throughout.

### Re-run the offline 47-question eval after PO/MRS-pending (fix.md #14)
- **Prompt:** "do it then" — confirmed running the offline eval, which needs Ollama, after I asked per
  CLAUDE.md §9. Surfaced first that `qwen3:14b` (the adopted eval model, fix.md #11) isn't actually pulled on
  this machine, only `qwen3:8b` — asked which to use rather than silently picking one or pulling a ~9GB model
  without asking; Tarun chose running with `qwen3:8b` now.
- **Done:** ran `scripts/evaluate_v1_real_questions.py`, diffed against a backed-up copy of the prior results.
  Aggregate classification counts came back **identical** (13/12/10/4/4/2/2) — unlike the stock/GRN catalog
  work, which flipped 10 of 13 `UNSUPPORTED_EXPECTED` questions, today's PO/MRS-pending work flipped none.
  Dug into why rather than reporting a flat "no change": the 2 closest candidate questions ("material hold /
  approval pending at Store officer") get `domain='unknown'` from QueryPlan extraction; a 3rd ("order pending
  material names") gets `domain='purchase'` but the model drops "pending" from entities/filters entirely
  (empty lists) before grounding is ever reached. None of this reflects badly on today's actual catalog/
  validator work — that's independently verified by 20 unit tests and a review — it's a separate, previously
  unmeasured gap in `app/query_plan_extractor.py`, now written up as fix.md #14 rather than left unexplained.
  Also noted, not chased: 2 unrelated questions swapped `PASS_PIPELINE`/`SQL_VALIDATION_FAILURE` between runs
  despite `temperature=0.0` — pre-existing model-serving non-determinism, not caused by this session.
- **Files:** `scripts/v1_real_question_eval_results.json` (regenerated), `fix.md` (#4 updated, #14 new),
  `progress.md`, `CLAUDE.md`. No code changes; no Oracle access; Ollama used with explicit permission asked
  and given for both the run itself and the model choice.
- **Tests:** unaffected (eval script only, no `app/` changes) — 10 core suites still **293/293**, re-confirmed
  after the run.
