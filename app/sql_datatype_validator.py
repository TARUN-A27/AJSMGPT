import json
import re
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = PROJECT_ROOT / "app" / "resources" / "business_schema_catalog.json"

# Offline datatype categories. Only the categories required by the V1 path.
NUMERIC = "NUMERIC"
TEXT = "TEXT"
DATE_CAT = "DATE"
# A date stored as VARCHAR2(8) 'YYYYMMDD' text (how the company ERP stores
# ORDERDATE, MRSDATE, ISSUEDATE, ...). Only an 8-digit string may be bound to
# it: a Python date would be sent as a DATE and fail (ORA-01861) or compare wrongly.
DATE_TEXT_CAT = "DATE_TEXT"
TIMESTAMP_CAT = "TIMESTAMP"
_VALID_CATEGORIES = {TEXT, NUMERIC, DATE_CAT, DATE_TEXT_CAT, TIMESTAMP_CAT}

# Verified-catalog role -> datatype category, used only as a fallback when a
# column has no explicit `datatype_category`. Order matters (first match wins).
# These two roles are the only ones the catalog uses to imply a datatype;
# every other role (identifier/display/grouping/entity_filter/status/...)
# says nothing about datatype and must not be defaulted to TEXT.
_ROLE_CATEGORY_PRIORITY = (
    ("date_filter", DATE_CAT),
    ("measure", NUMERIC),
)

_DATE_LITERAL_RE = re.compile(r"^\d{8}$|^\d{4}-\d{2}-\d{2}$")


def _normalize(value: str) -> str:
    return value.strip().strip('"').upper()


@lru_cache(maxsize=1)
def _load_offline_column_categories() -> dict[tuple[str, str, str], str]:
    """
    Builds an offline column -> datatype-category map strictly from the
    verified business schema catalog. Never queries Oracle and never infers
    a type from a column's name.

    A column's category comes only from:
      1. an explicit `datatype_category` field on the column entry, or
      2. the `date_filter`/`measure` role, which the catalog already uses
         to imply DATE/NUMERIC.
    Any other column is left unclassified (no implicit TEXT default), so
    validation for it fails closed.
    """
    catalog = json.loads(CATALOG_PATH.read_text())
    categories: dict[tuple[str, str, str], str] = {}

    def _record(col: dict, roles: set[str]) -> None:
        owner, table = col["table"].split(".", 1)
        key = (_normalize(owner), _normalize(table), _normalize(col["column"]))

        explicit = col.get("datatype_category")
        if explicit is not None:
            if explicit not in _VALID_CATEGORIES:
                raise ValueError(
                    f"Unknown datatype_category {explicit!r} for {key}; "
                    f"expected one of {sorted(_VALID_CATEGORIES)}."
                )
            category = explicit
        else:
            category = None
            for role, mapped_category in _ROLE_CATEGORY_PRIORITY:
                if role in roles:
                    category = mapped_category
                    break

        if category is None:
            return

        existing = categories.get(key)
        if existing is not None and existing != category:
            categories[key] = "AMBIGUOUS"
        else:
            categories[key] = category

    for concept in catalog.get("concepts", []):
        for col in concept.get("columns", []):
            _record(col, set(col.get("roles", [])))
        compound = concept.get("compound_condition")
        if compound:
            # Compound-condition columns have no per-column roles; a category
            # must be given explicitly, same as every other explicit case.
            for col in compound.get("columns", []):
                _record(col, roles=set())

    return categories


def _column_datatype_category(owner: str, table: str, column: str) -> str | None:
    """Offline lookup only. Returns None when metadata is unavailable."""
    categories = _load_offline_column_categories()
    key = (_normalize(owner), _normalize(table), _normalize(column))
    category = categories.get(key)
    if category == "AMBIGUOUS":
        return None
    return category


def _extract_table_aliases(sql: str) -> dict:
    aliases = {}
    pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+([A-Z][A-Z0-9_]*)\.([A-Z][A-Z0-9_]*)(?:\s+(?:AS\s+)?([A-Z][A-Z0-9_]*))?",
        re.IGNORECASE,
    )
    for match in pattern.finditer(sql):
        owner = _normalize(match.group(1))
        table = _normalize(match.group(2))
        alias = match.group(3)

        full_name = f"{owner}.{table}"
        aliases[full_name] = (owner, table)
        aliases[table] = (owner, table)

        if alias:
            alias = _normalize(alias)
            if alias not in {"WHERE", "INNER", "LEFT", "RIGHT", "FULL", "JOIN", "ON", "ORDER", "GROUP"}:
                aliases[alias] = (owner, table)

    return aliases


def _is_quoted_text(value: str) -> bool:
    value = value.strip()
    return len(value) >= 2 and value[0] == "'" and value[-1] == "'"


def _is_numeric_literal(value: str) -> bool:
    value = value.strip().strip("'")
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", value))


def _is_date_literal(value: str) -> bool:
    value = value.strip().strip("'")
    return bool(_DATE_LITERAL_RE.match(value))


def validate_sql_datatypes(sql: str, binds: Mapping[str, Any] | None = None) -> None:
    """
    Blocks incompatible literal comparisons using offline, source-controlled
    datatype categories. Unknown columns are skipped here (best-effort, for
    legacy/backward compatibility) rather than failing closed.
    """
    alias_map = _extract_table_aliases(sql)

    comparison_pattern = re.compile(
        r"\b([A-Z][A-Z0-9_]*(?:\.[A-Z][A-Z0-9_]*)?)\.([A-Z][A-Z0-9_]*)\s*(=|<>|!=|>=|<=|>|<)\s*"
        r"('[^']*'|-?\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )

    for match in comparison_pattern.finditer(sql):
        alias = _normalize(match.group(1))
        column = _normalize(match.group(2))
        value = match.group(4).strip()

        table_info = alias_map.get(alias)
        if not table_info:
            continue

        owner, table = table_info
        category = _column_datatype_category(owner, table, column)
        if not category:
            continue

        if category == NUMERIC and _is_quoted_text(value):
            inner_value = value.strip("'").strip()
            if inner_value and not _is_numeric_literal(inner_value):
                raise ValueError(
                    f"Invalid datatype comparison: {owner}.{table}.{column} is {category} "
                    f"but compared with text {value}."
                )
        elif category in (DATE_CAT, DATE_TEXT_CAT):
            if not _is_quoted_text(value) or not _is_date_literal(value):
                raise ValueError(
                    f"Invalid datatype comparison: {owner}.{table}.{column} is {category} "
                    f"but compared with a non-date value {value}."
                )

    like_pattern = re.compile(
        r"\b([A-Z][A-Z0-9_]*(?:\.[A-Z][A-Z0-9_]*)?)\.([A-Z][A-Z0-9_]*)\s+LIKE\s+'[^']*'",
        re.IGNORECASE,
    )
    for match in like_pattern.finditer(sql):
        alias = _normalize(match.group(1))
        column = _normalize(match.group(2))

        table_info = alias_map.get(alias)
        if not table_info:
            continue

        owner, table = table_info
        category = _column_datatype_category(owner, table, column)
        if not category:
            continue

        if category in (NUMERIC, DATE_CAT, TIMESTAMP_CAT):
            raise ValueError(
                f"Invalid LIKE comparison: {owner}.{table}.{column} is {category}. "
                f"LIKE can be used only on text columns."
            )

    if binds:
        _validate_bind_datatypes(sql, alias_map, binds)


def _bind_names_for_column(sql: str, alias: str, column: str) -> list[str]:
    pattern = re.escape(f"{alias}.{column}").replace(r"\.", r"\s*\.\s*")
    names: list[str] = []

    between = re.search(
        rf"\b{pattern}\b\s+BETWEEN\s+:([A-Z][A-Z0-9_]*)\s+AND\s+:([A-Z][A-Z0-9_]*)",
        sql,
        re.IGNORECASE,
    )
    if between:
        names.extend([between.group(1), between.group(2)])

    for op_match in re.finditer(
        rf"\b{pattern}\b\s*(?:=|<>|!=|>=|<=|>|<|LIKE)\s*:([A-Z][A-Z0-9_]*)",
        sql,
        re.IGNORECASE,
    ):
        names.append(op_match.group(1))

    in_match = re.search(rf"\b{pattern}\b\s+IN\s*\(([^)]*)\)", sql, re.IGNORECASE)
    if in_match:
        names.extend(re.findall(r":([A-Z][A-Z0-9_]*)", in_match.group(1), re.IGNORECASE))

    return [name.lower() for name in names]


def _value_matches_category(category: str, value: Any) -> bool:
    if value is None:
        return True
    if category == NUMERIC:
        return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)
    if category == DATE_CAT:
        if isinstance(value, (date, datetime)):
            return True
        if isinstance(value, str):
            return _is_date_literal(value)
        return False
    if category == DATE_TEXT_CAT:
        return isinstance(value, str) and re.fullmatch(r"\d{8}", value) is not None
    if category == TIMESTAMP_CAT:
        return isinstance(value, datetime)
    if category == TEXT:
        return isinstance(value, str)
    return False


def _validate_bind_datatypes(sql: str, alias_map: dict, binds: Mapping[str, Any]) -> None:
    """
    Bind-parameter datatype validation for the V1 execution path.

    Required datatype metadata that is unavailable in the verified catalog
    causes a fail-closed error, since bind-parameterized SQL carries no
    literal value that a best-effort skip could safely fall back on.
    """
    seen: set[tuple[str, str]] = set()
    column_pattern = re.compile(r"\b([A-Z][A-Z0-9_]*)\.([A-Z][A-Z0-9_]*)\b", re.IGNORECASE)
    for match in column_pattern.finditer(sql):
        alias = _normalize(match.group(1))
        column = _normalize(match.group(2))
        if (alias, column) in seen:
            continue
        seen.add((alias, column))

        table_info = alias_map.get(alias)
        if not table_info:
            continue

        bind_names = _bind_names_for_column(sql, alias, column)
        if not bind_names:
            continue

        owner, table = table_info
        category = _column_datatype_category(owner, table, column)
        if not category:
            raise ValueError(
                f"Datatype metadata is unavailable for {owner}.{table}.{column}; "
                f"refusing to execute (fail closed)."
            )

        for bind_name in bind_names:
            if bind_name not in binds:
                continue
            value = binds[bind_name]
            if not _value_matches_category(category, value):
                raise ValueError(
                    f"Invalid bind datatype: {owner}.{table}.{column} is {category} "
                    f"but bind ':{bind_name}' has value {value!r} of type {type(value).__name__}."
                )
