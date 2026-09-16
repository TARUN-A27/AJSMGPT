from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.query_plan import QueryPlan
from app.query_plan_semantic_validator import QueryPlanSemanticValidationError, normalize_temporal_dimensions, validate_query_plan_semantics
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
