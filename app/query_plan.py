"""Schema-independent meaning contract for a business data question."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


MAX_QUERY_PLAN_LIMIT = 1000
CLARIFICATION_CONFIDENCE_THRESHOLD = 0.65


class Aggregation(str, Enum):
    NONE = "none"
    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    AVERAGE = "average"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"


class EntityStatus(str, Enum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_REQUIRED = "not_required"


class FilterOperator(str, Enum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    IN = "in"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    BETWEEN = "between"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class DateRangeKind(str, Enum):
    ABSOLUTE = "absolute"
    RELATIVE = "relative"
    UNSPECIFIED = "unspecified"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


class BusinessSubject(BaseModel):
    concept: str
    original_text: str | None = None
    synonyms: list[str] = Field(default_factory=list)


class Measure(BaseModel):
    concept: str
    aggregation: Aggregation = Aggregation.NONE
    alias: str | None = None
    original_text: str | None = None


class Dimension(BaseModel):
    concept: str
    grouping: bool = False
    original_text: str | None = None


class EntityReference(BaseModel):
    concept: str
    original_value: str | None = None
    normalized_value: str | None = None
    selected_value: str | None = None
    candidates: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    status: EntityStatus

    @model_validator(mode="after")
    def validate_resolution(self) -> "EntityReference":
        if self.status is EntityStatus.RESOLVED and not self.selected_value:
            raise ValueError("A resolved entity must have a selected_value.")
        if self.status is EntityStatus.AMBIGUOUS and len(self.candidates) < 2:
            raise ValueError("An ambiguous entity must have at least two candidates.")
        return self


class QueryFilter(BaseModel):
    concept: str
    operator: FilterOperator
    value: Any = None
    value_type: str
    original_text: str | None = None

    @model_validator(mode="after")
    def validate_between_boundaries(self) -> "QueryFilter":
        if self.operator is FilterOperator.BETWEEN:
            if not isinstance(self.value, (list, tuple)) or len(self.value) != 2 or any(
                boundary is None for boundary in self.value
            ):
                raise ValueError("A between filter must contain two boundary values.")
        return self


class DateRange(BaseModel):
    kind: DateRangeKind = DateRangeKind.UNSPECIFIED
    start: str | None = None
    end: str | None = None
    inclusive_start: bool = True
    inclusive_end: bool = True
    original_text: str | None = None


class Comparison(BaseModel):
    type: str
    baseline: Any = None
    target: Any = None
    direction: str | None = None
    original_text: str | None = None


class SortInstruction(BaseModel):
    field_concept: str
    direction: SortDirection
    priority: int = Field(ge=0)


class RequestedOutput(BaseModel):
    format: str = "table"
    detail_level: str = "summary"
    fields: list[str] = Field(default_factory=list)


class Ambiguity(BaseModel):
    field: str
    reason: str
    options: list[str] = Field(default_factory=list)
    blocking: bool = False


class QueryPlan(BaseModel):
    """Logical intent only; physical schema and SQL belong to later stages."""

    version: str = "1.0"
    original_question: str
    domain: str
    operation: str
    business_subject: BusinessSubject | None = None
    measures: list[Measure] = Field(default_factory=list)
    dimensions: list[Dimension] = Field(default_factory=list)
    entities: list[EntityReference] = Field(default_factory=list)
    filters: list[QueryFilter] = Field(default_factory=list)
    date_range: DateRange | None = None
    comparison: Comparison | None = None
    sorting: list[SortInstruction] = Field(default_factory=list)
    limit: int | None = Field(default=None, gt=0, le=MAX_QUERY_PLAN_LIMIT)
    requested_output: RequestedOutput = Field(default_factory=RequestedOutput)
    ambiguities: list[Ambiguity] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)

    @field_validator("original_question")
    @classmethod
    def validate_original_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("original_question cannot be empty.")
        return value

    @model_validator(mode="after")
    def validate_semantic_content(self) -> "QueryPlan":
        if not self.business_subject and not self.measures and not self.dimensions:
            raise ValueError("A plan needs a business subject, measure, or dimension.")
        return self

    @property
    def requires_clarification(self) -> bool:
        return (
            self.confidence < CLARIFICATION_CONFIDENCE_THRESHOLD
            or any(item.blocking for item in self.ambiguities)
            or any(entity.status is EntityStatus.AMBIGUOUS for entity in self.entities)
        )
