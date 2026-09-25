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

## 2. ✅ Entity resolution rejections (master-data scoping closed; concept vocabulary open; fuzzy fallback added 2026-09-24)
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
- **Checked against real Oracle 2026-09-24 (on the server, via `oracle_entity_lookup`; only status checked, never
  the actual row values, per §3).** The "recheck on real master data" assumption above was wrong for 3 of the 4
  values still checkable this way: `supplier:"dell"`, `material:"mouse"`, `material:"dell system"` all come back
  **`UNRESOLVED`** against real data too, not just the small offline fixture. Root cause is not a data gap — it's
  `resolve_entity`'s own documented design: exact case/whitespace-normalized match only, no fuzzy/partial/LIKE
  matching, no edit distance (see the module docstring). Real users type shorthand ("mouse", "dell") while the
  real `ITEM_NAME`/`PARTYNAME` rows are almost certainly longer, more specific strings — an exact match correctly
  finds nothing. `material:"yarn"` returns **`AMBIGUOUS`** (2 real candidates) — working exactly as designed, not
  a bug. **Open product question, not a bug to silently fix:** should V1 widen this to a `LIKE '%value%'` search
  that returns multiple candidates as `AMBIGUOUS` (still never auto-picks one) when an exact match finds nothing?
  That's a deliberate change to an intentionally-designed "no fuzzy matching" module (see its own docstring
  rationale) — needs Tarun's sign-off, not a unilateral fix. Until decided, this stays a correct, working, but
  user-unfriendly refusal — acceptable under the freeze criteria's "a refusal is fine" rule, not a wrong answer.
- **Closed 2026-09-24 — Tarun signed off ("do the fuzzy matching thing").** Added exactly the design flagged
  above, nothing more: `resolve_entity` now takes an optional `fuzzy_lookup`, tried only when the exact match
  finds zero rows **and** the source is text-shaped (a code is either right or wrong — no shorthand version of
  one exists, so codes never get a fuzzy fallback). The fallback still never produces `RESOLVED` by itself, by
  design: one partial match stays `UNRESOLVED`, just with that match surfaced in `candidates` as a hint instead
  of a dead end; two or more become `AMBIGUOUS`, identical to two exact matches. New `oracle_entity_lookup_fuzzy`
  in `app/entity_resolution.py`: same bind-parameterized shape as the exact lookup, `LIKE '%' || :value || '%'
  ESCAPE '\'` with `%`/`_`/`\` in the searched text escaped first (so a real name containing those characters
  isn't misread as wildcards), capped at 10 candidates in Python (a "did you mean" list only makes sense short;
  `run_safe_select`'s own row cap is 100). Wired as the production default in
  `app/nlp_execution.py:_default_resolve_entities`. Also had to extend the rejection-message builder there — it
  previously only appended `Candidates: ...` for `AMBIGUOUS`, so a single fuzzy hit on an `UNRESOLVED` entity
  would have been silently dropped; now any candidates present are shown regardless of status.
- **Verified against real Oracle on the server, not just mocks** (the SQL shape — `LIKE`/`ESCAPE`/string
  concatenation — was new and had never actually run against the live database). Re-checked the exact same 4
  values confirmed `UNRESOLVED`/`AMBIGUOUS` on 2026-09-24 above, this time through the fuzzy-enabled path; never
  printed candidate names, only structural facts (§3): `supplier:"dell"` → still 0 candidates, genuinely no
  registered supplier name contains "dell" — a correct "not found," not a bug in the fallback.
  `material:"mouse"` → 6 real candidates, `material:"dell system"` → 2, both sets programmatically confirmed
  (by substring-containment check, not by reading them) to actually contain the searched text. `material:"yarn"`
  unchanged (still resolves via the exact match alone, confirming the fuzzy gate correctly never fires when it
  isn't needed). Two of the three previously-dead refusals now surface real, verified-correct "did you mean"
  candidates.
- **Files:** `app/entity_resolution.py`, `app/nlp_execution.py`, `scripts/test_entity_resolution.py`,
  `scripts/test_nlp_execution.py`.
- **Tests:** 7 new tests in `test_entity_resolution.py` (single-hit hint, multi-hit ambiguous, no-hit, never for a
  code-shaped source, never invoked once the exact match already succeeded, skipped entirely when no
  `fuzzy_lookup` is passed, and `oracle_entity_lookup_fuzzy`'s own escaping/capping), 1 new test in
  `test_nlp_execution.py` (the rejection message names a fuzzy hint). Every existing test still passes unchanged
  (`fuzzy_lookup` defaults to `None`, so every 2-argument call site keeps today's exact-match-only behaviour with
  no code changes) — 11 core suites **326/326**.

## 3. ✅ MRS `lookup`/`unknown` operation — re-diagnosed 2026-09-23, fixed 2026-09-24
- **Count (14b re-run):** 4 × `CAPABILITY_FAILURE`, all `domain=mrs`: `Mrs rejected reason?` (`operation=lookup`),
  `approved MRS for keyboard` / `MRS due for keyboard in 2026` / `MRS due in 2026` (`operation=unknown`, literally
  the string "unknown", not a missing enum value).
- **The original framing ("add a verified `lookup` capability for mrs") is not the right fix.** Only 1 of the 4
  cases even uses `operation=lookup`; the other 3 are the model failing to choose *any* of `detail`/`aggregate`/
  `ranking` (the mrs family's actual supported operations) and falling back to the literal word `"unknown"`. And
  for the one `lookup` case, `mrs_rejection_reason` is already a catalogued, verified concept
  (`app/resources/business_schema_catalog.json`) — the mrs family already supports `detail`, which is the correct
  operation for "show me this field for this MRS", not a new `lookup` capability. Adding `lookup` to the mrs family
  would not fix questions 2–4, and is arguably the wrong operation even for question 1.
- **Real fix is model/prompt (query_plan_extractor.py's operation ontology), same category as fix.md #6** — needs
  its own measured before/after eval pass like Step 3, not a same-session patch. Not attempted today.
- **Where:** `app/query_plan_extractor.py` (`SYSTEM_PROMPT`'s operation guidance), not `v1_capabilities.py`.
- **Root cause, found 2026-09-24 by reading the prompt, not guessing:** `SYSTEM_PROMPT` presented `lookup` as a
  flat item in the operations enum with no definition beyond the word itself. The model reasonably read "asking
  for one field of a record" (a rejection reason, a due date) as sounding like a "lookup" in the everyday English
  sense — but `lookup` is a narrow, specific term here (identifying a supplier/material's identity), never valid
  for `mrs`'s actual operations (`detail`/`aggregate`/`ranking`). For the other 3 questions, nothing told the
  model that a status/date/reason question with no single uniquely-identifying value is still `detail` (a
  multi-row listing), so it fell back to the literal string `"unknown"` instead.
- **Fix:** extended `SYSTEM_PROMPT`'s operations paragraph: `lookup` means identifying a supplier/material's
  identity only, never a status/date/reason field (that's always `detail`); a status/date/field question is
  `detail` whenever no single uniquely-identifying value narrows it to one row. Also clarified (needed once this
  landed): a plan can name both a real supplier/material entity and an independent status/approval entity in the
  same question (`"approved MRS for keyboard"`) — only the status one gets `status="not_required"`; a named
  material still goes through real identity verification and never inherits `not_required` from a sibling entity
  (round-1 probing on qwen3:14b showed the model over-generalizing this without the clarification).
- **Verified against the real model (qwen3:14b, via the same SSH tunnel as fix.md #14), iteratively:** all 4
  target questions now get `operation: detail` (was `lookup`/`unknown` on all 4). None are `CAPABILITY_FAILURE`
  any more; each now fails, when it does, at a later, specific, diagnosable stage instead of an opaque
  operation-choice failure: `"Mrs rejected reason?"` → low-confidence clarification (a real, separate model
  imprecision: it also emits a spurious second entity, `concept="reason"`, not caught by this fix and not chased
  further — lower severity, doesn't regress anything); `"approved MRS for keyboard"` → `GROUNDING_FAILURE` (no
  verified MRS→INVITEMS join for material display — a genuine, separate, pre-existing catalog gap, not part of
  this fix); `"MRS due for keyboard in 2026"` → `ENTITY_RESOLUTION_REJECTION` (`keyboard` correctly requires real
  identity verification now); `"MRS due in 2026"` → `GROUNDING_FAILURE`. All four reaching a specific, real
  failure instead of a generic operation-choice miss is the actual fix; none of the newly-surfaced downstream
  gaps were introduced by it.
- **Full 47-question re-run (14b), before vs after this fix only** (isolated from the fix.md #14 prompt, which
  was already in place for both runs): `CAPABILITY_FAILURE` 11→6, `PASS_PIPELINE` 4→5, `QUERY_PLAN_FAILURE` 7→6,
  `ENTITY_RESOLUTION_REJECTION` 6→10, `GROUNDING_FAILURE` 7→7 (same count, different specific questions),
  `UNSUPPORTED_EXPECTED` 12→13. Coherent shift, not noise: the big `CAPABILITY_FAILURE` drop is exactly the
  bucket this fix targeted, and the increases elsewhere are more *accurate* classification (questions now
  reaching the failure stage that actually describes them), not new breakage — confirmed by tracing each changed
  question by name, not just reading the aggregate counts.
- **Files:** `app/query_plan_extractor.py`, `scripts/v1_real_question_eval_results.json`.
- **Tests:** 11 core suites **318/318**, unaffected (prompt text only, no test fixture asserts on it verbatim).

## 4. ✅ Grounding: concept has no verified column (rate/cost closed 2026-09-22; PO-pending + MRS-pending closed 2026-09-23)
- **Count:** 3 × `GROUNDING_FAILURE` after Step 3 — rate, cost consumed, order pending.
- **Reason:** `No verified V1 column supports this required concept.`
- **Where:** `app/schema_grounding.py`, `app/grounded_sql_validator.py`, `app/grounded_sql_generator.py`, `app/sql_datatype_validator.py`, `app/resources/business_schema_catalog.json`, `app/resources/v1_query_capabilities.json`
- **Closed 2026-09-22 (P3, commit `0a11c17`):** `purchase_rate` → `INVENTORY.PURCHASEORDER.RATE` and `consumption_rate` → `INVENTORY.ISSUE.ISSRATE`, both verified in `docs/ORACLE_SCHEMA_STUDY_2026-09-22.md` §6.1 + the live column profile; `cost consumed` / `consumption cost` / `consumed cost` added as aliases of the existing `consumption_value` (`ISSUE.ISSUEVALUE`). Capabilities updated. Tests: `test_purchase_rate_grounds_as_measure`, `test_cost_consumed_grounds_to_issue_value`.
- **Closed 2026-09-23 — PO order pending:** the ERP's SO/IA/JMD approval ladder (study §6.1, `FUNCTION
  INVENTORY.GETORDERPENDINGSTATUS`) is now 5 catalogued concepts — `po_pending_at_so`, `po_pending_at_ia`,
  `po_pending_at_jmd`, `po_approved`, `po_pending` (the last derived by negation: SO=0 OR IA=0 OR JMD=0) — via
  **value-pinned compound conditions**, a new grounding mechanism (an AND/OR of column=literal-value fragments,
  not a one-column catalog alias). `PURCHASEORDER.STATUS` is still correctly never used (constant 0 in the live
  data). `purchase_orders` family in `app/resources/v1_query_capabilities.json` updated with the 5 concepts; its
  stale limitations text corrected.
- **Closed 2026-09-23 — MRS pending:** Tarun's own verified production query (not inferred; full definition in
  `docs/ORACLE_SCHEMA_STUDY_2026-09-22.md` §6.1) is now the catalogued concept `mrs_pending`:
  `MRS_TEMP.RejectionStatus=0 AND StoresRejectionStatus=0 AND ItemDelete=0 AND isDelete=0 AND MrsFlag=1 AND
  (MillCode=0 OR MillCode IS NULL)`, **plus** a `LEFT JOIN MRS ON MrsNo/SlNo` with `NVL(MRS.OrderNo, 0) = 0`. The
  flag-AND part used the existing `compound_condition` mechanism, extended with a new **value-or-null** variant
  for `MillCode` (0 or NULL, not just 0); the anti-join to `MRS` needed a genuinely new grounding primitive — a
  catalog-declared **anti-join fragment** (a join verified from ERP business logic, not a schema foreign key,
  since none exists in `data/schema_relationships.json`). `mrs` family's limitations text in
  `app/resources/v1_query_capabilities.json` corrected.
- **New tests (2026-09-23):** `scripts/test_schema_grounding.py` (+6 tests),
  `scripts/test_grounded_sql_validator.py` (`PoOrderPendingLadderTests`, `MrsPendingAntiJoinTests`, +13 tests),
  `scripts/test_sql_datatype_validator.py` (+1 test — the offline datatype-category loader also had to learn
  `compound_condition.value_or_null_columns`, `app/sql_datatype_validator.py`). 293/293 across the 10 core V1
  suites.
- **Independent review, same day, found and fixed 2 real gaps (both regression-tested):** (1) a duplicate of an
  already-satisfied compound-condition fragment appended as `OR (<already-true thing>)` anywhere outside the
  positions `_validate_compound_conditions` tracks was invisible to every check — Oracle precedence then reads
  the whole WHERE as "the real condition OR that other thing", silently widening the answer; reproduced against
  the pre-existing `mrs_approved` too, so this predated today's work. Closed by rejecting any WHERE-clause `OR`
  outside a required OR-combinator gap or a value-or-null clause's own span. (2) the anti-join's `NVL(...)=0`
  check resolved its column via any alias of the physical table, so a second, wrongly-shaped join (e.g. a plain
  `JOIN` on a partial key) could supply the alias the NULL check reads from while an unrelated, correctly-shaped
  `LEFT JOIN` under a different alias satisfied the composite-key check — passing validation on SQL that never
  actually used the verified join. Closed by requiring the NULL check to use specifically the verified join's
  own alias. 3 new regression tests (`test_appended_or_clause_cannot_widen_the_predicate`,
  `test_left_join_alias_confusion_is_rejected`, `test_appended_or_of_an_already_required_column_is_rejected`).
- **Eval re-run done (same day, qwen3:8b):** unlike the stock/GRN catalog work (fix.md #13, 10 of 13
  `UNSUPPORTED_EXPECTED` flipped), this one flipped none — the aggregate 13/12/10/4/4/2/2 split is unchanged.
  Root cause is upstream of grounding, in QueryPlan extraction; full detail and the specific near-miss
  questions: fix.md #14.

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

## 10. ✅ Cross-domain alias collision: a generic concept grounds to two domains' columns
- **Found:** 2026-09-22 re-run. `how much cost consumed last month?` → `GROUNDING_FAILURE` (date ambiguity). That
  ambiguity was only the visible symptom — **the real bug was worse and silent.**
- **Root cause (traced 2026-09-23, not what the note above assumed):** `_matching_columns` matches by alias text
  across the *whole* catalog, and the domain-affinity tie-break in `ground_query_plan` (`app/schema_grounding.py`)
  only runs when **more than one** candidate exists for a phrase. `"value"` was a bare alias on `purchase_value`
  only — `consumption_value`'s aliases were `["consumption value", "issue value", "issued value", "cost consumed",
  "consumption cost", "consumed cost"]`, no bare `"value"`. So a **consumption**-domain plan whose measure concept is
  the generic word `"value"` had exactly **one** catalog match — `purchase_value` (`INVENTORY.PURCHASEORDER.NET`) —
  and the tie-break never engaged because there was nothing to tie against: `ground_query_plan` grounded it
  successfully, `is_grounded=True`, zero ambiguities, zero rejections, joined via `ITEM_CODE` to make it look
  legitimate. **A "how much value was consumed" question would have silently reported the purchase-order NET total
  instead of `ISSUE.ISSUEVALUE`, with full confidence and no signal anything was wrong.** That mis-grounding then
  polluted `selected_tables` with `PURCHASEORDER`, which is what made the *later* date-filter step tie between
  `ORDERDATE` and `ISSUEDATE` — the reported symptom was two steps downstream of the actual defect.
  The same asymmetry existed for `"rate"` (`purchase_rate` has it, `consumption_rate` did not) — confirmed
  reproducible the same way, not yet hit by the 47-question set but the identical code path.
- **Fixed 2026-09-23 (catalog only, no grounding-code change):** added the bare `"value"` / `"rate"` aliases to
  `consumption_value` / `consumption_rate`, matching the pattern `purchase_quantity` / `mrs_quantity` /
  `consumption_quantity` already used correctly (all three consistently alias bare `"quantity"`/`"qty"`, which is why
  quantity never had this bug). This restores the tie-break's own domain-affinity scoring to actually engage: with
  both concepts as candidates, `consumption_value`'s table (`ISSUE`, a domain anchor) now outscores
  `purchase_value`'s table (`PURCHASEORDER`, not an anchor) outright — no tie, no cross-domain leak, and the
  purchase-domain case is unaffected (still resolves to `PURCHASEORDER.NET`, verified by regression test). 2-line
  diff in `app/resources/business_schema_catalog.json`. Tests:
  `test_generic_value_grounds_to_the_plans_own_domain_not_purchase`,
  `test_generic_value_still_grounds_to_purchase_for_a_purchase_plan`,
  `test_generic_rate_grounds_to_the_plans_own_domain_not_purchase`,
  `test_cost_consumed_last_month_no_longer_ties_on_date` (confirms the downstream date ambiguity is also gone).
- **Not changed:** `mrs_due_date` still deliberately excludes bare `"date"` (the dirty-DUEDATE reason already
  recorded in #8) — this fix does not touch that.
- **Still worth auditing later, not done today:** whether any other bare generic alias (beyond value/rate/date/qty)
  has the same one-domain-only asymmetry; today's audit covered all 26 concepts' alias lists by hand and found only
  these two.

## 11. ✅ Step 4 — Qwen3:8b vs Qwen3:14b comparison
- **Done 2026-09-23.** Protocol: `docs/STEP4_MODEL_COMPARISON.md`. 8b = the run already committed at `64e901e`
  (laptop, local Ollama). 14b = fresh run via SSH tunnel to the company server's Ollama (the server has `qwen3:14b`
  only, not `qwen3:8b` — confirmed by a 404 before falling back to the committed 8b result instead of pulling an
  unneeded second copy of 8b onto the server).
- **Result (in-scope buckets, 23 of 47):** `PASS_PIPELINE` 2 → **5**; `QUERY_PLAN_FAILURE` 4 → **1**;
  `SQL_VALIDATION_FAILURE` 1 → **0**. 8 of 47 questions changed bucket, all but one in the improving direction (the
  one non-improving move, `CAPABILITY_FAILURE → ENTITY_RESOLUTION_REJECTION` for an MRS question, is neutral — the
  question is still correctly rejected, just for the fixture-gap reason instead of the wrong-operation reason).
  Latency: 47.5s/question (8b, laptop CPU) vs 8.1s/question (14b, server RTX 5070) — **not a fair comparison**
  (different hardware), reported only as a rough figure per the protocol's own caveat.
- **New failure mode found while checking item (4) of the protocol (not a 14b defect — a pre-existing gate bug the
  comparison happened to surface):** see #12.
- **Decision:** 14b is adopted as the **dev/eval** model going forward (Step 5's acceptance re-run and any further
  prompt iteration). This is measurement-driven, not a production cutover — Step 6 (live Oracle) is unaffected and
  still needs its own confirmation before any runtime default changes.

## 12. ✅ Capability + grounding "lookup" shortcut discards `plan.domain` entirely
- **Found:** 2026-09-23, while checking a Step 4 per-question move (`is material mouse received?`, a GRN-shaped
  question the dataset correctly marks `expected_unsupported`). 14b tagged it `domain="grn", operation="lookup"`
  (grn is explicitly `"status": "unsupported"` in `v1_query_capabilities.json` — 0 verified concepts). It should have
  been rejected as `CAPABILITY_FAILURE`. It was not.
- **Root cause:** `app/v1_capabilities.py:_family_name` and `app/schema_grounding.py:_domain` **both** contain the
  identical special case:
  ```python
  if operation == "lookup" and subject in {"material", "item"}:      return "material_lookup"  # / material_lookup domain
  if operation == "lookup" and subject in {"supplier", "vendor", "party"}: return "supplier_lookup"
  ```
  This check runs **before** either function ever looks at `plan.domain`. Confirmed end-to-end with a *resolved*
  entity (`domain="grn", operation="lookup", business_subject.concept="material"`, entity resolved to `"KEYBOARD"`):
  `evaluate_capability` returns `supported=True, family="material_lookup"`; `ground_query_plan` returns
  `is_grounded=True`, selecting only `INVENTORY.INVITEMS.ITEM_NAME`. **The model's own domain tag — the only place
  "this is a receipt-status question, not an identity lookup" was ever recorded — is discarded by both gates before
  either one is reached, and the SQL that would be generated answers a completely different question ("what is
  this item") with no rejection, no low-confidence flag, and no indication that "received" was never addressed.**
  This is not a wrong-number bug (no false measure/aggregate is asserted) — it is a silently non-responsive answer
  delivered with full confidence, on any domain the model tags with `operation=lookup` + a material/supplier
  subject: `grn`, `stock`, or any future unsupported family reachable that way.
- **Why not fixed today:** the shortcut exists on purpose for genuine identity lookups ("what is the item code for
  KEYBOARD") where `plan.domain` is often absent, "unknown", or a plausible-but-imprecise guess and should *not*
  block the lookup. The fix has to distinguish that legitimate case from a domain the catalog explicitly marks
  unsupported, without breaking the former — options include (a) only taking the shortcut when `plan.domain` is
  empty/`"unknown"`/already one of the two lookup domains' own aliases, rejecting outright when it names a *different
  known* unsupported family; or (b) requiring the plan carry no measures/dimensions/filters beyond the entity itself
  before treating it as a plain lookup. Either changes capability+grounding together and needs its own regression
  pass across the whole matrix (supplier_lookup, material_lookup, and every domain that could be mistagged) — out of
  scope for today's task (fix.md #10 + Step 4), and CLAUDE.md §11 asks that scope not be expanded mid-task.
- **Where:** `app/v1_capabilities.py:_family_name` (~L53-57), `app/schema_grounding.py:_domain` (~L184-188).
- **Fixed 2026-09-23.** Both functions now exclude domains that name a DIFFERENT, explicitly unsupported family
  (`stock`/`inventory`, `grn`/`goods receipt`/`goods receipt note`) from the lookup shortcut, falling through to
  the normal domain-based path instead — which correctly resolves to that family's own `unsupported` status
  (`v1_capabilities.py`) or to no schema-catalog domain at all (`schema_grounding.py`, since neither family has
  verified columns), so both gates now reject with the real reason. Every previously-accepted case is unaffected:
  the shortcut still applies whenever `plan.domain` is empty, `"unknown"`, already one of the two lookup domains'
  own names, or a supported family's own name/alias used loosely (e.g. `domain="purchase"` + `operation="lookup"`)
  — verified directly, not just by absence of a test failure. One existing test
  (`test_capability_supported_plan_is_validated_regardless_of_domain_label`,
  `scripts/test_query_plan_extractor.py`) had pinned the OLD behaviour using `domain="grn"` as its example; updated
  to use `domain="unknown"` for the same point (an odd domain label must not let semantic validation be skipped)
  and given its own explicit assertion that `domain="grn"` is now correctly capability-unsupported. New tests:
  `test_lookup_shortcut_does_not_override_a_different_unsupported_domain`,
  `test_lookup_shortcut_still_applies_when_domain_is_generic` (`test_schema_grounding.py`); a `RejectionCase`
  for `"is material keyboard received?"` (`test_v1_acceptance_matrix.py`, rejection cases 3→4).

## 13. ✅ Step 6 deployment + read-only account verification — done 2026-09-23
- **Account:** Tarun created `ajsmgpt_ro` via SQL Developer (`system` connection) with the exact grants in
  `docs/ORACLE_READONLY_ACCOUNT.md`. Two rounds of `ORA-01017` (username/password mismatch) traced to `.env`
  transcription, not the account itself — resolved by retyping rather than copy-pasting.
- **Deployment:** branch pushed to `origin`; cloned to `/home/ajsmgpt/AJSMGPT_v1` on the server (beside the
  legacy `ajsmgpt-api.service`, untouched); venv + `requirements.txt` + `en_core_web_sm` installed; offline core
  suite **284/284** on the server. `.env` copied and edited by Tarun (I never read or wrote it).
- **`check_schema_access.py` had never checked what it claimed to.** It only ever verified table *visibility*
  (`ALL_TABLES` counts), not the account's actual privilege set. Added `USER_TAB_PRIVS`/`USER_SYS_PRIVS` checks —
  confirmed `ajsmgpt_ro` itself has `SELECT` only and `CREATE SESSION` only, nothing more.
- **Found while verifying that: `USER_TAB_PRIVS` doesn't see `GRANT ... TO PUBLIC`.** First pass (scoped to
  5 schemas) showed `UPDATE`/`DELETE` on `HRDNEW.CURRENTATTENDANCE` (2.3M rows), `OVERTIME`, `SHIFTALLOCATION`,
  `ADMIN.ONETOUCHEMPLOYEE`, `EXECUTE` on `HRDNEW.GETNAME`. Checking the actual scale (not assuming it stopped
  there) found **~28,700 PUBLIC object grants database-wide, ~26,500 beyond SELECT** — evidently old
  cross-database migration tooling; the `MICROSOFTDTPROPERTIES`/`MICROSOFTSEQDTPROPERTIES` pattern repeats in
  schemas AJSMGPT has never referenced (`ACCSHARES`, `ACCTEX`, …). Not something `ajsmgpt_ro`'s creation caused,
  not something AJSMGPT can revoke. The script's first PUBLIC-grants addition printed the raw unscoped list —
  useless noise at this scale — so it was rewritten to a per-schema summary plus a **direct, checked** (not
  assumed) verdict against the 14 tables AJSMGPT actually reads: none of them carry a PUBLIC grant beyond SELECT.
  **Does not affect V1** — checked, not just claimed. **Does matter to whoever owns this database** — flagged in
  `docs/ORACLE_READONLY_ACCOUNT.md`, not fixed (AJSMGPT never runs GRANT/REVOKE).
- **Live-question eval — 4 rounds, done same day.** `scripts/evaluate_v1_real_questions_live.py` (new; real
  entity resolution + real execution, never records `rows`/`columns`/`summary`, only `row_count` — see the
  file's own docstring). Each round ran all 47/47 questions against real Oracle with `ajsmgpt_ro`, 0 crashes,
  0 leaked row data (verified after every run: zero `rows`/`columns`/`summary` fields in the results file).
  **Run 1** (before stock/GRN): `UNSUPPORTED_EXPECTED 24 · ENTITY_RESOLUTION_REJECTION 15 · CAPABILITY_FAILURE 4 ·
  QUERY_PLAN_FAILURE 2 · GROUNDING_FAILURE 2 · PASS_PIPELINE 0`. **Run 4** (final, stock/GRN + every fix below
  deployed): `ENTITY_RESOLUTION_REJECTION 16 · UNSUPPORTED_EXPECTED 13 · CAPABILITY_FAILURE 10 ·
  QUERY_PLAN_FAILURE 4 · GROUNDING_FAILURE 3 · PASS_PIPELINE 1`. `ENTITY_RESOLUTION_REJECTION` rising to 15→16 on
  real master data is expected, not a regression — exactly what fix.md #2 predicted ("recheck on real master
  data"): real item/supplier names (`mouse`, `dell`, `dell system`, `yarn`, `printer toner`, a literal
  `barcode chrome label`) don't case/whitespace-normalize to an exact real `ITEM_NAME`/`PARTYNAME`, and the
  resolver has no fuzzy matching by design. `CAPABILITY_FAILURE` rising 4→10 is the honest signal the evaluator
  classifier fix below unlocked: stock questions using `operation=detail`/`unknown` genuinely reach the (now
  real) stock family and are correctly refused for the wrong operation, not silently mislabeled "working as
  intended."
- **First-ever live `PASS_PIPELINE` against real Oracle**, run 3: `how much cost consumed last month?` —
  `row_count=1`, a real aggregate row from `INVENTORY.ISSUE`. The exact result value was never seen or recorded
  anywhere (CLAUDE.md §3); only the count and the fact of success are reported here.
- **Three more live-only findings, each reproduced by isolated test before being fixed, each verified after:**
  (1) `qwen3:14b` used the bare word `"cost"` for both `business_subject` and `measure` on the question above —
  no concept had that bare alias (only two-word phrases like `"cost consumed"`); (2) `"how many qty received?"` /
  `"...in last one year?"` used the bare word `"quantity"` for the measure, which — before the fix — matched
  only purchase/mrs/consumption's shared `"quantity"`/`"qty"` alias and silently grounded to
  `INVENTORY.PURCHASEORDER.QTY`, `is_grounded=True`, no warning, for a goods-receipt question. Confirmed the
  silent leak directly, then fixed by giving GRN's own three quantity concepts the same bare alias — the correct
  outcome is now an honest ambiguity naming `GRNQTY`/`PENDING`/`REJQTY`, not a cross-domain guess; (3) the same
  two "qty received" questions separately used `business_subject="quantity received"`, which matched no domain
  alias and no identifier-role concept — added as a `grn` domain alias, same pattern as fix (1). Fix (3) verified
  directly rather than through a fifth full live-Oracle round trip (diminishing returns past this point).
- **Also found and fixed the same day:** the evaluator's own `KNOWN_UNSUPPORTED_DOMAINS`/`_expected_unsupported()`
  still listed stock/grn as by-design-refused after the catalog work made them real families — caught by run 2
  still showing stock questions as `UNSUPPORTED_EXPECTED`; both the offline and live evaluators share this
  constant, so both were restarted after the fix rather than reported with the stale label.
- **Not yet done:** the `ajsmgpt-v1.service` systemd unit; the acceptance-criteria comparison against
  `AJSMquery.sql` (needs more than the single `PASS_PIPELINE` this session produced to be a meaningful check).
- **Files:** `scripts/check_schema_access.py`, `scripts/evaluate_v1_real_questions_live.py`,
  `scripts/evaluate_v1_real_questions.py`,
  `docs/ORACLE_READONLY_ACCOUNT.md`.

## 14. ✅ Offline eval re-run after PO/MRS-pending: aggregate numbers unchanged — the real bottleneck is QueryPlan extraction, not grounding
- **Count:** 0 net change. 13/12/10/4/4/2/2 before and after re-running the same 47 questions (qwen3:8b — qwen3:14b
  is not pulled on this machine right now; ran with 8b on Tarun's explicit choice). 2 individual questions
  swapped classification, unrelated to this change (see model non-determinism note below).
- **Reason:** for the 2 closest candidate questions ("list out material hold at Store officer?", "list out
  material approval pending at Store officer?"), qwen3:8b's QueryPlan extraction produced `domain='unknown'`,
  never even attempting the mrs/purchase domain. For a 3rd ("list out order pending material names?"), the model
  produced `domain='purchase'` but dropped "pending" entirely — `entities: []`, `filters: []` — the plan asked
  only for "material names," so grounding never got a chance to try `po_pending`. That plan then separately
  failed grounding for an unrelated, already-counted reason (`business_subject="material"` has no
  identifier-role column and isn't a domain alias under `purchase` — one of the pre-existing 4
  `GROUNDING_FAILURE`s, not a new gap).
- **Conclusion:** `po_pending`/`po_pending_at_so/ia/jmd`/`po_approved`/`mrs_pending` are verified correct by 20
  targeted unit tests (hand-built QueryPlans that exercise them directly) plus an independent review — the
  deterministic grounding/validation layer works exactly as designed. What's unproven is whether qwen3:8b's
  QueryPlan extraction reliably represents "pending"/"approval"/"hold" as an entity or filter concept at all for
  real free-text phrasing; today's 47-question set doesn't contain a clean test of it, and the 2-3 near-miss
  questions show the model currently drops or misroutes it before grounding is ever reached. Same class of gap
  as fix.md #3 (operation-choice prompt tuning), but for entity/filter concept recognition specifically — not a
  reason to doubt today's catalog/validator work, which this run never actually exercised.
- **Not done:** prompt tuning for this specific gap — needs its own before/after measured pass with real
  "pending"/"approval"/"hold" phrasings, not a same-session patch.
- **Model non-determinism, noted not chased:** `"Last 3 purchase details of \"MONITOR\""` flipped
  `PASS_PIPELINE`→`SQL_VALIDATION_FAILURE` and `"how much cost consumed last month?"` flipped
  `SQL_VALIDATION_FAILURE`→`PASS_PIPELINE` between this run and the prior recorded one, despite
  `temperature=0.0`. Neither question touches PO/MRS-pending; treated as pre-existing model-serving noise
  (real for any offline re-run, not introduced by this session), not a regression.
- **Where:** `app/query_plan_extractor.py` (entity/filter concept recognition for pending/approval/hold), not
  `app/schema_grounding.py` or `app/grounded_sql_validator.py` (both unchanged by this eval run).

## 15. ✅ Stock questions extracted as operation=detail, which stock's capability never allows

- **Found by:** the first depth-bar held-out measurement (`--split test`, live Oracle + qwen3:14b, 2026-09-24).
  stock scored 0/10 -- every failure was `CAPABILITY_FAILURE: domain='stock': Operation 'detail' is not supported
  for the V1 stock family.` for real phrasings like "show stock for yarn", "stock availability for keyboard",
  "do we have yarn in stock".
- **Root cause:** `app/resources/v1_query_capabilities.json`'s stock family is deliberately `operations:
  ["aggregate"]` only (ITEMSTOCK is 1-31 rows per item -- per-mill/HOD -- so a non-aggregated read of one row is
  not a correct answer; only `SUM` per item matches the ERP's own `GETTOTALSTOCK`). That restriction is correct
  and untouched. The bug is upstream: `query_plan_extractor.py`'s `SYSTEM_PROMPT` never told the model that a
  current-quantity question is `aggregate` even when it names one item and sounds like "one fact" -- so it
  consistently chose `detail`.
- **Fixed:** added one sentence to `SYSTEM_PROMPT` -- "a question asking for a total or current quantity/amount
  ... is operation=aggregate ... even when it names exactly one item ... because the quantity itself is a
  running total across underlying records, not a value stored in any single one of them."
- **Real prompt-tuning instability, verified not guessed (3 rounds, via an SSH tunnel to the server's real
  qwen3:14b, temperature=0.0, 2-3 repeats per question to separate signal from the model-serving noise already
  documented in fix.md #14):**
  - Round 1 (bare sentence above): fixed 6/6 real failing stock questions, but flipped
    `"what is MRS number 830216"` (structurally identical to the real `PASS_PIPELINE` case "MRS details for MRS
    number 890330") from `detail` to `lookup` -- a regression against one of only 3 real passes in the whole run.
  - Round 2 (added a carve-out: "this does not apply when the question names a record by its own unique
    identifier, such as one specific MRS number"): fixed the MRS regression and kept 5/6 stock fixes, but
    flipped a self-invented probe question ("last purchase rate of item code A12203362") into a
    `QueryPlanValidationError` (`item_identifier` dimension not affecting output). Narrowing the carve-out's
    example list did not change this.
  - Round 3 (dropped the general rule for a stock-domain-named version): fixed all 6/6 stock questions and the
    round-2 probe regression, but flipped **both** real non-tiny `PASS_PIPELINE` cases (mrs-by-number *and*
    "purchase order for item code C02000094", the actual eval question) from `detail` to `lookup` -- unacceptable.
  - **Shipped round 2's wording.** It is the only one of the three that fixes 5 of 6 real stock failures while
    leaving both real, non-tiny `PASS_PIPELINE` cases from this run untouched. Its only known casualty is the
    self-invented probe question above, which is not part of any tracked question bank. `"do we have yarn in
    stock"` stays unfixed (now `lookup` instead of `detail` -- still a `CAPABILITY_FAILURE`, just a different
    one; not a regression, since it was already failing).
- **Not chased further:** a 4th wording round, on the theory that two consecutive rounds each trading one
  regression for another (fix.md #14's own threshold for "stop tweaking, reconsider") means the model's
  detail/lookup boundary for identifier-and-code-bearing questions is genuinely fuzzy at the prompt-wording
  level, not something a 5th sentence reliably resolves. If this needs to be more robust, the next step is
  probably deterministic (grounding-layer correction for the stock family specifically), not another prompt
  sentence -- flagged, not attempted, since it would touch more of the pipeline than this fix's scope.
- **Where:** `app/query_plan_extractor.py` (`SYSTEM_PROMPT` only). `app/v1_capabilities.py` and
  `app/resources/v1_query_capabilities.json` unchanged -- confirmed correct as designed, not the bug.
- **Tests:** 1 new in `test_query_plan_extractor.py` (asserts the new sentence and its carve-out are present in
  `SYSTEM_PROMPT`, matching the existing prompt-content-assertion pattern -- prompt effectiveness itself is
  model-verified above, not unit-testable). 11 core suites **327/327**.

## 16. ✅ A `material`/`supplier`/`item_identifier` entity tagged `status=not_required` skipped verification entirely

- **Found by:** diagnosing the one `SQL_EXECUTION_FAILURE` from the same held-out run ("Last 5 purchase qty of
  \"BARCODE SCANNER\""), which the harness recorded with no usable detail (`app/oracle_client.py`'s
  `_safe_database_error` deliberately raises `from None`, discarding the real Oracle error -- by design, not a
  harness bug). The captured `full_query_plan`/`full_sql_result` (never printed to chat -- only structural
  fields, no ERP rows, per §3) showed the real story: the entity `{"concept": "material", "original_value":
  "BARCODE SCANNER", "status": "not_required"}` produced a generated SQL with **`applied_filters: []` and no
  WHERE clause at all** -- an unfiltered, date-sorted read of the entire `INVENTORY.PURCHASEORDER` table, which
  then failed at Oracle execution (most plausibly a timeout on the full sort; the wrapping wasn't preserved
  enough to be certain, and isn't worth reproducing against production just to confirm a symptom whose cause is
  already clear).
- **Root cause:** `query_plan_extractor.py`'s `SYSTEM_PROMPT` explicitly reserves `status=not_required` for
  status/condition concepts and says the five identity concepts (`supplier`, `supplier_name`,
  `supplier_identifier`, `material`, `item_identifier`) must never use it -- the model violated its own
  contract here. `app/entity_resolution.py`'s `resolve_entity` (`if entity.status is EntityStatus.NOT_REQUIRED:
  return entity`) trusted that self-report unconditionally, contradicting its **own module and function
  docstrings**, which already promise "any status the model already put in its JSON is discarded and
  re-verified from scratch." This is the load-bearing fail-closed layer for exactly this kind of thing
  (`app/nlp_execution.py`'s execution gate rejects any `UNRESOLVED`/`AMBIGUOUS` entity, :748-758) -- it just
  never got the chance to run, because resolution was skipped, not because it would have failed to catch it.
- **Fixed:** one line -- `resolve_entity` now only honors a claimed `not_required` when
  `entity.concept not in resolvable_concepts()`. For the 5 always-verify concepts, the model's self-reported
  status is now discarded and real resolution always runs, exactly as already documented. Deterministic code
  fix, not a prompt change -- no live-model verification needed (unlike fix.md #15), since correctness here
  depends only on `entity.concept`, not on model wording.
- **Where:** `app/entity_resolution.py` (`resolve_entity`, one line). Nothing else changed --
  `app/nlp_execution.py`'s gate was already correct; it just needed this entity to actually reach it.
- **Tests:** 1 new in `test_entity_resolution.py` -- a `material` entity claiming `not_required` with the exact
  real-world value ("BARCODE SCANNER", already in the `MATERIAL_ROWS` fixture) is now forced through real
  resolution and comes back `RESOLVED`, not silently passed through unverified. 11 core suites **328/328**.
- **Not done:** confirming what the real Oracle error actually was (timeout vs. something else) -- not needed
  to fix or test this; the gap was upstream of execution and is now closed there instead.

## 17. 🟡 Approval-stage word split from its status word into a second entity (mrs family) -- one of two fixed

- **Found by:** the same held-out run, diagnosed by a background agent then independently verified against the
  real model (not trusted on the agent's word alone). "list out material approval pending at Store officer?"
  and "list out material hold at Store officer?" both `GROUNDING_FAILURE`'d with "No verified V1 column
  supports this required concept" repeated -- suspicious, since `mrs_ready_for_approval`'s alias "store officer
  pending" already exists in the catalog for exactly the first question's concept (added 2026-09-23, per fix.md
  #4/CLAUDE.md §6). Confirmed by extracting both questions directly: the model produced **two entities**
  ("pending"/"hold" and "Store officer" separately) instead of one, so grounding never even got a single
  string to try against the existing alias.
- **This is a recurrence of a residual fix.md #14 already flagged and left unfixed** ("the model still
  sometimes splits the status phrase into two entities... flagged there as known, lower-priority, not yet
  fixed") -- not a new class of bug, fresh evidence for an old one. The prompt already said "never split one
  such phrase into more than one entity" with an example ("pending at store officer") that exactly matches this
  shape, and the model still split it -- the existing instruction alone wasn't reliable enough.
- **Fixed (partially):** added one sentence naming the specific pattern -- an approval-stage word (store
  officer, internal audit, JMD) co-occurring with a status word in the same clause is part of that same entity.
  Verified against the real model, 2 repeats: **"approval pending at Store officer" now correctly fuses into
  one entity** (`concept="pending at store officer"`, matching the existing alias). Zero regressions across a
  battery of prior fixes (fix.md #15's stock cases, fix.md #16's MRS-number/purchase-item-code, "approved MRS
  for keyboard" correctly keeping its two genuinely-independent entities).
- **Not fixed: "hold at Store officer" still splits into two entities, unchanged by this edit.** Root cause is
  different from the pending case: `mrs_hold_flag`'s only aliases are `["mrs hold", "hold", "on hold"]` -- there
  is no stage-qualified alias for hold at all, because `HOLDINGSTATUS` (`INVENTORY.MRS_TEMP`) is a single flag
  with no evidence it's tracked per-approval-stage in the data. Fusing the entity string wouldn't fix grounding
  here even if the model did it, because there's nothing in the catalog to fuse it to -- this needs a decision
  (does the data even support "on hold, specifically at the store-officer stage" as a distinct condition, or
  should "at Store officer" just be dropped as non-restrictive for hold questions?), not another prompt
  sentence. Left open rather than iterating further on wording alone -- matches the same "stop after a couple
  of rounds, don't force it" judgment as fix.md #15.
- **Where:** `app/query_plan_extractor.py` (`SYSTEM_PROMPT` only).
- **Tests:** 1 new (asserts the new sentence is present in `SYSTEM_PROMPT`; effectiveness verified live above).
  11 core suites **328/328**.

## 18. ✅ "grn for order 800151" -- no catalog concept for GRN's own order-number filter

- **Found by:** the grn cluster diagnosis (background agent), independently confirmed against
  `docs/ORACLE_SCHEMA_STUDY_2026-09-22.md` (`GRN.ORDERNO`, `NUMBER(22)`, `GRN.ORDERNO = PURCHASEORDER.ORDERNO`)
  and `data/multi_schema_metadata.json` before trusting it. Clean, additive, evidence-backed gap -- unlike the
  agent's other findings this run, this one didn't need any risk/scope judgment call, so fixed it directly.
- **Fixed:** new catalog concept `grn_order_number` on `INVENTORY.GRN.ORDERNO` (`roles: entity_filter,
  identifier`, matching the existing `mrs_number` pattern). Verified against the real model that "grn for order
  800151" now grounds with zero reject reasons -- but the model extracts the entity as the bare word `"order"`,
  not "order number", so that bare word had to be in the alias list too (checked first that no other concept
  already claims it -- it doesn't, no new ambiguity introduced).
- **Where:** `app/resources/business_schema_catalog.json` only. No code changed.
- **Tests:** 1 new in `test_schema_grounding.py`. 11 core suites **330/330**.
- **Related, NOT fixed -- deliberately left for a decision, not attempted blind:**
  - **Measure-phrase fidelity (grn's #1/#3 from the diagnosis):** `received_quantity`/`pending_receipt_quantity`/
    `rejected_receipt_quantity` each already have a unique, unambiguous alias ("qty received", "pending qty",
    "rejected qty" respectively) alongside the shared bare "quantity"/"qty" that fix.md #13 deliberately left
    tied ("honest ambiguity" over silent guessing). "how many qty received in last one year?" fails because
    the model drops "received" and extracts the bare, tied concept instead -- the question itself already had
    the unambiguous word. A prompt-fidelity fix (teach the model to keep "received"/"pending"/"rejected" when
    the question uses them) is the same *class* of fix as fix.md #15/#17, but broader in scope (measures in
    general, not one domain) -- more surface area for the same kind of side effect fix.md #15 took 3 rounds to
    tame. Not attempted this session; flagging the shape of the fix, not the wording.
  - **`business_subject` role requirement (grn's #2):** "Today received material names and qty?" fails because
    `business_subject.concept="material"` has no column with role `"identifier"` (only display/grouping/
    entity_filter) -- `app/schema_grounding.py`'s `_requirements()` hardcodes that role for business_subject.
    Checked the actual QueryPlan (not just the agent's characterization): "material" is *also* already present
    as a `dimension` and in `requested_output.fields` in the same plan -- redundant as business_subject. This
    could be fixed two different ways with different risk profiles: loosen the grounding role requirement
    (touches every domain's business_subject check, not just grn) or teach extraction to prefer a domain-level
    subject ("received"/"receipt", which already has a free-pass grounding rule for domain aliases) over an
    entity-echoing one. Genuinely unclear which is right without more thought -- not a quick fix either way,
    left open.
  - **Bare "`<domain> for <entity>`" operation-choice (grn's #4, "grn for yarn"):** same shape as fix.md #15's
    stock fix, but the pattern (a domain name immediately followed by a bare entity, no verb) is generic across
    all domains, not scoped to one family -- higher blast radius for the same kind of prompt-wording
    instability fix.md #15 hit. Not attempted this session.
- **Scope note carried over from the diagnosis, not re-litigated:** fix.md #13 already deliberately chose the
  quantity 3-way tie as "honest ambiguity"; that decision is correct and untouched here.

## 19. ✅ Depth-bar eval: a genuine multi-candidate entity ambiguity is now classified separately, not blended into failure

- **Decided by Tarun:** when a shorthand entity ("keyboard", "yarn") genuinely matches 2+ real catalog items and
  the fuzzy fallback (fix.md #2) correctly refuses to guess, that is not a defect -- it's the same thing a
  human given the same shorthand would also need to ask about. Confirmed: this should not count as a depth-bar
  failure, but also shouldn't be silently credited as a plain pass, since the question still didn't get
  answered.
- **Implemented as a third bucket, not a reinterpretation of existing numbers:** new classification
  `ENTITY_AMBIGUOUS_DEFERRED`, reported separately from both `PASS_PIPELINE` and `ENTITY_RESOLUTION_REJECTION`
  -- same idea as `UNSUPPORTED_EXPECTED` already being excluded from the failure count for out-of-scope
  refusals. Detection is deterministic, not a guess: `nlp_execution.py` builds each entity-ambiguity string in
  one exact, fixed shape (`"Entity '<concept>' requires verified resolution before execution."` plus, only when
  candidates exist, `" Candidates: <a>; <b>; ..."`) -- parsing that exact suffix for 2+ semicolon-separated
  candidates is reading a structured signal through a string, not heuristic NLP.
- **Deliberately narrow, to avoid over-crediting:** classified as deferred only when *every* ambiguity in the
  rejection is an entity ambiguity with 2+ candidates -- one entity with 0 or 1 candidates (an extraction bug
  like fix.md #16's, or a genuinely-not-found value) or a co-occurring low-confidence flag means the question
  still doesn't have a clean answer even if the ambiguous part were resolved, so it stays a real rejection.
  4 unit tests cover exactly this: pure multi-candidate (deferred), zero-candidate (rejection), mixed with a
  confidence flag (rejection), and two entities where only one is genuinely ambiguous (rejection).
- **Verified against today's real run, not just synthetic tests:** re-classified the existing
  `v1_real_question_eval_results_live.json` using the stored rejection reason (not `full_query_plan`, which
  turned out to be the *pre*-resolution plan -- caught by spot-checking one record before trusting it, not
  assumed). 5 of 68 questions move from real failure to genuinely-deferred: purchase 3, grn 1, stock 1. Moves
  purchase 5%->20%, grn 0%->14%, stock 0%->10% -- real movement, but nowhere near the 75% bar on its own, matching
  the expectation set when this was proposed: most of what's failing is grounding/capability gaps this doesn't
  touch.
- **Where:** `scripts/evaluate_v1_real_questions.py` (`_classify()`, new `_is_genuine_multi_candidate_ambiguity`
  helper), `scripts/evaluate_v1_real_questions_live.py` (imports and reuses the helper, duplicates the
  `_classify()` branch to match, same maintenance pattern as the rest of that file).
- **Tests:** 8 new in a new file, `scripts/test_evaluate_v1_real_questions.py` (this evaluator had none before
  -- the classification logic was simple enough not to need one until this parser/branch was added). 11 core
  suites plus this new file: **338/338**.

- **Closed 2026-09-23 (same day, continued).** Traced the actual root cause by reading `app/entity_resolution.py`
  directly rather than guessing further: `resolve_entity` forces **any** entity concept outside the 5-token
  identity-verification whitelist (`supplier`/`supplier_name`/`supplier_identifier`/`material`/`item_identifier`)
  to `UNRESOLVED` — which blocks execution — *unless* the model itself tags it `status: "not_required"`. The
  SYSTEM_PROMPT never taught the model this concept exists at all. Confirmed with a real example already in the
  47-question set: `"MRS details for MRS number 890330"` — the model correctly names `concept="mrs_number"`, but
  since `mrs_number` isn't in the identity-verification whitelist and the model (reasonably, per the old prompt)
  tagged it `status="resolved"`, it was forced to `UNRESOLVED` — `ENTITY_RESOLUTION_REJECTION`, not the "value
  absent from the offline fixture" reason the rest of that bucket has. This is a **pre-existing gap**, not
  introduced by today's PO/MRS-pending work — it already blocked `mrs_number`, and would have blocked
  `mrs_rejected`/`mrs_approved`/`mrs_pending`/`po_pending*` too the first time any of them reached a real
  question, despite being fully correct at the grounding/validation layer (20 unit tests, a review) — those
  tests never exercise `resolve_plan_entities`, so this never showed up until a real end-to-end eval did.
- **Fix:** extended `SYSTEM_PROMPT` in `app/query_plan_extractor.py`: (1) an entity's concept may be a record
  identifier or a status/approval/workflow condition, not only supplier/material — described in the model's own
  words, not a generic label like "status"; (2) for any concept other than the 5 identity-verified tokens, set
  `status: "not_required"`; (3) added one-line hints connecting `mrs`/`purchase` domains to their
  approval/hold/rejection vocabulary, since nothing previously told the model those domains cover workflow
  status at all.
- **Verified, isolated, not just asserted:** (a) new regression tests in `scripts/test_entity_resolution.py`
  reproduce the exact failure mode on a compound-condition concept and confirm the fix; (b) a direct isolation
  test — same question, same model (qwen3:14b), old prompt vs new prompt — showed the *old* prompt makes even
  14b misclassify `"890330"` as `concept="supplier_identifier"` (guessing it's a supplier code, the closest of
  the 5 allowed tokens) and fail the same way; only the new prompt gets `concept="mrs_number"`,
  `status="not_required"`, reaching `PASS_PIPELINE`. The fix, not the model, is what closes this gap.
- **Real-model caveat:** qwen3:8b (this machine's only local model) could not reliably follow the new
  concept-naming instruction after two rounds of prompt refinement — it kept inventing generic labels
  (`"status"`, `"approval_status"`) instead of using the question's own words, and domain stayed `unknown` for
  the two Store-Officer questions regardless. qwen3:14b (the model fix.md #11 already adopted, for exactly this
  kind of instruction-following gap) did measurably better: both Store-Officer questions now reach `domain=mrs`
  (was `unknown`), and concepts came back as literal words (`"hold"`, `"pending"`) instead of invented labels.
  14b is not pulled on this laptop; reached instead over an SSH port-forward to the company server
  (`ajsmgpt@103.171.13.142:5555`, itself running Ollama with `qwen3:14b`) at Tarun's direction, since the
  `.env`-configured `OLLAMA_URL` (a separate LAN host) was transiently unreachable ("no route to host") when
  this was attempted — unrelated network flakiness, not a code issue; the tunnel was closed after use.
- **Full 47-question re-run, qwen3:14b + fixed prompt** (`scripts/v1_real_question_eval_results.json`):
  `CAPABILITY_FAILURE 11, ENTITY_RESOLUTION_REJECTION 6 (was 10), GROUNDING_FAILURE 7, PASS_PIPELINE 4 (was 2),
  QUERY_PLAN_FAILURE 7, UNSUPPORTED_EXPECTED 12 (was 13), SQL_VALIDATION_FAILURE 0`. This run is **not a clean
  ablation** of the prompt fix alone — it also reflects 14b's generally stronger QueryPlan extraction (fix.md
  #11), so the 20 questions that changed classification are a mix of both effects, not attributable to this fix
  alone. The one cleanly isolated result is the `mrs_number` case above.
- **Still not resolved (real, smaller, lower priority):** the model still sometimes splits a multi-word status
  phrase into two separate entities (`"pending"` + `"Store officer"` rather than one phrase matching an existing
  alias like `mrs_ready_for_approval`'s "store officer pending"), and still occasionally duplicates the same
  concept into both `entities` and `filters` (harmless — grounding tolerates the duplicate — but not clean).
  Neither blocks execution; both are prompt-wording refinements for a future pass, not attempted further today
  (diminishing returns after two refinement rounds).
- **Files:** `app/query_plan_extractor.py`, `scripts/test_entity_resolution.py`,
  `scripts/v1_real_question_eval_results.json`, `fix.md`.
- **Tests:** 11 core suites (added `test_entity_resolution.py` to the tracked list — always part of the real
  V1 chain per CLAUDE.md §4, just not previously counted here) **318/318**.

## 20. ✅ A numeric-looking filter value extracted as text crashed uncaught against a NUMERIC column

- **Found by:** re-running the held-out eval end-to-end after fix.md #15-19 landed ("carry on with 2 and 3" --
  measure again, act on what it shows). "grn for order 800151" -- the exact question fix.md #18 had just fixed
  at the grounding layer -- now reached execution and **crashed** (`HARNESS_FAILURE`, an uncaught `ValueError`
  from `sql_datatype_validator.py`), worse than the clean `GROUNDING_FAILURE` rejection it had before #18.
- **Root cause, traced through the actual captured plan (not guessed):** the model represented "800151" as
  **both** a `not_required` entity (no real value) **and** a `filter` with `value_type: "string"`. The filter
  is what actually drives the bind -- `build_bind_parameters()`'s filter loop passes the raw string `"800151"`
  through untouched, since `"order"` isn't one of the 5 always-verify concepts, and Oracle's `NUMERIC` bind
  category requires a real `int`/`float`/`Decimal` (`_value_matches_category`), never a numeric-looking string.
  Confirmed this couldn't have happened via the entity path: `supplier`/`material`/`item_identifier`'s verified
  columns are all `TEXT` category, so a `RESOLVED` entity's value is never checked against `NUMERIC` today --
  this specific mismatch was only reachable through a filter, and only once a NUMERIC-category identifier
  concept (`grn_order_number`) existed for a real question to filter by.
- **Fixed deterministically, not by loosening the datatype check:** `build_bind_parameters()`'s filter loop now
  coerces a clean digit-string filter value to `int` when every column the concept actually grounded to is
  verified `NUMERIC` (reusing `sql_datatype_validator`'s own offline metadata lookup as the source of truth --
  not a second, divergent category source). Fails closed on anything else: non-digit text, mixed/unknown
  categories, or a `CONTAINS`/`STARTS_WITH`-wrapped value (`"%800151%"` already fails the digit check on its
  own, so LIKE-shaped filters are never touched). Matches "Qwen proposes, deterministic code verifies" --
  correctness depends only on the verified column category, never on the model's self-reported `value_type`.
- **Where:** `app/nlp_execution.py` (`_numeric_filter_values`, new; one call site in `build_bind_parameters`'s
  filter loop).
- **Tests:** 1 new in `test_nlp_execution.py`, reproducing the exact real plan/SQL captured from the actual
  crash (not a synthetic stand-in) -- asserts the bind comes back as `int`, not the model's `str`. 11 core
  suites plus the eval-classifier test file: **339/339**.
- **Not independently live-verified end-to-end:** tried, but the model produced an unrelated `SELECT *`
  rejection on every retry this time (separate, pre-existing validator, unrelated to this fix -- ordinary
  model non-determinism, same class already documented in fix.md #14). The unit test reproduces the real
  captured inputs faithfully enough to trust without chasing a live round-trip further.

## 21. ✅ ISSUE.ISSUENO had no catalog concept -- found while hand-labeling training data

- **Found by:** hand-labeling real consumption questions for a training/few-shot dataset (Tarun asked about
  heavy training as a lever for option 2 of the freeze decision). "issue for issue number 737" is a real,
  already-logged question with no catalog concept to ground it against -- same class of gap as fix.md #18's
  `grn_order_number`, found the same way (checking the real schema study before assuming, not guessing).
- **Verified before adding:** `docs/ORACLE_SCHEMA_STUDY_2026-09-22.md` documents `ISSUE.ISSUENO` directly
  (177,627 distinct values, per-series document number); `data/multi_schema_metadata.json` confirms
  `NUMBER(22) NOT NULL`.
- **Fixed:** new catalog concept `issue_number` on `INVENTORY.ISSUE.ISSUENO` (`roles: entity_filter,
  identifier`), matching the `mrs_number`/`grn_order_number` pattern exactly. Pure catalog addition, no code
  or prompt changed.
- **Where:** `app/resources/business_schema_catalog.json` only.
- **Tests:** 1 new in `test_schema_grounding.py`. 11 core suites plus the eval-classifier file: **339/339**.

## 22. ✅ fix.md #17 verified the wrong thing -- the fused mrs entity never actually grounded

- **Found by:** building a few-shot example for the "pending at store officer" pattern (the next step after
  the labeled-QueryPlan seed dataset) and verifying the example itself against `ground_query_plan()` before
  using it -- a habit that caught a real gap in a fix I'd already shipped and called done.
- **What fix.md #17 actually verified:** that the model fuses "approval pending at Store officer" into ONE
  entity instead of two, with `concept="pending at store officer"`. True and still true.
- **What it never checked:** whether that exact string then *grounds*. It doesn't: the catalog's
  `mrs_ready_for_approval` alias was `"store officer pending"` -- the reverse word order. Exact-alias matching
  means "pending at store officer" ≠ "store officer pending"; the fused entity still failed to ground, just
  with a single cleaner-looking rejection instead of two, which is presumably why this passed a quick visual
  check without a full `ground_query_plan()` run.
- **Fixed:** added `"pending at store officer"` as an additional alias on `mrs_ready_for_approval` -- the
  exact real word order the model actually produces (verified against the live model in fix.md #17, not
  guessed here), not a theoretical alternative.
- **Second gap found in the same verification pass:** the same question also needs `material` as a listed
  dimension (to show which items are pending). That failed too -- the catalog's `material` concept only had
  `INVENTORY.INVITEMS.ITEM_NAME`, which needs an unverified join from the `mrs` domain anchor
  (`INVENTORY.MRS_TEMP`). `MRS_TEMP` carries its own denormalised `ITEM_NAME` (schema study said so; confirmed
  against the raw metadata file too -- `VARCHAR2(70)` nullable). Added it as a second column entry under the
  existing `material` concept, the same multi-table pattern `item_identifier` already uses.
- **Where:** `app/resources/business_schema_catalog.json` only (2 additions: 1 alias, 1 column). No code or
  prompt changed.
- **Tests:** 2 new in `test_schema_grounding.py`. 11 core suites plus the eval-classifier file: **342/342**.
- **Lesson, stated plainly:** "the model now produces the right shape" and "the plan now grounds" are two
  different claims, and only a full `ground_query_plan()` run proves the second one. Re-verifying an already-
  shipped fix's example before reusing it (rather than assuming it still means what the fixlog said) is what
  caught this -- worth doing before trusting any earlier "verified" claim that wasn't re-run end to end.

## 23. ✅ Two worked examples fixed what 3 rounds of declarative prompt-wording couldn't

- **Prompt:** "continue next plan" -- the few-shot step of the recommended plan (real questions, verified
  labels, few-shot before fine-tuning). Targeted the two patterns that were fragile under pure declarative
  rules today: stock's operation-choice (fix.md #15 needed 3 wording rounds and never fixed "do we have yarn
  in stock") and mrs's approval-stage entity-fusion (fix.md #17/#22).
- **What was added:** two worked examples (real question -> the exact verified-correct QueryPlan JSON,
  confirmed against the real grounding code before use -- see fix.md #22, found while doing exactly that) in
  `SYSTEM_PROMPT`, right before "Return one JSON object only." Not new rules -- the existing declarative rules
  were left exactly as they were.
- **Verified against the real model, one pass, no whack-a-mole this time:**
  - "do we have yarn in stock" -- the ONE stock case that survived all 3 declarative-wording rounds (fix.md
    #15) -- **now grounds cleanly**, 2/2 repeats.
  - All previously-working stock cases stayed correct (no over-anchoring on the example's exact wording).
  - "list out material hold at Store officer?" (fix.md #17/#22's still-open case) **now fuses into one
    entity** ("hold at store officer") without a second example ever mentioning "hold" -- the model
    generalized the fusion *pattern* from the one "pending" example. Still correctly rejects at grounding
    (no catalog data distinguishes "hold" per stage -- unchanged, still an open scope question, not resolved
    here) but now with one honest rejection instead of a confusing two-entity split.
  - Both real non-tiny `PASS_PIPELINE` cases (MRS-by-number, purchase-item-code) unaffected.
- **One pre-existing failure found during this check, confirmed NOT a regression:** "issue for yarn" hits
  `QueryPlanValidationError` 3/3 -- verified via `git stash` that this fails identically 3/3 *without* this
  change too. Unrelated, pre-existing, not chased further here.
- **Where:** `app/query_plan_extractor.py` (`SYSTEM_PROMPT` only).
- **Tests:** 1 new (asserts both examples are present in `SYSTEM_PROMPT`; effectiveness verified live above).
  11 core suites plus the eval-classifier file: **343/343**.
- **Takeaway, worth carrying forward:** for a pattern that resists declarative wording, a worked example is
  worth trying before a third or fourth attempt at the sentence -- it fixed the case that resisted 3 rounds
  of wording changes, on the first try, with no observed side effects.

## 24. ✅ grn measure-fidelity: one worked example shipped, two dropped after a real regression

- **Prompt:** 2026-09-25, continuing the plan's few-shot step onto grn's remaining diagnosed gaps (fix.md
  #18's "Related, NOT fixed" list): measure-phrase fidelity, the `business_subject`-as-dimension question,
  and the generic "domain for entity, no verb" operation-choice gap.
- **All three examples verified correct in isolation first** (against real `ground_query_plan()` +
  `validate_query_plan_semantics()`, same discipline as fix.md #22/#23) -- confirmed along the way that
  `material` genuinely grounds as a dimension on the `grn` domain anchor via the existing `GRN.CODE ->
  INVITEMS.ITEM_CODE` FK, so "Today received material names and qty?" really was an extraction-choice
  problem (wrong `business_subject`), not a grounding-strictness one, exactly as fix.md #18 suspected but
  left undecided.
- **All three added together caused a real regression, caught before shipping:** verified against the real
  model that all three of the target grn questions grounded cleanly -- but 2 of the only 3 real
  `PASS_PIPELINE` cases in the whole eval broke, both by fabricating extra measures that don't exist
  ("MRS details for MRS number 890330" invented `qty requested`/`approval status`/`material`/`supplier`;
  "purchase order for item code C02000094" invented `purchase order details`). Reproduced 2/2, not a flake.
- **Isolated the cause instead of abandoning the whole attempt:** reverted all three, then re-added just the
  measure-fidelity example alone. That one alone fixes its target with zero regressions across the full
  battery (stock, mrs-pending, mrs-hold unchanged, both real passes). The other two -- individually or in
  combination -- are what caused the fabrication; not isolated further than that (would be a 3rd/4th
  diagnostic round on top of an already-diagnosed problem, matching the same "stop, don't force it"
  discipline as fix.md #15/#17).
- **Shipped:** one worked example (`"how many qty received in last one year?"` -> `aggregate`,
  `measures:[{"concept":"qty received","aggregation":"sum"}]`). **Not shipped:** the material-dimension
  example and the bare-domain-for-entity example -- both correct in isolation, but their combination with
  each other or with the others produces fabricated measures on real passing cases. Left as a genuinely
  open problem, not silently dropped without a trace.
- **Where:** `app/query_plan_extractor.py` (`SYSTEM_PROMPT`, +1 worked example).
- **Tests:** 1 new (asserts the shipped example's content in `SYSTEM_PROMPT`). 11 core suites plus the
  eval-classifier file: **344/344**.
- **Takeaway:** few-shot isn't risk-free just because it beat declarative wording once (fix.md #23) -- each
  new example still needs the same regression battery as a prompt-sentence change, and combining several at
  once can interact in ways that adding one at a time reveals cleanly.

## 25. 🟡 Purchase deep-dive: a high-value extraction bug confirmed real, few-shot repeatedly regresses one specific real pass

- **Prompt:** "start" -- the purchase deep-dive, same cluster-by-cluster method that took stock 30%->70%.
  Pulled the actual `full_query_plan` for every purchase `GROUNDING_FAILURE` in the 2026-09-25 run (11 of 20)
  rather than guess from the reason strings alone.
- **Found: a widespread, high-value bug shared by 6 of the 11.** The model puts the raw spoken value itself
  as the entity concept -- `concept="Keyboard"`, `concept="BARCODE SCANNER"`, `concept="dell system"`,
  `concept="monitor"` -- instead of `concept="material"` with the value in `original_value`. The existing
  prompt already says "material for an item name" (§ near the entity rules); the model just doesn't follow
  it reliably for bare, unquoted names, quoted or not, "item" cue word or not. This is upstream of and
  distinct from fix.md #16's fix (which only forces real verification when `concept` is already one of the
  5 correct tokens -- it can't help when the concept string itself is wrong).
- **Verified a fix, twice, both times with a real regression on the same real pass:**
  - Round 1: one worked example (bare material -> `concept="material"`). Fixed all 6 target questions'
    entity concept cleanly. But broke both real non-tiny `PASS_PIPELINE` cases: `"MRS details for MRS
    number 890330"` got a fabricated concept (`"material requisition slip number"`), and `"purchase order
    for item code C02000094"` got a fabricated measure (`"purchase order"`) that doesn't exist. Reverted.
  - Round 2 (diagnostic, not a fix attempt): swapped the new example in for fix.md #24's grn one (same
    total example count, to separate "too many examples" from "this example's content"). The MRS case
    recovered (differently -- now grounds via an accidental, still-wrong `material` match), but
    `"purchase order for item code C02000094"` **broke the same way again**, ruling out "example count"
    as the cause. This is the second time this exact question has broken from a nearby purchase-domain
    example (fix.md #24 broke it the same way, with a different fabricated measure name) -- a real,
    repeatable fragility in this specific model around this specific question shape, not a fluke.
- **Shipped only the safe part:** a `"purchase qty"` catalog alias (found in the same investigation --
  `purchase_quantity` had `"purchase quantity"` but not the abbreviated form several real questions use).
  Pure catalog addition, verified to cause zero model-behavior change on its own (grounding-only, doesn't
  touch extraction).
- **Not shipped, left open, not silently dropped:** the material-concept-naming bug itself. It's real,
  confirmed, and worth roughly 6 of purchase's 11 grounding failures if fixed cleanly -- but two rounds of
  few-shot both hit the same wall. This needs a different approach next time: maybe a declarative sentence
  instead of an example (the reverse of fix.md #15/#23's usual lesson, since few-shot is what's unstable
  here), maybe restructuring where in the prompt examples sit, or maybe accepting this specific interaction
  needs dedicated, careful iteration on its own rather than being bundled into a broader sweep.
- **Also found, not yet investigated:** "purchase date" used as a `measure` when the catalog correctly
  restricts that column's role to `date_filter`/`grouping` (a `detail`-with-sorting shape, like the existing
  "last N" rule already describes, is probably the right fix -- but that rule apparently isn't reliably
  applied when the thing being asked about is a bare date field rather than a quantity). "purchase details"
  as an invented, non-existent measure for a generic "show me details" phrasing. One domain-misrouting bug
  ("last supply of mouse" extracted as `domain='stock'` instead of `purchase`). None of these attempted yet.
- **Where:** `app/resources/business_schema_catalog.json` only (1 alias). `app/query_plan_extractor.py`
  tried and reverted twice -- no net change there this round.
- **Tests:** 1 new in `test_schema_grounding.py`. 11 core suites plus the eval-classifier file: **345/345**.

## 26. 🟡 Purchase concept-naming bug, round 3: a declarative sentence also regresses a real pass -- three
different techniques, three different real questions broken, same prompt region

- **Prompt:** "continue with that" -- following up on fix.md #25's own suggestion to try a declarative
  sentence instead of a worked example, since few-shot was what broke twice.
- **What was tried:** extended the existing "material for an item name" rule (already in the prompt) with
  five concrete named examples ("keyboard", "barcode scanner", "monitor", "printer toner", "dell system")
  and one clarifying clause ("material names a *kind* of entity, it is never itself the value of that
  entity") -- a plain rule-text edit, not a new Question/Output pair.
- **Partial win on the target bug:** of the 8 real purchase questions confirmed broken by this pattern,
  3 now ground correctly with `concept="material"` (`"BARCODE SCANNER"`, `"dell system"`,
  `"keyboard last purchase date"`). 1 more got the concept right but still fails for an unrelated,
  pre-existing reason (a bare recency word, "latest", gets extracted as its own spurious entity). 4 stayed
  broken (`"Keyboard"`, `"printer toner"`, `"computer monitor"`, `"monitor"`) -- no discernible pattern
  found yet for why these four didn't move (quoting doesn't explain it: `keyboard last purchase date` fixed
  unquoted, `printer toner` stayed broken unquoted).
- **New regression, cleanly isolated:** `"Which supplier is given lowest price?"` -- a real, currently
  shipped `PASS_PIPELINE` case with no material entity at all -- failed extraction outright (invalid
  QueryPlan even after the one correction retry). Reproduced 3/3 with the change in place. Controlled with
  `git stash push -- app/query_plan_extractor.py` and re-ran the identical 3 calls against the unmodified
  prompt, same session, same tunnel, same model: 3/3 passed. Not model-serving flakiness (fix.md #14) --
  a real, deterministic-within-this-session regression caused by this specific edit.
- **Why this matters more than a third failed attempt:** fix.md #24 broke a real pass via one worked
  example; fix.md #25 broke a different real pass via a different worked example; this round broke a
  *third* real pass via a declarative sentence -- not a worked example at all. Three different techniques,
  three different casualties, all in the same prompt region (purchase's entity/material rules). That
  rules out "few-shot specifically is unstable" as the explanation -- the more accurate read is that this
  region of the prompt is fragile to *any* addition right now, regardless of mechanism.
- **Reverted, matching the same discipline as #24/#25.** `git checkout -- app/query_plan_extractor.py`;
  confirmed zero diff. Did not attempt a fourth prompt variant.
- **Recommendation for next time, not yet implemented:** stop iterating on the prompt for this specific
  bug and fix it where the architecture already says fixes belong -- deterministic code. The failure
  signature is mechanical and detectable without any new LLM judgment call: the model already puts the
  raw value in both `concept` and `original_value` when it makes this mistake, and legitimate
  status/workflow entities (`concept="pending"`, `concept="pending at store officer"`, etc.) already
  ground successfully on their literal phrase today (their alias is in the catalog), so a fallback that
  only fires *after* an entity's literal `concept` fails to match anything in the catalog, retried once as
  `concept="material"`, would never touch a case that already works -- and if the fallback's own guess is
  wrong too, entity resolution's existing fail-closed behavior (fix.md #2) turns it into a safe refusal,
  never a wrong answer. Not implemented this round -- it touches `app/schema_grounding.py`'s core entity
  loop, which is safety-relevant (§3), and changing *where* a fix lives (prompt vs. deterministic code) is
  a design fork worth flagging before writing it, not just doing silently.
- **Where:** nothing shipped this round -- `app/query_plan_extractor.py` diff is zero.
- **Tests:** none new (nothing shipped). 11 core suites plus the eval-classifier file re-confirmed:
  **345/345**.

## 27. ✅ Purchase concept-naming bug, fixed deterministically -- first attempt at fix.md #26's own
recommendation caught its own gap before shipping

- **Prompt:** "lets do as you recommend" -- implementing fix.md #26's proposal: fix the concept-naming bug
  in deterministic code instead of the prompt, since three prompt-side attempts had each broken a
  different real pass.
- **First attempt (caught, not shipped): a grounding-only retry was insufficient.** The obvious version --
  in `ground_query_plan`'s entity loop, if an entity's literal `concept` matches nothing, retry once as
  `concept="material"` -- looked right and passed all 48 existing grounding tests unchanged. But tracing
  the *rest* of the pipeline (not just grounding) surfaced a real gap before shipping: `resolve_entity`
  (`app/entity_resolution.py:177`) only forces real Oracle verification for a concept in
  `resolvable_concepts()` (fix.md #16); it never sees grounding's internal decision, only the plan's own
  `entity.concept` field, which a grounding-only fix never rewrites. Worse, the prompt already tells the
  model to set `status="not_required"` for exactly this case (any concept outside the 5 canonical tokens)
  -- and `build_bind_parameters` (`app/nlp_execution.py:528`) skips binding *any* value for a
  `NOT_REQUIRED` entity entirely. So a grounding-only fix would have let the pipeline run further, then
  fail with `ParameterBindingError("Generated SQL contains unresolved bind parameters.")` -- safe (still no
  wrong answer), but pointless: it would never have produced a single new `PASS_PIPELINE`, just moved the
  same refusal to a later, less clear stage. Caught by tracing `execute_nlp_query`'s real call order
  end-to-end before trusting the passing unit tests; reverted before writing a single test for it.
- **Placed the fix where it actually reaches every consumer:** a new `correct_mislabeled_entity_concepts`
  in `app/schema_grounding.py` (reusing its existing `_matching_columns`/`_matching_compound_concept`
  alias lookups) that rewrites an entity's `concept` field itself, on the `QueryPlan`, to `"material"` --
  but only when it matches neither a real catalog column concept nor a compound-condition concept. A
  genuine status/workflow entity (`concept="pending"`, `"pending at store officer"`, ...) always matches
  the second lookup on its own literal phrase -- exactly how it already grounds today -- so it is never
  touched; verified directly against the real model's own actual output for the mrs worked example, not
  just a hand-built fixture. Wired into `app/nlp_execution.py`'s `execute_nlp_query`, called once, right
  after `extract_plan` and before `resolve_entities` -- the one point upstream of every downstream consumer
  (resolution, capability, grounding, SQL generation, bind construction), so one rewrite fixes all of them
  consistently instead of patching each separately.
- **Verified against the real model (qwen3:14b, SSH tunnel), extraction only re-run, no prompt touched:**
  6 of the 8 previously-confirmed-broken purchase questions now ground cleanly end to end (`concept`
  corrected, then grounds). 1 more gets the concept fixed but is still blocked by a different, already-
  documented gap ("purchase rate" as a *dimension* has no verified column -- fix.md #25's own leftover
  list, unrelated to this fix). The 3 previously fragile real passes (`"MRS details for MRS number
  890330"`, `"purchase order for item code C02000094"`, and now also `"list out material approval pending
  at Store officer"` and `"do we have yarn in stock"` checked for good measure) all ground unchanged --
  their concepts were already valid tokens, so the correction is confirmed a no-op for them, not a rewrite
  that happens to net out the same.
- **One question, "Which supplier is given lowest price?", failed extraction again** -- but this time
  provably unrelated to this fix: `app/query_plan_extractor.py` has a zero diff against `HEAD` (confirmed
  via `git diff --stat` at the moment of the failure), and my correction function only ever runs *after*
  extraction already returned a plan, so it cannot be the cause of an extraction-time failure. Re-ran the
  identical call 4/4 times, unmodified prompt, and got 4/4 failures -- roughly 20-30 minutes after this
  exact question passed 3/3 with the very same unmodified prompt earlier in this session (fix.md #26's
  isolation control). Model-serving non-determinism drifting across time, not across a single burst --
  the same phenomenon fix.md #14 already documented ("yarn": 3/3 fail one day, 7/7 pass the next), just
  observed at a finer time grain. Not chased, matching this session's own established practice.
- **Tests:** 7 new in `test_schema_grounding.py` (the pure correction function, including the two
  safety-critical negative cases -- a genuine status phrase and a real identifier concept must stay
  untouched) + 1 new in `test_nlp_execution.py` (proves the orchestration wiring: `resolve_entities`
  receives the corrected concept, not the model's raw one). 11 core suites plus the eval-classifier file:
  **353/353**.
- **Confirmed live on the server (same day): the largest single-fix jump this session.** Deployed and
  re-ran the identical held-out test split. **purchase 25%→50%, grn 14%→43%, mrs 20%→30%; stock
  unchanged at 70%** (proves no regression to the two entities this fix must never touch --
  `"pending at store officer"` and stock's own `"material"` -- both confirmed unchanged question-by-
  question, not just in the aggregate). Purchase's `GROUNDING_FAILURE` fell from 11 to 1; almost all of
  it converted to `ENTITY_AMBIGUOUS_DEFERRED` (a real "did you mean X/Y/Z" against actual Oracle master
  data) or a clean `ENTITY_RESOLUTION_REJECTION` -- both safe, neither a new failure shape, no
  `HARNESS_FAILURE`/crash category appeared anywhere. One brand-new `PASS_PIPELINE` in grn
  (`"how many qty received in last one year?"`). The other previously-fragile real pass,
  `"purchase order for item code C02000094"`, stayed `PASS_PIPELINE`. One purchase pass
  (`"Which supplier is given lowest price?"`) flipped to `ENTITY_RESOLUTION_REJECTION` -- confirmed
  unrelated (see above: same unmodified prompt, 3/3 then 4/4, ~20-30 minutes apart). Full detail and the
  question-by-question diff: `docs/V1_FREEZE_CRITERIA.md`.
- **Where:** `app/schema_grounding.py` (+1 function), `app/nlp_execution.py` (1 import + 2 lines wiring it
  in), `scripts/test_schema_grounding.py` (+7 tests), `scripts/test_nlp_execution.py` (+1 test),
  `docs/V1_FREEZE_CRITERIA.md`.

## Not fixes (do not do)
- Switching to Qwen3:14b/30B before #1 is classified.
- Wiring RAG/Qdrant into the runtime.
- Rewriting the QueryPlan contract.
- Loosening any validator to make a question pass.
