"""Schema-independent consistency checks for logical QueryPlans."""

from __future__ import annotations

import re

from app.query_plan import Aggregation, Dimension, QueryPlan, SortDirection
from app.spacy_nlp import NLPAnalysis
from app.v1_capabilities import load_capability_catalog


class QueryPlanSemanticValidationError(ValueError):
    """A logical plan is structurally valid but semantically inconsistent."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = tuple(violations)
        super().__init__("; ".join(self.violations))


_DATE_DIMENSIONS = {"date", "month", "year"}
_DATE_GROUPING_WORDS = re.compile(r"\b(by\s+(?:date|month|year)|monthly|trend|over time)\b", re.IGNORECASE)
_TIME_SERIES_WORDS = re.compile(r"\b(?:time[- ]series|timeseries|chronological)\b", re.IGNORECASE)
_DUE_WORD = re.compile(r"\bdue\b", re.IGNORECASE)
# Operations that may name a dimension purely as an output field, without
# also marking it grouping=true: "detail" already had this exemption; a
# lookup ("who supplies keyboard") is the same shape (one implied answer
# field, not a grouped report) and was missing it.
_OUTPUT_ONLY_OPERATIONS = {"detail", "lookup"}


def _is_date_concept(concept: str) -> bool:
    key = concept.strip().lower()
    return key in _DATE_DIMENSIONS or "date" in key


def _supported_domains() -> set[str]:
    return {
        domain.lower()
        for family in load_capability_catalog().families
        if family.status == "supported"
        for domain in family.domains
    }


def has_conflicting_ranking_directions(plan: QueryPlan) -> bool:
    """True when the plan asks for two opposite rankings of the same concept.

    Shared by the extractor (to withhold a deterministic limit=1 default for
    a genuinely compound question, e.g. "highest and lowest rate") and by
    this module's own fail-closed check — kept in one place so the two never
    drift apart.
    """
    aggregations_by_concept: dict[str, set[Aggregation]] = {}
    for measure in plan.measures:
        aggregations_by_concept.setdefault(measure.concept.lower(), set()).add(measure.aggregation)
    if any({Aggregation.MAXIMUM, Aggregation.MINIMUM} <= aggs for aggs in aggregations_by_concept.values()):
        return True
    directions_by_field: dict[str, set[SortDirection]] = {}
    for instruction in plan.sorting:
        directions_by_field.setdefault(instruction.field_concept.lower(), set()).add(instruction.direction)
    return any({SortDirection.ASC, SortDirection.DESC} <= dirs for dirs in directions_by_field.values())


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


def normalize_recency_sorting(plan: QueryPlan) -> QueryPlan:
    """Let a detail/lookup plan sort by a date concept it never declared as a dimension.

    "Last purchase details of mouse" naturally arrives as operation=detail
    with a `sorting` entry on a date concept ("purchase date") that isn't
    also listed in `dimensions`/`requested_output.fields` — today that trips
    "Sorting field unavailable" or "Dimension does not affect grouping or
    output". This only ADDS a non-grouped `Dimension` (and output field) for
    a concept the plan's own `sorting` already names; it never invents a new
    concept, and grouping is always left False, so it can never turn into a
    GROUP BY. Scoped to date concepts only (matching `_DATE_DIMENSIONS`/a
    "*date" name) — a blanket version for any sort field would silently
    defeat the "sorting field is unavailable" check for every hallucinated
    sort target, not just recency ones.
    """
    if plan.operation.lower() not in _OUTPUT_ONLY_OPERATIONS or not plan.sorting:
        return plan
    known_concepts = {dimension.concept.lower() for dimension in plan.dimensions}
    known_concepts.update(measure.concept.lower() for measure in plan.measures)
    missing_date_concepts = [
        instruction.field_concept
        for instruction in plan.sorting
        if _is_date_concept(instruction.field_concept) and instruction.field_concept.lower() not in known_concepts
    ]
    if not missing_date_concepts:
        return plan
    normalized = plan.model_copy(deep=True)
    existing_dimension_keys = {dimension.concept.lower() for dimension in normalized.dimensions}
    existing_fields = {field.lower() for field in normalized.requested_output.fields}
    added_fields = list(normalized.requested_output.fields)
    for concept in missing_date_concepts:
        key = concept.lower()
        if key not in existing_dimension_keys:
            normalized.dimensions.append(Dimension(concept=concept, grouping=False))
            existing_dimension_keys.add(key)
        if key not in existing_fields:
            added_fields.append(concept)
            existing_fields.add(key)
    normalized.requested_output = normalized.requested_output.model_copy(update={"fields": added_fields})
    return normalized


def normalize_entity_dimensions(plan: QueryPlan) -> QueryPlan:
    """Drop a dimension that merely restates an entity filter.

    Qwen frequently emits `entities=[supplier: "Prime compu systems"]` AND
    `dimensions=[supplier, grouping=false]` for "last supply from supplier
    Prime compu systems" (fix.md #6b). The dimension carries no grouping and
    no output role -- the entity filter is the meaning -- and would only trip
    "Dimension does not affect grouping or requested output". Removing it
    changes nothing the plan asks for. Lookups are left alone: there the
    dimension is the implied answer field, exactly as that rule already treats it.
    """
    if plan.operation.lower() == "lookup" or not plan.entities or not plan.dimensions:
        return plan
    entity_concepts = {entity.concept.lower() for entity in plan.entities}
    output_fields = {field.lower() for field in plan.requested_output.fields}
    redundant = [
        dimension
        for dimension in plan.dimensions
        if not dimension.grouping
        and dimension.concept.lower() in entity_concepts
        and dimension.concept.lower() not in output_fields
    ]
    if not redundant:
        return plan
    normalized = plan.model_copy(deep=True)
    normalized.dimensions = [dimension for dimension in normalized.dimensions if dimension not in redundant]
    return normalized


def validate_query_plan_semantics(plan: QueryPlan, nlp_analysis: NLPAnalysis | None = None) -> QueryPlan:
    """Validate logical output relationships without relying on physical data sources."""
    plan = normalize_temporal_dimensions(plan, nlp_analysis)
    plan = normalize_entity_dimensions(plan)
    # Date dimensions the MODEL declared (after the date-range cleanup above).
    # normalize_recency_sorting may add a sort-only, grouping=False date
    # dimension for "last/latest" plans; that one is justified by the sort,
    # not by the date range, so the date-range rule below must not judge it.
    declared_dimensions = {dimension.concept.lower() for dimension in plan.dimensions}
    plan = normalize_recency_sorting(plan)
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

    if operation == "detail" and has_aggregate:
        # "last 5 purchase qty of X" lists records; summing them would need a
        # GROUP BY the plan never asked for (fix.md #7, ORA-00937 on real Oracle).
        violations.append("Detail operation lists individual records; measures must use aggregation none.")

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
        # A lookup's dimension is its one implied answer field ("who
        # supplies keyboard" -> dimension=supplier), so it does not need to
        # also be grouping=true or separately listed in requested_output;
        # "detail" is intentionally left unexempted here, unchanged from
        # today, since this specific check never had a detail exemption.
        if not dimension.grouping and not is_output and operation != "lookup":
            violations.append(f"Dimension '{name}' does not affect grouping or requested output.")
        if (
            name in _DATE_DIMENSIONS
            and name in declared_dimensions
            and date_only_evidence
            and not date_grouping_requested
        ):
            violations.append(f"Date dimension '{name}' is not justified by a date range alone.")

    output_field_exempt = operation in _OUTPUT_ONLY_OPERATIONS
    for field in output_fields:
        if field not in measures and field not in grouping and not output_field_exempt:
            violations.append(f"Requested output field '{field}' is not an available measure or grouping dimension.")

    available_sort_fields = measures | set(dimensions)
    for instruction in plan.sorting:
        if instruction.field_concept.lower() not in available_sort_fields:
            violations.append(f"Sorting field '{instruction.field_concept}' is unavailable in the plan.")

    if plan.domain.lower() == "unknown" and plan.confidence >= 0.65:
        violations.append("Unknown domain cannot have high confidence.")
    if operation == "unknown" and plan.domain.lower() in _supported_domains():
        violations.append(
            f"Operation 'unknown' is not usable for the supported domain '{plan.domain}'; "
            "choose detail, aggregate, ranking, comparison, trend, or lookup."
        )
    if any(ambiguity.blocking for ambiguity in plan.ambiguities) and not plan.requires_clarification:
        violations.append("Blocking ambiguity must require clarification.")

    if plan.date_range is not None and _DUE_WORD.search(plan.original_question):
        nameable = any("due" in name for name in dimensions) or any(
            "due" in filter_item.concept.lower() for filter_item in plan.filters
        )
        if not nameable:
            violations.append(
                "The question mentions a due date but the plan does not name which date concept "
                "'due' refers to; ask for clarification instead of assuming the general date filter."
            )

    if operation == "ranking" and has_conflicting_ranking_directions(plan):
        violations.append(
            "The plan requests two conflicting ranking directions (e.g. highest and lowest) for the "
            "same concept; ask which single ranking is wanted."
        )

    if violations:
        raise QueryPlanSemanticValidationError(violations)
    return plan
