from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.query_plan import EntityStatus
from app.query_plan_extractor import (
    SYSTEM_PROMPT,
    QueryPlanExtractionError,
    QueryPlanResponseError,
    QueryPlanValidationError,
    extract_query_plan,
)
from app.spacy_nlp import analyze_question_with_spacy
from app.v1_capabilities import evaluate_capability
from app.entity_resolution import resolvable_concepts


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
    def test_missing_relative_date_text_is_restored_from_spacy(self) -> None:
        analysis = analyze_question_with_spacy("show purchases in the last 30 days")
        call = Calls(plan_json(
            original_question="placeholder",
            date_range={"kind": "relative", "start": "30 days ago", "end": "now", "original_text": None},
        ))
        plan = extract_query_plan("show purchases in the last 30 days", model_call=call, nlp_analysis=analysis)
        self.assertEqual(plan.date_range.original_text, "last 30 days")

    def test_non_empty_relative_date_text_is_preserved(self) -> None:
        analysis = analyze_question_with_spacy("show purchases in the last 30 days")
        call = Calls(plan_json(date_range={"kind": "relative", "start": "30 days ago", "end": "now", "original_text": "past 30 days"}))
        plan = extract_query_plan("show purchases in the last 30 days", model_call=call, nlp_analysis=analysis)
        self.assertEqual(plan.date_range.original_text, "past 30 days")
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

    def test_corrected_input_preserves_raw_original_question(self) -> None:
        call = Calls(plan_json(original_question="show supplier purchase quantity"))
        result = extract_query_plan(
            "show supplier purchase quantity",
            original_question="show suplier purchse qunatity",
            model_call=call,
        )
        self.assertEqual(result.original_question, "show suplier purchse qunatity")

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

    def test_correction_round_keeps_full_instructions(self) -> None:
        # fix.md #6 cross-cutting: in the 47-question eval the retry returned the
        # first JSON unchanged in 19/20 cases. The retry used to run with the
        # system prompt "Return one JSON object only." -- no question, no
        # extraction rules -- so the model had nothing to correct against.
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
        extract_query_plan(question, model_call=call, nlp_analysis=analyze_question_with_spacy(question))
        retry_system, retry_user = call.calls[1]
        self.assertEqual(retry_system, SYSTEM_PROMPT)
        self.assertIn(f"Question: {question}", retry_user)
        self.assertIn("do not return the previous JSON unchanged", retry_user)

    # -- unsupported domains bypass semantic validation (fix.md #6, gate ordering)

    def test_unsupported_domain_plan_is_returned_for_the_capability_gate(self) -> None:
        # Real Qwen shape for "how many qty received in last one year?" made
        # deliberately semantically INVALID (sort on a concept the plan never
        # declares): with the bypass removed this raises. Domain grn is not a
        # V1 family, so evaluate_capability rejects it and the plan must come
        # back for that gate to say "GRN is not supported".
        question = "how many qty received in last one year?"
        response = plan_json(
            original_question=question,
            domain="grn",
            operation="detail",
            business_subject={"concept": "receipt"},
            measures=[{"concept": "quantity"}],
            sorting=[{"field_concept": "ghost column", "direction": "desc", "priority": 0}],
            date_range={"kind": "relative", "original_text": "last one year"},
            requested_output={"fields": ["quantity"]},
            confidence=0.8,
        )
        call = Calls(response)
        plan = extract_query_plan(question, model_call=call, nlp_analysis=analyze_question_with_spacy(question))
        self.assertEqual(plan.domain, "grn")
        self.assertFalse(evaluate_capability(plan).supported)
        self.assertEqual(len(call.calls), 1)

    def test_capability_supported_plan_is_validated_regardless_of_domain_label(self) -> None:
        # Review finding: evaluate_capability maps ANY domain with
        # operation=lookup + a supplier/material subject to a supported
        # family, and accepts domain aliases (purchasing, po, ...). Such
        # plans can execute, so they must never skip semantic validation.
        question = "who supplies keyboard"
        lookup_with_ghost_sort = plan_json(
            original_question=question,
            domain="grn",
            operation="lookup",
            business_subject={"concept": "supplier"},
            measures=[],
            dimensions=[{"concept": "supplier", "grouping": False}],
            entities=[{"concept": "material", "original_value": "keyboard"}],
            sorting=[{"field_concept": "ghost column", "direction": "desc", "priority": 0}],
        )
        call = Calls(lookup_with_ghost_sort, lookup_with_ghost_sort)
        with self.assertRaises(QueryPlanValidationError):
            extract_query_plan(question, model_call=call, nlp_analysis=analyze_question_with_spacy(question))
        self.assertEqual(len(call.calls), 2)

        question = "rank suppliers by purchase value"
        alias_domain_bad_ranking = plan_json(
            original_question=question,
            domain="purchasing",
            operation="ranking",
            dimensions=[{"concept": "supplier", "grouping": False}],
            sorting=[],
            limit=None,
        )
        call = Calls(alias_domain_bad_ranking, alias_domain_bad_ranking)
        with self.assertRaises(QueryPlanValidationError):
            extract_query_plan(question, model_call=call, nlp_analysis=analyze_question_with_spacy(question))
        self.assertEqual(len(call.calls), 2)

    def test_supported_domain_plan_is_still_semantically_validated(self) -> None:
        question = "Show top 10 suppliers by purchase value in the last 6 months"
        invalid = plan_json(
            original_question=question,
            operation="ranking",
            dimensions=[{"concept": "supplier", "grouping": False}],
            sorting=[{"field_concept": "value", "direction": "desc", "priority": 0}],
            limit=10,
        )
        call = Calls(invalid, invalid)
        with self.assertRaises(QueryPlanValidationError):
            extract_query_plan(question, model_call=call, nlp_analysis=analyze_question_with_spacy(question))
        self.assertEqual(len(call.calls), 2)

    def test_system_prompt_entity_concepts_match_the_resolver(self) -> None:
        # The prompt's allowed entity concepts must be exactly the resolver's
        # verified sources, so the two cannot drift apart.
        match = re.search(r"must be exactly one of these\s+tokens:\s*([^.]+)\.", SYSTEM_PROMPT)
        self.assertIsNotNone(match)
        listed = {token.strip() for token in match.group(1).split(",")}
        self.assertEqual(listed, set(resolvable_concepts()))
        flat = " ".join(SYSTEM_PROMPT.split())
        self.assertIn("never use the value itself as the concept", flat)
        self.assertIn("Always set business_subject", flat)
        self.assertIn("A filtered entity is not also a dimension", flat)

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

    # -- deterministic post-parse overrides ----------------------------------

    def test_explicit_recency_limit_overrides_hallucinated_date_range(self) -> None:
        # The exact real-world bug: Qwen mis-parsed "Last 5" as a date range
        # instead of a row limit.
        question = 'Last 5 purchase qty of "BARCODE SCANNER"'
        response = plan_json(
            original_question=question,
            operation="detail",
            business_subject={"concept": "purchase"},
            measures=[{"concept": "quantity", "aggregation": "none"}],
            entities=[{"concept": "material", "original_value": "barcode scanner"}],
            date_range={"kind": "relative", "original_text": "last 5"},
            confidence=0.7,
        )
        plan = extract_query_plan(
            question, model_call=Calls(response), nlp_analysis=analyze_question_with_spacy(question),
        )
        self.assertEqual(plan.limit, 5)
        self.assertIsNone(plan.date_range)

    def test_recency_limit_does_not_clear_a_genuine_date_range(self) -> None:
        question = "last 3 purchases in 2026"
        response = plan_json(
            original_question=question,
            operation="detail",
            business_subject={"concept": "purchase"},
            date_range={"kind": "relative", "original_text": "in 2026"},
            confidence=0.8,
        )
        plan = extract_query_plan(
            question, model_call=Calls(response), nlp_analysis=analyze_question_with_spacy(question),
        )
        self.assertEqual(plan.limit, 3)
        self.assertIsNotNone(plan.date_range)
        self.assertEqual(plan.date_range.start, "2026-01-01")
        self.assertEqual(plan.date_range.end, "2026-12-31")

    def test_explicit_year_sets_absolute_date_range(self) -> None:
        question = "last purchase rate of barcode scanner in 2026"
        response = plan_json(
            original_question=question,
            operation="detail",
            business_subject={"concept": "purchase"},
            measures=[{"concept": "rate", "aggregation": "none"}],
            dimensions=[{"concept": "purchase date", "grouping": False}],
            sorting=[{"field_concept": "purchase date", "direction": "desc", "priority": 0}],
            requested_output={"fields": ["purchase date"]},
            confidence=0.8,
        )
        plan = extract_query_plan(
            question, model_call=Calls(response), nlp_analysis=analyze_question_with_spacy(question),
        )
        self.assertEqual(plan.date_range.kind.value, "absolute")
        self.assertEqual(plan.date_range.start, "2026-01-01")
        self.assertEqual(plan.date_range.end, "2026-12-31")

    def test_explicit_yyyymmdd_sets_single_day_date_range(self) -> None:
        question = "current attendance for empcode 165224 on 20260212"
        response = plan_json(
            original_question=question,
            operation="lookup",
            business_subject={"concept": "attendance"},
            confidence=0.8,
        )
        plan = extract_query_plan(
            question, model_call=Calls(response), nlp_analysis=analyze_question_with_spacy(question),
        )
        self.assertEqual(plan.date_range.start, "2026-02-12")
        self.assertEqual(plan.date_range.end, "2026-02-12")

    def test_quoted_entity_text_is_corrected_to_exact_verbatim(self) -> None:
        question = 'Last 5 purchase details of "dell system"'
        response = plan_json(
            original_question=question,
            operation="detail",
            business_subject={"concept": "purchase"},
            entities=[{"concept": "material", "original_value": "Dell System"}],
            dimensions=[{"concept": "purchase date", "grouping": False}],
            sorting=[{"field_concept": "purchase date", "direction": "desc", "priority": 0}],
            requested_output={"fields": ["purchase date"]},
            confidence=0.9,
        )
        plan = extract_query_plan(
            question, model_call=Calls(response), nlp_analysis=analyze_question_with_spacy(question),
        )
        self.assertEqual(plan.entities[0].original_value, "dell system")

    def test_recency_direction_corrects_sort_direction(self) -> None:
        question = "last supply of mouse"
        response = plan_json(
            original_question=question,
            operation="detail",
            business_subject={"concept": "purchase"},
            entities=[{"concept": "material", "original_value": "mouse"}],
            dimensions=[{"concept": "purchase date", "grouping": False}],
            sorting=[{"field_concept": "purchase date", "direction": "asc", "priority": 0}],
            requested_output={"fields": ["purchase date"]},
            confidence=0.85,
        )
        plan = extract_query_plan(
            question, model_call=Calls(response), nlp_analysis=analyze_question_with_spacy(question),
        )
        self.assertEqual(plan.sorting[0].direction.value, "desc")

    def test_first_supply_sets_ascending_sort_direction(self) -> None:
        question = "first supply of mouse"
        response = plan_json(
            original_question=question,
            operation="detail",
            business_subject={"concept": "purchase"},
            entities=[{"concept": "material", "original_value": "mouse"}],
            dimensions=[{"concept": "purchase date", "grouping": False}],
            sorting=[{"field_concept": "purchase date", "direction": "desc", "priority": 0}],
            requested_output={"fields": ["purchase date"]},
            confidence=0.85,
        )
        plan = extract_query_plan(
            question, model_call=Calls(response), nlp_analysis=analyze_question_with_spacy(question),
        )
        self.assertEqual(plan.sorting[0].direction.value, "asc")

    def test_superlative_ranking_without_limit_defaults_to_one(self) -> None:
        question = "Which supplier is given lowest price?"
        response = plan_json(
            original_question=question,
            operation="ranking",
            business_subject={"concept": "supplier"},
            measures=[{"concept": "rate", "aggregation": "minimum"}],
            dimensions=[{"concept": "supplier", "grouping": True}],
            sorting=[{"field_concept": "rate", "direction": "asc", "priority": 0}],
            confidence=0.8,
        )
        plan = extract_query_plan(
            question, model_call=Calls(response), nlp_analysis=analyze_question_with_spacy(question),
        )
        self.assertEqual(plan.limit, 1)

    def test_compound_ranking_direction_does_not_get_default_limit(self) -> None:
        # "highest and lowest" is genuinely compound: no deterministic
        # limit=1 default should be applied, so the plan is left to fail the
        # existing "ranking requires a positive limit" rule instead of
        # silently picking one direction.
        question = "list who is given highest rate and who is given lowest rate?"
        response = plan_json(
            original_question=question,
            operation="ranking",
            business_subject={"concept": "supplier"},
            measures=[
                {"concept": "rate", "aggregation": "maximum"},
                {"concept": "rate", "aggregation": "minimum"},
            ],
            dimensions=[{"concept": "supplier", "grouping": True}],
            confidence=0.7,
        )
        call = Calls(response, response)
        with self.assertRaises(QueryPlanValidationError):
            extract_query_plan(question, model_call=call, nlp_analysis=analyze_question_with_spacy(question))

    # -- shape-preserving defaults --------------------------------------------

    def test_entity_status_defaults_to_unresolved_when_omitted(self) -> None:
        question = "last supply of mouse"
        response = plan_json(
            original_question=question,
            operation="detail",
            business_subject={"concept": "purchase"},
            entities=[{"concept": "material", "original_value": "mouse", "confidence": 0.9}],
            dimensions=[{"concept": "purchase date", "grouping": False}],
            sorting=[{"field_concept": "purchase date", "direction": "desc", "priority": 0}],
            requested_output={"fields": ["purchase date"]},
            confidence=0.85,
        )
        plan = extract_query_plan(
            question, model_call=Calls(response), nlp_analysis=analyze_question_with_spacy(question),
        )
        self.assertEqual(plan.entities[0].status.value, "unresolved")

    def test_sort_priority_defaults_to_zero_when_omitted(self) -> None:
        question = "last supply of mouse"
        response = plan_json(
            original_question=question,
            operation="detail",
            business_subject={"concept": "purchase"},
            dimensions=[{"concept": "purchase date", "grouping": False}],
            sorting=[{"field_concept": "purchase date", "direction": "desc"}],
            requested_output={"fields": ["purchase date"]},
            confidence=0.85,
        )
        plan = extract_query_plan(
            question, model_call=Calls(response), nlp_analysis=analyze_question_with_spacy(question),
        )
        self.assertEqual(plan.sorting[0].priority, 0)

    # -- correction-prompt enrichment ------------------------------------------

    def test_correction_prompt_carries_field_path_for_pydantic_errors(self) -> None:
        question = "Show top 10 suppliers by purchase value"
        bad_response = plan_json(
            original_question=question,
            sorting=[{"field_concept": "value", "direction": "descending"}],
        )
        call = Calls(bad_response, plan_json(original_question=question))
        extract_query_plan(question, model_call=call, nlp_analysis=analyze_question_with_spacy(question))
        retry_prompt = call.calls[1][1]
        self.assertIn("field: sorting.0.direction", retry_prompt)
        self.assertNotIn("Traceback", retry_prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
