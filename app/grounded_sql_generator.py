"""Qwen-backed SQL preview generation constrained by a GroundedSchemaPlan."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.grounded_sql_validator import (
    GroundedSqlGroundingError,
    GroundedSqlSemanticError,
    GroundedSqlValidationError,
    UnsafeGroundedSqlError,
    collect_grounded_sql_violations,
    validate_grounded_sql,
)
from app.ollama_client import chat_with_qwen
from app.query_plan import QueryPlan
from app.schema_grounding import GroundedSchemaPlan


class GroundedSqlGenerationError(RuntimeError):
    """Base error for grounded preview generation."""


class GroundedSqlModelUnavailableError(GroundedSqlGenerationError):
    """The configured model could not be reached."""


class GroundedSqlResponseError(GroundedSqlGenerationError):
    """The model response did not match the exact structured contract."""


ModelCall = Callable[[str, str], str]
GROUNDED_SQL_NUM_PREDICT = 1200


class GroundedSqlResult(BaseModel):
    """Exact JSON contract returned by Qwen and exposed by the preview API."""

    model_config = ConfigDict(extra="forbid")

    sql: str
    selected_fields: list[str]
    applied_filters: list[str]
    assumptions: list[str]
    confidence: float = Field(ge=0, le=1)

    @field_validator("sql")
    @classmethod
    def validate_sql_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("SQL cannot be empty.")
        return value


SYSTEM_PROMPT = """Generate one grounded Oracle SQL preview from the supplied logical and physical plans.
Use only the supplied tables, columns, logical roles, and verified relationship paths.
Use fully qualified schema.table names and qualified column references.
Use named bind placeholders for every user/entity value; never embed those values in SQL.
Honor every required measure, grouping dimension, output field, date filter, sort, and limit.
Use every required grounded display/grouping column for user-visible entity output.
Join identifier columns may be used only for verified joins and cannot replace required user-facing display columns.
Every entity in query_plan.entities whose bind_required is true MUST appear in the WHERE
clause as exactly one equality comparison against its grounded column, bound by exactly one
named placeholder (<grounded column> = :<name>); never omit it, never test it with a
different operator, and never leave it out because it seems implied by a join.
Never write a row limit of any kind (no FETCH FIRST, OFFSET, ROWNUM, or LIMIT): the system applies the
plan's limit after validation. Express "last N" purely as ORDER BY on the date column.
Never invent a status meaning, join, table, column, date conversion, or business rule.
For each entry in grounding.compound_conditions, the WHERE clause must combine exactly
those listed columns using exactly the given combinator (AND/OR); never use a different
combinator, never omit one of the columns, and never substitute a different column.
Do not use SELECT *, comments, semicolons, DML, DDL, or PL/SQL.
For this preview endpoint, assumptions must always be an empty list.
Every date range (relative or absolute) is written as exactly
date_column >= :date_start AND date_column < :date_end on the grounded date column, with two
named bind placeholders whose values the system supplies. Never write SYSDATE, ADD_MONTHS,
TRUNC, BETWEEN, TO_DATE, TO_TIMESTAMP, a literal date, or any date arithmetic or conversion.
Return exactly one JSON object and no markdown or prose. The object must contain only:
sql, selected_fields, applied_filters, assumptions, confidence."""


def _result_schema() -> str:
    return json.dumps(GroundedSqlResult.model_json_schema(), ensure_ascii=False, separators=(",", ":"))


def _compact_context(query_plan: QueryPlan, grounding: GroundedSchemaPlan) -> dict[str, Any]:
    date_range = None
    if query_plan.date_range is not None:
        date_range = query_plan.date_range.model_dump(mode="json")
    required_output_columns = [
        f"{item.full_table_name}.{item.column_name}"
        for item in grounding.selected_columns
        if item.role in {"display", "grouping"}
    ]
    join_identifier_columns = [
        f"{item.full_table_name}.{item.column_name}"
        for item in grounding.selected_columns
        if item.role == "join_identifier"
    ]
    date_guidance = None
    if query_plan.date_range is not None and query_plan.date_range.kind.value != "unspecified":
        date_guidance = (
            "Date range required: write date_column >= :date_start AND date_column < :date_end on the "
            "grounded date column (two named binds; values are supplied by the system). No SYSDATE, "
            "ADD_MONTHS, TRUNC, BETWEEN, TO_DATE, or literal dates."
        )
    return {
        "query_plan": {
            "intent": {"domain": query_plan.domain, "operation": query_plan.operation},
            "measures": [item.model_dump(mode="json") for item in query_plan.measures],
            "dimensions": [item.model_dump(mode="json") for item in query_plan.dimensions],
            "entities": [
                {
                    "concept": item.concept,
                    "status": item.status.value,
                    "bind_required": item.status.value != "not_required",
                }
                for item in query_plan.entities
            ],
            "filters": [
                {
                    "concept": item.concept,
                    "operator": item.operator.value,
                    "value_type": item.value_type,
                    "bind_required": item.value is not None,
                }
                for item in query_plan.filters
            ],
            "date_range": date_range,
            "date_guidance": date_guidance,
            "sorting": [item.model_dump(mode="json") for item in query_plan.sorting],
            "limit": query_plan.limit,
            "required_output_fields": query_plan.requested_output.fields,
        },
        "grounding": {
            "tables": [item.model_dump(mode="json") for item in grounding.selected_tables],
            "columns": [item.model_dump(mode="json") for item in grounding.selected_columns],
            "required_output_display_columns": required_output_columns,
            "join_identifier_columns_not_output_substitutes": join_identifier_columns,
            "verified_relationship_paths": [
                item.model_dump(mode="json") for item in grounding.allowed_relationship_paths
            ],
            "entity_column_candidates": grounding.entity_column_candidates,
            "compound_conditions": [
                item.model_dump(mode="json") for item in grounding.compound_conditions
            ],
        },
    }


def _model_call(system_prompt: str, user_prompt: str) -> str:
    return chat_with_qwen(
        system_prompt,
        user_prompt,
        think=False,
        temperature=0.0,
        num_predict=GROUNDED_SQL_NUM_PREDICT,
    )


def _parse_result(response: str) -> GroundedSqlResult:
    if not isinstance(response, str):
        raise GroundedSqlResponseError("Model response was not text.")
    try:
        value = json.loads(response.strip())
    except json.JSONDecodeError as exc:
        raise GroundedSqlResponseError("Model response was not exactly one JSON object.") from exc
    if not isinstance(value, dict):
        raise GroundedSqlResponseError("Model response was not a JSON object.")
    try:
        return GroundedSqlResult.model_validate(value)
    except ValidationError as exc:
        raise GroundedSqlResponseError("Model JSON did not satisfy the SQL preview contract.") from exc


def _align_selected_fields(result: GroundedSqlResult) -> GroundedSqlResult:
    matches = list(re.finditer(r"\bSELECT\s+(.*?)\s+FROM\b", result.sql, re.IGNORECASE | re.DOTALL))
    if not matches:
        return result
    match = matches[-1]
    body, fields, depth, start = match.group(1), [], 0, 0
    for index, char in enumerate(body + ","):
        depth += (char == "(") - (char == ")")
        if char == "," and depth == 0:
            expression = body[start:index].strip()
            alias = re.search(r"\bAS\s+([A-Z][A-Z0-9_]*)\s*$", expression, re.IGNORECASE)
            fields.append(alias.group(1) if alias else expression)
            start = index + 1
    return result.model_copy(update={"selected_fields": fields})


def _validation_failure_text(error: Exception) -> str:
    if isinstance(error, GroundedSqlValidationError):
        return str(error)
    return "Structured output did not satisfy the required JSON schema."


def generate_grounded_sql(
    query_plan: QueryPlan,
    grounded_schema_plan: GroundedSchemaPlan,
    *,
    model_call: ModelCall | None = None,
) -> GroundedSqlResult:
    """Generate and statically validate SQL, allowing one correction attempt."""
    if not grounded_schema_plan.is_grounded:
        raise GroundedSqlGroundingError("GroundedSchemaPlan is not safe for SQL generation.")

    call_model = model_call or _model_call
    context = _compact_context(query_plan, grounded_schema_plan)
    context_text = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    initial_prompt = "\n".join((
        f"Compact grounded context: {context_text}",
        f"Required result JSON schema: {_result_schema()}",
    ))
    try:
        response = call_model(SYSTEM_PROMPT, initial_prompt)
    except Exception as exc:
        raise GroundedSqlModelUnavailableError("Grounded SQL model is unavailable.") from exc

    first_result: GroundedSqlResult | None = None
    try:
        first_result = _parse_result(response)
    except GroundedSqlResponseError as first_error:
        violations = [_validation_failure_text(first_error)]
    else:
        violations = collect_grounded_sql_violations(
            first_result.sql, query_plan, grounded_schema_plan
        )
        if first_result.assumptions:
            violations.append("Assumptions must be empty for SQL preview output.")
        if not violations:
            return _align_selected_fields(first_result)

    if violations:
        previous = first_result.model_dump(mode="json") if first_result is not None else {}
        correction_prompt = "\n".join((
            "Validation failures: " + json.dumps(violations, ensure_ascii=False, separators=(",", ":")),
            "Previous structured result: " + json.dumps(previous, ensure_ascii=False, separators=(",", ":")),
            f"Compact grounded context: {context_text}",
            f"Required result JSON schema: {_result_schema()}",
        ))
        try:
            corrected_response = call_model(SYSTEM_PROMPT, correction_prompt)
        except Exception as exc:
            raise GroundedSqlModelUnavailableError("Grounded SQL correction model is unavailable.") from exc
        corrected = _parse_result(corrected_response)
        validate_grounded_sql(corrected.sql, query_plan, grounded_schema_plan)
        if corrected.assumptions:
            raise GroundedSqlSemanticError("Assumptions must be empty for SQL preview output.")
        return _align_selected_fields(corrected)


__all__ = [
    "GroundedSqlGenerationError",
    "GroundedSqlGroundingError",
    "GroundedSqlModelUnavailableError",
    "GroundedSqlResponseError",
    "GroundedSqlResult",
    "GroundedSqlSemanticError",
    "UnsafeGroundedSqlError",
    "generate_grounded_sql",
]
