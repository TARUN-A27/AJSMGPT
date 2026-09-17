"""Conservative, offline validation for grounded Oracle SQL previews.

This module intentionally validates the small SQL surface generated for the V1
preview endpoint.  It is not a general SQL parser: generated SQL must use fully
qualified physical tables and qualified column references so each identifier can
be checked against a :class:`GroundedSchemaPlan` without Oracle access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.query_plan import Aggregation, DateRangeKind, EntityStatus, QueryPlan
from app.schema_grounding import GroundedSchemaPlan


class GroundedSqlValidationError(ValueError):
    """Base class for a rejected SQL preview."""


class UnsafeGroundedSqlError(GroundedSqlValidationError):
    """The SQL contains a structurally unsafe construct."""


class OracleDialectGroundedSqlError(GroundedSqlValidationError):
    """The SQL uses syntax that Oracle does not support."""


class GroundedSqlGroundingError(GroundedSqlValidationError):
    """The SQL uses a table, column, or join outside its grounding plan."""


class GroundedSqlSemanticError(GroundedSqlValidationError):
    """The SQL does not implement a required QueryPlan instruction."""


_IDENTIFIER = r"[A-Z][A-Z0-9_$#]*"
_ALIAS_STOP_PATTERN = (
    r"WHERE|JOIN|INNER|LEFT|RIGHT|FULL|CROSS|OUTER|ON|GROUP|ORDER|FETCH|OFFSET|"
    r"UNION|MINUS|INTERSECT|CONNECT"
)
_TABLE_REF_RE = re.compile(
    rf"\b(FROM|JOIN)\s+({_IDENTIFIER}(?:\.{_IDENTIFIER})?)"
    rf"(?:\s+(?:AS\s+)?((?!(?:{_ALIAS_STOP_PATTERN})\b){_IDENTIFIER}))?",
    re.IGNORECASE,
)
_THREE_PART_COLUMN_RE = re.compile(
    rf"\b({_IDENTIFIER})\.({_IDENTIFIER})\.({_IDENTIFIER})\b", re.IGNORECASE
)
_TWO_PART_COLUMN_RE = re.compile(rf"\b({_IDENTIFIER})\.({_IDENTIFIER})\b", re.IGNORECASE)
_BLOCKED_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "ALTER", "TRUNCATE", "CREATE",
    "REPLACE", "GRANT", "REVOKE", "COMMIT", "ROLLBACK", "SAVEPOINT", "EXEC",
    "EXECUTE", "CALL", "BEGIN", "DECLARE",
}
_ALIAS_STOP_WORDS = {
    "WHERE", "JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS", "OUTER", "ON",
    "GROUP", "ORDER", "FETCH", "OFFSET", "UNION", "MINUS", "INTERSECT", "CONNECT",
}
_SQL_KEYWORDS = _ALIAS_STOP_WORDS | {
    "SELECT", "FROM", "AS", "AND", "OR", "NOT", "IS", "NULL", "LIKE", "IN",
    "BETWEEN", "BY", "HAVING", "ASC", "DESC", "NULLS", "FIRST", "LAST", "ROWS",
    "ROW", "ONLY", "NEXT", "DISTINCT", "ALL", "CASE", "WHEN", "THEN", "ELSE", "END",
    "WITH", "RECURSIVE", "SYSDATE", "CURRENT_DATE", "CURRENT_TIMESTAMP", "ROWNUM", "LIMIT",
}


@dataclass(frozen=True)
class _SqlReferences:
    tables: set[str]
    aliases: dict[str, str]
    ctes: set[str]


def _normalise_concept(value: str) -> str:
    return " ".join(value.lower().replace("_", " ").replace("-", " ").split())


def _relative_date_requirement(query_plan: QueryPlan) -> tuple[str, int] | None:
    """Return the unit and count encoded by a relative date-range description."""
    date_range = query_plan.date_range
    if date_range is None or date_range.kind is not DateRangeKind.RELATIVE:
        return None
    text = date_range.original_text or f"{date_range.start or ''} {date_range.end or ''}"
    match = re.search(
        r"\b(?:last|past|previous)\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve)\s+(months?|days?)\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+(months?|days?)\b",
            text, re.IGNORECASE,
        )
    if not match:
        return None
    number = match.group(1).lower()
    count = int(number) if number.isdigit() else {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    }[number]
    return ("months" if match.group(2).lower().startswith("month") else "days", count)


def _mask_string_literals(sql: str) -> str:
    """Preserve positions while hiding quoted content from structural regexes."""
    return re.sub(r"'(?:''|[^'])*'", lambda match: " " * len(match.group(0)), sql)


def _column_pattern(reference: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in reference.split(".")]
    return re.compile(r"\b" + r"\s*\.\s*".join(parts) + r"\b", re.IGNORECASE)


def _cte_names(masked_sql: str) -> set[str]:
    if not re.match(r"^\s*WITH\b", masked_sql, re.IGNORECASE):
        return set()
    return {
        match.group(1).upper()
        for match in re.finditer(rf"(?:\bWITH\b|,)\s*({_IDENTIFIER})\s+AS\s*\(", masked_sql, re.IGNORECASE)
    }


def _extract_references(masked_sql: str) -> _SqlReferences:
    ctes = _cte_names(masked_sql)
    tables: set[str] = set()
    aliases: dict[str, str] = {}
    for match in _TABLE_REF_RE.finditer(masked_sql):
        token = match.group(2).upper()
        alias = (match.group(3) or "").upper()
        if token in ctes:
            cte_target = "@CTE:" + token
            aliases[token] = cte_target
            if alias and alias not in _ALIAS_STOP_WORDS:
                aliases[alias] = cte_target
            continue
        if "." not in token:
            raise GroundedSqlGroundingError(
                f"Physical table '{token}' must be schema-qualified or be a declared CTE."
            )
        tables.add(token)
        schema, table = token.split(".", 1)
        aliases[token] = token
        aliases[table] = token
        if alias and alias not in _ALIAS_STOP_WORDS:
            aliases[alias] = token
        aliases[schema + "." + table] = token
    if not tables:
        raise GroundedSqlGroundingError("SQL does not reference a grounded physical table.")
    return _SqlReferences(tables=tables, aliases=aliases, ctes=ctes)


def _basic_safety(sql: str) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise UnsafeGroundedSqlError("SQL is empty.")
    if "--" in sql or "/*" in sql or "*/" in sql:
        raise UnsafeGroundedSqlError("SQL comments are not allowed.")
    if ";" in sql:
        raise UnsafeGroundedSqlError("Semicolons and multiple statements are not allowed.")
    if '"' in sql:
        raise UnsafeGroundedSqlError("Quoted identifiers are outside the preview SQL subset.")

    masked = _mask_string_literals(sql.strip())
    if not re.match(r"^(?:SELECT\b|WITH\b)", masked, re.IGNORECASE):
        raise UnsafeGroundedSqlError("Only one SELECT or WITH statement is allowed.")
    for keyword in _BLOCKED_KEYWORDS:
        if re.search(rf"\b{keyword}\b", masked, re.IGNORECASE):
            raise UnsafeGroundedSqlError(f"Blocked SQL keyword: {keyword}.")
    if re.search(r"\bLIMIT\b", masked, re.IGNORECASE):
        raise OracleDialectGroundedSqlError(
            "Oracle SQL does not support LIMIT; use FETCH FIRST <N> ROWS ONLY."
        )
    if re.search(r"\bSELECT\s+(?:DISTINCT\s+)?(?:[A-Z][A-Z0-9_$#]*\s*\.\s*)?\*", masked, re.IGNORECASE):
        raise UnsafeGroundedSqlError("SELECT * is not allowed.")
    if re.search(rf"\b{_IDENTIFIER}\s*\.\s*\*", masked, re.IGNORECASE):
        raise UnsafeGroundedSqlError("Wildcard column selection is not allowed.")
    return masked


def _allowed_columns(plan: GroundedSchemaPlan) -> dict[str, set[str]]:
    allowed: dict[str, set[str]] = {}
    for column in plan.selected_columns:
        allowed.setdefault(column.full_table_name.upper(), set()).add(column.column_name.upper())
    return allowed


def _validate_tables_and_columns(
    masked_sql: str,
    grounding: GroundedSchemaPlan,
    references: _SqlReferences,
) -> None:
    allowed_tables = {table.full_table_name.upper() for table in grounding.selected_tables}
    unexpected_tables = sorted(references.tables - allowed_tables)
    if unexpected_tables:
        raise GroundedSqlGroundingError("Ungrounded table(s): " + ", ".join(unexpected_tables))

    allowed_columns = _allowed_columns(grounding)
    consumed_spans: list[tuple[int, int]] = []
    for match in _THREE_PART_COLUMN_RE.finditer(masked_sql):
        table = f"{match.group(1)}.{match.group(2)}".upper()
        column = match.group(3).upper()
        if table not in allowed_tables or column not in allowed_columns.get(table, set()):
            raise GroundedSqlGroundingError(f"Ungrounded column: {table}.{column}.")
        consumed_spans.append(match.span())

    table_spans = [match.span(2) for match in _TABLE_REF_RE.finditer(masked_sql)]
    for match in _TWO_PART_COLUMN_RE.finditer(masked_sql):
        if any(start <= match.start() and match.end() <= end for start, end in consumed_spans + table_spans):
            continue
        prefix, column = match.group(1).upper(), match.group(2).upper()
        table = references.aliases.get(prefix)
        if table is None:
            raise GroundedSqlGroundingError(f"Unknown table alias in column reference: {prefix}.{column}.")
        if table.startswith("@CTE:"):
            continue
        if column not in allowed_columns.get(table, set()):
            raise GroundedSqlGroundingError(f"Ungrounded column: {prefix}.{column}.")

    qualified_spans = [match.span() for match in _THREE_PART_COLUMN_RE.finditer(masked_sql)]
    qualified_spans.extend(match.span() for match in _TWO_PART_COLUMN_RE.finditer(masked_sql))
    declared_aliases = {
        match.group(1).upper()
        for match in re.finditer(rf"\bAS\s+({_IDENTIFIER})\b", masked_sql, re.IGNORECASE)
    }
    known_names = set(references.aliases) | references.ctes | declared_aliases | _SQL_KEYWORDS
    referenced_tables = references.tables
    for match in re.finditer(rf"\b({_IDENTIFIER})\b", masked_sql, re.IGNORECASE):
        token = match.group(1).upper()
        if any(start <= match.start() and match.end() <= end for start, end in qualified_spans):
            continue
        if token in known_names or masked_sql[match.end():].lstrip().startswith("("):
            continue
        if match.start() > 0 and masked_sql[match.start() - 1] == ":":
            continue
        candidates = [table for table in referenced_tables if token in allowed_columns.get(table, set())]
        if len(candidates) == 1:
            continue
        if len(candidates) > 1:
            raise GroundedSqlGroundingError(f"Ambiguous unqualified column: {token}.")
        raise GroundedSqlGroundingError(f"Unknown unqualified column or identifier: {token}.")


def _unqualified_column_map(
    grounding: GroundedSchemaPlan,
    references: _SqlReferences,
) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for column in grounding.selected_columns:
        table = column.full_table_name.upper()
        if table in references.tables:
            candidates.setdefault(column.column_name.upper(), set()).add(table)
    return {column: next(iter(tables)) for column, tables in candidates.items() if len(tables) == 1}


def _resolve_column(
    reference: str,
    aliases: dict[str, str],
    unqualified_columns: dict[str, str] | None = None,
) -> tuple[str, str] | None:
    parts = reference.upper().split(".")
    if len(parts) == 3:
        return ".".join(parts[:2]), parts[2]
    if len(parts) == 2 and parts[0] in aliases:
        return aliases[parts[0]], parts[1]
    if len(parts) == 1 and unqualified_columns and parts[0] in unqualified_columns:
        return unqualified_columns[parts[0]], parts[0]
    return None


def _validate_joins(masked_sql: str, grounding: GroundedSchemaPlan, references: _SqlReferences) -> None:
    join_matches = list(re.finditer(
        rf"\bJOIN\s+({_IDENTIFIER}\.{_IDENTIFIER})(?:\s+(?:AS\s+)?({_IDENTIFIER}))?\s+ON\s+"
        rf"(.*?)(?=\b(?:INNER|LEFT|RIGHT|FULL|CROSS)?\s*JOIN\b|\bWHERE\b|\bGROUP\s+BY\b|"
        rf"\bORDER\s+BY\b|\bFETCH\b|\bOFFSET\b|$)",
        masked_sql,
        re.IGNORECASE | re.DOTALL,
    ))
    if len(references.tables) > 1 and not join_matches:
        raise GroundedSqlGroundingError("Multiple grounded tables require explicit verified JOIN clauses.")

    allowed_pairs: set[frozenset[str]] = set()
    for path in grounding.allowed_relationship_paths:
        for left, right in zip(path.tables, path.tables[1:]):
            allowed_pairs.add(frozenset((left.upper(), right.upper())))
    join_columns = {
        (column.full_table_name.upper(), column.column_name.upper())
        for column in grounding.selected_columns
        if column.role == "join_identifier"
    }
    unqualified_columns = _unqualified_column_map(grounding, references)

    for match in join_matches:
        joined_table = match.group(1).upper()
        condition = match.group(3)
        verified = False
        for equality in re.finditer(
            rf"\b({_IDENTIFIER}(?:\.{_IDENTIFIER}){{0,2}})\s*=\s*({_IDENTIFIER}(?:\.{_IDENTIFIER}){{0,2}})\b",
            condition,
            re.IGNORECASE,
        ):
            left = _resolve_column(equality.group(1), references.aliases, unqualified_columns)
            right = _resolve_column(equality.group(2), references.aliases, unqualified_columns)
            if not left or not right or joined_table not in {left[0], right[0]}:
                continue
            if frozenset((left[0], right[0])) not in allowed_pairs:
                continue
            if left in join_columns and right in join_columns:
                verified = True
                break
        if not verified:
            raise GroundedSqlGroundingError(f"JOIN to {joined_table} does not match a verified relationship path.")


def _columns_for_concept(grounding: GroundedSchemaPlan, concept: str, role: str | None = None) -> list[str]:
    target = _normalise_concept(concept)
    return [
        f"{column.full_table_name}.{column.column_name}".upper()
        for column in grounding.selected_columns
        if _normalise_concept(column.logical_concept) == target and (role is None or column.role == role)
    ]


def _columns_for_requirement(grounding: GroundedSchemaPlan, concept: str, role: str) -> list[str]:
    """Use the canonical concept when possible, then the role chosen by grounding."""
    exact = _columns_for_concept(grounding, concept, role)
    if exact:
        return exact
    return [
        f"{column.full_table_name}.{column.column_name}".upper()
        for column in grounding.selected_columns
        if column.role == role
    ]


def _reference_variants(
    full_column: str,
    aliases: dict[str, str],
    unqualified_columns: dict[str, str] | None = None,
) -> list[str]:
    table, column = full_column.rsplit(".", 1)
    variants = [full_column]
    variants.extend(f"{alias}.{column}" for alias, target in aliases.items() if target == table)
    if unqualified_columns and unqualified_columns.get(column) == table:
        variants.append(column)
    return variants


def _contains_column(
    fragment: str,
    full_column: str,
    aliases: dict[str, str],
    unqualified_columns: dict[str, str] | None = None,
) -> bool:
    return any(
        _column_pattern(variant).search(fragment)
        for variant in _reference_variants(full_column, aliases, unqualified_columns)
    )


def _select_fragment(masked_sql: str) -> str:
    matches = list(re.finditer(r"\bSELECT\b(.*?)\bFROM\b", masked_sql, re.IGNORECASE | re.DOTALL))
    if not matches:
        raise UnsafeGroundedSqlError("SELECT list could not be identified.")
    return matches[-1].group(1)


def _clause(masked_sql: str, start: str, stops: str) -> str:
    match = re.search(rf"\b{start}\b(.*?)(?={stops}|$)", masked_sql, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else ""


def _validate_measures_and_grouping(
    masked_sql: str,
    query_plan: QueryPlan,
    grounding: GroundedSchemaPlan,
    references: _SqlReferences,
) -> None:
    select_part = _select_fragment(masked_sql)
    group_part = _clause(masked_sql, r"GROUP\s+BY", r"\bORDER\s+BY\b|\bFETCH\b|\bOFFSET\b")
    unqualified_columns = _unqualified_column_map(grounding, references)
    aggregate_map = {
        Aggregation.SUM: "SUM",
        Aggregation.COUNT: "COUNT",
        Aggregation.COUNT_DISTINCT: "COUNT",
        Aggregation.AVERAGE: "AVG",
        Aggregation.MINIMUM: "MIN",
        Aggregation.MAXIMUM: "MAX",
    }
    violations: list[str] = []
    has_aggregate = False
    for measure in query_plan.measures:
        if measure.aggregation is Aggregation.NONE:
            continue
        has_aggregate = True
        columns = _columns_for_requirement(grounding, measure.concept, "measure")
        function = aggregate_map[measure.aggregation]
        matched = False
        for column in columns:
            for variant in _reference_variants(column, references.aliases, unqualified_columns):
                distinct = r"DISTINCT\s+" if measure.aggregation is Aggregation.COUNT_DISTINCT else ""
                if re.search(
                    rf"\b{function}\s*\(\s*{distinct}{_column_pattern(variant).pattern}\s*\)",
                    select_part,
                    re.IGNORECASE,
                ):
                    matched = True
                    break
            if matched:
                break
        if not matched:
            violations.append(f"Required aggregate measure is missing: {measure.concept}.")

    for dimension in query_plan.dimensions:
        if not dimension.grouping:
            continue
        columns = _columns_for_requirement(grounding, dimension.concept, "grouping")
        if not columns or not any(
            _contains_column(select_part, column, references.aliases, unqualified_columns)
            for column in columns
        ):
            required = ", ".join(columns) if columns else dimension.concept
            violations.append(
                f"Required user-facing display/grouping column is missing from SELECT: {required}."
            )
        if has_aggregate and not any(
            _contains_column(group_part, column, references.aliases, unqualified_columns)
            for column in columns
        ):
            required = ", ".join(columns) if columns else dimension.concept
            violations.append(f"Required GROUP BY column is missing: {required}.")

    for field in query_plan.requested_output.fields:
        columns = _columns_for_concept(grounding, field)
        if not columns:
            dimension = next(
                (item for item in query_plan.dimensions if _normalise_concept(item.concept) == _normalise_concept(field)),
                None,
            )
            measure = next(
                (item for item in query_plan.measures if _normalise_concept(item.concept) == _normalise_concept(field)),
                None,
            )
            if dimension is not None:
                columns = _columns_for_requirement(grounding, dimension.concept, "grouping")
            elif measure is not None:
                columns = _columns_for_requirement(grounding, measure.concept, "measure")
        if columns and not any(
            _contains_column(select_part, column, references.aliases, unqualified_columns)
            for column in columns
        ):
            violations.append(f"Requested output field is missing: {field}.")
    if violations:
        raise GroundedSqlSemanticError("; ".join(violations))


def _validate_sort_limit_and_date(
    masked_sql: str,
    query_plan: QueryPlan,
    grounding: GroundedSchemaPlan,
    references: _SqlReferences,
) -> None:
    unqualified_columns = _unqualified_column_map(grounding, references)
    order_part = _clause(masked_sql, r"ORDER\s+BY", r"\bFETCH\b|\bOFFSET\b")
    violations: list[str] = []
    for instruction in query_plan.sorting:
        direction = instruction.direction.value.upper()
        if not order_part or not re.search(rf"\b{direction}\b", order_part, re.IGNORECASE):
            violations.append(f"Required sort direction is missing: {direction}.")

    if query_plan.limit is not None:
        limit = query_plan.limit
        fetch = re.search(rf"\bFETCH\s+(?:FIRST|NEXT)\s+{limit}\s+ROWS?\s+ONLY\b", masked_sql, re.IGNORECASE)
        rownum = re.search(rf"\bROWNUM\s*<=?\s*{limit}\b", masked_sql, re.IGNORECASE)
        if not fetch and not rownum:
            violations.append(
                f"Required row limit is missing: use FETCH FIRST {limit} ROWS ONLY."
            )

    if query_plan.date_range and query_plan.date_range.kind is not DateRangeKind.UNSPECIFIED:
        where_part = _clause(masked_sql, "WHERE", r"\bGROUP\s+BY\b|\bORDER\s+BY\b|\bFETCH\b|\bOFFSET\b")
        date_columns = [
            f"{column.full_table_name}.{column.column_name}".upper()
            for column in grounding.selected_columns
            if column.role == "date_filter"
        ]
        if not where_part or not any(
            _contains_column(where_part, column, references.aliases, unqualified_columns)
            for column in date_columns
        ):
            violations.append("Required date filter is missing.")
        if query_plan.date_range.kind is DateRangeKind.RELATIVE:
            relative_requirement = _relative_date_requirement(query_plan)
            if relative_requirement and relative_requirement[0] == "months":
                count = relative_requirement[1]
                month_filter_valid = False
                for date_column in date_columns:
                    for variant in _reference_variants(date_column, references.aliases, unqualified_columns):
                        column_ref = _column_pattern(variant).pattern
                        lower = re.search(
                            rf"{column_ref}\s*>=\s*ADD_MONTHS\s*\(\s*TRUNC\s*\(\s*SYSDATE\s*\)\s*,\s*-\s*{count}\s*\)",
                            where_part, re.IGNORECASE,
                        )
                        upper = re.search(
                            rf"{column_ref}\s*<\s*TRUNC\s*\(\s*SYSDATE\s*\)\s*\+\s*1\b",
                            where_part, re.IGNORECASE,
                        )
                        if lower and upper:
                            month_filter_valid = True
                            break
                    if month_filter_valid:
                        break
                if not month_filter_valid:
                    violations.append(
                        f"Month-based relative date filter requires both predicates: "
                        f"date_column >= ADD_MONTHS(TRUNC(SYSDATE), -{count}) and "
                        "date_column < TRUNC(SYSDATE) + 1; day approximations, single-month "
                        "BETWEEN ranges, and incomplete bounds are not allowed."
                    )
            elif relative_requirement and relative_requirement[0] == "days":
                count = relative_requirement[1]
                day_filter_valid = False
                for date_column in date_columns:
                    for variant in _reference_variants(date_column, references.aliases, unqualified_columns):
                        column_ref = _column_pattern(variant).pattern
                        lower = re.search(
                            rf"{column_ref}\s*>=\s*TRUNC\s*\(\s*SYSDATE\s*\)\s*-\s*{count}\b",
                            where_part, re.IGNORECASE,
                        )
                        upper = re.search(
                            rf"{column_ref}\s*<\s*TRUNC\s*\(\s*SYSDATE\s*\)\s*\+\s*1\b",
                            where_part, re.IGNORECASE,
                        )
                        if lower and upper:
                            day_filter_valid = True
                            break
                    if day_filter_valid:
                        break
                if not day_filter_valid:
                    violations.append(
                        f"Day-based relative date filter requires both predicates: "
                        f"date_column >= TRUNC(SYSDATE) - {count} and "
                        "date_column < TRUNC(SYSDATE) + 1."
                    )
            elif not re.search(
                r"\b(?:SYSDATE|CURRENT_DATE|CURRENT_TIMESTAMP|ADD_MONTHS|TRUNC)\b", where_part, re.IGNORECASE
            ):
                violations.append("Relative date filter must use Oracle current-date semantics.")
    if violations:
        raise GroundedSqlSemanticError("; ".join(violations))


def _filter_requirements(query_plan: QueryPlan) -> list[tuple[str, list[object]]]:
    requirements: list[tuple[str, list[object]]] = []
    for entity in query_plan.entities:
        if entity.status is EntityStatus.NOT_REQUIRED:
            continue
        values = [entity.original_value, entity.normalized_value, entity.selected_value]
        values.extend(entity.candidates)
        requirements.append((entity.concept, [value for value in values if value not in (None, "")]))
    for item in query_plan.filters:
        if item.value is None or item.operator.value in {"is_null", "is_not_null"}:
            continue
        if query_plan.date_range and _normalise_concept(item.concept) in {"date", "time", "period"}:
            continue
        values = list(item.value) if isinstance(item.value, (list, tuple)) else [item.value]
        requirements.append((item.concept, [value for value in values if value not in (None, "")]))
    return requirements


def _validate_entity_binds(
    sql: str,
    masked_sql: str,
    query_plan: QueryPlan,
    grounding: GroundedSchemaPlan,
    references: _SqlReferences,
) -> None:
    requirements = _filter_requirements(query_plan)
    if not requirements:
        return
    unqualified_columns = _unqualified_column_map(grounding, references)
    for concept, values in requirements:
        candidates = grounding.entity_column_candidates.get(concept) or _columns_for_concept(grounding, concept)
        candidate_present = any(
            _contains_column(masked_sql, candidate.upper(), references.aliases, unqualified_columns)
            for candidate in candidates
        )
        if not candidate_present:
            raise GroundedSqlSemanticError(f"Entity filter column is missing: {concept}.")
        bound = False
        for candidate in candidates:
            for variant in _reference_variants(candidate.upper(), references.aliases, unqualified_columns):
                if re.search(
                    rf"{_column_pattern(variant).pattern}\s*(?:=|<>|!=|LIKE|IN\s*\()\s*:[A-Z][A-Z0-9_]*",
                    masked_sql,
                    re.IGNORECASE,
                ):
                    bound = True
                    break
            if bound:
                break
        if not bound:
            raise GroundedSqlSemanticError(f"Entity filter must use a named bind placeholder: {concept}.")
        for value in values:
            if isinstance(value, str) and value and re.search(
                rf"'\s*{re.escape(value)}\s*'", sql, re.IGNORECASE
            ):
                raise GroundedSqlSemanticError(f"Raw entity value is embedded in SQL: {concept}.")


def validate_grounded_sql(sql: str, query_plan: QueryPlan, grounded_schema_plan: GroundedSchemaPlan) -> None:
    """Reject SQL that exceeds grounding or misses a required logical instruction."""
    if not grounded_schema_plan.is_grounded:
        raise GroundedSqlGroundingError("GroundedSchemaPlan is not safe for SQL generation.")
    masked_sql = _basic_safety(sql)
    references = _extract_references(masked_sql)
    _validate_tables_and_columns(masked_sql, grounded_schema_plan, references)
    _validate_joins(masked_sql, grounded_schema_plan, references)
    _validate_measures_and_grouping(masked_sql, query_plan, grounded_schema_plan, references)
    _validate_sort_limit_and_date(masked_sql, query_plan, grounded_schema_plan, references)
    _validate_entity_binds(sql, masked_sql, query_plan, grounded_schema_plan, references)


def collect_grounded_sql_violations(
    sql: str,
    query_plan: QueryPlan,
    grounded_schema_plan: GroundedSchemaPlan,
) -> list[str]:
    """Collect independent correction feedback without changing or repairing SQL."""
    if not grounded_schema_plan.is_grounded:
        return ["GroundedSchemaPlan is not safe for SQL generation."]

    violations: list[str] = []
    try:
        masked_sql = _basic_safety(sql)
    except OracleDialectGroundedSqlError as exc:
        violations.append(str(exc))
        masked_sql = _mask_string_literals(sql.strip())
    except GroundedSqlValidationError as exc:
        return [str(exc)]

    try:
        references = _extract_references(masked_sql)
    except GroundedSqlValidationError as exc:
        violations.append(str(exc))
        return list(dict.fromkeys(violations))

    checks = (
        lambda: _validate_tables_and_columns(masked_sql, grounded_schema_plan, references),
        lambda: _validate_joins(masked_sql, grounded_schema_plan, references),
        lambda: _validate_measures_and_grouping(
            masked_sql, query_plan, grounded_schema_plan, references
        ),
        lambda: _validate_sort_limit_and_date(
            masked_sql, query_plan, grounded_schema_plan, references
        ),
        lambda: _validate_entity_binds(
            sql, masked_sql, query_plan, grounded_schema_plan, references
        ),
    )
    for check in checks:
        try:
            check()
        except GroundedSqlValidationError as exc:
            violations.extend(part.strip() for part in str(exc).split(";") if part.strip())
    return list(dict.fromkeys(violations))
