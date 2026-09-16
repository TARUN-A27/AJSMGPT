"""Schema-independent consistency checks for logical QueryPlans."""

from __future__ import annotations

import re

from app.query_plan import Aggregation, QueryPlan
from app.spacy_nlp import NLPAnalysis


class QueryPlanSemanticValidationError(ValueError):
    """A logical plan is structurally valid but semantically inconsistent."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = tuple(violations)
        super().__init__("; ".join(self.violations))


_DATE_DIMENSIONS = {"date", "month", "year"}
_DATE_GROUPING_WORDS = re.compile(r"\b(by\s+(?:date|month|year)|monthly|trend|over time)\b", re.IGNORECASE)
_TIME_SERIES_WORDS = re.compile(r"\b(?:time[- ]series|timeseries|chronological)\b", re.IGNORECASE)


def normalize_temporal_dimensions(plan: QueryPlan, nlp_analysis: NLPAnalysis | None = None) -> QueryPlan:
    """Remove date-range-induced temporal dimensions when no grouping was requested."""
    if nlp_analysis is None or nlp_analysis.has_explicit_time_grouping or plan.operation.lower() == "trend":
        return plan
    if not plan.date_range or _TIME_SERIES_WORDS.search(plan.original_question):
        return plan
    temporal = {"date", "month", "year"}
    evidenced_dimensions = {dimension.lower() for dimension in nlp_analysis.detected_dimensions}
    removable = {
        dimension.concept.lower()
        for dimension in plan.dimensions
        if dimension.concept.lower() in temporal and dimension.concept.lower() not in evidenced_dimensions
    }
    if not removable:
        return plan
    normalized = plan.model_copy(deep=True)
    normalized.dimensions = [dimension for dimension in normalized.dimensions if dimension.concept.lower() not in removable]
    normalized.requested_output.fields = [field for field in normalized.requested_output.fields if field.lower() not in removable]
    normalized.sorting = [instruction for instruction in normalized.sorting if instruction.field_concept.lower() not in removable]
    return normalized


def validate_query_plan_semantics(plan: QueryPlan, nlp_analysis: NLPAnalysis | None = None) -> QueryPlan:
    """Validate logical output relationships without relying on physical data sources."""
    plan = normalize_temporal_dimensions(plan, nlp_analysis)
    violations: list[str] = []
    dimensions = {dimension.concept.lower(): dimension for dimension in plan.dimensions}
    measures = {measure.concept.lower() for measure in plan.measures}
    measures.update(measure.alias.lower() for measure in plan.measures if measure.alias)
    grouping = {name for name, dimension in dimensions.items() if dimension.grouping}
    output_fields = {field.lower() for field in plan.requested_output.fields}
    has_aggregate = any(measure.aggregation is not Aggregation.NONE for measure in plan.measures)
    operation = plan.operation.lower()

    if operation == "ranking":
        if plan.limit is None or plan.limit <= 0:
            violations.append("Ranking requires a positive limit.")
        if not plan.sorting:
            violations.append("Ranking requires sorting.")
        if not grouping:
            violations.append("Ranking requires at least one grouping dimension.")
        if has_aggregate:
            for name, dimension in dimensions.items():
                if not dimension.grouping:
                    violations.append(f"Aggregate ranking dimension '{name}' must use grouping=true.")

    if operation == "aggregate" and has_aggregate:
        for field in output_fields - measures:
            dimension = dimensions.get(field)
            if dimension is None or not dimension.grouping:
                violations.append(f"Aggregate output field '{field}' requires a grouping dimension.")

    date_only_evidence = bool(nlp_analysis and nlp_analysis.date_expressions)
    date_grouping_requested = (
        bool(nlp_analysis and nlp_analysis.has_explicit_time_grouping)
        or bool(_DATE_GROUPING_WORDS.search(plan.original_question))
        or operation == "trend"
    )
    for name, dimension in dimensions.items():
        is_output = name in output_fields
        if not dimension.grouping and not is_output:
            violations.append(f"Dimension '{name}' does not affect grouping or requested output.")
        if name in _DATE_DIMENSIONS and date_only_evidence and not date_grouping_requested:
            violations.append(f"Date dimension '{name}' is not justified by a date range alone.")

    detail_request = operation == "detail"
    for field in output_fields:
        if field not in measures and field not in grouping and not detail_request:
            violations.append(f"Requested output field '{field}' is not an available measure or grouping dimension.")

    available_sort_fields = measures | set(dimensions)
    for instruction in plan.sorting:
        if instruction.field_concept.lower() not in available_sort_fields:
            violations.append(f"Sorting field '{instruction.field_concept}' is unavailable in the plan.")

    if plan.domain.lower() == "unknown" and plan.confidence >= 0.65:
        violations.append("Unknown domain cannot have high confidence.")
    if any(ambiguity.blocking for ambiguity in plan.ambiguities) and not plan.requires_clarification:
        violations.append("Blocking ambiguity must require clarification.")

    if violations:
        raise QueryPlanSemanticValidationError(violations)
    return plan
