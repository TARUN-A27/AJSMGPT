import os
import re
from functools import lru_cache
from pathlib import Path

import oracledb
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


NUMBER_TYPES = {
    "NUMBER",
    "FLOAT",
    "BINARY_FLOAT",
    "BINARY_DOUBLE",
    "INTEGER",
    "INT",
    "SMALLINT",
    "DECIMAL",
    "NUMERIC",
}

TEXT_TYPES = {
    "VARCHAR2",
    "VARCHAR",
    "CHAR",
    "NCHAR",
    "NVARCHAR2",
    "CLOB",
    "NCLOB",
}


def _init_oracle_client():
    lib_dir = os.getenv("ORACLE_CLIENT_LIB_DIR")

    if lib_dir:
        try:
            oracledb.init_oracle_client(lib_dir=lib_dir)
        except Exception:
            pass


def _get_connection():
    _init_oracle_client()

    user = os.getenv("ORACLE_USER")
    password = os.getenv("ORACLE_PASSWORD")
    dsn = os.getenv("ORACLE_DSN")

    missing = []
    if not user:
        missing.append("ORACLE_USER")
    if not password:
        missing.append("ORACLE_PASSWORD")
    if not dsn:
        missing.append("ORACLE_DSN")

    if missing:
        raise RuntimeError("Missing Oracle env values: " + ", ".join(missing))

    return oracledb.connect(
        user=user,
        password=password,
        dsn=dsn,
    )

def _normalize(value: str) -> str:
    return value.strip().strip('"').upper()


def _is_quoted_text(value: str) -> bool:
    value = value.strip()
    return len(value) >= 2 and value[0] == "'" and value[-1] == "'"


def _is_numeric_literal(value: str) -> bool:
    value = value.strip().strip("'")
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", value))


def _extract_table_aliases(sql: str) -> dict:
    """
    Returns:
    {
        "PO": ("INVENTORY", "PURCHASEORDER"),
        "INVENTORY.PURCHASEORDER": ("INVENTORY", "PURCHASEORDER")
    }
    """

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

            # Avoid treating SQL keywords as aliases
            if alias not in {
                "WHERE",
                "INNER",
                "LEFT",
                "RIGHT",
                "FULL",
                "JOIN",
                "ON",
                "ORDER",
                "GROUP",
            }:
                aliases[alias] = (owner, table)

    return aliases


@lru_cache(maxsize=2048)
def _column_datatype(owner: str, table: str, column: str) -> str | None:
    owner = _normalize(owner)
    table = _normalize(table)
    column = _normalize(column)

    sql = """
        SELECT DATA_TYPE
        FROM ALL_TAB_COLUMNS
        WHERE OWNER = :owner_name
          AND TABLE_NAME = :table_name
          AND COLUMN_NAME = :column_name
    """

    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                {
                    "owner_name": owner,
                    "table_name": table,
                    "column_name": column,
                },
            )
            row = cur.fetchone()

    if not row:
        return None

    return str(row[0]).upper()


def validate_sql_datatypes(sql: str) -> None:
    """
    Blocks obvious unsafe datatype comparisons before Oracle execution.

    Examples blocked:
        po.STATUS = 'Pending'
        po.APP_STATUS LIKE '%PENDING%'

    These cause ORA-01722 when NUMBER columns are compared with text.
    """

    alias_map = _extract_table_aliases(sql)

    comparison_pattern = re.compile(
        r"\b([A-Z][A-Z0-9_]*(?:\.[A-Z][A-Z0-9_]*)?)\.([A-Z][A-Z0-9_]*)\s*(=|<>|!=|>=|<=|>|<)\s*('[^']*'|-?\d+(?:\.\d+)?)",
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
        datatype = _column_datatype(owner, table, column)

        if not datatype:
            continue

        base_type = datatype.split("(")[0].upper()

        if base_type in NUMBER_TYPES and _is_quoted_text(value):
            inner_value = value.strip("'").strip()

            if inner_value and not _is_numeric_literal(inner_value):
                raise ValueError(
                    f"Invalid datatype comparison: {owner}.{table}.{column} is {datatype} "
                    f"but compared with text {value}. Use numeric status code or remove this filter."
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
        datatype = _column_datatype(owner, table, column)

        if not datatype:
            continue

        base_type = datatype.split("(")[0].upper()

        if base_type in NUMBER_TYPES:
            raise ValueError(
                f"Invalid LIKE comparison: {owner}.{table}.{column} is {datatype}. "
                f"LIKE can be used only on text columns."
            )
