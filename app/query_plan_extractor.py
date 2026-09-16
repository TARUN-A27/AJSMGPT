"""Extract a schema-independent :class:`QueryPlan` from a business question."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.ollama_client import chat_with_qwen
from app.query_plan import QueryPlan


class QueryPlanExtractionError(RuntimeError):
    """The model call could not produce a usable response."""


class QueryPlanResponseError(QueryPlanExtractionError):
    """The model response did not contain one JSON object."""


class QueryPlanValidationError(QueryPlanExtractionError):
    """The model returned JSON that is not a valid QueryPlan."""


ModelCall = Callable[[str, str], str]


SYSTEM_PROMPT = """Interpret an ERP business question into one JSON object matching the supplied QueryPlan JSON schema.
This is question understanding only: never generate SQL, and never invent tables, columns, joins, or resolved database values.
Keep entity text as the user spoke it. Preserve uncertainty as ambiguities; use null or empty fields rather than guessing.
Use calibrated confidence, and make ambiguity blocking when competing interpretations would materially change the answer.
Supported domains are purchase, mrs, consumption, and unknown. Use unknown when none fits.
Operations are detail, aggregate, trend, ranking, comparison, lookup, and unknown. Select the closest operation without implying implementation details.
Return one JSON object only."""


def _schema_text() -> str:
    return json.dumps(QueryPlan.model_json_schema(), ensure_ascii=False, separators=(",", ":"))


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


def _parse_plan(response: str) -> QueryPlan:
    try:
        data = _extract_json_object(response)
    except QueryPlanResponseError:
        raise
    try:
        return QueryPlan.model_validate(data)
    except ValidationError as exc:
        raise QueryPlanValidationError("Model JSON did not satisfy the QueryPlan contract.") from exc


def _correction_prompt(error: QueryPlanExtractionError, response: str) -> str:
    """Keep the retry bounded to the failed response and contract."""
    return "\n".join(
        (
            f"Validation/parsing error: {error}",
            f"Previous model response: {response}",
            f"Required JSON schema: {_schema_text()}",
        )
    )


def extract_query_plan(question: str, *, model_call: ModelCall | None = None) -> QueryPlan:
    """Return a validated logical plan, with one correction attempt for bad output."""
    if not isinstance(question, str) or not question.strip():
        raise QueryPlanExtractionError("Question cannot be empty.")

    call_model = model_call or chat_with_qwen
    initial_user_prompt = "\n".join(
        (
            f"Question: {question.strip()}",
            f"QueryPlan JSON schema: {_schema_text()}",
        )
    )
    try:
        response = call_model(SYSTEM_PROMPT, initial_user_prompt)
    except Exception as exc:
        raise QueryPlanExtractionError("Unable to obtain a model response.") from exc

    try:
        return _parse_plan(response)
    except (QueryPlanResponseError, QueryPlanValidationError) as first_error:
        try:
            corrected = call_model("Return one JSON object only.", _correction_prompt(first_error, response))
        except Exception as exc:
            raise QueryPlanExtractionError("Unable to obtain a corrected model response.") from exc
        try:
            return _parse_plan(corrected)
        except QueryPlanResponseError as exc:
            raise QueryPlanResponseError("Corrected model response contained no JSON object.") from exc
        except QueryPlanValidationError as exc:
            raise QueryPlanValidationError("Corrected model JSON did not satisfy the QueryPlan contract.") from exc
