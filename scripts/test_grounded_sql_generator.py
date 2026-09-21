from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from app.grounded_sql_generator import (
    GroundedSqlModelUnavailableError,
    GroundedSqlResult,
    generate_grounded_sql,
)
from app.grounded_sql_validator import UnsafeGroundedSqlError
from app.query_plan import (
    Aggregation,
    BusinessSubject,
    DateRange,
    DateRangeKind,
    Dimension,
    EntityReference,
    EntityStatus,
    Measure,
    QueryPlan,
    RequestedOutput,
    SortDirection,
    SortInstruction,
)
from app.schema_grounding import ground_query_plan
from scripts.test_grounded_sql_validator import PURCHASE_RANKING_SQL, purchase_ranking_plan


def response(sql: str, *, assumptions: list[str] | None = None) -> str:
    return json.dumps({
        "sql": sql,
        "selected_fields": ["supplier", "total_purchase_value"],
        "applied_filters": ["last six months"],
        "assumptions": assumptions or [],
        "confidence": 0.94,
    })


class GroundedSqlGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = purchase_ranking_plan()
        self.grounding = ground_query_plan(self.plan)

    def test_valid_result_is_typed_and_uses_bounded_grounded_prompt(self) -> None:
        calls: list[tuple[str, str]] = []

        def model_call(system: str, user: str) -> str:
            calls.append((system, user))
            return response(PURCHASE_RANKING_SQL)

        result = generate_grounded_sql(self.plan, self.grounding, model_call=model_call)
        self.assertIsInstance(result, GroundedSqlResult)
        self.assertEqual(result.sql, PURCHASE_RANKING_SQL)
        self.assertEqual(len(calls), 1)
        prompt = calls[0][1]
        self.assertIn("INVENTORY.PURCHASEORDER", prompt)
        self.assertIn("SCM.PARTYMASTER", prompt)
        self.assertIn("required_output_display_columns", prompt)
        self.assertIn("join_identifier_columns_not_output_substitutes", prompt)
        self.assertIn("FETCH FIRST N ROWS ONLY", calls[0][0])
        self.assertIn("assumptions must always be an empty list", calls[0][0])
        self.assertNotIn("multi_schema_metadata", prompt)
        self.assertNotIn("evaluation", prompt.lower())

    def test_selected_fields_follow_select_outputs_not_raw_measure_column(self) -> None:
        raw = json.loads(response(PURCHASE_RANKING_SQL))
        raw["selected_fields"] = ["pm.PARTYNAME", "po.NET"]
        result = generate_grounded_sql(
            self.plan, self.grounding, model_call=lambda _system, _user: json.dumps(raw)
        )
        self.assertEqual(result.selected_fields, ["supplier", "total_purchase_value"])
        self.assertNotIn("po.NET", result.selected_fields)

    def test_invalid_response_is_corrected_once(self) -> None:
        replies = iter((response("SELECT * FROM INVENTORY.PURCHASEORDER"), response(PURCHASE_RANKING_SQL)))
        calls: list[str] = []

        def model_call(_system: str, user: str) -> str:
            calls.append(user)
            return next(replies)

        result = generate_grounded_sql(self.plan, self.grounding, model_call=model_call)
        self.assertEqual(result.sql, PURCHASE_RANKING_SQL)
        self.assertEqual(len(calls), 2)
        self.assertIn("Validation failure", calls[1])
        self.assertIn("Previous structured result", calls[1])

    def test_correction_receives_all_violations_and_fixes_them(self) -> None:
        invalid_sql = """SELECT po.SUP_CODE AS supplier, SUM(po.NET) AS value
FROM INVENTORY.PURCHASEORDER po
WHERE po.ORDERDATE >= ADD_MONTHS(TRUNC(SYSDATE), -6)
GROUP BY po.SUP_CODE
ORDER BY value DESC
LIMIT 10"""
        replies = iter((
            response(invalid_sql, assumptions=["SUP_CODE identifies a supplier"]),
            response(PURCHASE_RANKING_SQL),
        ))
        calls: list[str] = []

        def model_call(_system: str, user: str) -> str:
            calls.append(user)
            return next(replies)

        result = generate_grounded_sql(self.plan, self.grounding, model_call=model_call)
        self.assertEqual(result.sql, PURCHASE_RANKING_SQL)
        self.assertEqual(len(calls), 2)
        correction = calls[1]
        self.assertIn("Assumptions must be empty", correction)
        self.assertIn("Oracle SQL does not support LIMIT", correction)
        self.assertIn("PARTYNAME", correction)
        self.assertIn("FETCH FIRST 10 ROWS ONLY", correction)

    def test_month_date_correction_prompt_requires_calendar_month_logic(self) -> None:
        invalid_sql = PURCHASE_RANKING_SQL.replace(
            "ADD_MONTHS(TRUNC(SYSDATE), -6)", "TRUNC(SYSDATE - 182.5)"
        )
        replies = iter((response(invalid_sql), response(PURCHASE_RANKING_SQL)))
        prompts: list[str] = []

        def model_call(_system: str, user: str) -> str:
            prompts.append(user)
            return next(replies)

        result = generate_grounded_sql(self.plan, self.grounding, model_call=model_call)
        self.assertEqual(result.sql, PURCHASE_RANKING_SQL)
        self.assertEqual(len(prompts), 2)
        self.assertIn("ADD_MONTHS(TRUNC(SYSDATE), -6)", prompts[1])
        self.assertIn("TRUNC(SYSDATE) + 1", prompts[1])

    def test_day_date_correction_prompt_requires_two_predicates(self) -> None:
        plan = self.plan.model_copy(update={"date_range": self.plan.date_range.model_copy(update={"original_text": "last 30 days"})})
        grounding = ground_query_plan(plan)
        invalid_sql = PURCHASE_RANKING_SQL.replace("ADD_MONTHS(TRUNC(SYSDATE), -6)", "TRUNC(SYSDATE) - 30").replace("AND po.ORDERDATE < TRUNC(SYSDATE) + 1", "")
        corrected_sql = PURCHASE_RANKING_SQL.replace("ADD_MONTHS(TRUNC(SYSDATE), -6)", "TRUNC(SYSDATE) - 30")
        replies = iter((response(invalid_sql), response(corrected_sql)))
        prompts: list[str] = []
        def model_call(_system: str, user: str) -> str:
            prompts.append(user)
            return next(replies)
        generate_grounded_sql(plan, grounding, model_call=model_call)
        self.assertIn("TRUNC(SYSDATE) - N", prompts[1])
        self.assertIn("TRUNC(SYSDATE) + 1", prompts[1])

    def test_two_invalid_responses_fail_safely(self) -> None:
        model_call = Mock(return_value=response("SELECT * FROM INVENTORY.PURCHASEORDER"))
        with self.assertRaises(UnsafeGroundedSqlError):
            generate_grounded_sql(self.plan, self.grounding, model_call=model_call)
        self.assertEqual(model_call.call_count, 2)

    def test_model_unavailable_has_focused_error(self) -> None:
        def unavailable(_system: str, _user: str) -> str:
            raise ConnectionError("offline")

        with self.assertRaises(GroundedSqlModelUnavailableError):
            generate_grounded_sql(self.plan, self.grounding, model_call=unavailable)

    def test_default_model_call_is_non_thinking_deterministic_and_bounded(self) -> None:
        with patch("app.grounded_sql_generator.chat_with_qwen", return_value=response(PURCHASE_RANKING_SQL)) as chat:
            generate_grounded_sql(self.plan, self.grounding)
        self.assertEqual(chat.call_args.kwargs["think"], False)
        self.assertEqual(chat.call_args.kwargs["temperature"], 0.0)
        self.assertLessEqual(chat.call_args.kwargs["num_predict"], 1500)


# -- Phase 9b regressions ----------------------------------------------------
# One test per real historical-probe question. The "bad" first attempt in
# each case is the actual raw SQL Qwen produced during the Phase 9b probe
# (before this fix); the "good" second attempt is what the strengthened
# prompt asks for and what the validator already accepts.

def supplier_last_supply_plan() -> QueryPlan:
    return QueryPlan(
        original_question="last supply from supplier Prime compu systems",
        domain="purchase", operation="detail",
        business_subject=BusinessSubject(concept="purchase"),
        entities=[EntityReference(
            concept="supplier_name", original_value="Prime compu systems",
            selected_value="Prime Compu Systems", status=EntityStatus.RESOLVED, confidence=0.9,
        )],
        dimensions=[Dimension(concept="purchase date", grouping=False)],
        sorting=[SortInstruction(field_concept="purchase date", direction=SortDirection.DESC, priority=0)],
        requested_output=RequestedOutput(fields=["purchase date"]),
        confidence=0.85,
    )


def material_last_purchase_supplier_name_plan() -> QueryPlan:
    return QueryPlan(
        original_question="mouse last purchased supplier name?",
        domain="purchase", operation="detail",
        business_subject=BusinessSubject(concept="purchase"),
        entities=[EntityReference(
            concept="material", original_value="mouse",
            selected_value="MOUSE", status=EntityStatus.RESOLVED, confidence=0.9,
        )],
        dimensions=[Dimension(concept="purchase date", grouping=False)],
        sorting=[SortInstruction(field_concept="purchase date", direction=SortDirection.DESC, priority=0)],
        limit=1,
        requested_output=RequestedOutput(fields=["purchase date"]),
        confidence=0.8,
    )


def supplier_absolute_date_plan() -> QueryPlan:
    return QueryPlan(
        original_question="latest purchase order for supplier 800967 in 2026",
        domain="purchase", operation="detail",
        business_subject=BusinessSubject(concept="purchase"),
        entities=[EntityReference(
            concept="supplier", original_value="800967",
            selected_value="Prime Compu Systems", status=EntityStatus.RESOLVED, confidence=0.9,
        )],
        dimensions=[Dimension(concept="purchase date", grouping=False)],
        sorting=[SortInstruction(field_concept="purchase date", direction=SortDirection.DESC, priority=0)],
        date_range=DateRange(kind=DateRangeKind.ABSOLUTE, start="2026-01-01", end="2026-12-31", original_text="in 2026"),
        requested_output=RequestedOutput(fields=["purchase date"]),
        confidence=0.85,
    )


def material_qty_limit_plan() -> QueryPlan:
    return QueryPlan(
        original_question='Last 5 purchase qty of "BARCODE SCANNER"',
        domain="purchase", operation="detail",
        business_subject=BusinessSubject(concept="purchase"),
        entities=[EntityReference(
            concept="material", original_value="BARCODE SCANNER",
            selected_value="BARCODE SCANNER", status=EntityStatus.RESOLVED, confidence=0.9,
        )],
        measures=[Measure(concept="quantity", aggregation=Aggregation.NONE)],
        limit=5,
        requested_output=RequestedOutput(fields=["quantity"]),
        confidence=0.7,
    )


class Phase9bSqlGenerationRegressionTests(unittest.TestCase):
    def test_supplier_filter_omission_is_corrected_with_explicit_guidance(self) -> None:
        bad_sql = (
            "SELECT INVENTORY.PURCHASEORDER.ORDERDATE FROM INVENTORY.PURCHASEORDER "
            "JOIN SCM.PARTYMASTER ON INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE "
            "ORDER BY INVENTORY.PURCHASEORDER.ORDERDATE DESC"
        )
        good_sql = (
            "SELECT INVENTORY.PURCHASEORDER.ORDERDATE FROM INVENTORY.PURCHASEORDER "
            "JOIN SCM.PARTYMASTER ON INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE "
            "WHERE SCM.PARTYMASTER.PARTYNAME = :supplier_name "
            "ORDER BY INVENTORY.PURCHASEORDER.ORDERDATE DESC"
        )
        plan = supplier_last_supply_plan()
        grounding = ground_query_plan(plan)
        replies = iter((response(bad_sql), response(good_sql)))
        calls: list[str] = []

        def model_call(_system: str, user: str) -> str:
            calls.append(user)
            return next(replies)

        result = generate_grounded_sql(plan, grounding, model_call=model_call)
        self.assertEqual(result.sql, good_sql)
        self.assertEqual(len(calls), 2)
        self.assertIn("supplier_name", calls[1])
        self.assertIn("equality bind", calls[1])

    def test_material_filter_omission_is_corrected_with_explicit_guidance(self) -> None:
        bad_sql = (
            "SELECT INVENTORY.PURCHASEORDER.ORDERDATE, INVENTORY.INVITEMS.ITEM_NAME "
            "FROM INVENTORY.PURCHASEORDER JOIN INVENTORY.INVITEMS "
            "ON INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE "
            "ORDER BY INVENTORY.PURCHASEORDER.ORDERDATE DESC FETCH FIRST 1 ROWS ONLY"
        )
        good_sql = (
            "SELECT INVENTORY.PURCHASEORDER.ORDERDATE, INVENTORY.INVITEMS.ITEM_NAME "
            "FROM INVENTORY.PURCHASEORDER JOIN INVENTORY.INVITEMS "
            "ON INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE "
            "WHERE INVENTORY.INVITEMS.ITEM_NAME = :material "
            "ORDER BY INVENTORY.PURCHASEORDER.ORDERDATE DESC FETCH FIRST 1 ROWS ONLY"
        )
        plan = material_last_purchase_supplier_name_plan()
        grounding = ground_query_plan(plan)
        replies = iter((response(bad_sql), response(good_sql)))
        calls: list[str] = []

        def model_call(_system: str, user: str) -> str:
            calls.append(user)
            return next(replies)

        result = generate_grounded_sql(plan, grounding, model_call=model_call)
        self.assertEqual(result.sql, good_sql)
        self.assertEqual(len(calls), 2)
        self.assertIn("material", calls[1])
        self.assertIn("equality bind", calls[1])

    def test_absolute_date_to_date_literal_is_corrected_to_named_binds(self) -> None:
        bad_sql = (
            "SELECT INVENTORY.PURCHASEORDER.ORDERDATE FROM INVENTORY.PURCHASEORDER "
            "WHERE INVENTORY.PURCHASEORDER.ORDERDATE >= TO_DATE('2026-01-01', 'YYYY-MM-DD') "
            "AND INVENTORY.PURCHASEORDER.ORDERDATE < TO_DATE('2027-01-01', 'YYYY-MM-DD') "
            "ORDER BY INVENTORY.PURCHASEORDER.ORDERDATE DESC"
        )
        good_sql = (
            "SELECT INVENTORY.PURCHASEORDER.ORDERDATE FROM INVENTORY.PURCHASEORDER "
            "WHERE INVENTORY.PURCHASEORDER.SUP_CODE = :supplier "
            "AND INVENTORY.PURCHASEORDER.ORDERDATE BETWEEN :date_start AND :date_end "
            "ORDER BY INVENTORY.PURCHASEORDER.ORDERDATE DESC"
        )
        plan = supplier_absolute_date_plan()
        grounding = ground_query_plan(plan)
        replies = iter((response(bad_sql), response(good_sql)))
        calls: list[str] = []

        def model_call(_system: str, user: str) -> str:
            calls.append(user)
            return next(replies)

        result = generate_grounded_sql(plan, grounding, model_call=model_call)
        self.assertEqual(result.sql, good_sql)
        self.assertEqual(len(calls), 2)
        self.assertIn("TO_DATE", calls[1])
        self.assertIn("named bind parameters", calls[1])

    def test_material_filter_omission_with_limit_is_corrected(self) -> None:
        bad_sql = (
            "SELECT INVENTORY.PURCHASEORDER.ITEM_CODE, INVENTORY.PURCHASEORDER.QTY "
            "FROM INVENTORY.PURCHASEORDER JOIN INVENTORY.INVITEMS "
            "ON INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE "
            "ORDER BY INVENTORY.PURCHASEORDER.ITEM_CODE FETCH FIRST 5 ROWS ONLY"
        )
        good_sql = (
            "SELECT INVENTORY.PURCHASEORDER.ITEM_CODE, INVENTORY.PURCHASEORDER.QTY "
            "FROM INVENTORY.PURCHASEORDER JOIN INVENTORY.INVITEMS "
            "ON INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE "
            "WHERE INVENTORY.INVITEMS.ITEM_NAME = :material "
            "FETCH FIRST 5 ROWS ONLY"
        )
        plan = material_qty_limit_plan()
        grounding = ground_query_plan(plan)
        replies = iter((response(bad_sql), response(good_sql)))
        calls: list[str] = []

        def model_call(_system: str, user: str) -> str:
            calls.append(user)
            return next(replies)

        result = generate_grounded_sql(plan, grounding, model_call=model_call)
        self.assertEqual(result.sql, good_sql)
        self.assertEqual(len(calls), 2)
        self.assertIn("material", calls[1])

    def test_system_prompt_states_entity_bind_and_absolute_date_rules(self) -> None:
        calls: list[tuple[str, str]] = []

        def model_call(system: str, user: str) -> str:
            calls.append((system, user))
            return response(PURCHASE_RANKING_SQL)

        generate_grounded_sql(purchase_ranking_plan(), ground_query_plan(purchase_ranking_plan()), model_call=model_call)
        system_prompt = calls[0][0]
        self.assertIn("exactly one equality comparison", system_prompt)
        self.assertIn("never a bind parameter for the row count", system_prompt)
        self.assertIn("never use TO_DATE", system_prompt)

    def test_absolute_date_guidance_reaches_the_prompt(self) -> None:
        plan = supplier_absolute_date_plan()
        grounding = ground_query_plan(plan)
        calls: list[tuple[str, str]] = []

        def model_call(system: str, user: str) -> str:
            calls.append((system, user))
            return response(
                "SELECT INVENTORY.PURCHASEORDER.ORDERDATE FROM INVENTORY.PURCHASEORDER "
                "WHERE INVENTORY.PURCHASEORDER.SUP_CODE = :supplier "
                "AND INVENTORY.PURCHASEORDER.ORDERDATE BETWEEN :date_start AND :date_end "
                "ORDER BY INVENTORY.PURCHASEORDER.ORDERDATE DESC"
            )

        generate_grounded_sql(plan, grounding, model_call=model_call)
        prompt = calls[0][1]
        self.assertIn("Absolute date range requires two named bind placeholders", prompt)
        self.assertIn("Never TO_DATE", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
