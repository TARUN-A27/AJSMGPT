# AJSMGPT — Progress

Goal: user types a business question → report from the Oracle ERP database.
Current phase: **V1** (grounded, validated, read-only pipeline). Branch: `feature/v1-query-execution`.

---

## V1 completion status

### Pipeline stages
| # | Stage | Module | Status | Unit tests |
|---|---|---|---|---|
| 1 | Typo correction | `text_correction.py` | ✅ done | 18 |
| 2 | spaCy NLP signals | `spacy_nlp.py` | ✅ done | 26 |
| 3 | Qwen QueryPlan extraction | `query_plan_extractor.py`, `query_plan.py` | ✅ built · 3/47 real questions still fail here (all model behaviour) | 50 |
| 4 | Semantic validation | `query_plan_semantic_validator.py` | ✅ done · self-conflict + entity-dimension normalizer fixed 2026-09-22 | 30 |
| 5 | Deterministic schema grounding | `schema_grounding.py` | ✅ done · 2/47 grounding gaps | 22 |
| 6 | Qwen grounded SQL generation | `grounded_sql_generator.py` | ✅ built · 3 real questions reach it and pass validation | 15 |
| 7 | Static SQL validation | `grounded_sql_validator.py`, `sql_safety.py`, `sql_datatype_validator.py` | ✅ done · GROUP BY both ways, half-open date binds, no model-written row limit (fix.md #7, #8, #9) | 46 + 20 |
| 8 | Read-only Oracle execution | `nlp_execution.py`, `oracle_client.py` | ✅ built · 11g `ROWNUM` wrapper + `'YYYYMMDD'` date binds; never run against company server | 33 |
| 9 | Business report | `answer_formatter.py` | ✅ built · unverified on real results | — |
| — | Acceptance matrix | `test_v1_acceptance_matrix.py` | ✅ 17 cases + 4 rejections (2026-09-23) | 2 (supported accept / unsupported reject) |

Core V1 suites (10): 293/293 (2026-09-23, after Step 6 live eval, stock/GRN, PO/MRS-pending, and its review — fix.md #4/#13).

### API endpoints (`app/nlp_router.py`)
| Endpoint | Status |
|---|---|
| `POST /v1/nlp/analyze` | ✅ |
| `POST /v1/nlp/understand` | ✅ |
| `POST /v1/nlp/ground` | ✅ |
| `POST /v1/nlp/sql-preview` | ✅ |
| `POST /v1/nlp/execute` | ✅ guarded · not validated against live Oracle |

### Verified business coverage (`app/resources/v1_query_capabilities.json`)
| Family | Domains | Operations | Concepts | Status |
|---|---|---|---|---|
| purchase_orders | purchase | detail, aggregate, ranking | 14 | ✅ supported (2026-09-23 — PO-pending SO/IA/JMD ladder added, fix.md #4) |
| mrs | mrs | detail, aggregate, ranking | 7 | ✅ supported (no `lookup`; 2026-09-23 — MRS-pending anti-join added, fix.md #4) |
| consumption | consumption / issue | detail, aggregate, ranking | 6 | ✅ supported |
| supplier_lookup | purchase, supplier_lookup | lookup | 3 | ✅ supported |
| material_lookup | purchase, material_lookup | lookup | 3 | ✅ supported |
| stock | stock, inventory | aggregate | 3 | ✅ supported (2026-09-23, aggregate-only by design — fix.md #13) |
| grn | goods receipt | detail, aggregate, ranking | 9 | ✅ supported (2026-09-23 — fix.md #13) |

Catalog: 7 domains · 37 concepts · 6 relationships (stock + grn added 2026-09-23 from verified schema columns,
fix.md #13; PO-pending + MRS-pending added 2026-09-23, fix.md #4).

### Real-question evaluation (47 questions)
```text
                              2026-09-22 (a)   2026-09-22 (b)
PASS_PIPELINE                        3               2
UNSUPPORTED_EXPECTED                24              23
ENTITY_RESOLUTION_REJECTION          8              10
CAPABILITY_FAILURE                   6               5
QUERY_PLAN_FAILURE                   3               4
GROUNDING_FAILURE                    3               2
SQL_VALIDATION_FAILURE               0               1
SQL_GENERATION / ENVIRONMENT         0               0
```
(a) = after Step 3; (b) = after the Oracle-executability fixes (3b, P1–P4). Both offline, stub runner, 0 Oracle calls.

Honest read: the count went 3 → 2 and the quality went up. In (a), 2 of the 3 passing SQLs used `SUM(QTY)` beside
`ORDERDATE` with no `GROUP BY` (ORA-00937) and all 3 used `FETCH FIRST` with DATE binds — none of them would have
returned a row on the company database. In (b) both passes are executable as written: `ROWNUM` wrapper applied by
code, `'YYYYMMDD'` string binds, entity bound as one equality. The lost pass is `Last purchase qty ... Keyboard`,
where the model dropped the material filter entirely — the validator caught it, which is the correct outcome.
`rate` no longer fails at grounding (P3); the remaining grounding failures are order-pending (P6) and a
cross-domain date-alias collision (fix.md #10). Model behaviour is now the dominant failure mode, which is exactly
what Step 4 (8b vs 14b) is for.

### Rough V1 completion
```text
Deterministic layers (2, 4, 5, 7)         ~97%   #4, #7, #10, #12, #13 closed; fix.md #2/#3 remain (need real
                                                  data or a scoped prompt task, not quick fixes)
LLM layers (3, 6)                          ~75%   Step 4 done: 14b lifts in-scope PASS_PIPELINE 2→5, QPF 4→1
Execution + report (8, 9)                  ~80%   Step 6 done: real Oracle, first live PASS_PIPELINE (fix.md #13)
Coverage                                   7/7    stock + grn added 2026-09-23 (10 of 24 previously-refused
                                                  questions now genuinely reachable, not just refused)
Live-question eval (14b, real Oracle)     1/47   first pass ever against real Oracle; 4 rounds, 4 real gaps
                                                  found and fixed same day (fix.md #13) -- see the eval detail
Overall V1                                 ~85%   fix.md #4 (PO-pending, MRS-pending) fully closed 2026-09-23 --
                                                  coverage within existing families grew; number not re-derived
                                                  pending the eval re-run; remaining: bigger question bank,
                                                  freeze sign-off
```

---

## Done (commits)
| Commit | Milestone |
|---|---|
| 11ebc2e | Structured QueryPlan contract |
| 9e90e22 | Dynamic query plan extraction (Qwen) |
| beb4a34 | spaCy NLP analysis API |
| 72789cc / eb70bd2 | Safe NLP understanding pipeline stabilized |
| d8ed293 | Deterministic schema grounding |
| 0ee16fd | Grounded NLP pipeline |
| 1495b00 | Validated grounded SQL preview |
| f7db1c2 | Guarded V1 query execution |
| ab77443 | Entity resolution wired, QueryFilter bypass gate, validator false-positive fixes, MRS compound conditions |
| 2448e75 | Evaluation harness (offline resolver, stage classification, raw model output), tracking docs, harness policy |
| (Step 3) | Semantic-validator self-conflict, entity-dimension normalizer, capability-first gate ordering, correction round, entity-concept prompt — first 3 end-to-end passes |

## Committed 2026-09-21 as `ab77443` (V1 hardening) + `2448e75` (evaluator, docs)
- Entity resolution `app/entity_resolution.py` — **wired into V1**: `nlp_execution.py` calls `resolve_plan_entities(plan, oracle_entity_lookup)` before the execution gate; deterministic exact-match against `SCM.PARTYMASTER` / `INVENTORY.INVITEMS`, fail-closed (0 rows → UNRESOLVED, >1 → AMBIGUOUS). Tests: `test_entity_resolution.py`, resolver-integration tests in `test_nlp_execution.py`.
- Older resolvers `app/entity_resolver.py`, `app/full_value_resolver.py` + `data/entity_index.sqlite3`, `data/full_value_index.sqlite3` — built, **not wired** (post-V1 candidate source, see B below).
- Security fix: `build_bind_parameters` in `nlp_execution.py` now rejects a `QueryFilter` whose concept is in `resolvable_concepts()` — closes the path where a supplier/material value could reach Oracle as a raw filter bind without entity verification. Tests: `test_supplier_filter_bypassing_entity_resolution_is_rejected`, `test_material_filter_bypassing_entity_resolution_is_rejected`.
- Validator fix: `_column_pattern` in `grounded_sql_validator.py` excludes a bind whose name contains the column name (`ITEM_NAME = :ITEM_NAME` false-positive). Test: `test_valid_material_preview_when_bind_name_collides_with_column_name`.
- Evaluator (`scripts/evaluate_v1_real_questions.py`): entity resolution now uses the offline fixture lookup from `test_entity_resolution.py` (zero Oracle calls); classification splits `ENTITY_RESOLUTION_REJECTION` and capability rejections out of `QUERY_PLAN_FAILURE`.
- AutomateQuery review UI templates + router-patch export scripts (tooling, not runtime).
- Catalog / ontology / typo-dictionary updates in `app/resources/`.

---

## V1 — remaining work, in order

### Step 1 — Recover raw QueryPlan outputs ✅ 2026-09-21
- [x] Evaluator now stores `raw_model_calls` for every record + `cause` (`exc.__cause__`). Evaluator-only change.
- [x] Re-ran only the 20 `QUERY_PLAN_FAILURE` (Ollama, approved; Oracle calls = 0). Ollama dropped mid-run once; 12 retried.
- [x] Exit met: 20/20 have `raw_model_calls`. 1 flipped to `UNSUPPORTED_EXPECTED` → 19 remain.

### Step 2 — Classify the 19 QueryPlan failures ✅ 2026-09-21
- [x] Buckets recorded in `fix.md` #6: 6a validator self-conflict (6) · 6b entity duplicated as dimension (5) · 6c unknown-domain/high-confidence (3) · 6d MRS due-date clarification (2) · 6e contract: entity-only plan (2) · 6f conflicting ranking (1).
- [x] Only 2/19 are contract failures; 17/19 are our semantic validator rejecting schema-valid JSON. 9/19 are unsupported-domain questions that never reached the capability gate.
- [x] Correction round is a no-op (19/20 identical retries) — retry drops `SYSTEM_PROMPT`.

### Step 3 — Fix the proven bottleneck only ✅ 2026-09-22
- [x] 6a normalizer/rule agreement · entity-dimension normalizer · gate ordering (capability decision, after review) · correction round keeps `SYSTEM_PROMPT` · prompt: canonical entity concepts (pinned to `resolvable_concepts()`), always `business_subject`, entity ≠ dimension.
- [x] Independent review (fresh Opus 5 session): BLOCKER on first gate-ordering predicate fixed; reviewer's probe shapes are tests.
- [x] 11 new tests; core suites 263/263. No validator rule loosened.
- [x] Exit met: QUERY_PLAN_FAILURE 20 → 3; PASS_PIPELINE 0 → 3.
- [ ] Follow-up found by the first passes: fix.md #7 (aggregate without GROUP BY) — do before Step 5.

### Step 4 — Entity resolution (9 rejections)
The deterministic resolver is already wired (`app/entity_resolution.py`, fail-closed, no LLM resolution). The 9 rejections split two ways:
- [ ] 4 × model used a non-canonical concept (`item`, `vendor`) that `resolvable_concepts()` never accepts — fails against real Oracle too. Fix in the QueryPlan prompt/ontology so Qwen emits `material` / `supplier`; unit test per pattern in `test_query_plan_extractor.py`. (Overlaps Step 3.)
- [ ] 5 × correct concept, value absent from the offline 6-row eval fixture (`mouse`, `printer toner`, `dell system`, …) — expected offline; re-check against real master data in Step 8. One (`Which supplier is taken highest orders?`) extracted the literal word `supplier` as an entity value on a ranking question → model issue, goes to Step 2's buckets.
- [ ] Exit: 0 unexplained `ENTITY_RESOLUTION_REJECTION`.

### Step 5 — Capability + grounding gaps
- [ ] MRS `lookup` (3): add verified capability or mark expected-unsupported.
- [ ] 2 grounding gaps: identify concept; add catalog column only if verified against `data/multi_schema_metadata.json`.
- [ ] Exit: 0 unexplained `CAPABILITY_FAILURE` / `GROUNDING_FAILURE`.

### Step 6 — Controlled Qwen3:8b vs Qwen3:14b
- [ ] Same 47 questions, same prompts, same catalog, same evaluator.
- [ ] Compare: QueryPlan contract pass rate · SQL validation pass rate · latency · tokens.
- [ ] Exit: runtime model chosen on evidence; decision recorded in `docs/`.

### Step 7 — First end-to-end passes
- [ ] Target `PASS_PIPELINE > 0`, then climbing; SQL generation/validation stages finally exercised by real questions.
- [ ] Any `SQL_VALIDATION_FAILURE` that appears → fix generator prompt, never the validator.

### Step 8 — Company Oracle connection
- [ ] Connect via `app/oracle_client.py` on the company server (not laptop).
- [ ] Verify read-only session; verify statement timeout and row limit in `nlp_execution.py`.
- [ ] Run supported questions; compare results against known-correct manual SQL (`AJSMquery.sql`).
- [ ] Validate `answer_formatter.py` output on real rows (never paste rows into chat/logs).

### Step 9 — Freeze V1
- [ ] Targeted suites green; acceptance matrix green; eval has zero unexplained failures.
- [ ] Tag `v1.0`; write `docs/V1_FREEZE.md` (what is supported, what is not, known limits).
- [ ] Merge `feature/v1-query-execution` → `main`.

---

## Post-V1 — future needs (hyper-detailed, not started)

### A. Coverage expansion
- **stock / inventory family** — currently 0 concepts. Needs verified stock tables, on-hand vs. reserved semantics, location/warehouse dimension, as-of-date handling.
- **GRN family** — 0 concepts. Needs GRN header/line tables, link to PO lines, received vs. accepted vs. rejected quantities.
- **MRS `lookup`** — if not done in V1.
- **Cross-family questions** — "PO vs GRN vs consumption for material X" needs verified multi-family joins; catalog `relationships` currently has only 3.
- **Time semantics** — fiscal year/quarter, "last month" vs. "last 30 days", Oracle `DATE` vs. `TIMESTAMP` columns per table; `date_filter_engine.py` exists but is not in V1 chain.
- **Attendance / camera_ip** — appear in real questions, no domain; decide in-scope or permanently unsupported.

### B. Entity and value resolution (RAG / Qdrant)
- Wire `full_value_resolver.py` + Qdrant (`scripts/embed_full_value_index_to_qdrant.py`, `search_full_value_qdrant.py`) as a **deterministic candidate source** only — LLM never picks the final value; resolver returns verified candidates, user confirms ambiguities.
- Index refresh job for `data/entity_index.sqlite3` / `full_value_index.sqlite3` from Oracle master tables (read-only, scheduled on the server).
- Ambiguity UX: when >1 candidate, return a clarification instead of guessing.

### B2. Conversation memory (multi-turn context) — idea + proposed design, 2026-09-23
Today every `/v1/nlp/*` call is stateless: one question, one grounded answer, nothing carried forward. Tarun's
example: Q1 "keyboard stock", Q2 "when will it reach" — Q2 has no subject at all without Q1's context. Recorded
here, not started — a different shape than V1's current single-question API.

**Proposed approach (Tarun, 2026-09-23): hybrid NLP + Qwen, not Qwen alone.** This is a natural extension of
V1's existing shape (`text correction → spaCy NLP signals → Qwen QueryPlan` is already a hybrid pipeline), not
a new architecture:
1. **Deterministic reference check (new, small):** before calling Qwen, a cheap check — does the current
   question stand alone (has its own subject/entity), or does it look like a follow-up (bare pronoun "it"/"that",
   or a business_subject/entity-free plan on the first extraction pass)? spaCy already gives most of this for
   free (dependency parse flags a pronoun with no local antecedent).
2. **Memory lookup (new, small):** if it looks like a follow-up, pull the last stored QueryPlan for this
   session (business_subject, entities, domain).
3. **Enrich, don't override, the Qwen call:** feed Qwen the resolved context as an explicit note (e.g. "the
   previous question concerned material 'KEYBOARD', domain 'stock'"), not by silently rewriting the question
   text. Qwen still builds the QueryPlan; it just isn't guessing what "it" means.
4. **No shortcut on verification:** the resulting plan — carried-forward entity included — goes through
   the *exact same* semantic validation, grounding, and fail-closed `resolve_plan_entities` gate as a fresh
   plan. Memory supplies a candidate, never a verified fact; a stale resolved entity from 10 turns ago is not
   trusted forever without re-verification.

This keeps the core principle intact ("Qwen proposes, deterministic code verifies") — memory only changes what
Qwen is given to propose from, not what gets verified or how.

- **Still open, not decided:** in-memory per-session dict (simple, lost on restart) vs. a real store; how many
  turns of history; whether this is a new field on the existing endpoints (a `session_id` param) or needs a new
  endpoint (CLAUDE.md §2 currently says "do not add endpoints" — would need revisiting deliberately).
- **Not started.** Post-V1 (after freeze) — see `docs/V1_FREEZE_CRITERIA.md`.
- **Not started.** Do not build this piece by piece alongside other V1 work; it changes the interaction model and
  deserves its own scoped task once V1 is frozen.

### C. Report layer
- `answer_formatter.py` → structured report: title, filters applied, table, totals, row count, SQL shown on request.
- Export: CSV/XLSX download from `/v1/nlp/execute` result. ⏳ CSV exists unwired: `app/plugins.py` `export` (2026-09-22).
- Post-execution plugins (`app/plugins.py`, unwired, 2026-09-22): `export`, `share`, `anomaly`, `trend`, `compare`, `explain` — deterministic processors over `NLPExecuteResponse`; CLI `python -m app.plugins`; tests `scripts/test_plugins.py` 15/15. Wiring (one optional request field, no new endpoint) waits for Step 9.
- Business templates (`business_template_engine.py`, `data/business_query_templates.json`) for recurring reports — deterministic, no LLM.
- Charts only after tabular output is trusted.

### D. Runtime hardening
- Per-query statement timeout, max rows, max joins, max result bytes — enforced in `nlp_execution.py`, configurable via env.
- Query cost guard: reject full-table scans on large tables (needs verified table size metadata).
- Caching of identical validated SQL for N minutes.
- Rate limiting per user.
- Structured logging (`question_logger.py`, `backend_logger.py`): question, plan, SQL, validation verdict, timing — never result rows.

### E. Evaluation and quality
- Grow the question bank from `AutomateQuery` review UI; every real user question → `data/evaluation_questions.json` with expected classification.
- Golden SQL set: for each supported question, a human-verified SQL + expected row count; eval compares generated SQL results against golden.
- Regression gate: eval must not drop pass rate on any commit to `main`.
- Model regression: re-run eval when Qwen version or prompt changes; store per-run results with model tag.
- Evaluator always stores raw model I/O (fix.md #1 made permanent).

### F. Model and prompt management
- Prompts versioned in repo, not inline strings; prompt change = commit + eval run.
- Qwen version pinned in config; upgrade only via eval comparison.
- Fallback policy when Ollama is down: fail closed with clear error, no legacy `/ask` fallback.

### G. Legacy retirement
- Migrate any `/ask` behaviour still relied on by users into V1 families.
- Then delete `/ask`, `query_engine.py`, `purchase_analytics_router.py`, `sql_generator_v2.py`, `schema_search.py` and their tests in one explicit task.

### H. Deployment and operations
- Server deployment on company infrastructure: uvicorn behind reverse proxy, read-only Oracle account, Ollama co-located.
- Health endpoint: Oracle reachable · Ollama reachable · catalog version.
- Auth: company SSO or at minimum per-user tokens; audit log of who asked what.
- Backup of catalog/index files; catalog changes go through PR review.

### I. Documentation
- `docs/V1_FREEZE.md`, `docs/CAPABILITIES.md` (user-facing: what you can ask), `docs/ADDING_A_FAMILY.md` (how to verify and add a new business family), ADRs for model choice and resolver design.

---

## Deferred until after V1 freeze
RAG / Qdrant in runtime, 30B models, QueryPlan rewrite, architecture redesign, everything in "Post-V1" above.

## Log
- 2026-09-21 — CLAUDE.md rewritten; harness policy saved to `docs/`; progress.md / fix.md / fixlog.md created.
- 2026-09-22 — Step 3 done: QueryPlan failures 20 → 3, first 3 PASS_PIPELINE; fix.md #7 opened (aggregate/GROUP BY validator gap).
- 2026-09-22 — `app/plugins.py` added (6 post-execution plugins, unwired, tests 15/15); recorded under Post-V1 C.
- 2026-09-21 — progress.md expanded: per-stage V1 status, endpoint/coverage tables, ~55% completion estimate, 9-step remaining plan, post-V1 needs A–I.
- 2026-09-23 — fix.md #10 closed (root cause was silent cross-domain measure mis-grounding, not just the date tie
  it surfaced as); Step 4 done (14b adopted as dev/eval model, `docs/STEP4_MODEL_COMPARISON.md`); fix.md #12 opened
  (capability/grounding lookup shortcut discards `plan.domain`).
- 2026-09-23 — Step 5 done: acceptance matrix grown 15→17 cases with the real 14b-produced plan/SQL for a
  purchase-rate question (Step 4 evidence, pinned verbatim) and a consumption-domain regression anchor for fix.md
  #10 (generic "value" alias). All 17 + 3 rejection cases pass; 282/282 core suites unchanged.
- 2026-09-23 — Step 6 deployment done: `ajsmgpt_ro` read-only account created and verified genuinely SELECT-only
  (`scripts/check_schema_access.py`, extended to actually check privileges instead of just table visibility);
  V1 deployed to `/home/ajsmgpt/AJSMGPT_v1` beside the legacy service, 284/284 offline. Found and recorded (not
  fixed, out of AJSMGPT's control): this Oracle instance has ~28,700 pre-existing PUBLIC object grants
  database-wide, ~26,500 beyond SELECT — confirmed none land on AJSMGPT's own 14 tables. Live-question eval
  against real Oracle is next.
- 2026-09-23 — fix.md #4 fully closed: PO-pending SO/IA/JMD approval ladder (5 new catalog concepts —
  `po_pending_at_so`, `po_pending_at_ia`, `po_pending_at_jmd`, `po_approved`, `po_pending`) and MRS-pending
  (`mrs_pending`) implemented, pulled forward from the v1.1+ deferral in `docs/V1_FREEZE_CRITERIA.md` at Tarun's
  explicit direction. Two new grounding mechanisms: value-pinned compound conditions + a value-or-null variant,
  and a new catalog-declared anti-join primitive (verified from ERP business logic, no schema FK backs it).
  `app/schema_grounding.py`, `app/grounded_sql_validator.py`, `app/grounded_sql_generator.py`,
  `app/sql_datatype_validator.py`, `app/resources/business_schema_catalog.json`,
  `app/resources/v1_query_capabilities.json` changed. New tests: `scripts/test_schema_grounding.py` (+6),
  `scripts/test_grounded_sql_validator.py` (`PoOrderPendingLadderTests`, `MrsPendingAntiJoinTests`, +13),
  `scripts/test_sql_datatype_validator.py` (+1). An independent review the same day found and fixed 2 real gaps
  in the shared validator logic (both regression-tested, 3 new tests): a duplicate of an already-satisfied
  compound-condition fragment appended as a trailing `OR (...)` was invisible to every check (predated this
  session's work — also reproduced against the pre-existing `mrs_approved`); the anti-join's `NVL(...)=0` check
  could bind to a different, wrongly-joined alias of the same physical table than the one verified as the
  correct `LEFT JOIN`. Full detail: fix.md #4. 293/293 across the 10 core V1 suites.
- 2026-09-23 — offline 47-question eval re-run (qwen3:8b, requested by Tarun) after the PO/MRS-pending work
  above: aggregate counts **unchanged** (13/12/10/4/4/2/2) — unlike the stock/GRN flip (10 of 13), none of
  today's `UNSUPPORTED_EXPECTED` questions moved. Root cause is upstream of grounding: QueryPlan extraction
  either tags the closest candidate questions `domain='unknown'`, or drops "pending" from entities/filters
  entirely before grounding is ever reached. Not a defect in today's catalog/validator work, which 20 unit tests
  and an independent review already verified correct — it's an unproven, separate model-behaviour gap in
  `app/query_plan_extractor.py`. Full detail: fix.md #14.
