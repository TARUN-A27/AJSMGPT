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
| 3 | Qwen QueryPlan extraction | `query_plan_extractor.py`, `query_plan.py` | ✅ built · ⚠️ 20/47 real questions fail contract | 39 |
| 4 | Semantic validation | `query_plan_semantic_validator.py` | ✅ done | 23 |
| 5 | Deterministic schema grounding | `schema_grounding.py` | ✅ done · 2/47 grounding gaps | 22 |
| 6 | Qwen grounded SQL generation | `grounded_sql_generator.py` | ✅ built · untested by real questions (nothing reaches it) | 15 |
| 7 | Static SQL validation | `grounded_sql_validator.py`, `sql_safety.py`, `sql_datatype_validator.py` | ✅ done | 49 + 17 |
| 8 | Read-only Oracle execution | `nlp_execution.py`, `oracle_client.py` | ✅ built · never run against company server | 22 |
| 9 | Business report | `answer_formatter.py` | ✅ built · unverified on real results | — |
| — | Acceptance matrix | `test_v1_acceptance_matrix.py` | ✅ | 2 (supported accept / unsupported reject) |

Targeted V1 suites: 105/105 (last recorded run).

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
| purchase_orders | purchase | detail, aggregate, ranking | 8 | ✅ supported |
| mrs | mrs | detail, aggregate, ranking | 4 | ✅ supported (no `lookup`) |
| consumption | consumption / issue | detail, aggregate, ranking | 5 | ✅ supported |
| supplier_lookup | purchase, supplier_lookup | lookup | 3 | ✅ supported |
| material_lookup | purchase, material_lookup | lookup | 3 | ✅ supported |
| stock | stock, inventory | — | 0 | ❌ unsupported |
| grn | goods receipt | — | 0 | ❌ unsupported |

Catalog: 5 domains · 24 concepts · 3 relationships.

### Real-question evaluation (47 questions)
```text
PASS_PIPELINE                 0   ← nothing has gone end-to-end yet
QUERY_PLAN_FAILURE           20
UNSUPPORTED_EXPECTED         13   (by design)
ENTITY_RESOLUTION_REJECTION   9
CAPABILITY_FAILURE            3
GROUNDING_FAILURE             2
```
Honest read: **every deterministic layer is built and unit-tested; the LLM layer has not yet produced a single end-to-end pass on real questions.** The blocker is diagnostic — raw Qwen outputs for the 20 failures were never saved (fix.md #1).

### Rough V1 completion
```text
Deterministic layers (2, 4, 5, 7)         ~95%   built, tested, catalog-backed
LLM layers (3, 6)                          ~50%   built, unproven on real questions
Execution + report (8, 9)                  ~60%   built, never run on company Oracle
Real-question pass rate                     0%
Overall V1                                 ~55%
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

## In progress (uncommitted on this branch)
- Entity resolution `app/entity_resolution.py` — **wired into V1**: `nlp_execution.py` calls `resolve_plan_entities(plan, oracle_entity_lookup)` before the execution gate; deterministic exact-match against `SCM.PARTYMASTER` / `INVENTORY.INVITEMS`, fail-closed (0 rows → UNRESOLVED, >1 → AMBIGUOUS). Tests: `test_entity_resolution.py`, resolver-integration tests in `test_nlp_execution.py`.
- Older resolvers `app/entity_resolver.py`, `app/full_value_resolver.py` + `data/entity_index.sqlite3`, `data/full_value_index.sqlite3` — built, **not wired** (post-V1 candidate source, see B below).
- Security fix: `build_bind_parameters` in `nlp_execution.py` now rejects a `QueryFilter` whose concept is in `resolvable_concepts()` — closes the path where a supplier/material value could reach Oracle as a raw filter bind without entity verification. Tests: `test_supplier_filter_bypassing_entity_resolution_is_rejected`, `test_material_filter_bypassing_entity_resolution_is_rejected`.
- Validator fix: `_column_pattern` in `grounded_sql_validator.py` excludes a bind whose name contains the column name (`ITEM_NAME = :ITEM_NAME` false-positive). Test: `test_valid_material_preview_when_bind_name_collides_with_column_name`.
- Evaluator (`scripts/evaluate_v1_real_questions.py`): entity resolution now uses the offline fixture lookup from `test_entity_resolution.py` (zero Oracle calls); classification splits `ENTITY_RESOLUTION_REJECTION` and capability rejections out of `QUERY_PLAN_FAILURE`.
- AutomateQuery review UI templates + router-patch export scripts (tooling, not runtime).
- Catalog / ontology / typo-dictionary updates in `app/resources/`.

---

## V1 — remaining work, in order

### Step 1 — Recover raw QueryPlan outputs
- [ ] Change `scripts/evaluate_v1_real_questions.py` / `app/query_plan_extractor.py` to store the raw Qwen response and the corrected JSON in the result record *before* contract validation rejects it.
- [ ] Re-run the 47-question eval (Ollama, explicit approval).
- [ ] Exit: all 20 `QUERY_PLAN_FAILURE` records have `raw_model_calls` populated.

### Step 2 — Classify the 20 QueryPlan failures
- [ ] Bucket each into: bad prompt · bad contract field · missing ontology term · model can't do it · question genuinely unsupported.
- [ ] Record counts per bucket in `fix.md` #1.
- [ ] Exit: every failure has a bucket and a one-line reason.

### Step 3 — Fix the proven bottleneck only
- [ ] Apply fixes for the largest bucket(s) — prompt wording, contract loosening only where deterministic validation still holds, ontology additions from `nlp_ontology.json`.
- [ ] Add a unit test per fixed pattern in `test_query_plan_extractor.py`.
- [ ] Exit: targeted suites green; QueryPlan failures reduced; no validator loosened.

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

### C. Report layer
- `answer_formatter.py` → structured report: title, filters applied, table, totals, row count, SQL shown on request.
- Export: CSV/XLSX download from `/v1/nlp/execute` result.
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
- 2026-09-21 — progress.md expanded: per-stage V1 status, endpoint/coverage tables, ~55% completion estimate, 9-step remaining plan, post-V1 needs A–I.
