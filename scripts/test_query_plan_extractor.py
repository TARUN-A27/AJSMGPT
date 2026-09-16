from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.query_plan import EntityStatus
from app.query_plan_extractor import (
    QueryPlanExtractionError,
    QueryPlanResponseError,
    QueryPlanValidationError,
    extract_query_plan,
)


def plan_json(**overrides) -> str:
    value = {
        "original_question": "placeholder",
        "domain": "purchase",
        "operation": "aggregate",
        "business_subject": {"concept": "purchases"},
        "measures": [{"concept": "value", "aggregation": "sum"}],
        "confidence": 0.9,
    }
    value.update(overrides)
    return json.dumps(value)


class Calls:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return str(result)


class QueryPlanExtractorTests(unittest.TestCase):
    def test_purchase_aggregate(self) -> None:
        call = Calls(plan_json(original_question="What is total purchases?"))
        plan = extract_query_plan("What is total purchases?", model_call=call)
        self.assertEqual((plan.domain, plan.operation), ("purchase", "aggregate"))

    def test_mrs_detail(self) -> None:
        call = Calls(plan_json(original_question="Show MRS details", domain="mrs", operation="detail", business_subject={"concept": "MRS"}))
        self.assertEqual(extract_query_plan("Show MRS details", model_call=call).domain, "mrs")

    def test_consumption_trend(self) -> None:
        call = Calls(plan_json(original_question="Show consumption by month", domain="consumption", operation="trend", business_subject={"concept": "consumption"}))
        self.assertEqual(extract_query_plan("Show consumption by month", model_call=call).operation, "trend")

    def test_unknown_blocking_ambiguity(self) -> None:
        call = Calls(plan_json(original_question="Show movement", domain="unknown", operation="unknown", business_subject={"concept": "movement"}, confidence=0.4, ambiguities=[{"field": "meaning", "reason": "movement has multiple business meanings", "blocking": True}]))
        self.assertTrue(extract_query_plan("Show movement", model_call=call).requires_clarification)

    def test_code_fenced_json(self) -> None:
        call = Calls("```json\n" + plan_json() + "\n```")
        self.assertEqual(extract_query_plan("Question", model_call=call).domain, "purchase")

    def test_prose_around_json(self) -> None:
        call = Calls("Result follows: " + plan_json() + " End.")
        self.assertEqual(extract_query_plan("Question", model_call=call).confidence, 0.9)

    def test_empty_question_does_not_call_model(self) -> None:
        call = Calls(plan_json())
        with self.assertRaises(QueryPlanExtractionError):
            extract_query_plan("  ", model_call=call)
        self.assertEqual(call.calls, [])

    def test_no_json_object(self) -> None:
        call = Calls("not structured", "still not structured")
        with self.assertRaises(QueryPlanResponseError):
            extract_query_plan("Question", model_call=call)
        self.assertEqual(len(call.calls), 2)

    def test_invalid_confidence(self) -> None:
        call = Calls(plan_json(confidence=2), plan_json(confidence=2))
        with self.assertRaises(QueryPlanValidationError):
            extract_query_plan("Question", model_call=call)

    def test_invalid_resolved_entity(self) -> None:
        entity = {"concept": "supplier", "confidence": 0.9, "status": "resolved"}
        call = Calls(plan_json(entities=[entity]), plan_json(entities=[entity]))
        with self.assertRaises(QueryPlanValidationError):
            extract_query_plan("Question", model_call=call)

    def test_malformed_first_response_then_correction(self) -> None:
        call = Calls("{broken", plan_json(original_question="Question"))
        self.assertEqual(extract_query_plan("Question", model_call=call).original_question, "Question")
        self.assertIn("Previous model response: {broken", call.calls[1][1])

    def test_two_invalid_responses_stop_after_one_correction(self) -> None:
        call = Calls("{broken", "{also broken")
        with self.assertRaises(QueryPlanResponseError):
            extract_query_plan("Question", model_call=call)
        self.assertEqual(len(call.calls), 2)

    def test_network_error_is_focused(self) -> None:
        call = Calls(RuntimeError("connection detail"))
        with self.assertRaisesRegex(QueryPlanExtractionError, "Unable to obtain a model response"):
            extract_query_plan("Question", model_call=call)

    def test_serialization_round_trip(self) -> None:
        call = Calls(plan_json(original_question="Question"))
        plan = extract_query_plan("Question", model_call=call)
        restored = type(plan).model_validate(json.loads(plan.model_dump_json()))
        self.assertEqual(restored, plan)
        self.assertNotEqual(EntityStatus.UNRESOLVED.value, "resolved")


if __name__ == "__main__":
    unittest.main(verbosity=2)
