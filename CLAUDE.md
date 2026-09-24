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
5. Re-run acceptance                                     ✅ test_v1_acceptance_matrix.py 15→17 cases (real 14b result + fix.md #10 anchor)
6. Connect company Oracle server, validate real results  ✅ fix.md #13 — deployed, ajsmgpt_ro verified SELECT-only,
   4 live-eval rounds against real Oracle, first real PASS_PIPELINE, 4 real gaps found + fixed same day
7. Freeze V1                                             ← next — exit criteria proposed, awaiting sign-off — docs/V1_FREEZE_CRITERIA.md
```
Not now: RAG, Qdrant in runtime, 30B models, QueryPlan rewrite, architecture redesign.

## 6. Current known failures
Detail and fix plan per item: `fix.md`.
47-question real evaluation (`scripts/v1_real_question_eval_results.json`):
```text
UNSUPPORTED_EXPECTED         13   ← by design (attendance, camera_ip, dell system stock*)
ENTITY_RESOLUTION_REJECTION  10   ← correctly reaching this stage now (fix.md #3); some checked against real
                                     Oracle 2026-09-24 and are genuine exact-match refusals (shorthand like
                                     "mouse"/"dell" isn't the real full item/party name), not a data gap --
                                     fuzzy matching is an open product question, fix.md #2
GROUNDING_FAILURE              7   ← catalog gaps + real, diagnosable model output
CAPABILITY_FAILURE             6   ← was 11 -- fix.md #3 (MRS operation-choice) closed 2026-09-24
QUERY_PLAN_FAILURE             6   ← all model behaviour (low confidence, undeclared sort field)
PASS_PIPELINE                  5
SQL_VALIDATION_FAILURE         0
SQL_GENERATION / ENVIRONMENT   0
```
**This table now uses qwen3:14b, not 8b** (see below) — not directly comparable to older 8b-based counts without
accounting for that model change. History: re-run 2026-09-23 offline (8b, stub runner) after the stock/GRN
catalog work (fix.md #13): 10 of 13 remaining `UNSUPPORTED_EXPECTED` moved to genuine failures, no longer refused
by design. Same session, real evaluator against **live Oracle** for the first time (Step 6, fix.md #13):
first-ever live `PASS_PIPELINE`, 4 rounds, 4 real gaps found and fixed same day.

Later the same day: the PO-pending SO/IA/JMD approval ladder and the MRS-pending anti-join (both previously
`UNSUPPORTED_EXPECTED`, fix.md #4) were catalogued and implemented — see `po_pending_at_so/ia/jmd`, `po_approved`,
`po_pending`, and `mrs_pending` in `app/resources/business_schema_catalog.json`. This needed two new grounding/
validation mechanisms (value-pinned and value-or-null compound conditions, and a catalog-declared anti-join not
backed by a database FK) in `app/schema_grounding.py` / `app/grounded_sql_validator.py`. An offline eval re-run
(8b) then showed the aggregate counts unchanged — the real bottleneck was upstream of grounding, in QueryPlan
extraction. Investigating why found a bigger, pre-existing gap (fix.md #14): `resolve_entity`
(`app/entity_resolution.py`) forces *any* entity concept outside the 5-token identity-verified whitelist
(supplier/material) to `UNRESOLVED` — blocking execution — unless the model itself tags it
`status: "not_required"`, and the extractor's prompt never taught it that concept exists. This already silently
blocked `mrs_number` (confirmed on a real question, `"MRS details for MRS number 890330"`), and would have
blocked every compound-condition concept the moment a real question reached one, despite all of them being
correct at the grounding/validation layer. Fixed in `app/query_plan_extractor.py`'s `SYSTEM_PROMPT`. qwen3:8b
(this machine's only local model) could not reliably follow the new concept-naming instruction after two rounds
of refinement; qwen3:14b (reached over an SSH tunnel to the company server, since it isn't pulled locally) did
measurably better. The table above is the 14b + fixed-prompt result: `PASS_PIPELINE` 2→4,
`ENTITY_RESOLUTION_REJECTION` 10→6, `UNSUPPORTED_EXPECTED` 13→12 — not a clean ablation of the prompt fix alone
(14b is also just generally stronger, fix.md #11), but the `mrs_number` case was isolated cleanly: same
question, same model, old prompt still fails the same way, only the new prompt reaches `PASS_PIPELINE`. Full
detail: fix.md #14.

An independent review of that change (fresh-context agent, adversarial construction against the real validator,
not just reasoning) found and confirmed two real §3-relevant gaps, both fixed same day: (1) a duplicate of an
already-satisfied compound-condition fragment appended as `OR (...)` anywhere outside the tracked positions was
invisible to every check — closed by rejecting any WHERE-clause `OR` outside a required OR-combinator gap or a
value-or-null clause's own span; (2) the anti-join's `NVL(...)=0` check could bind to a different, wrongly-joined
alias of the same physical table than the one verified as the correct composite-key `LEFT JOIN` — closed by
requiring the NULL check to use specifically the verified join's own alias. Both had concrete adversarial SQL
that passed validation before the fix; both now have named regression tests.

**2026-09-24:** checked fix.md #2's "recheck on real master data" assumption against real Oracle on the server —
wrong for `dell`/`mouse`/`dell system` (still `UNRESOLVED`; `resolve_entity` is exact-match only by design, real
users type shorthand); `yarn` correctly comes back `AMBIGUOUS` (2 real candidates). Not a bug; recorded as an open
product question (add fuzzy matching?) for Tarun to decide. Then closed fix.md #3: the operations paragraph never
defined what `lookup` means (the model read "asking for one field" as sounding like a lookup) nor that a
status/date/reason question with no unique identifier is still `detail`, so MRS status/date questions fell back
to `lookup` or the literal string `"unknown"`. Fixed `app/query_plan_extractor.py`'s `SYSTEM_PROMPT`; verified
against qwen3:14b iteratively (one round-1 side effect — the model over-generalized `status="not_required"` from
a status entity onto a co-occurring material entity — caught and fixed with a clarifying sentence before
shipping). All 4 target questions now reach `operation: detail`; none are `CAPABILITY_FAILURE` any more, each
failing (when it does) at a specific, later, diagnosable stage instead. Full 47-question re-run, isolating just
this fix: `CAPABILITY_FAILURE` 11→6, `PASS_PIPELINE` 4→5. The table above is the final result of both today's
fixes. Full detail: fix.md #2, #3.

## 7. Test commands
Tests are `unittest` scripts. Run the one for the component you changed:
```bash
python scripts/test_<component>.py
```
Core V1 suites: `test_text_correction`, `test_spacy_nlp`, `test_query_plan_extractor`, `test_query_plan_semantic_validator`, `test_schema_grounding`, `test_grounded_sql_generator`, `test_grounded_sql_validator`, `test_sql_datatype_validator`, `test_nlp_execution`, `test_v1_acceptance_matrix`, `test_entity_resolution`.
Baseline: 11 core suites 318/318 (2026-09-23, after the PO/MRS-pending catalog work, its review, and the fix.md #14 entity-resolution prompt fix). `test_entity_resolution` added to this list — always part of the real V1 chain (§4), just not previously counted here.

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
