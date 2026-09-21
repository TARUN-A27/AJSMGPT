from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.query_plan import QueryPlan
from app.query_plan_semantic_validator import (
    QueryPlanSemanticValidationError,
    has_conflicting_ranking_directions,
    normalize_recency_sorting,
    normalize_temporal_dimensions,
    validate_query_plan_semantics,
)
from app.spacy_nlp import analyze_question_with_spacy


def plan(**overrides) -> QueryPlan:
    data = {
        "original_question": "Show top 10 suppliers by purchase value in the last 6 months",
        "domain": "purchase",
        "operation": "ranking",
        "business_subject": {"concept": "purchases"},
        "measures": [{"concept": "value", "aggregation": "sum"}],
        "dimensions": [{"concept": "supplier", "grouping": True}],
        "sorting": [{"field_concept": "value", "direction": "desc", "priority": 0}],
        "limit": 10,
        "confidence": 0.9,
    }
    data.update(overrides)
    return QueryPlan.model_validate(data)


class QueryPlanSemanticValidatorTests(unittest.TestCase):
    def test_valid_ranking_plan(self) -> None:
        ranking = plan()
        self.assertIs(validate_query_plan_semantics(ranking), ranking)

    def test_ranking_dimension_must_group(self) -> None:
        invalid = plan(dimensions=[{"concept": "supplier", "grouping": False}])
        with self.assertRaisesRegex(QueryPlanSemanticValidationError, "grouping=true"):
            validate_query_plan_semantics(invalid)

    def test_date_range_does_not_justify_month_dimension(self) -> None:
        question = "Show purchase value in the last 6 months"
        invalid = plan(
            original_question=question,
            operation="aggregate",
            dimensions=[{"concept": "month", "grouping": False}],
            sorting=[],
            limit=None,
            date_range={"kind": "relative", "original_text": "last 6 months"},
        )
        normalized = validate_query_plan_semantics(invalid, analyze_question_with_spacy(question))
        self.assertNotIn("month", [item.concept for item in normalized.dimensions])

    def test_date_range_temporal_dimension_is_normalized(self) -> None:
        question = "Show top 10 suppliers by purchase value in the last 6 months"
        invalid = plan(
            dimensions=[
                {"concept": "supplier", "grouping": True},
                {"concept": "month", "grouping": True},
            ],
            requested_output={"fields": ["month", "supplier", "value"]},
            sorting=[
                {"field_concept": "month", "direction": "asc", "priority": 0},
                {"field_concept": "value", "direction": "desc", "priority": 1},
            ],
            date_range={"kind": "relative", "original_text": "last 6 months"},
        )
        normalized = normalize_temporal_dimensions(invalid, analyze_question_with_spacy(question))
        self.assertEqual([item.concept for item in normalized.dimensions], ["supplier"])
        self.assertEqual(normalized.requested_output.fields, ["supplier", "value"])
        self.assertEqual([item.field_concept for item in normalized.sorting], ["value"])

    def test_non_temporal_dimension_is_never_removed(self) -> None:
        question = "Show purchase value in the last 6 months"
        value = plan(
            original_question=question,
            dimensions=[{"concept": "supplier", "grouping": True}, {"concept": "month", "grouping": True}],
            date_range={"kind": "relative", "original_text": "last 6 months"},
        )
        normalized = normalize_temporal_dimensions(value, analyze_question_with_spacy(question))
        self.assertIn("supplier", [item.concept for item in normalized.dimensions])

    def test_explicit_month_grouping_is_allowed(self) -> None:
        question = "Show purchase value by month"
        monthly = plan(
            original_question=question,
            operation="aggregate",
            dimensions=[{"concept": "month", "grouping": True}],
            sorting=[],
            limit=None,
        )
        validate_query_plan_semantics(monthly, analyze_question_with_spacy(question))

    def test_per_month_grouping_uses_typed_spacy_signal(self) -> None:
        question = "Show purchase value per month in the last 6 months"
        monthly = plan(
            original_question=question,
            operation="aggregate",
            dimensions=[{"concept": "month", "grouping": True}],
            sorting=[],
            limit=None,
            date_range={"kind": "relative", "original_text": "last 6 months"},
        )
        result = validate_query_plan_semantics(monthly, analyze_question_with_spacy(question))
        self.assertEqual([dimension.concept for dimension in result.dimensions], ["month"])

    def test_trend_by_month_is_allowed(self) -> None:
        trend = plan(
            original_question="Show purchase value trend by month",
            operation="trend",
            dimensions=[{"concept": "month", "grouping": True}],
            sorting=[],
            limit=None,
        )
        validate_query_plan_semantics(trend, analyze_question_with_spacy(trend.original_question))

    def test_aggregate_output_requires_grouping(self) -> None:
        aggregate = plan(
            original_question="Show purchase value by supplier",
            operation="aggregate",
            dimensions=[{"concept": "supplier", "grouping": False}],
            requested_output={"fields": ["supplier", "value"]},
            sorting=[],
            limit=None,
        )
        with self.assertRaisesRegex(QueryPlanSemanticValidationError, "requires a grouping dimension"):
            validate_query_plan_semantics(aggregate)

    def test_valid_detail_plan_is_allowed(self) -> None:
        detail = plan(
            original_question="Show purchase details",
            operation="detail",
            measures=[],
            dimensions=[],
            sorting=[],
            limit=None,
            requested_output={"fields": ["supplier"]},
        )
        validate_query_plan_semantics(detail)

    # -- recency normalizer ---------------------------------------------------

    def test_recency_normalizer_adds_ungrouped_date_dimension(self) -> None:
        detail = plan(
            original_question="last purchase details of mouse",
            operation="detail",
            measures=[],
            dimensions=[],
            sorting=[{"field_concept": "purchase date", "direction": "desc", "priority": 0}],
            limit=None,
            requested_output={"fields": []},
        )
        normalized = validate_query_plan_semantics(detail)
        self.assertEqual([d.concept for d in normalized.dimensions], ["purchase date"])
        self.assertFalse(normalized.dimensions[0].grouping)
        self.assertIn("purchase date", normalized.requested_output.fields)

    def test_recency_normalizer_never_sets_grouping_true(self) -> None:
        detail = plan(
            operation="detail",
            measures=[],
            dimensions=[],
            sorting=[{"field_concept": "mrs date", "direction": "asc", "priority": 0}],
            limit=None,
            requested_output={"fields": []},
        )
        normalized = normalize_recency_sorting(detail)
        self.assertFalse(normalized.dimensions[0].grouping)

    def test_recency_normalizer_does_not_touch_non_date_sort_fields(self) -> None:
        # A blanket normalizer would silently bypass "sorting field
        # unavailable" for any hallucinated sort target; it must stay
        # scoped to date concepts only.
        detail = plan(
            operation="detail",
            measures=[],
            dimensions=[],
            sorting=[{"field_concept": "supplier rating", "direction": "desc", "priority": 0}],
            limit=None,
            requested_output={"fields": []},
        )
        with self.assertRaisesRegex(QueryPlanSemanticValidationError, "Sorting field 'supplier rating' is unavailable"):
            validate_query_plan_semantics(detail)

    def test_recency_normalizer_only_applies_to_detail_and_lookup(self) -> None:
        ranking = plan(
            operation="ranking",
            dimensions=[],
            sorting=[{"field_concept": "purchase date", "direction": "desc", "priority": 0}],
        )
        unchanged = normalize_recency_sorting(ranking)
        self.assertEqual(unchanged.dimensions, [])

    # -- lookup output exemption ------------------------------------------

    def test_lookup_dimension_without_output_field_is_allowed(self) -> None:
        lookup = plan(
            original_question="who supplies keyboard",
            operation="lookup",
            measures=[],
            dimensions=[{"concept": "supplier", "grouping": False}],
            sorting=[],
            limit=None,
            requested_output={"fields": []},
        )
        validate_query_plan_semantics(lookup)

    def test_detail_dimension_without_output_field_is_still_rejected(self) -> None:
        # Detail's existing behavior at this check must stay unchanged: only
        # "lookup" is exempted, not "detail".
        detail = plan(
            operation="detail",
            measures=[],
            dimensions=[{"concept": "supplier", "grouping": False}],
            sorting=[],
            limit=None,
            requested_output={"fields": []},
        )
        with self.assertRaisesRegex(QueryPlanSemanticValidationError, "does not affect grouping or requested output"):
            validate_query_plan_semantics(detail)

    # -- unknown operation on a supported domain -----------------------------

    def test_unknown_operation_on_supported_domain_is_rejected(self) -> None:
        invalid = plan(
            domain="purchase",
            operation="unknown",
            dimensions=[],
            sorting=[],
            limit=None,
            confidence=0.6,
        )
        with self.assertRaisesRegex(QueryPlanSemanticValidationError, "Operation 'unknown' is not usable"):
            validate_query_plan_semantics(invalid)

    def test_unknown_operation_on_unsupported_domain_is_not_caught_here(self) -> None:
        # Left to the capability gate downstream, which has the catalogued
        # unsupported-family message; this validator must not duplicate it.
        stock = plan(
            domain="stock",
            operation="unknown",
            dimensions=[],
            sorting=[],
            limit=None,
            confidence=0.5,
        )
        validate_query_plan_semantics(stock)

    # -- fail-closed: "due" without a nameable date concept ------------------

    def test_due_without_nameable_date_concept_requires_clarification(self) -> None:
        mrs_due = plan(
            original_question="MRS due in 2026",
            domain="mrs",
            operation="detail",
            measures=[],
            dimensions=[],
            sorting=[],
            limit=None,
            requested_output={"fields": []},
            date_range={"kind": "absolute", "start": "2026-01-01", "end": "2026-12-31", "original_text": "in 2026"},
        )
        with self.assertRaisesRegex(QueryPlanSemanticValidationError, "does not name which date concept"):
            validate_query_plan_semantics(mrs_due)

    def test_due_with_explicit_due_date_concept_is_allowed(self) -> None:
        mrs_due = plan(
            original_question="MRS due in 2026",
            domain="mrs",
            operation="detail",
            measures=[],
            dimensions=[{"concept": "mrs due date", "grouping": False}],
            sorting=[],
            limit=None,
            requested_output={"fields": ["mrs due date"]},
            date_range={"kind": "absolute", "start": "2026-01-01", "end": "2026-12-31", "original_text": "in 2026"},
        )
        validate_query_plan_semantics(mrs_due)

    # -- fail-closed: conflicting ranking directions -------------------------

    def test_conflicting_ranking_measures_are_rejected(self) -> None:
        conflicting = plan(
            original_question="list who is given highest rate and who is given lowest rate?",
            measures=[
                {"concept": "rate", "aggregation": "maximum"},
                {"concept": "rate", "aggregation": "minimum"},
            ],
            sorting=[{"field_concept": "rate", "direction": "desc", "priority": 0}],
        )
        with self.assertRaisesRegex(QueryPlanSemanticValidationError, "conflicting ranking directions"):
            validate_query_plan_semantics(conflicting)

    def test_has_conflicting_ranking_directions_detects_opposite_sort_instructions(self) -> None:
        conflicting = plan(
            measures=[{"concept": "rate", "aggregation": "sum"}],
            sorting=[
                {"field_concept": "rate", "direction": "asc", "priority": 0},
                {"field_concept": "rate", "direction": "desc", "priority": 1},
            ],
        )
        self.assertTrue(has_conflicting_ranking_directions(conflicting))

    def test_single_direction_ranking_is_not_conflicting(self) -> None:
        self.assertFalse(has_conflicting_ranking_directions(plan()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
