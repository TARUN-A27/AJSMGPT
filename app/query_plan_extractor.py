"""Extract a schema-independent :class:`QueryPlan` from a business question."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.ollama_client import chat_with_qwen
from app.query_plan import DateRange, DateRangeKind, QueryPlan, SortDirection
from app.query_plan_semantic_validator import (
    QueryPlanSemanticValidationError,
    has_conflicting_ranking_directions,
    validate_query_plan_semantics,
)
from app.spacy_nlp import NLPAnalysis
from app.v1_capabilities import evaluate_capability


class QueryPlanExtractionError(RuntimeError):
    """The model call could not produce a usable response."""


class QueryPlanResponseError(QueryPlanExtractionError):
    """The model response did not contain one JSON object."""


class QueryPlanValidationError(QueryPlanExtractionError):
    """The model returned JSON that is not a valid QueryPlan."""


ModelCall = Callable[[str, str], str]
QUERY_PLAN_NUM_PREDICT = 1000

_IN_YEAR_PATTERN = re.compile(r"\bin\s+((?:19|20)\d{2})\b", re.IGNORECASE)
_ON_YYYYMMDD_PATTERN = re.compile(r"\bon\s+((?:19|20)\d{2})(\d{2})(\d{2})\b", re.IGNORECASE)


SYSTEM_PROMPT = """Interpret an ERP business question into one JSON object matching the supplied QueryPlan JSON schema.
This is question understanding only: never generate SQL, and never invent tables, columns, joins, or resolved database values.
Keep entity text as the user spoke it. Preserve uncertainty as ambiguities; use null or empty fields rather than guessing.
Use calibrated confidence, and make ambiguity blocking when competing interpretations would materially change the answer.
Recognized domains are purchase, mrs, consumption, stock, grn, and unknown. A domain describes the question's real-world
subject even when it is not yet a supported family; use unknown only when no real-world subject can be identified at all.
mrs covers material requisition slips, including their approval/hold/rejection status (e.g. pending, approved, or
rejected, at a stores officer, internal audit, or similar approval stage). purchase covers purchase orders, including
their own approval/pending status.
Operations are detail, aggregate, trend, ranking, comparison, lookup, and unknown. Select the closest operation without
implying implementation details. Use unknown only when the intent genuinely cannot be determined, not merely because no
operation feels like a perfect fit.
lookup means identifying which supplier or material a name or code refers to (translating an identity) -- it is never
the right operation for asking about a status, date, reason, or any other field of a record; that is always detail,
even when the question sounds like it wants a single fact. A question asking for one field or status of matching
records (a rejection reason, an approval state, a due date, whether something is pending) is detail whenever it could
match more than one record -- i.e. whenever no single uniquely-identifying value (such as one specific MRS number)
narrows it to exactly one row. Do not use unknown for this kind of question just because it asks for a status or a
single field rather than a list.
A question asking for a total or current quantity/amount (how much, how many, current stock, balance, available
quantity) is different from asking for one field of a record: it is operation=aggregate with that measure's
aggregation set to sum, even when it names exactly one item and sounds like it wants a single fact -- never
detail -- because the quantity itself is a running total across underlying records, not a value stored in any
single one of them. This does not apply when the question names a record by its own unique identifier (such as
one specific MRS number): that is still detail regardless of phrasing, exactly as above.
For "last/latest/recent N" or "first/earliest N" questions (records, not a calendar range), use operation=detail with a
sorting entry on the relevant date concept (descending for last/latest/recent, ascending for first/earliest) and
limit=N. For the same wording without an explicit N, use operation=detail with that same sorting entry and no limit.
A detail operation lists individual records: every measure in it uses aggregation none (never sum/count/average).
Dimensions only affect requested output or grouping. Date ranges are filters, not dimensions. Ranking by an aggregate requires grouping=true for the ranked result dimension.
Always set business_subject to the thing the question is about (e.g. purchase, mrs, issue, supplier, material); a plan
with only entities and no business_subject, measure, or dimension is invalid.
An entity is a specific supplier or material named in the question. entities[].concept must be exactly one of these
tokens: supplier, supplier_name, supplier_identifier, material, item_identifier. Use supplier when a supplier is named by
name or code, supplier_name for a name only, supplier_identifier for a code only, material for an item name, and
item_identifier for an item code. Put the spoken value in entities[].original_value; never use the value itself as the
concept. A filtered entity is not also a dimension: do not repeat it in dimensions unless the user asked to group or
list by it.
Not every entity is a supplier or material: a specific record identifier (e.g. an MRS number) or a status/approval/
workflow condition the question filters by is also a valid entities[].concept. For a status/approval/workflow
condition, entities[].concept must be the specific status word or phrase itself, exactly as the question states it
(e.g. concept="pending", concept="approved", concept="rejected", concept="on hold", concept="pending at store
officer") -- never a generic label such as "status" or "condition", and never split one such phrase into more than
one entity. For any entity concept other than supplier, supplier_name, supplier_identifier, material, or
item_identifier, set status to "not_required" -- never "unresolved" or "resolved" -- because its correctness is
guaranteed directly by the SQL condition generated for it, not by a separate identity lookup; keep the spoken value
in original_value (or "true" if the concept word itself is the whole condition). Represent such a condition as
exactly one entity, never also as a filter with the same meaning.
An approval-stage word (store officer, internal audit, JMD, or similar) that names WHERE a status applies, in the
same clause as that status word, is part of that same entity, not a separate one -- e.g. "approval pending at
Store officer" is one entity with concept="pending at store officer", never two entities for "pending" and
"Store officer" separately.
A question can name both a real supplier/material AND a status/approval condition at once (e.g. "approved MRS for
keyboard" names both "approved" and the material "keyboard") -- these are two independent entities. Only the
status/approval/identifier entity gets status="not_required"; a named supplier or material is still exactly one of
the five tokens above (never the status word), and never gets status="not_required" just because another entity in
the same plan does.
Return one JSON object only."""


def _schema_text() -> str:
    return json.dumps(QueryPlan.model_json_schema(), ensure_ascii=False, separators=(",", ":"))


def _query_plan_model_call(system_prompt: str, user_prompt: str) -> str:
    """Use bounded, non-thinking Qwen only for QueryPlan extraction."""
    return chat_with_qwen(
        system_prompt,
        user_prompt,
        think=False,
        temperature=0.0,
        num_predict=QUERY_PLAN_NUM_PREDICT,
    )


def _extract_json_object(response: str) -> dict[str, Any]:
    """Find the first JSON object, allowing harmless surrounding prose."""
    if not isinstance(response, str):
        raise QueryPlanResponseError("Model response was not text.")

    decoder = json.JSONDecoder()
    for index, char in enumerate(response):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(response[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise QueryPlanResponseError("Model response contained no JSON object.")


def _parse_plan(
    response: str,
    nlp_analysis: NLPAnalysis | None = None,
    original_question: str | None = None,
) -> QueryPlan:
    try:
        data = _extract_json_object(response)
    except QueryPlanResponseError:
        raise
    try:
        plan = QueryPlan.model_validate(data)
    except ValidationError as exc:
        raise QueryPlanValidationError("Model JSON did not satisfy the QueryPlan contract.") from exc
    if original_question is not None:
        plan = plan.model_copy(update={"original_question": original_question.strip()})
    if plan.date_range is not None and plan.date_range.kind.value == "relative" and not plan.date_range.original_text:
        expressions = (nlp_analysis.date_expressions if nlp_analysis is not None else [])
        restored = next((value.strip() for value in expressions if isinstance(value, str) and value.strip()), None)
        if restored is None and nlp_analysis is not None:
            match = re.search(r"\b(?:last|past|previous)\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(?:months?|days?|years?)\b", nlp_analysis.normalized_question, re.IGNORECASE)
            restored = match.group(0) if match else None
        if restored:
            plan = plan.model_copy(update={
                "date_range": plan.date_range.model_copy(update={"original_text": restored})
            })
    plan = _apply_deterministic_overrides(plan, nlp_analysis)
    if not evaluate_capability(plan).supported:
        # The capability gate in execute_nlp_query rejects this exact plan
        # (same decision, plan unchanged by the normalizers below), so it can
        # never execute. Skipping semantic checks lets the pipeline answer
        # "GRN is not supported in V1" instead of a misleading "model JSON did
        # not satisfy the contract". Anything the gate would accept is still
        # fully validated here.
        return plan
    return validate_query_plan_semantics(plan, nlp_analysis)


def _normalise_value(text: str) -> str:
    return " ".join(text.split()).lower()


def _apply_deterministic_overrides(plan: QueryPlan, nlp_analysis: NLPAnalysis | None) -> QueryPlan:
    """Let explicit user signals override contradictory Qwen output.

    Every branch here only corrects a field the plan already asserts (a
    limit value, a date range, an existing entity's text, an existing sort
    instruction's direction, or a missing ranking limit the plan already
    implies) — none of it invents a new entity, schema concept, join, or
    business status the model itself never referenced.
    """
    if nlp_analysis is None:
        return plan

    updates: dict[str, object] = {}

    # "last 5" / "latest 10" / "first 3": an explicit row count always wins,
    # and a date_range Qwen invented from that same wording (no genuine date
    # evidence anywhere in the question) must be cleared rather than risk
    # grounding to the wrong column.
    if nlp_analysis.recency_limit is not None:
        updates["limit"] = nlp_analysis.recency_limit
        if (
            plan.date_range is not None
            and plan.date_range.kind != DateRangeKind.UNSPECIFIED
            and not nlp_analysis.date_expressions
        ):
            updates["date_range"] = None

    # "in 2026" / "on 20260212": an explicit calendar date always wins.
    question_text = nlp_analysis.normalized_question
    day_match = _ON_YYYYMMDD_PATTERN.search(question_text)
    year_match = _IN_YEAR_PATTERN.search(question_text)
    if day_match:
        year, month, day = day_match.groups()
        updates["date_range"] = DateRange(
            kind=DateRangeKind.ABSOLUTE, start=f"{year}-{month}-{day}", end=f"{year}-{month}-{day}",
            original_text=day_match.group(0),
        )
    elif year_match:
        year = year_match.group(1)
        updates["date_range"] = DateRange(
            kind=DateRangeKind.ABSOLUTE, start=f"{year}-01-01", end=f"{year}-12-31",
            original_text=year_match.group(0),
        )

    if updates:
        plan = plan.model_copy(update=updates)

    # Quoted / contextual-identifier values: correct an existing entity's
    # text to match exactly what the user wrote (case, spacing); never
    # invent an entity for a value the model never referenced at all.
    authoritative_values = list(nlp_analysis.quoted_entities) + list(nlp_analysis.contextual_identifiers)
    if authoritative_values and plan.entities:
        exact_by_normalised = {_normalise_value(value): value for value in authoritative_values}
        updated_entities = []
        for entity in plan.entities:
            if entity.original_value:
                exact = exact_by_normalised.get(_normalise_value(entity.original_value))
                if exact is not None and exact != entity.original_value:
                    entity = entity.model_copy(update={"original_value": exact})
            updated_entities.append(entity)
        plan = plan.model_copy(update={"entities": updated_entities})

    # Recency direction: correct the direction of a sort instruction the
    # model already wrote for a date concept; never add one it never wrote.
    if nlp_analysis.recency_direction and plan.sorting:
        target_direction = SortDirection.DESC if nlp_analysis.recency_direction == "desc" else SortDirection.ASC
        updated_sorting = []
        for instruction in plan.sorting:
            if "date" in instruction.field_concept.lower() and instruction.direction != target_direction:
                instruction = instruction.model_copy(update={"direction": target_direction})
            updated_sorting.append(instruction)
        plan = plan.model_copy(update={"sorting": updated_sorting})

    # Superlative ranking without an explicit number ("which supplier is
    # given lowest price?"): default limit=1 only when the ranking is a
    # single, unambiguous direction; never for a genuinely compound question
    # ("highest and lowest"), which is left for the semantic validator to
    # reject as requiring clarification.
    if (
        plan.operation.lower() == "ranking"
        and plan.limit is None
        and not has_conflicting_ranking_directions(plan)
    ):
        plan = plan.model_copy(update={"limit": 1})

    return plan


def _format_validation_errors(exc: ValidationError) -> str:
    """Turn pydantic's error list into field-level guidance the model can act on.

    Each entry is only a JSON field path plus a constraint message (e.g.
    "sorting.0.priority: Input should be greater than or equal to 0") — the
    same information `exc.errors()` already carries, with no stack trace or
    runtime detail attached.
    """
    lines = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error.get("loc", ())) or "(root)"
        lines.append(f"field: {field}; error: {error.get('type', 'invalid')}; expected: {error.get('msg', 'a valid value')}")
    return "\n".join(lines)


def _correction_prompt(
    error: QueryPlanExtractionError | QueryPlanSemanticValidationError,
    response: str,
    nlp_analysis: NLPAnalysis | None,
    question: str,
) -> str:
    """Keep the retry bounded to the failed response and contract."""
    if isinstance(error, QueryPlanSemanticValidationError):
        previous_json = json.dumps(_extract_json_object(response), ensure_ascii=False, separators=(",", ":"))
        evidence = _spacy_evidence_text(nlp_analysis) if nlp_analysis is not None else "{}"
        return "\n".join(
            (
                f"Question: {question}",
                "Your previous QueryPlan was rejected. Return a corrected QueryPlan that removes every "
                "violation below; do not return the previous JSON unchanged.",
                f"Semantic violations: {error}",
                f"Previous JSON: {previous_json}",
                f"Required JSON schema: {_schema_text()}",
                f"spaCy evidence: {evidence}",
            )
        )
    cause = error.__cause__
    detail = _format_validation_errors(cause) if isinstance(cause, ValidationError) else str(error)
    return "\n".join(
        (
            f"Question: {question}",
            "Your previous response was rejected. Return a corrected QueryPlan JSON object that fixes "
            "the error below; do not return the previous response unchanged.",
            f"Validation/parsing error: {detail}",
            f"Previous model response: {response}",
            f"Required JSON schema: {_schema_text()}",
        )
    )


def _spacy_evidence_text(analysis: NLPAnalysis) -> str:
    """Serialize only compact, advisory signals useful to the runtime model."""
    fields = {
        "normalized_question", "detected_domains", "detected_operations", "primary_operation",
        "detected_measures", "detected_dimensions", "date_expressions", "ranking_limit",
        "candidate_entity_spans", "negations", "comparative_terms",
        "has_explicit_time_grouping", "time_grouping_granularity",
        "quoted_entities", "contextual_identifiers", "recency_direction", "recency_limit",
    }
    return json.dumps(analysis.model_dump(include=fields), ensure_ascii=False, separators=(",", ":"))


def extract_query_plan(
    question: str,
    *,
    model_call: ModelCall | None = None,
    nlp_analysis: NLPAnalysis | None = None,
    original_question: str | None = None,
) -> QueryPlan:
    """Return a validated logical plan, with one correction attempt for bad output."""
    if not isinstance(question, str) or not question.strip():
        raise QueryPlanExtractionError("Question cannot be empty.")
    if original_question is not None and (not isinstance(original_question, str) or not original_question.strip()):
        raise QueryPlanExtractionError("Original question cannot be empty.")

    call_model = model_call or _query_plan_model_call
    prompt_parts = [f"Question: {question.strip()}"]
    if nlp_analysis is not None:
        prompt_parts.append(
            "Advisory spaCy evidence (not definitive; resolve conflicts from the original question): "
            + _spacy_evidence_text(nlp_analysis)
        )
    prompt_parts.append(f"QueryPlan JSON schema: {_schema_text()}")
    initial_user_prompt = "\n".join(prompt_parts)
    try:
        response = call_model(SYSTEM_PROMPT, initial_user_prompt)
    except Exception as exc:
        raise QueryPlanExtractionError("Unable to obtain a model response.") from exc

    try:
        return _parse_plan(response, nlp_analysis, original_question)
    except (QueryPlanResponseError, QueryPlanValidationError, QueryPlanSemanticValidationError) as first_error:
        try:
            corrected = call_model(SYSTEM_PROMPT, _correction_prompt(first_error, response, nlp_analysis, question.strip()))
        except Exception as exc:
            raise QueryPlanExtractionError("Unable to obtain a corrected model response.") from exc
        try:
            return _parse_plan(corrected, nlp_analysis, original_question)
        except (QueryPlanResponseError, QueryPlanValidationError, QueryPlanSemanticValidationError) as exc:
            raise QueryPlanValidationError("Corrected model JSON did not satisfy the QueryPlan contract.") from exc
