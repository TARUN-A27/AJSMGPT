"""Conservative, offline validation for grounded Oracle SQL previews.

This module intentionally validates the small SQL surface generated for the V1
preview endpoint.  It is not a general SQL parser: generated SQL must use fully
qualified physical tables and qualified column references so each identifier can
be checked against a :class:`GroundedSchemaPlan` without Oracle access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.query_plan import Aggregation, DateRangeKind, EntityStatus, FilterOperator, QueryPlan
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
_ALLOWED_FUNCTIONS = {
    "AVG", "COUNT", "MAX", "MIN", "ROUND", "SUM",
}
# Never added to _ALLOWED_FUNCTIONS: date boundaries are QueryPlan-derived and
# must always reach SQL as named binds, never as a literal-conversion call.
# Named here only so the rejection message can point the model at the
# correct fix instead of a bare "unsupported function" list.
_DATE_LITERAL_FUNCTIONS = {"TO_DATE", "TO_TIMESTAMP", "DATE"}


@dataclass(frozen=True)
class _SqlReferences:
    tables: set[str]
    aliases: dict[str, str]
    ctes: set[str]


def _normalise_concept(value: str) -> str:
    return " ".join(value.lower().replace("_", " ").replace("-", " ").split())


def _mask_string_literals(sql: str) -> str:
    """Preserve positions while hiding quoted content from structural regexes."""
    return re.sub(r"'(?:''|[^'])*'", lambda match: " " * len(match.group(0)), sql)


def _column_pattern(reference: str) -> re.Pattern[str]:
    # (?<!:) excludes a bind PARAMETER whose name happens to contain the
    # column name, e.g. `:ITEM_NAME` -- the leading colon means this is a
    # placeholder identifier, not a reference to the ITEM_NAME column. Same
    # rationale as the `:limit` exclusion in _basic_safety.
    parts = [re.escape(part) for part in reference.split(".")]
    return re.compile(r"(?<!:)\b" + r"\s*\.\s*".join(parts) + r"\b", re.IGNORECASE)


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
    if "@" in sql:
        raise UnsafeGroundedSqlError("Database links are not allowed.")

    masked = _mask_string_literals(sql.strip())
    if not re.match(r"^SELECT\b", masked, re.IGNORECASE):
        raise UnsafeGroundedSqlError("V1 execution requires one top-level SELECT without CTEs.")
    if len(re.findall(r"\bSELECT\b", masked, re.IGNORECASE)) != 1:
        raise UnsafeGroundedSqlError("V1 execution allows exactly one SELECT keyword.")
    for keyword in _BLOCKED_KEYWORDS:
        if re.search(rf"\b{keyword}\b", masked, re.IGNORECASE):
            raise UnsafeGroundedSqlError(f"Blocked SQL keyword: {keyword}.")
    # (?<!:) excludes a bind PARAMETER named e.g. `:limit` -- the colon means
    # this is a placeholder, not the unsupported MySQL/Postgres LIMIT clause.
    if re.search(r"(?<!:)\bLIMIT\b", masked, re.IGNORECASE):
        raise OracleDialectGroundedSqlError(
            "Oracle SQL does not support LIMIT; do not write a row limit, the system applies it."
        )
    if re.search(r"\bFOR\s+UPDATE\b", masked, re.IGNORECASE):
        raise UnsafeGroundedSqlError("SELECT FOR UPDATE is not allowed.")
    if re.search(r"\b(?:UNION|MINUS|INTERSECT)\b", masked, re.IGNORECASE):
        raise UnsafeGroundedSqlError("Set operations are outside the V1 SQL subset.")
    if re.search(r"\bSELECT\s+(?:DISTINCT\s+)?(?:[A-Z][A-Z0-9_$#]*\s*\.\s*)?\*", masked, re.IGNORECASE):
        raise UnsafeGroundedSqlError("SELECT * is not allowed.")
    if re.search(rf"\b{_IDENTIFIER}\s*\.\s*\*", masked, re.IGNORECASE):
        raise UnsafeGroundedSqlError("Wildcard column selection is not allowed.")
    function_names = {
        match.group(1).upper()
        for match in re.finditer(rf"\b({_IDENTIFIER})\s*\(", masked, re.IGNORECASE)
        if match.group(1).upper() not in _SQL_KEYWORDS
    }
    unsupported_functions = sorted(function_names - _ALLOWED_FUNCTIONS)
    if unsupported_functions:
        message = "Unsupported SQL function(s): " + ", ".join(unsupported_functions) + "."
        if _DATE_LITERAL_FUNCTIONS & set(unsupported_functions):
            message += (
                " Date boundaries must use named bind parameters on the grounded date "
                "column (e.g. date_column BETWEEN :date_start AND :date_end), never a "
                "date-literal function or string."
            )
        raise UnsafeGroundedSqlError(message)
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


def _split_sql_list(fragment: str) -> list[str]:
    items: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(fragment + ","):
        depth += (char == "(") - (char == ")")
        if char == "," and depth == 0:
            item = fragment[start:index].strip()
            if item:
                items.append(item)
            start = index + 1
    return items


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
    violations.extend(_aggregate_grouping_violations(select_part, group_part))
    if violations:
        raise GroundedSqlSemanticError("; ".join(violations))


# Matches an aggregate call ANYWHERE in a select item, not only as its
# outermost call: ROUND(SUM(po.NET), 2) aggregates just as SUM(po.NET) does,
# and anchoring here would switch the whole ORA-00937 rule off for it.
_AGGREGATE_CALL_RE = re.compile(r"\b(?:SUM|COUNT|AVG|MIN|MAX)\s*\(", re.IGNORECASE)
# The trailing token is an alias only when it does not belong to a qualified
# reference: "PM . PARTYNAME" (the spaced form _column_pattern accepts) ends in
# a column name, not an alias.
_TRAILING_ALIAS_RE = re.compile(r"(?<!\.)\s+(?:AS\s+)?[A-Z_][A-Z0-9_$#]*\s*$", re.IGNORECASE)


def _squash(fragment: str) -> str:
    return re.sub(r"\s+", "", fragment).upper()


def _aggregate_grouping_violations(select_part: str, group_part: str) -> list[str]:
    """Oracle rule (ORA-00937): once the SELECT list aggregates, every other
    selected expression must be in GROUP BY. Checked on the SQL text itself,
    independent of the plan, so a plan/prompt mistake cannot smuggle an
    unexecutable statement past validation.

    The reverse direction is checked too, and is not an Oracle rule but a
    correctness one: GROUP BY a column the SELECT list does not return is valid
    SQL that silently changes the grain of the answer (one row per supplier per
    ORDER DATE presented as one row per supplier), so it fails closed here."""
    items = _split_sql_list(select_part)
    if not any(_AGGREGATE_CALL_RE.search(item) for item in items):
        return []
    grouped = {_squash(item): item.strip() for item in _split_sql_list(group_part)}
    violations = []
    selected_plain: set[str] = set()
    for item in items:
        if _AGGREGATE_CALL_RE.search(item):
            continue
        expression = _TRAILING_ALIAS_RE.sub("", item).strip()
        selected_plain.add(_squash(expression))
        if _squash(expression) not in grouped:
            violations.append(f"Aggregate SELECT requires every non-aggregated column in GROUP BY: {expression}.")
    for key, original in grouped.items():
        if key not in selected_plain:
            violations.append(
                f"GROUP BY must not add a column the SELECT list does not return, "
                f"it changes the grain of the answer: {original}."
            )
    return violations


def _validate_sort_limit_and_date(
    masked_sql: str,
    query_plan: QueryPlan,
    grounding: GroundedSchemaPlan,
    references: _SqlReferences,
) -> None:
    unqualified_columns = _unqualified_column_map(grounding, references)
    order_part = _clause(masked_sql, r"ORDER\s+BY", r"\bFETCH\b|\bOFFSET\b")
    order_terms = _split_sql_list(order_part)
    select_part = _select_fragment(masked_sql)
    violations: list[str] = []
    for sort_index, instruction in enumerate(sorted(query_plan.sorting, key=lambda item: item.priority)):
        direction = instruction.direction.value.upper()
        if sort_index >= len(order_terms):
            violations.append(f"Required sort field is missing from ORDER BY: {instruction.field_concept}.")
            continue
        order_term = order_terms[sort_index]
        if not re.search(rf"\b{direction}\b", order_term, re.IGNORECASE):
            violations.append(f"Required sort direction is missing: {direction}.")
            continue
        concept = _normalise_concept(instruction.field_concept)
        measure = next(
            (
                item
                for item in query_plan.measures
                if concept in {_normalise_concept(item.concept), _normalise_concept(item.alias or "")}
            ),
            None,
        )
        dimension = next(
            (item for item in query_plan.dimensions if concept == _normalise_concept(item.concept)),
            None,
        )
        sort_matches = False
        if measure is not None:
            aggregate_names = {
                Aggregation.SUM: "SUM",
                Aggregation.COUNT: "COUNT",
                Aggregation.COUNT_DISTINCT: "COUNT",
                Aggregation.AVERAGE: "AVG",
                Aggregation.MINIMUM: "MIN",
                Aggregation.MAXIMUM: "MAX",
            }
            columns = _columns_for_requirement(grounding, measure.concept, "measure")
            aliases: list[str] = []
            for column in columns:
                for variant in _reference_variants(column, references.aliases, unqualified_columns):
                    function = aggregate_names.get(measure.aggregation)
                    expression = (
                        rf"{function}\s*\(\s*(?:DISTINCT\s+)?{_column_pattern(variant).pattern}\s*\)"
                        if function
                        else _column_pattern(variant).pattern
                    )
                    alias_match = re.search(
                        rf"{expression}\s+(?:AS\s+)?({_IDENTIFIER})\b",
                        select_part,
                        re.IGNORECASE,
                    )
                    if alias_match:
                        aliases.append(alias_match.group(1))
                    if re.search(expression, order_term, re.IGNORECASE):
                        sort_matches = True
            if measure.alias:
                aliases.append(measure.alias)
            sort_matches = sort_matches or any(
                re.search(rf"\b{re.escape(alias)}\b", order_term, re.IGNORECASE)
                for alias in aliases
            )
        elif dimension is not None:
            columns = _columns_for_requirement(grounding, dimension.concept, "grouping")
            sort_matches = any(
                _contains_column(order_term, column, references.aliases, unqualified_columns)
                for column in columns
            )
        if not sort_matches:
            violations.append(
                f"Required sort field is missing from ORDER BY: {instruction.field_concept}."
            )

    # The row limit is never the model's to write. Oracle 11.2 (the company
    # database) has no FETCH FIRST, and ROWNUM only limits correctly around an
    # ordered subquery, which the one-SELECT rule forbids here. After this
    # validation passes, deterministic code wraps the statement
    # (sql_safety.add_oracle_row_limit) using the plan's limit or the service
    # maximum -- see nlp_execution.add_execution_probe_limit.
    if re.search(r"\bFETCH\b|\bOFFSET\b|\bROWNUM\b", masked_sql, re.IGNORECASE):
        violations.append(
            "Do not write a row limit (FETCH FIRST / OFFSET / ROWNUM); the system applies the requested limit."
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
        # Every date range -- relative or absolute -- is two half-open binds
        # computed by nlp_execution.date_bounds; the model writes no date
        # arithmetic. The company database stores these columns as
        # VARCHAR2(8) 'YYYYMMDD', so SYSDATE math would not even be valid.
        if re.search(r"\b(?:SYSDATE|CURRENT_DATE|CURRENT_TIMESTAMP|SYSTIMESTAMP)\b", masked_sql, re.IGNORECASE):
            violations.append("Date filters never use SYSDATE arithmetic; use the two named date binds.")
        bounded = False
        for date_column in date_columns:
            for variant in _reference_variants(date_column, references.aliases, unqualified_columns):
                column_ref = _column_pattern(variant).pattern
                if re.search(rf"{column_ref}\s+BETWEEN\b", where_part, re.IGNORECASE):
                    violations.append("Date filters use >= :date_start AND < :date_end, never BETWEEN.")
                lower = re.search(rf"{column_ref}\s*>=\s*:({_IDENTIFIER})", where_part, re.IGNORECASE)
                upper = re.search(rf"{column_ref}\s*<(?!\s*=)\s*:({_IDENTIFIER})", where_part, re.IGNORECASE)
                if lower and upper and lower.group(1).upper() != upper.group(1).upper():
                    bounded = True
        if date_columns and not bounded:
            violations.append(
                "Date filter requires exactly date_column >= :date_start AND date_column < :date_end "
                "with two distinct named binds."
            )
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


def _predicate_matches(
    where_part: str,
    grounding: GroundedSchemaPlan,
    references: _SqlReferences,
    concept: str,
    operator_pattern: str,
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    candidates = grounding.entity_column_candidates.get(concept) or _columns_for_requirement(
        grounding, concept, "entity_filter"
    )
    all_occurrences: set[tuple[int, int]] = set()
    approved: set[tuple[int, int]] = set()
    unqualified_columns = _unqualified_column_map(grounding, references)
    for candidate in candidates:
        for variant in _reference_variants(candidate.upper(), references.aliases, unqualified_columns):
            column_pattern = _column_pattern(variant).pattern
            all_occurrences.update(
                match.span()
                for match in re.finditer(column_pattern, where_part, re.IGNORECASE)
            )
            approved.update(
                match.span()
                for match in re.finditer(
                    rf"{column_pattern}{operator_pattern}",
                    where_part,
                    re.IGNORECASE,
                )
            )
    def outermost(spans: set[tuple[int, int]]) -> set[tuple[int, int]]:
        return {
            span
            for span in spans
            if not any(
                other != span and other[0] <= span[0] and span[1] <= other[1]
                for other in spans
            )
        }

    return outermost(all_occurrences), outermost(approved)


def _is_compound_condition_concept(grounding: GroundedSchemaPlan, concept: str) -> bool:
    """True when a QueryPlan concept phrase resolved (during grounding) to a
    bounded compound condition rather than a single bindable column.

    Uses `entity_column_candidates`, which grounding already populates with
    the literal plan phrase -> resolved column set for both ordinary and
    compound concepts alike -- so this works regardless of which catalog
    alias the plan happened to use, without needing to re-derive the
    canonical concept name here.
    """
    candidates = grounding.entity_column_candidates.get(concept)
    if not candidates:
        return False
    candidate_set = set(candidates)
    return any(set(condition.columns) == candidate_set for condition in grounding.compound_conditions)


def _validate_filter_operators(
    masked_sql: str,
    query_plan: QueryPlan,
    grounding: GroundedSchemaPlan,
    references: _SqlReferences,
) -> None:
    where_part = _clause(
        masked_sql,
        "WHERE",
        r"\bGROUP\s+BY\b|\bORDER\s+BY\b|\bFETCH\b|\bOFFSET\b",
    )
    bind = rf":{_IDENTIFIER}"
    for entity in query_plan.entities:
        if entity.status is EntityStatus.NOT_REQUIRED:
            continue
        if _is_compound_condition_concept(grounding, entity.concept):
            # A compound condition is verified in full by
            # _validate_compound_conditions; it is never a single bindable
            # column, so the ordinary equality-bind shape does not apply.
            continue
        occurrences, approved = _predicate_matches(
            where_part,
            grounding,
            references,
            entity.concept,
            rf"\s*=\s*{bind}",
        )
        if len(occurrences) != 1 or len(approved) != 1:
            raise GroundedSqlSemanticError(
                f"Resolved entity filter must use exactly one equality bind: {entity.concept}."
            )

    operator_patterns = {
        FilterOperator.EQUALS: rf"\s*=\s*{bind}",
        FilterOperator.NOT_EQUALS: rf"\s*(?:<>|!=)\s*{bind}",
        FilterOperator.CONTAINS: rf"\s+LIKE\s*{bind}",
        FilterOperator.STARTS_WITH: rf"\s+LIKE\s*{bind}",
        FilterOperator.IN: rf"\s+IN\s*\(\s*{bind}(?:\s*,\s*{bind})*\s*\)",
        FilterOperator.GREATER_THAN: rf"\s*>\s*{bind}",
        FilterOperator.GREATER_THAN_OR_EQUAL: rf"\s*>=\s*{bind}",
        FilterOperator.LESS_THAN: rf"\s*<\s*{bind}",
        FilterOperator.LESS_THAN_OR_EQUAL: rf"\s*<=\s*{bind}",
        FilterOperator.BETWEEN: rf"\s+BETWEEN\s*{bind}\s+AND\s+{bind}",
        FilterOperator.IS_NULL: r"\s+IS\s+NULL\b",
        FilterOperator.IS_NOT_NULL: r"\s+IS\s+NOT\s+NULL\b",
    }
    for item in query_plan.filters:
        if query_plan.date_range and _normalise_concept(item.concept) in {"date", "time", "period"}:
            continue
        if _is_compound_condition_concept(grounding, item.concept):
            continue
        occurrences, approved = _predicate_matches(
            where_part,
            grounding,
            references,
            item.concept,
            operator_patterns[item.operator],
        )
        if len(occurrences) != 1 or len(approved) != 1:
            raise GroundedSqlSemanticError(
                f"Filter operator does not match the QueryPlan contract: {item.concept}."
            )


def _validate_entity_binds(
    sql: str,
    masked_sql: str,
    query_plan: QueryPlan,
    grounding: GroundedSchemaPlan,
    references: _SqlReferences,
) -> None:
    if re.search(r"'(?:''|[^'])*'", sql):
        raise GroundedSqlSemanticError(
            "Filter predicates must use named binds rather than quoted string literals."
        )
    _validate_filter_operators(masked_sql, query_plan, grounding, references)
    requirements = _filter_requirements(query_plan)
    filter_concepts = [
        entity.concept
        for entity in query_plan.entities
        if entity.status is not EntityStatus.NOT_REQUIRED
    ]
    filter_concepts.extend(
        item.concept
        for item in query_plan.filters
        if not (
            query_plan.date_range
            and _normalise_concept(item.concept) in {"date", "time", "period"}
        )
    )
    has_date_filter = bool(
        query_plan.date_range and query_plan.date_range.kind is not DateRangeKind.UNSPECIFIED
    )
    where_part = _clause(
        masked_sql,
        "WHERE",
        r"\bGROUP\s+BY\b|\bORDER\s+BY\b|\bFETCH\b|\bOFFSET\b",
    )
    if where_part and not filter_concepts and not has_date_filter:
        raise GroundedSqlSemanticError("Generated SQL contains an unrequested WHERE filter.")

    allowed_filter_columns = {
        candidate.upper()
        for concept in filter_concepts
        for candidate in (
            grounding.entity_column_candidates.get(concept)
            or _columns_for_concept(grounding, concept)
        )
    }
    if has_date_filter:
        allowed_filter_columns.update(
            f"{column.full_table_name}.{column.column_name}".upper()
            for column in grounding.selected_columns
            if column.role == "date_filter"
        )
    for column in grounding.selected_columns:
        full_column = f"{column.full_table_name}.{column.column_name}".upper()
        if full_column in allowed_filter_columns:
            continue
        if any(
            _column_pattern(variant).search(where_part)
            for variant in _reference_variants(full_column, references.aliases)
        ):
            raise GroundedSqlSemanticError(
                f"Generated SQL filters an unrequested column: {full_column}."
            )

    numeric_scan = where_part
    unqualified_for_scan = _unqualified_column_map(grounding, references)
    for condition in grounding.compound_conditions:
        # A compound condition's comparison values are fixed, catalog-declared
        # constants (e.g. flag = 1), not user-supplied values -- so unlike an
        # arbitrary literal, these do not need a bind. Only the exact
        # required columns' own comparisons are exempted here.
        for full_column in condition.columns:
            for variant in _reference_variants(full_column, references.aliases, unqualified_for_scan):
                pattern = _column_pattern(variant).pattern
                numeric_scan = re.sub(rf"{pattern}\s*=\s*\d+", "", numeric_scan, flags=re.IGNORECASE)
    if re.search(r"(?<![:A-Z0-9_$#])\d+(?:\.\d+)?(?![A-Z0-9_$#])", numeric_scan, re.IGNORECASE):
        raise GroundedSqlSemanticError("Filter literals must be represented by validated named binds.")

    if not requirements:
        return
    unqualified_columns = _unqualified_column_map(grounding, references)
    for concept, values in requirements:
        if _is_compound_condition_concept(grounding, concept):
            # Verified in full by _validate_compound_conditions; a compound
            # condition's fixed constants are not user-supplied values, so
            # there is nothing here for a bind or embedded-literal check to
            # apply to.
            continue
        candidates = grounding.entity_column_candidates.get(concept) or _columns_for_requirement(
            grounding, concept, "entity_filter"
        )
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


def _validate_compound_conditions(
    masked_sql: str,
    grounded_schema_plan: GroundedSchemaPlan,
    references: _SqlReferences,
) -> None:
    """Bounded check: every grounded compound condition's exact columns must
    appear in the SQL joined by exactly its catalog-declared combinator.

    This never inspects a free-form expression string -- there isn't one.
    The columns and the combinator both come from the verified catalog
    (schema_grounding.py), so this only has to confirm the model reproduced
    that fixed structure, not that it invented something plausible-looking.
    Deliberately regex-based (not a full boolean-expression parser), matching
    the rest of this module's style; it assumes the two columns of a given
    condition are not independently reused elsewhere in the same WHERE
    clause, which holds for the bounded MRS status conditions this supports.
    """
    if not grounded_schema_plan.compound_conditions:
        return
    unqualified_columns = _unqualified_column_map(grounded_schema_plan, references)
    where_part = _clause(masked_sql, "WHERE", r"\bGROUP\s+BY\b|\bORDER\s+BY\b|\bFETCH\b|\bOFFSET\b")

    for condition in grounded_schema_plan.compound_conditions:
        positions: list[tuple[int, int]] = []
        for full_column in condition.columns:
            match = None
            for variant in _reference_variants(full_column, references.aliases, unqualified_columns):
                match = _column_pattern(variant).search(where_part)
                if match:
                    break
            if not match:
                raise GroundedSqlSemanticError(
                    f"Compound condition '{condition.logical_concept}' is missing required column {full_column}."
                )
            positions.append((match.start(), match.end()))

        positions.sort()
        between_text = where_part[positions[0][1]:positions[-1][0]]
        required = condition.combinator.upper()
        other = "AND" if required == "OR" else "OR"
        if not re.search(rf"\b{required}\b", between_text, re.IGNORECASE):
            raise GroundedSqlSemanticError(
                f"Compound condition '{condition.logical_concept}' requires its columns to be "
                f"combined with {required}."
            )
        if re.search(rf"\b{other}\b", between_text, re.IGNORECASE):
            raise GroundedSqlSemanticError(
                f"Compound condition '{condition.logical_concept}' must not mix {other} into "
                f"its required {required} combination."
            )


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
    _validate_compound_conditions(masked_sql, grounded_schema_plan, references)


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
        lambda: _validate_compound_conditions(masked_sql, grounded_schema_plan, references),
    )
    for check in checks:
        try:
            check()
        except GroundedSqlValidationError as exc:
            violations.extend(part.strip() for part in str(exc).split(";") if part.strip())
    return list(dict.fromkeys(violations))
