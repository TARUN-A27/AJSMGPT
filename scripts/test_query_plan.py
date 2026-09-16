from __future__ import annotations

import json
import unittest
from pathlib import Path
import sys

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.query_plan import (
    Ambiguity,
    Aggregation,
    BusinessSubject,
    EntityReference,
    EntityStatus,
    FilterOperator,
    Measure,
    QueryFilter,
    QueryPlan,
    SortDirection,
    SortInstruction,
)


def purchase_plan(**overrides) -> QueryPlan:
    values = {
        "original_question": "What was total purchase value for yarn last quarter?",
        "domain": "procurement",
        "operation": "aggregate",
        "business_subject": BusinessSubject(concept="purchase"),
        "measures": [Measure(concept="purchase_value", aggregation=Aggregation.SUM)],
        "confidence": 0.9,
    }
    values.update(overrides)
    return QueryPlan(**values)


class QueryPlanContractTests(unittest.TestCase):
    def test_valid_purchase_aggregate_plan(self) -> None:
        plan = purchase_plan(limit=100)
        self.assertEqual(plan.measures[0].aggregation, Aggregation.SUM)
        self.assertFalse(plan.requires_clarification)

    def test_valid_grouped_supplier_plan(self) -> None:
        plan = purchase_plan(
            operation="grouped_aggregate",
            dimensions=[{"concept": "supplier", "grouping": True}],
            sorting=[SortInstruction(field_concept="purchase_value", direction=SortDirection.DESC, priority=0)],
        )
        self.assertTrue(plan.dimensions[0].grouping)

    def test_blocking_ambiguity_requires_clarification(self) -> None:
        self.assertTrue(purchase_plan(ambiguities=[Ambiguity(field="period", reason="unclear", blocking=True)]).requires_clarification)

    def test_low_confidence_requires_clarification(self) -> None:
        self.assertTrue(purchase_plan(confidence=0.64).requires_clarification)

    def test_ambiguous_entity_requires_clarification(self) -> None:
        entity = EntityReference(concept="supplier", candidates=["Alpha", "Alfa"], confidence=0.7, status=EntityStatus.AMBIGUOUS)
        self.assertTrue(purchase_plan(entities=[entity]).requires_clarification)

    def test_resolved_entity_without_selected_value_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EntityReference(concept="supplier", confidence=0.9, status=EntityStatus.RESOLVED)

    def test_ambiguous_entity_with_fewer_than_two_candidates_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EntityReference(concept="supplier", candidates=["Alpha"], confidence=0.6, status=EntityStatus.AMBIGUOUS)

    def test_confidence_outside_range_is_rejected(self) -> None:
        for confidence in (-0.01, 1.01):
            with self.subTest(confidence=confidence), self.assertRaises(ValidationError):
                purchase_plan(confidence=confidence)

    def test_zero_or_negative_limit_is_rejected(self) -> None:
        for limit in (0, -1):
            with self.subTest(limit=limit), self.assertRaises(ValidationError):
                purchase_plan(limit=limit)

    def test_limit_above_maximum_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            purchase_plan(limit=1001)

    def test_invalid_between_filter_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            QueryFilter(concept="amount", operator=FilterOperator.BETWEEN, value=[10], value_type="number")

    def test_empty_question_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            purchase_plan(original_question="   ")

    def test_empty_semantic_content_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            QueryPlan(original_question="Show something", domain="general", operation="retrieve", confidence=0.9)

    def test_serializes_to_plain_json_compatible_dictionary(self) -> None:
        data = purchase_plan().model_dump(mode="json")
        self.assertIsInstance(data, dict)
        self.assertEqual(json.loads(json.dumps(data))["measures"][0]["aggregation"], "sum")


if __name__ == "__main__":
    unittest.main(verbosity=2)
