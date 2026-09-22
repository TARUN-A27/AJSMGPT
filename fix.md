# AJSMGPT — Fix List

Source: `scripts/v1_real_question_eval_results.json` (47 questions). `UNSUPPORTED_EXPECTED` (13) are by design and not listed.

Status: ⬜ open · 🔄 in progress · ✅ fixed

## 1. ✅ Evaluator does not save raw model output on QueryPlan failure
- **Fixed 2026-09-21:** removed the `!= "QUERY_PLAN_FAILURE"` guard in `_run_one`; added `cause` (= `exc.__cause__`). Re-ran the 20 (Ollama, zero Oracle): 20/20 now have `raw_model_calls` (2 calls each: first attempt + correction). Records tagged `previous_classification`. One flipped to `UNSUPPORTED_EXPECTED` on re-run (`Who is given lowest price in last one year?`) → 19 remain.
- **Headline finding:** the reason text was misleading. Only **2/20** are real contract (pydantic) failures. **17/20** are `QueryPlanSemanticValidationError` — the model returned schema-valid JSON and *our deterministic semantic validator* rejected it. See #6 for the buckets.
- **Cross-cutting:** the correction round is a no-op — `call1 == call2` byte-for-byte in 19/20. The retry uses system prompt `"Return one JSON object only."` instead of `SYSTEM_PROMPT`, and for the date bucket the violation names a dimension the model never emitted, so it cannot comply.

## 6. ✅ QueryPlan failures — classified (Step 2) and fixed (Step 3)
19 × `QUERY_PLAN_FAILURE` after re-run. Counts per bucket; **S** = supported domain (real loss), **U** = grn/stock/attendance/unknown (should have landed in `UNSUPPORTED_EXPECTED` / `CAPABILITY_FAILURE` but the semantic validator runs first, inside `extract_query_plan`).

| # | Bucket | Count | S/U | Root cause | Kind |
|---|---|---|---|---|---|
| 6a | `Date dimension 'date' is not justified by a date range alone.` | 6 | 4 S / 2 U | Model emits `sorting=[date desc]` for "last/latest/today" (correct). `normalize_recency_sorting` **adds** `Dimension('date')` for that sort; the very next rule rejects any date dimension when a date range exists and no grouping was asked. Self-inflicted: normalizer and rule contradict each other. Model cannot fix on retry. | **validator bug** |
| 6b | `Dimension 'X' does not affect grouping or requested output.` | 5 | 2 S / 3 U | Model lists the entity filter a second time as a non-grouping dimension (`entities=[supplier:…]` **and** `dimensions=[supplier, grouping=false]`). | **prompt** (or a deterministic normalizer that drops a dimension whose concept equals an entity concept) |
| 6c | `Unknown domain cannot have high confidence.` | 3 | 1 S / 2 U | Model says `domain=unknown` with confidence 0.8–0.9. The 1 S case (`WHO are the suppliers for "BARCODE CHROMO LABEL"`) is a supported `supplier_lookup` the model failed to recognise. The 2 U are genuinely out of scope and are being rejected as *model failures* instead of routed to unsupported. | 1 **model**; 2 **gate ordering** |
| 6d | `Operation 'unknown' not usable for domain 'mrs'` + due-date clarification | 2 | 2 S | `MRS due …`: model gives `operation=unknown`; validator also asks which date "due" means. Fail-closed clarification — arguably correct behaviour. | **expected** (clarification) — decide whether MRS due-date is a V1 concept |
| 6e | `Model JSON did not satisfy the QueryPlan contract.` (pydantic) | 2 | 1 S / 1 U | Plan has only `entities`, no `business_subject`/measure/dimension → `validate_semantic_content`. S case: `MRS details for MRS number 890330`. | **prompt/contract** |
| 6f | Ranking: no limit + conflicting highest/lowest | 1 | U | Compound question; validator asks for a single ranking. | **expected** |

Totals: S = 10, U = 9. Of the 10 supported-domain losses: 4 validator bug (6a), 2 prompt (6b), 2 clarification (6d), 1 model domain error (6c), 1 prompt/contract (6e).

- **Also observed (feeds #2):** in ≥8 plans the model fills `entities[].concept` with the literal value (`barcode scanner/barcode scanner`, `yarn/yarn`, `keyboard/keyboard`, `mouse/mouse`, `due in 2026/…`). `resolvable_concepts()` will never match these, so even a fixed QueryPlan would then die at entity resolution. The schema/prompt must make `concept ∈ {supplier, supplier_name, supplier_identifier, material, item_identifier}` explicit.
- **Step 3 fixes applied 2026-09-21** (all in `app/query_plan_semantic_validator.py` / `app/query_plan_extractor.py`; no validator rule loosened):
  1. **6a** — `validate_query_plan_semantics` captures `declared_dimensions` after `normalize_temporal_dimensions`; the date-range rule only judges those, not the sort-only `date` dimension `normalize_recency_sorting` injects. A model-declared date dimension with only a date range is still rejected (pinned).
  2. **6b** — new `normalize_entity_dimensions`: drops a non-grouping, non-output dimension whose concept equals an entity concept (lookups exempt). Entity filter untouched.
  3. **Gate ordering** — `_parse_plan` skips semantic validation only when `evaluate_capability(plan).supported` is False (the same decision the execution gate makes; plan unchanged by normalizers). Independent review (Pass 2) caught the first version — `domain not in supported_domains()` — as a BLOCKER: `_family_name` maps any-domain `lookup` + supplier/material subject to a supported family and accepts domain aliases (`purchasing`, `po`), so an unvalidated plan could have reached grounding/SQL. Replaced by the capability decision itself; reviewer's probe shapes are now tests.
  4. **Correction round** — retry uses `SYSTEM_PROMPT` (was `"Return one JSON object only."`), includes the question and an explicit "do not return the previous JSON unchanged".
  5. **Prompt** — always set `business_subject`; `entities[].concept` ∈ exactly `resolvable_concepts()` (test pins prompt tokens == resolver keys); a filtered entity is not also a dimension.
  Tests added: `test_recency_sort_with_date_range_is_allowed`, `test_model_declared_date_dimension_with_only_a_date_range_is_still_rejected`, `test_dimension_that_restates_an_entity_filter_is_dropped`, `test_entity_dimension_is_kept_when_it_is_grouping_or_output`, `test_non_entity_dimension_without_output_is_still_rejected`, `test_lookup_keeps_dimension_that_matches_an_entity_concept`, `test_correction_round_keeps_full_instructions`, `test_unsupported_domain_plan_is_returned_for_the_capability_gate`, `test_capability_supported_plan_is_validated_regardless_of_domain_label`, `test_supported_domain_plan_is_still_semantically_validated`, `test_system_prompt_entity_concepts_match_the_resolver`. Core suites 263/263.
- **Measured, 6a + correction round only** (19 re-run, before items 2/3/5 were loaded): `QUERY_PLAN_FAILURE` 19 → 10; 4 supported-domain 6a cases now pass the QueryPlan stage on the **first** model call (`raw_calls=1`) and stop at entity resolution (offline fixture has no `barcode scanner`/`yarn`/`MRS number` values — expected offline); 5 → `UNSUPPORTED_EXPECTED`.
- **Measured, all fixes (24 re-run = every `QUERY_PLAN_FAILURE` + `ENTITY_RESOLUTION_REJECTION`; Oracle calls 0):**

| Bucket | Before | After | Where they went |
|---|---|---|---|
| 6a date-dimension self-conflict (6) | 6 QPF | 0 QPF | 2 → **PASS_PIPELINE** (MONITOR, BARCODE SCANNER), 1 → GROUNDING (`measure:rate` not catalogued), 3 → ENTITY_RESOLUTION (`yarn`, `MRS Nos`, `printer toner` not in offline fixture / model value) |
| 6b entity restated as dimension (5) | 5 QPF | 1 QPF | 1 → **PASS_PIPELINE** (Keyboard), 3 → UNSUPPORTED_EXPECTED, 1 stays QPF (`Prime compu systems`: confidence 0.5 < 0.65 — model, not validator) |
| 6c unknown domain / high confidence (3) | 3 QPF | 0 QPF | 3 → UNSUPPORTED_EXPECTED (incl. `BARCODE CHROMO LABEL`: model still says domain unknown) |
| 6d MRS due-date (2) + 6e contract (2) + 6f ranking (1) | 5 QPF | 1 QPF | 3 → CAPABILITY_FAILURE (mrs `unknown`/`lookup` op), 1 → UNSUPPORTED_EXPECTED, `MRS number 890330` stays QPF (entity-only plan, no business_subject — prompt not followed) |
| new | — | 1 QPF | `Which supplier is taken highest orders?` now fails on `Sorting field 'orders' is unavailable` — model sorts by a measure it never declared (was hidden behind the entity gate before) |

47-question totals after Step 3: **PASS_PIPELINE 3** (was 0) · UNSUPPORTED_EXPECTED 24 · ENTITY_RESOLUTION_REJECTION 8 · CAPABILITY_FAILURE 6 · QUERY_PLAN_FAILURE 3 (was 20) · GROUNDING_FAILURE 3. All 3 passes have a grounded join, one entity bind, `FETCH FIRST N ROWS ONLY`. Remaining QPF are all model behaviour (low confidence; undeclared sort field; missing business_subject) — genuine Step 4 evidence now, not validator noise.

## 2. 🟡 Entity resolution rejections (master-data scoping closed; concept vocabulary open)
- **Count after Step 3:** 8 × `ENTITY_RESOLUTION_REJECTION`. The prompt vocabulary fix worked: all 8 now use a canonical concept (`material`/`supplier`/`supplier_name`); none use `item`/`vendor` any more. 7 are values absent from the 6-row offline fixture (`mouse`, `dell`, `dell system`, `printer toner`, `yarn`) → recheck on real master data (Step 8). 1 is a model error: `mouse last purchased supplier name?` → `supplier_name: mouse` (mouse is the material).
- **Original count:** 9 (purchase_analytics 7, supplier_purchase 2)
- **Reasons:** `Entity '<concept>' requires verified resolution before execution.` on all 9; two also carry dimension/date-justification rejections.
- **Where:** `app/nlp_execution.py` execution gate. The resolver **is wired**: `_default_resolve_entities` → `app/entity_resolution.py:resolve_plan_entities(plan, oracle_entity_lookup)`. (`entity_resolver.py` / `full_value_resolver.py` are the unwired older ones.) The eval swaps in the offline fixture lookup from `scripts/test_entity_resolution.py`, so no Oracle is touched.
- **Split:**
  - 4 × non-canonical concept from the model — `item` (MONITOR, BARCODE SCANNER, dell system), `vendor` (dell). `resolvable_concepts()` only knows `supplier`, `supplier_name`, `supplier_identifier`, `material`, `item_identifier`, so these fail regardless of data. **Model/ontology issue.**
  - 5 × correct concept, value not in the 6-row offline fixture (`mouse` ×3, `printer toner`, and `supplier` where Qwen used the literal word as the value on a ranking question). Expected offline; recheck on real master data (progress.md Step 8). The `supplier`-as-value case is a model issue.
- **Fix:** for the 4, adjust QueryPlan prompt/ontology so Qwen emits catalog-canonical concept names; add a `test_query_plan_extractor.py` case per pattern. Do not add aliases to `_VERIFIED_SOURCES` to paper over it. Do not resolve entities via the LLM.
- **Depends on:** #1 result (some may reclassify once QueryPlans are correct).
- **Closed 2026-09-22 (P4, commit `7816149`):** resolution now matches the ERP's own definitions instead of the whole party table — supplier sources carry the fixed scope `GOODSTYPECODE = 2` (the `INVENTORY.SUPPLIER` view, study §6.1), and candidates dedupe by **code** not display name, because 407 `ITEM_NAME`s are shared by more than one `ITEM_CODE` in the live master. Those now return AMBIGUOUS with `NAME [CODE] (obsolete)` candidates surfaced in the rejection message instead of silently resolving to one item. This changes offline counts only after Step 6 (real master data).

## 3. ⬜ MRS `lookup` operation unsupported
- **Count:** 3 × `CAPABILITY_FAILURE` (mrs)
- **Reason:** `domain='mrs': Operation 'lookup' is not supported for the V1 mrs family`
- **Where:** `app/v1_capabilities.py`, `app/resources/v1_query_capabilities.json`
- **Fix:** either add a verified `lookup` capability for the mrs family (needs catalog backing) or mark these questions `expected_unsupported`.

## 4. 🟡 Grounding: concept has no verified column (rate/cost closed; order-pending open)
- **Count:** 3 × `GROUNDING_FAILURE` after Step 3 — rate, cost consumed, order pending.
- **Reason:** `No verified V1 column supports this required concept.`
- **Where:** `app/schema_grounding.py`, `app/resources/business_schema_catalog.json`
- **Closed 2026-09-22 (P3, commit `0a11c17`):** `purchase_rate` → `INVENTORY.PURCHASEORDER.RATE` and `consumption_rate` → `INVENTORY.ISSUE.ISSRATE`, both verified in `docs/ORACLE_SCHEMA_STUDY_2026-09-22.md` §6.1 + the live column profile; `cost consumed` / `consumption cost` / `consumed cost` added as aliases of the existing `consumption_value` (`ISSUE.ISSUEVALUE`). Capabilities updated. Tests: `test_purchase_rate_grounds_as_measure`, `test_cost_consumed_grounds_to_issue_value`.
- **Still open — order pending:** the ERP computes PO-pending through the SO/IA/JMD approval ladder (study §6.1), not a single column. Needs value-pinned compound conditions (P6), not a catalog alias. Do not approximate it with `STATUS` — `PURCHASEORDER.STATUS` is constant 0 in the live data.

## 5. ✅ Zero end-to-end passes
- **Closed 2026-09-22.** First passes on the Step 3 run (3), then 2 on the re-run after the Oracle-executability
  fixes — and unlike the first three, both of those are executable on the company database as written. The SQL
  stages are no longer untested by real questions: `SQL_VALIDATION_FAILURE` is now 1 (a model that dropped the
  material filter), which is the gate doing its job.

## 7. ✅ SQL validator accepts aggregate + non-aggregated column without GROUP BY
- **Fixed 2026-09-22:** `_aggregate_grouping_violations` in `grounded_sql_validator.py` (text-level ORA-00937 rule); semantic validator rejects `operation=detail` with an aggregated measure; extractor prompt states it. Tests: `test_rejects_aggregate_beside_ungrouped_column`, `test_detail_operation_rejects_aggregated_measure`.
- **Found:** first real passes, 2026-09-22. `Last purchase qty ... Keyboard` and `Last 5 purchase qty of "BARCODE SCANNER"` produced `SELECT ORDERDATE, SUM(QTY) ... ORDER BY ORDERDATE DESC FETCH FIRST N ROWS ONLY` with no `GROUP BY`. `validate_grounded_sql` accepted it; Oracle will raise ORA-00937. Root cause upstream: Qwen used `aggregation=sum` on `quantity` for a "last N purchase qty" (detail) question — the SQL generator then followed the plan.
- **Where:** `app/grounded_sql_validator.py` (add the deterministic rule: any aggregate function ⇒ every non-aggregated select column must be in GROUP BY), and the QueryPlan/SQL prompts (a recency detail question is `aggregation=none`).
- **Fix order:** validator rule first (fail closed, cheap, catches every future case), then prompt. Would turn these 2 PASS into `SQL_VALIDATION_FAILURE` until the prompt is fixed — that is the correct outcome.

## 8. ✅ V1 SQL was not executable on the company Oracle (11.2 + `VARCHAR2(8)` dates)
- **Found:** 2026-09-22 Oracle schema study, offline — before any live run.
- **Two independent blockers, both in every dated or limited query:**
  1. `FETCH FIRST n ROWS ONLY` (required by the validator, the generator prompt, and `add_execution_probe_limit`) is a syntax error on Oracle 11.2.0.1.
  2. `ORDERDATE` / `MRSDATE` / `DUEDATE` / `ISSUEDATE` are `VARCHAR2(8)` `'YYYYMMDD'` text and the session NLS is `DD-MON-RR`, so V1's Python `date` binds and its required `ADD_MONTHS(TRUNC(SYSDATE), -N)` predicates raise ORA-01861.
- **Fixed 2026-09-22 (P1 + P2, commit `088b908`):** the model may no longer write a row limit of any kind (`FETCH`/`OFFSET`/`ROWNUM` rejected outright); `add_execution_probe_limit` wraps the already-validated SQL with the 11g-safe `SELECT * FROM (...) WHERE ROWNUM <= n`. Every date range — relative and absolute — is now exactly `col >= :date_start AND col < :date_end`, with both bounds computed deterministically in `app/nlp_execution.py` (`date_bounds`, Oracle `ADD_MONTHS` month-end clamping) and bound as `'YYYYMMDD'` strings. `SYSDATE`/`CURRENT_DATE`/`CURRENT_TIMESTAMP`, `BETWEEN` on a date column, and the functions `ADD_MONTHS`/`TRUNC` are all rejected; the new `DATE_TEXT` datatype category rejects a Python `date` bind outright.
- **Known limitation (do not fix by guessing):** `INVENTORY.MRS_TEMP.DUEDATE` holds mixed formats in the live data, so a string comparison misfiles the dirty rows. Requires a data-quality answer from the ERP owners, not a parser in AJSMGPT.

## 9. 🟡 Independent review of P1–P4 (2026-09-22) — fixed vs. recorded

Fresh-context Opus review of `088b908`/`0a11c17`/`7816149`/`8d83c74`, read-only, no Oracle, no model runs.
It confirmed P1's central claim: the string handed to the runner is provably the validated SQL wrapped only by
`SELECT * FROM (...) WHERE ROWNUM <= n`, `_masked_sql` cannot hide the limit check, `_add_months` matches Oracle
`ADD_MONTHS` exactly, absolute bounds are correct half-open, the supplier scope predicate cannot carry model text,
dedupe-by-code cannot turn AMBIGUOUS into RESOLVED, and every catalog change agrees with the real column types.

**Fixed the same day (all reproduced first):**
- `ROUND(SUM(x))` switched the whole ORA-00937 rule off — `_AGGREGATE_CALL_RE` was anchored to the outermost call,
  so fix.md #7 caught the literal reported SQL and nothing one token away from it. Now matches an aggregate anywhere
  in a select item.
- `GROUP BY` was only checked one way (SELECT ⊆ GROUP BY). A model adding `, po.ORDERDATE` produced valid Oracle that
  silently answers a different question — top supplier-*days* reported as top suppliers. Both directions are now required.
- `relative_date_spec` searched for `N <unit>` anywhere in the text: *"the 6 months ending March 2025"* was answered as
  *"the last 6 months"*. The unanchored form is now accepted only when it is the whole text; anything else fails closed.
- `oracle_entity_lookup` projected the identifier column twice for code-shaped sources (`SELECT ITEM_CODE, ITEM_CODE …`),
  which is ORA-00918 inside the `ROWNUM` inline view — every item-code / supplier-code resolution would have failed on
  the live database. The display copy is now aliased.
- The bind-datatype scan never ran on fully qualified columns (`INVENTORY.PURCHASEORDER.ORDERDATE` was consumed as
  schema.table), and `LIKE` was allowed on a `DATE_TEXT` column. Both closed.
- Generator prompt now states the GROUP BY rule; `_TRAILING_ALIAS_RE` no longer mis-reads `PM . PARTYNAME` as an alias.

**Recorded, not fixed (each needs a decision or the server, not a guess):**
- `last 30 days` covers 31 days and `last month` / `last year` are trailing periods, not calendar ones. Deliberate and
  test-pinned, but "purchases last month" probably means September. Business decision — study §8 lists it as open.
- `oracle_entity_lookup` inherits `SQL_MAX_ROWS` (unordered, no truncation detection): at `SQL_MAX_ROWS=1` a duplicated
  item name would resolve to one arbitrary code instead of AMBIGUOUS. Read the server's value before Step 6.
- `HAVING` is swallowed into the GROUP BY clause (false rejection, fail-closed) and the numeric-literal ban does not
  scan `HAVING`.
- `mrs_due_date` can never be chosen as the date filter today (its aliases contain no `"date"`), which is the only
  reason the dirty `MRS_TEMP.DUEDATE` (3 rows in `D/M/YYYY`) does not misfile yet. Adding `"date"` switches it on.
- `resolve_entity` unpacks rows outside the `try` that produces `EntityLookupError` (unreachable with today's lookup).
- `add_oracle_row_limit`'s `SQLSafetyError` is not a `GroundedSqlValidationError` → 500 instead of 422.
- 1 of the 1,645 distinct PO suppliers is not `GOODSTYPECODE = 2` and is now permanently unresolvable.
- AMBIGUOUS candidates put ERP item/party codes in the API response (not in logs).

## 10. ⬜ Cross-domain alias collision: a generic concept grounds to two domains' columns
- **Found:** 2026-09-22 re-run. `how much cost consumed last month?` → `GROUNDING_FAILURE`, but not for the reason
  #4 assumed. The model emits generic concepts (`measure.concept = "value"`, date concept `"date"`), and the catalog
  carries bare aliases on more than one domain: `"value"` on `purchase_value` and `"date"` on both `purchase_date`
  (`PURCHASEORDER.ORDERDATE`) and `consumption_date` (`ISSUE.ISSUEDATE`). The measure pulls `PURCHASEORDER` into the
  selected tables, both date columns then score identically, and grounding reports
  `Multiple equally supported V1 columns match this concept: [INVENTORY.ISSUE.ISSUEDATE, INVENTORY.PURCHASEORDER.ORDERDATE]`
  on a plan whose `domain` is unambiguously `consumption`.
- **Where:** `app/schema_grounding.py` candidate scoring (the tie-break uses selected tables/anchors, never
  `plan.domain`), plus the bare aliases in `app/resources/business_schema_catalog.json`.
- **Fix (not attempted yet — it changes grounding for every family, so it needs its own task):** prefer candidates
  whose concept belongs to the plan's domain before falling back to the table-anchor score, or drop the bare
  `"value"` / `"date"` aliases and require the domain-qualified ones. Do not special-case consumption.
- **Correct as it stands:** failing closed on the ambiguity is right; silently answering with purchase dates would be
  the bug.

## Not fixes (do not do)
- Switching to Qwen3:14b/30B before #1 is classified.
- Wiring RAG/Qdrant into the runtime.
- Rewriting the QueryPlan contract.
- Loosening any validator to make a question pass.
