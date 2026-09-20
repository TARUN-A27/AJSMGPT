"""Guarded V1 question-to-Oracle execution and deterministic reporting."""

from __future__ import annotations

import math
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.grounded_sql_generator import GroundedSqlResult, generate_grounded_sql
from app.grounded_sql_validator import GroundedSqlValidationError, validate_grounded_sql
from app.oracle_client import (
    OracleBindValue,
    OracleExecutionError,
    OracleUnavailableError,
    run_safe_select,
)
from app.query_plan import Aggregation, DateRangeKind, EntityStatus, FilterOperator, QueryPlan
from app.query_plan_extractor import extract_query_plan
from app.schema_grounding import GroundedSchemaPlan, ground_query_plan
from app.spacy_nlp import NLPAnalysis, analyze_question_with_spacy
from app.text_correction import TextCorrectionResult, correct_question_text
from app.v1_capabilities import CapabilityDecision, evaluate_capability


DEFAULT_EXECUTION_MAX_ROWS = 100
HARD_EXECUTION_MAX_ROWS = 1000
_BIND_RE = re.compile(r"(?<!:):([A-Z][A-Z0-9_]*)", re.IGNORECASE)
_IDENTIFIER = r"[A-Z][A-Z0-9_$#]*"
_TABLE_RE = re.compile(
    rf"\b(?:FROM|JOIN)\s+({_IDENTIFIER}\.({_IDENTIFIER}))"
    rf"(?:\s+(?:AS\s+)?({_IDENTIFIER}))?",
    re.IGNORECASE,
)


class ParameterBindingError(GroundedSqlValidationError):
    """Generated SQL binds do not exactly match validated plan values."""


class ExecutionResultError(RuntimeError):
    """The runner returned data outside the deterministic response contract."""


class UnsupportedResultValueError(ExecutionResultError):
    """An opaque or non-JSON-safe Oracle value was returned."""


class ExecutionRejectedError(RuntimeError):
    def __init__(self, response: "NLPExecuteFailureResponse") -> None:
        super().__init__(response.error_code)
        self.response = response


class ReportType(str, Enum):
    DETAIL = "detail"
    GROUPED_TOTALS = "grouped_totals"
    RANKING = "ranking"
    AGGREGATE = "aggregate"
    EMPTY = "empty"


class QueryPlanSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    operation: str
    subject: str | None
    measures: list[str]
    grouping_dimensions: list[str]
    date_range: str | None
    requested_limit: int | None
    confidence: float


class GroundingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    family: str
    confidence: float
    tables: list[str]
    columns: list[str]
    relationships: list[str]


class ExecutionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    elapsed_ms: int = Field(ge=0)
    row_limit: int = Field(gt=0)
    limit_source: Literal["requested", "service_default"]


class TypedExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str]
    rows: list[list[Any]]
    row_count: int = Field(ge=0)
    truncated: bool
    metadata: ExecutionMetadata


class NLPExecuteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: Literal[True] = True
    question: str
    correction: TextCorrectionResult
    query_plan: QueryPlanSummary
    grounding: GroundingSummary
    sql_preview: str | None
    selected_fields: list[str]
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    report_type: ReportType
    summary: str
    requires_clarification: Literal[False] = False
    ambiguities: list[str] = Field(default_factory=list)
    reject_reasons: list[str] = Field(default_factory=list)
    execution: ExecutionMetadata


class NLPExecuteFailureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: Literal[False] = False
    error_code: Literal["clarification_required", "unsupported"]
    question: str
    corrected_question: str | None = None
    query_plan: QueryPlanSummary | None = None
    grounding: GroundingSummary | None = None
    sql_preview: None = None
    requires_clarification: Literal[True] = True
    ambiguities: list[str] = Field(default_factory=list)
    reject_reasons: list[str] = Field(default_factory=list)


class SelectRunner(Protocol):
    def __call__(
        self,
        sql: str,
        binds: Mapping[str, OracleBindValue],
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class NLPExecutionDependencies:
    correct_text: Callable[[str], TextCorrectionResult] = correct_question_text
    analyze: Callable[[str], NLPAnalysis] = analyze_question_with_spacy
    extract_plan: Callable[..., QueryPlan] = extract_query_plan
    ground_plan: Callable[[QueryPlan], GroundedSchemaPlan] = ground_query_plan
    generate_sql: Callable[..., GroundedSqlResult] = generate_grounded_sql
    runner: SelectRunner | None = None


def _configured_max_rows() -> int:
    try:
        value = int(os.getenv("NLP_EXECUTION_MAX_ROWS", str(DEFAULT_EXECUTION_MAX_ROWS)))
    except ValueError:
        return DEFAULT_EXECUTION_MAX_ROWS
    return max(1, min(value, HARD_EXECUTION_MAX_ROWS))


def _include_sql_preview() -> bool:
    return os.getenv("NLP_EXECUTION_INCLUDE_SQL_PREVIEW", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _plan_summary(plan: QueryPlan) -> QueryPlanSummary:
    date_text = plan.date_range.original_text if plan.date_range else None
    return QueryPlanSummary(
        domain=plan.domain,
        operation=plan.operation,
        subject=plan.business_subject.concept if plan.business_subject else None,
        measures=[f"{item.aggregation.value}:{item.concept}" for item in plan.measures],
        grouping_dimensions=[item.concept for item in plan.dimensions if item.grouping],
        date_range=date_text,
        requested_limit=plan.limit,
        confidence=plan.confidence,
    )


def _grounding_summary(
    grounding: GroundedSchemaPlan,
    capability: CapabilityDecision,
) -> GroundingSummary:
    return GroundingSummary(
        family=capability.family,
        confidence=grounding.confidence,
        tables=[item.full_table_name for item in grounding.selected_tables],
        columns=[f"{item.full_table_name}.{item.column_name}" for item in grounding.selected_columns],
        relationships=[name for path in grounding.allowed_relationship_paths for name in path.constraint_names],
    )


def _reject(
    *,
    question: str,
    corrected_question: str | None,
    plan: QueryPlan | None,
    grounding: GroundedSchemaPlan | None,
    capability: CapabilityDecision | None,
    ambiguities: Sequence[str] = (),
    reject_reasons: Sequence[str] = (),
) -> None:
    reasons = list(reject_reasons)
    response = NLPExecuteFailureResponse(
        error_code="clarification_required" if ambiguities else "unsupported",
        question=question,
        corrected_question=corrected_question,
        query_plan=_plan_summary(plan) if plan else None,
        grounding=(
            _grounding_summary(grounding, capability)
            if grounding is not None and capability is not None
            else None
        ),
        ambiguities=list(ambiguities),
        reject_reasons=reasons,
    )
    raise ExecutionRejectedError(response)


def _normalise(value: str) -> str:
    return " ".join(value.lower().replace("_", " ").replace("-", " ").split())


def _masked_sql(sql: str) -> str:
    return re.sub(r"'(?:''|[^'])*'", lambda match: " " * len(match.group(0)), sql)


def _aliases(sql: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in _TABLE_RE.finditer(_masked_sql(sql)):
        table = match.group(1).upper()
        result[table] = table
        result[match.group(2).upper()] = table
        if match.group(3):
            alias = match.group(3).upper()
            if alias not in {"WHERE", "JOIN", "ON", "GROUP", "ORDER", "FETCH"}:
                result[alias] = table
    return result


def _column_variants(sql: str, full_column: str) -> list[str]:
    table, column = full_column.upper().rsplit(".", 1)
    variants = [full_column.upper(), f"{table.rsplit('.', 1)[-1]}.{column}", column]
    variants.extend(f"{alias}.{column}" for alias, target in _aliases(sql).items() if target == table)
    return list(dict.fromkeys(variants))


def _concept_columns(grounding: GroundedSchemaPlan, concept: str) -> list[str]:
    explicit = grounding.entity_column_candidates.get(concept)
    if explicit:
        return explicit
    target = _normalise(concept)
    exact = [
        f"{column.full_table_name}.{column.column_name}"
        for column in grounding.selected_columns
        if _normalise(column.logical_concept) == target
        or _normalise(column.logical_concept).removesuffix(" name") == target
    ]
    if exact:
        return exact
    return [
        f"{column.full_table_name}.{column.column_name}"
        for column in grounding.selected_columns
        if column.role == "entity_filter"
    ]


def _bind_names_for_column(sql: str, full_column: str) -> list[str]:
    masked = _masked_sql(sql)
    names: list[str] = []
    for variant in _column_variants(sql, full_column):
        pattern = re.escape(variant).replace(r"\.", r"\s*\.\s*")
        between = re.search(
            rf"\b{pattern}\b\s+BETWEEN\s+:([A-Z][A-Z0-9_]*)\s+AND\s+:([A-Z][A-Z0-9_]*)",
            masked,
            re.IGNORECASE,
        )
        if between:
            names.extend([between.group(1), between.group(2)])
        for match in re.finditer(
            rf"\b{pattern}\b\s*(?:=|<>|!=|>=|<=|>|<|LIKE)\s*:([A-Z][A-Z0-9_]*)",
            masked,
            re.IGNORECASE,
        ):
            names.append(match.group(1))
        in_match = re.search(
            rf"\b{pattern}\b\s+IN\s*\(([^)]*)\)",
            masked,
            re.IGNORECASE,
        )
        if in_match:
            names.extend(_BIND_RE.findall(in_match.group(1)))
    return list(dict.fromkeys(name.lower() for name in names))


def _absolute_date_bind_names(sql: str, full_column: str) -> tuple[str, str] | None:
    masked = _masked_sql(sql)
    for variant in _column_variants(sql, full_column):
        pattern = re.escape(variant).replace(r"\.", r"\s*\.\s*")
        between = re.search(
            rf"\b{pattern}\b\s+BETWEEN\s+:([A-Z][A-Z0-9_]*)\s+AND\s+:([A-Z][A-Z0-9_]*)",
            masked,
            re.IGNORECASE,
        )
        if between:
            return between.group(1).lower(), between.group(2).lower()
        lower = re.search(
            rf"\b{pattern}\b\s*(?:>=|>)\s*:([A-Z][A-Z0-9_]*)",
            masked,
            re.IGNORECASE,
        )
        upper = re.search(
            rf"\b{pattern}\b\s*(?:<=|<)\s*:([A-Z][A-Z0-9_]*)",
            masked,
            re.IGNORECASE,
        )
        if lower and upper:
            return lower.group(1).lower(), upper.group(1).lower()
    return None


def _safe_bind_value(value: Any) -> OracleBindValue:
    if value is None or isinstance(value, (bool, int, date, datetime, Decimal)):
        if isinstance(value, Decimal) and not value.is_finite():
            raise ParameterBindingError("A bind value is not finite.")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ParameterBindingError("A bind value is not finite.")
        return value
    if isinstance(value, str):
        if len(value) > 1000:
            raise ParameterBindingError("A bind value exceeds the V1 length limit.")
        return value
    raise ParameterBindingError("A bind value has an unsupported type.")


def _assign_bind(
    result: dict[str, OracleBindValue],
    name: str,
    value: Any,
) -> None:
    key = name.lower()
    safe_value = _safe_bind_value(value)
    if key in result and result[key] != safe_value:
        raise ParameterBindingError("A named bind maps to conflicting validated values.")
    result[key] = safe_value


def _map_requirement(
    sql: str,
    grounding: GroundedSchemaPlan,
    concept: str,
    values: list[Any],
    result: dict[str, OracleBindValue],
) -> None:
    names: list[str] = []
    for column in _concept_columns(grounding, concept):
        names.extend(_bind_names_for_column(sql, column))
    names = list(dict.fromkeys(names))
    if len(names) != len(values):
        raise ParameterBindingError(f"Required bind parameters are unresolved for concept '{concept}'.")
    for name, value in zip(names, values):
        _assign_bind(result, name, value)


def _filter_values(operator: FilterOperator, value: Any) -> list[Any]:
    values = list(value) if isinstance(value, (list, tuple)) else [value]
    if operator is FilterOperator.CONTAINS:
        return [f"%{values[0]}%"]
    if operator is FilterOperator.STARTS_WITH:
        return [f"{values[0]}%"]
    return values


def build_bind_parameters(
    sql: str,
    plan: QueryPlan,
    grounding: GroundedSchemaPlan,
) -> dict[str, OracleBindValue]:
    """Derive an exact bind mapping only from validated plan values."""
    positional = re.findall(r"(?<!:):(\d+)\b", _masked_sql(sql))
    if positional:
        raise ParameterBindingError("Positional bind parameters are not allowed.")

    result: dict[str, OracleBindValue] = {}
    for entity in plan.entities:
        if entity.status is EntityStatus.NOT_REQUIRED:
            continue
        if entity.status is not EntityStatus.RESOLVED or entity.selected_value in (None, ""):
            raise ParameterBindingError(f"Entity '{entity.concept}' is not resolved.")
        _map_requirement(sql, grounding, entity.concept, [entity.selected_value], result)

    for item in plan.filters:
        if item.value is None or item.operator in {FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL}:
            continue
        if plan.date_range and _normalise(item.concept) in {"date", "time", "period"}:
            continue
        _map_requirement(
            sql,
            grounding,
            item.concept,
            _filter_values(item.operator, item.value),
            result,
        )

    if plan.date_range and plan.date_range.kind is DateRangeKind.ABSOLUTE:
        if not plan.date_range.start or not plan.date_range.end:
            raise ParameterBindingError("Absolute date ranges require start and end values.")
        date_columns = [
            f"{column.full_table_name}.{column.column_name}"
            for column in grounding.selected_columns
            if column.role == "date_filter"
        ]
        date_names = next(
            (
                names
                for column in date_columns
                if (names := _absolute_date_bind_names(sql, column)) is not None
            ),
            None,
        )
        if date_names is None:
            raise ParameterBindingError("Absolute date range binds are unresolved.")
        try:
            start_value = date.fromisoformat(plan.date_range.start)
            end_value = date.fromisoformat(plan.date_range.end)
        except ValueError as exc:
            raise ParameterBindingError("Absolute date values must use ISO dates.") from exc
        if start_value > end_value:
            raise ParameterBindingError("Absolute date range start must not be after end.")
        _assign_bind(result, date_names[0], start_value)
        _assign_bind(result, date_names[1], end_value)

    placeholders = {name.lower() for name in _BIND_RE.findall(_masked_sql(sql))}
    keys = set(result)
    if placeholders != keys:
        missing = placeholders - keys
        extra = keys - placeholders
        if missing:
            raise ParameterBindingError("Generated SQL contains unresolved bind parameters.")
        if extra:
            raise ParameterBindingError("Validated parameters are missing from generated SQL.")
    return result


def add_execution_probe_limit(sql: str, plan: QueryPlan, max_rows: int) -> tuple[str, int, str]:
    """Apply a database-side sentinel limit without trusting model-supplied limits."""
    if plan.limit is not None:
        if plan.limit > max_rows:
            raise ParameterBindingError("Requested row limit exceeds the V1 execution maximum.")
        return sql, plan.limit, "requested"
    if re.search(r"\bFETCH\s+(?:FIRST|NEXT)\b|\bROWNUM\b", _masked_sql(sql), re.IGNORECASE):
        raise GroundedSqlValidationError("Unexpected model-supplied row limit.")
    return f"{sql.rstrip()}\nFETCH FIRST {max_rows + 1} ROWS ONLY", max_rows, "service_default"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsupportedResultValueError("A floating-point result is not finite.")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise UnsupportedResultValueError("A decimal result is not finite.")
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise UnsupportedResultValueError(
        f"Unsupported result value type: {type(value).__name__}."
    )


def _normalise_result(
    raw: Mapping[str, Any],
    *,
    row_limit: int,
    limit_source: Literal["requested", "service_default"],
    fallback_elapsed_ms: int,
) -> TypedExecutionResult:
    columns_raw = raw.get("columns")
    rows_raw = raw.get("rows")
    if not isinstance(columns_raw, (list, tuple)) or not columns_raw:
        raise ExecutionResultError("The executor returned no valid columns.")
    if not isinstance(rows_raw, (list, tuple)):
        raise ExecutionResultError("The executor returned no valid rows collection.")
    columns = [str(column).strip() for column in columns_raw]
    if any(not column for column in columns) or len({item.upper() for item in columns}) != len(columns):
        raise ExecutionResultError("Executor columns must be non-empty and unique.")

    if limit_source == "requested" and len(rows_raw) > row_limit:
        raise ExecutionResultError("The executor exceeded the requested SQL limit.")
    truncated = limit_source == "service_default" and len(rows_raw) > row_limit
    selected_rows = rows_raw[:row_limit]
    rows: list[list[Any]] = []
    for row in selected_rows:
        if not isinstance(row, (list, tuple)) or len(row) != len(columns):
            raise ExecutionResultError("An executor row does not match the column contract.")
        rows.append([_json_safe(value) for value in row])
    elapsed = raw.get("elapsed_ms", fallback_elapsed_ms)
    elapsed_ms = elapsed if isinstance(elapsed, int) and elapsed >= 0 else fallback_elapsed_ms
    return TypedExecutionResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        metadata=ExecutionMetadata(
            elapsed_ms=elapsed_ms,
            row_limit=row_limit,
            limit_source=limit_source,
        ),
    )


def selected_fields_from_sql(sql: str) -> list[str]:
    matches = list(re.finditer(r"\bSELECT\b(.*?)\bFROM\b", sql, re.IGNORECASE | re.DOTALL))
    if not matches:
        raise GroundedSqlValidationError("SQL output fields could not be identified.")
    body = matches[-1].group(1)
    fields: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(body + ","):
        depth += (char == "(") - (char == ")")
        if char == "," and depth == 0:
            expression = body[start:index].strip()
            alias = re.search(r"\bAS\s+([A-Z][A-Z0-9_$#]*)\s*$", expression, re.IGNORECASE)
            if alias:
                fields.append(alias.group(1))
            elif re.fullmatch(rf"{_IDENTIFIER}(?:\.{_IDENTIFIER}){{0,2}}", expression, re.IGNORECASE):
                fields.append(expression.rsplit(".", 1)[-1])
            else:
                fields.append(expression)
            start = index + 1
    return fields


def _field_key(value: str) -> str:
    return re.sub(r"\s+", "", value).upper()


def _validate_output_columns(selected_fields: list[str], columns: list[str]) -> None:
    if len(selected_fields) != len(columns) or any(
        _field_key(selected) != _field_key(column)
        for selected, column in zip(selected_fields, columns)
    ):
        raise ExecutionResultError("Executor columns do not agree with validated SQL output fields.")


def _report_type(plan: QueryPlan, result: TypedExecutionResult) -> ReportType:
    if result.row_count == 0:
        return ReportType.EMPTY
    if plan.operation.lower() == "ranking":
        return ReportType.RANKING
    aggregated = any(item.aggregation is not Aggregation.NONE for item in plan.measures)
    grouped = any(item.grouping for item in plan.dimensions)
    if aggregated and grouped:
        return ReportType.GROUPED_TOTALS
    if aggregated:
        return ReportType.AGGREGATE
    return ReportType.DETAIL


def _summary(plan: QueryPlan, result: TypedExecutionResult, report_type: ReportType) -> str:
    suffix = " Results were truncated at the configured row limit." if result.truncated else ""
    if report_type is ReportType.EMPTY:
        return "No matching records were returned."
    if report_type is ReportType.DETAIL:
        return f"Returned {result.row_count} detail record(s).{suffix}"
    dimensions = ", ".join(item.concept for item in plan.dimensions if item.grouping)
    measures = ", ".join(item.concept for item in plan.measures)
    if report_type is ReportType.RANKING:
        return f"Returned {result.row_count} ranked row(s) for {dimensions} by {measures}.{suffix}"
    if report_type is ReportType.GROUPED_TOTALS:
        return f"Returned {result.row_count} grouped row(s) by {dimensions}, using {measures}.{suffix}"
    if result.row_count != 1:
        raise ExecutionResultError("A non-grouped aggregate must return exactly one row.")
    values = [
        f"{column}={value}"
        for column, value in zip(result.columns, result.rows[0])
        if value is not None
    ]
    return "Aggregate result: " + (", ".join(values) + "." if values else "no value.")


def _default_runner(sql: str, binds: Mapping[str, OracleBindValue]) -> Mapping[str, Any]:
    return run_safe_select(
        sql,
        binds=binds,
        enforce_row_limit=False,
        validate_datatypes=True,
    )


def execute_nlp_query(
    question: str,
    *,
    dependencies: NLPExecutionDependencies | None = None,
    max_rows: int | None = None,
    include_sql_preview: bool | None = None,
) -> NLPExecuteResponse:
    """Run the V1 pipeline once, with all rejection gates before the runner."""
    deps = dependencies or NLPExecutionDependencies()
    runner = deps.runner or _default_runner
    service_limit = max_rows if max_rows is not None else _configured_max_rows()
    if not 1 <= service_limit <= HARD_EXECUTION_MAX_ROWS:
        raise ValueError("Execution row limit is outside the safe range.")

    correction = deps.correct_text(question)
    analysis = deps.analyze(correction.corrected_question)
    plan = deps.extract_plan(
        correction.corrected_question,
        nlp_analysis=analysis,
        original_question=correction.original_question,
    )
    capability = evaluate_capability(plan)
    ambiguities = [item.reason for item in plan.ambiguities if item.blocking]
    unresolved = [
        f"Entity '{item.concept}' requires verified resolution before execution."
        for item in plan.entities
        if item.status in {EntityStatus.UNRESOLVED, EntityStatus.AMBIGUOUS}
    ]
    if plan.confidence < 0.65:
        ambiguities.append("The validated QueryPlan confidence is below the execution threshold.")
    ambiguities.extend(unresolved)
    if ambiguities or not capability.supported:
        _reject(
            question=question,
            corrected_question=correction.corrected_question,
            plan=plan,
            grounding=None,
            capability=capability,
            ambiguities=ambiguities,
            reject_reasons=capability.reject_reasons,
        )

    grounding = deps.ground_plan(plan)
    if not grounding.is_grounded:
        _reject(
            question=question,
            corrected_question=correction.corrected_question,
            plan=plan,
            grounding=grounding,
            capability=capability,
            ambiguities=[item.reason for item in grounding.ambiguities if item.blocking],
            reject_reasons=[item.reason for item in grounding.reject_reasons],
        )

    preview = deps.generate_sql(plan, grounding)
    execution_sql, response_limit, limit_source = add_execution_probe_limit(
        preview.sql,
        plan,
        service_limit,
    )
    binds = build_bind_parameters(execution_sql, plan, grounding)
    selected_fields = selected_fields_from_sql(execution_sql)

    # This must remain the final operation before the single runner call.
    validate_grounded_sql(execution_sql, plan, grounding)
    started = time.monotonic()
    try:
        raw = runner(execution_sql, binds)
    except (OracleUnavailableError, OracleExecutionError):
        raise
    except Exception as exc:
        raise ExecutionResultError("The safe query executor failed.") from exc
    fallback_elapsed_ms = int((time.monotonic() - started) * 1000)
    result = _normalise_result(
        raw,
        row_limit=response_limit,
        limit_source=limit_source,
        fallback_elapsed_ms=fallback_elapsed_ms,
    )
    _validate_output_columns(selected_fields, result.columns)
    report_type = _report_type(plan, result)
    summary = _summary(plan, result, report_type)
    expose_sql = _include_sql_preview() if include_sql_preview is None else include_sql_preview
    return NLPExecuteResponse(
        question=question,
        correction=correction,
        query_plan=_plan_summary(plan),
        grounding=_grounding_summary(grounding, capability),
        sql_preview=execution_sql if expose_sql else None,
        selected_fields=selected_fields,
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        report_type=report_type,
        summary=summary,
        execution=result.metadata,
    )


__all__ = [
    "ExecutionRejectedError",
    "ExecutionResultError",
    "NLPExecuteFailureResponse",
    "NLPExecuteResponse",
    "NLPExecutionDependencies",
    "ParameterBindingError",
    "ReportType",
    "TypedExecutionResult",
    "UnsupportedResultValueError",
    "add_execution_probe_limit",
    "build_bind_parameters",
    "execute_nlp_query",
    "selected_fields_from_sql",
]
