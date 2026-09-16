"""Extract a schema-independent :class:`QueryPlan` from a business question."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.ollama_client import chat_with_qwen
from app.query_plan import QueryPlan
from app.query_plan_semantic_validator import (
    QueryPlanSemanticValidationError,
    validate_query_plan_semantics,
)
from app.spacy_nlp import NLPAnalysis


class QueryPlanExtractionError(RuntimeError):
    """The model call could not produce a usable response."""


class QueryPlanResponseError(QueryPlanExtractionError):
    """The model response did not contain one JSON object."""


class QueryPlanValidationError(QueryPlanExtractionError):
    """The model returned JSON that is not a valid QueryPlan."""


ModelCall = Callable[[str, str], str]
QUERY_PLAN_NUM_PREDICT = 1000


SYSTEM_PROMPT = """Interpret an ERP business question into one JSON object matching the supplied QueryPlan JSON schema.
This is question understanding only: never generate SQL, and never invent tables, columns, joins, or resolved database values.
Keep entity text as the user spoke it. Preserve uncertainty as ambiguities; use null or empty fields rather than guessing.
Use calibrated confidence, and make ambiguity blocking when competing interpretations would materially change the answer.
Supported domains are purchase, mrs, consumption, and unknown. Use unknown when none fits.
Operations are detail, aggregate, trend, ranking, comparison, lookup, and unknown. Select the closest operation without implying implementation details.
Dimensions only affect requested output or grouping. Date ranges are filters, not dimensions. Ranking by an aggregate requires grouping=true for the ranked result dimension.
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
    return validate_query_plan_semantics(plan, nlp_analysis)


def _correction_prompt(
    error: QueryPlanExtractionError | QueryPlanSemanticValidationError,
    response: str,
    nlp_analysis: NLPAnalysis | None,
) -> str:
    """Keep the retry bounded to the failed response and contract."""
    if isinstance(error, QueryPlanSemanticValidationError):
        previous_json = json.dumps(_extract_json_object(response), ensure_ascii=False, separators=(",", ":"))
        evidence = _spacy_evidence_text(nlp_analysis) if nlp_analysis is not None else "{}"
        return "\n".join(
            (
                f"Semantic violations: {error}",
                f"Previous JSON: {previous_json}",
                f"Required JSON schema: {_schema_text()}",
                f"spaCy evidence: {evidence}",
            )
        )
    return "\n".join(
        (
            f"Validation/parsing error: {error}",
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
            corrected = call_model("Return one JSON object only.", _correction_prompt(first_error, response, nlp_analysis))
        except Exception as exc:
            raise QueryPlanExtractionError("Unable to obtain a corrected model response.") from exc
        try:
            return _parse_plan(corrected, nlp_analysis, original_question)
        except (QueryPlanResponseError, QueryPlanValidationError, QueryPlanSemanticValidationError) as exc:
            raise QueryPlanValidationError("Corrected model JSON did not satisfy the QueryPlan contract.") from exc
