from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.query_plan import EntityStatus
from app.query_plan_extractor import (
    QueryPlanExtractionError,
    QueryPlanResponseError,
    QueryPlanValidationError,
    extract_query_plan,
)
from app.spacy_nlp import analyze_question_with_spacy


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
    def test_default_runtime_path_disables_thinking_and_bounds_output(self) -> None:
        with patch("app.query_plan_extractor.chat_with_qwen", return_value=plan_json(original_question="Question")) as chat:
            extract_query_plan("Question")
        self.assertEqual(
            chat.call_args.kwargs,
            {"think": False, "temperature": 0.0, "num_predict": 1000},
        )

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
        with self.assertRaises(QueryPlanValidationError):
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
        with self.assertRaises(QueryPlanValidationError):
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

    def test_compact_spacy_evidence_is_included(self) -> None:
        question = "Show top 10 suppliers by purchase value last month"
        call = Calls(plan_json(
            original_question=question,
            operation="ranking",
            dimensions=[{"concept": "supplier", "grouping": True}],
            sorting=[{"field_concept": "value", "direction": "desc", "priority": 0}],
            limit=10,
        ))
        extract_query_plan(question, model_call=call, nlp_analysis=analyze_question_with_spacy(question))
        prompt = call.calls[0][1]
        self.assertIn('"ranking_limit":10', prompt)
        self.assertIn('"primary_operation":"ranking"', prompt)
        self.assertIn('"has_explicit_time_grouping":false', prompt)
        self.assertIn('"time_grouping_granularity":null', prompt)
        self.assertNotIn('"lemmas"', prompt)
        self.assertNotIn('"meaningful_tokens"', prompt)

    def test_injected_two_argument_model_call_remains_compatible(self) -> None:
        call = Calls(plan_json(original_question="Question"))
        self.assertEqual(extract_query_plan("Question", model_call=call).original_question, "Question")

    @patch("app.ollama_client.requests.post")
    def test_chat_defaults_preserve_existing_payload(self, post: Mock) -> None:
        from app.ollama_client import chat_with_qwen

        post.return_value.json.return_value = {"message": {"content": "ok"}}
        chat_with_qwen("system", "user")
        payload = post.call_args.kwargs["json"]
        self.assertNotIn("think", payload)
        self.assertEqual(payload["options"], {"temperature": 0.1})

    @patch("app.ollama_client.requests.post")
    def test_chat_optional_controls_are_sent(self, post: Mock) -> None:
        from app.ollama_client import chat_with_qwen

        post.return_value.json.return_value = {"message": {"content": "ok"}}
        chat_with_qwen("system", "user", think=False, num_predict=900, temperature=0.0)
        payload = post.call_args.kwargs["json"]
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["options"]["num_predict"], 900)
        self.assertEqual(payload["options"]["temperature"], 0.0)

    def test_semantic_failure_is_corrected_once(self) -> None:
        question = "Show top 10 suppliers by purchase value in the last 6 months"
        invalid = plan_json(
            original_question=question,
            operation="ranking",
            dimensions=[{"concept": "supplier", "grouping": False}],
            sorting=[{"field_concept": "value", "direction": "desc", "priority": 0}],
            limit=10,
        )
        corrected = plan_json(
            original_question=question,
            operation="ranking",
            dimensions=[{"concept": "supplier", "grouping": True}],
            sorting=[{"field_concept": "value", "direction": "desc", "priority": 0}],
            limit=10,
        )
        call = Calls(invalid, corrected)
        plan = extract_query_plan(question, model_call=call, nlp_analysis=analyze_question_with_spacy(question))
        self.assertTrue(plan.dimensions[0].grouping)
        self.assertEqual(len(call.calls), 2)
        self.assertIn("Semantic violations:", call.calls[1][1])

    def test_unused_month_dimension_is_corrected(self) -> None:
        question = "Show purchase value in the last 6 months"
        invalid = plan_json(
            original_question=question,
            dimensions=[{"concept": "month", "grouping": False}],
        )
        corrected = plan_json(original_question=question)
        call = Calls(invalid, corrected)
        self.assertEqual(
            extract_query_plan(question, model_call=call, nlp_analysis=analyze_question_with_spacy(question)).dimensions,
            [],
        )

    def test_date_filter_only_month_grouping_is_normalized_without_retry(self) -> None:
        question = "Show top 10 suppliers by purchase value in the last 6 months"
        raw = plan_json(
            original_question=question,
            operation="ranking",
            dimensions=[
                {"concept": "supplier", "grouping": True},
                {"concept": "month", "grouping": True},
            ],
            date_range={"kind": "relative", "original_text": "last 6 months"},
            sorting=[{"field_concept": "value", "direction": "desc", "priority": 0}],
            limit=10,
            requested_output={"fields": ["supplier", "value", "month"]},
        )
        call = Calls(raw)
        result = extract_query_plan(question, model_call=call, nlp_analysis=analyze_question_with_spacy(question))
        self.assertEqual([dimension.concept for dimension in result.dimensions], ["supplier"])
        self.assertEqual(result.requested_output.fields, ["supplier", "value"])
        self.assertEqual(len(call.calls), 1)

    def test_explicit_month_grouping_is_retained(self) -> None:
        question = "Show monthly purchase value by supplier in the last 6 months"
        raw = plan_json(
            original_question=question,
            operation="trend",
            dimensions=[
                {"concept": "month", "grouping": True},
                {"concept": "supplier", "grouping": True},
            ],
            date_range={"kind": "relative", "original_text": "last 6 months"},
            requested_output={"fields": ["month", "supplier", "value"]},
        )
        result = extract_query_plan(question, model_call=Calls(raw), nlp_analysis=analyze_question_with_spacy(question))
        self.assertEqual([dimension.concept for dimension in result.dimensions], ["month", "supplier"])
        self.assertIn("month", result.requested_output.fields)

    def test_malformed_then_semantic_failure_has_no_extra_retry(self) -> None:
        question = "Show top 10 suppliers by purchase value"
        semantic_error = plan_json(
            original_question=question,
            operation="ranking",
            dimensions=[{"concept": "supplier", "grouping": False}],
            sorting=[{"field_concept": "value", "direction": "desc", "priority": 0}],
            limit=10,
        )
        call = Calls("{broken", semantic_error)
        with self.assertRaises(QueryPlanValidationError):
            extract_query_plan(question, model_call=call, nlp_analysis=analyze_question_with_spacy(question))
        self.assertEqual(len(call.calls), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
