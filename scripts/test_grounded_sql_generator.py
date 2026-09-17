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


if __name__ == "__main__":
    unittest.main(verbosity=2)
