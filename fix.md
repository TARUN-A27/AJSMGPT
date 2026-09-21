# AJSMGPT — Fix List

Source: `scripts/v1_real_question_eval_results.json` (47 questions). `UNSUPPORTED_EXPECTED` (13) are by design and not listed.

Status: ⬜ open · 🔄 in progress · ✅ fixed

## 1. 🔄 Evaluator does not save raw model output on QueryPlan failure
- **Status 2026-09-21:** evaluator fixed (guard removed, `cause` field added). Re-run of the 20 pending → then ✅.
- **Count:** 20 × `QUERY_PLAN_FAILURE`, all with reason `Corrected model JSON did not satisfy the QueryPlan contract.`
- **Evidence:** 0 of the 20 have `raw_model_calls` populated; only passing/later-stage records do.
- **Where:** `scripts/evaluate_v1_real_questions.py`, `app/query_plan_extractor.py`
- **Root cause (located):** `_run_one` in the evaluator already records every `chat_with_qwen` response into `capture["raw_model_calls"]`, but then explicitly drops it — `if record["classification"] != "QUERY_PLAN_FAILURE": record["raw_model_calls"] = ...` — for exactly this bucket. Separately, `QueryPlanValidationError` is raised `from` the pydantic `ValidationError`, so the failing-field detail exists in `exc.__cause__` but is never persisted.
- **Fix:** remove the `!= "QUERY_PLAN_FAILURE"` guard so `raw_model_calls` is stored for every record, and persist `str(exc.__cause__)` (pydantic field errors) alongside `reason`. Evaluator-only change; no production code.
- **Blocks:** every other QueryPlan decision. Do not touch prompts, contract, or model size before this.
- **Categories affected:** purchase_analytics 5, mrs 5, inventory_movement 4, supplier_purchase 2, other 4.

## 2. ⬜ Entity resolution rejections
- **Count:** 9 × `ENTITY_RESOLUTION_REJECTION` (purchase_analytics 7, supplier_purchase 2)
- **Reasons:** `Entity '<concept>' requires verified resolution before execution.` on all 9; two also carry dimension/date-justification rejections.
- **Where:** `app/nlp_execution.py` execution gate. The resolver **is wired**: `_default_resolve_entities` → `app/entity_resolution.py:resolve_plan_entities(plan, oracle_entity_lookup)`. (`entity_resolver.py` / `full_value_resolver.py` are the unwired older ones.) The eval swaps in the offline fixture lookup from `scripts/test_entity_resolution.py`, so no Oracle is touched.
- **Split:**
  - 4 × non-canonical concept from the model — `item` (MONITOR, BARCODE SCANNER, dell system), `vendor` (dell). `resolvable_concepts()` only knows `supplier`, `supplier_name`, `supplier_identifier`, `material`, `item_identifier`, so these fail regardless of data. **Model/ontology issue.**
  - 5 × correct concept, value not in the 6-row offline fixture (`mouse` ×3, `printer toner`, and `supplier` where Qwen used the literal word as the value on a ranking question). Expected offline; recheck on real master data (progress.md Step 8). The `supplier`-as-value case is a model issue.
- **Fix:** for the 4, adjust QueryPlan prompt/ontology so Qwen emits catalog-canonical concept names; add a `test_query_plan_extractor.py` case per pattern. Do not add aliases to `_VERIFIED_SOURCES` to paper over it. Do not resolve entities via the LLM.
- **Depends on:** #1 result (some may reclassify once QueryPlans are correct).

## 3. ⬜ MRS `lookup` operation unsupported
- **Count:** 3 × `CAPABILITY_FAILURE` (mrs)
- **Reason:** `domain='mrs': Operation 'lookup' is not supported for the V1 mrs family`
- **Where:** `app/v1_capabilities.py`, `app/resources/v1_query_capabilities.json`
- **Fix:** either add a verified `lookup` capability for the mrs family (needs catalog backing) or mark these questions `expected_unsupported`.

## 4. ⬜ Grounding: concept has no verified column
- **Count:** 2 × `GROUNDING_FAILURE` (purchase_analytics)
- **Reason:** `No verified V1 column supports this required concept.`
- **Where:** `app/schema_grounding.py`, `app/resources/business_schema_catalog.json`
- **Fix:** identify the concept; add the column to the catalog only if it is verified against real schema metadata (`data/multi_schema_metadata.json`). Otherwise mark unsupported. Never guess.

## 5. ⬜ Zero end-to-end passes
- `PASS_PIPELINE = 0`, `SQL_GENERATION_FAILURE = 0`, `SQL_VALIDATION_FAILURE = 0` — SQL stages are untested by real questions because nothing reaches them.
- Resolves itself as #1–#4 land; track first `PASS_PIPELINE > 0` in progress.md.

## Not fixes (do not do)
- Switching to Qwen3:14b/30B before #1 is classified.
- Wiring RAG/Qdrant into the runtime.
- Rewriting the QueryPlan contract.
- Loosening any validator to make a question pass.
