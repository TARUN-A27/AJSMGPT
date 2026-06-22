import os
import re
import oracledb
from dotenv import load_dotenv

load_dotenv(".env")

_ALLOWED_SCHEMAS = {
    s.strip().upper()
    for s in os.getenv("ORACLE_SCHEMAS", "").split(",")
    if s.strip()
}

_METADATA_CACHE = {}


def _init_oracle_client():
    lib_dir = os.getenv("ORACLE_CLIENT_LIB_DIR")
    if lib_dir and oracledb.is_thin_mode():
        oracledb.init_oracle_client(lib_dir=lib_dir)


def _connect():
    _init_oracle_client()
    return oracledb.connect(
        user=os.getenv("ORACLE_USER"),
        password=os.getenv("ORACLE_PASSWORD"),
        dsn=os.getenv("ORACLE_DSN"),
    )


def get_table_columns(schema: str, table: str) -> set[str]:
    schema = schema.upper()
    table = table.upper()
    key = f"{schema}.{table}"

    if key in _METADATA_CACHE:
        return _METADATA_CACHE[key]

    if schema not in _ALLOWED_SCHEMAS:
        raise ValueError(f"Schema not allowed: {schema}")

    conn = _connect()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT column_name
        FROM all_tab_columns
        WHERE owner = :owner
          AND table_name = :table_name
        """,
        owner=schema,
        table_name=table,
    )

    columns = {row[0].upper() for row in cur.fetchall()}

    cur.close()
    conn.close()

    if not columns:
        raise ValueError(f"Table not found or no columns visible: {key}")

    _METADATA_CACHE[key] = columns
    return columns


def extract_tables(sql: str) -> dict[str, str]:
    tables = {}
    pattern = r"\b(?:FROM|JOIN)\s+([A-Z0-9_]+)\.([A-Z0-9_]+)(?:\s+([A-Z0-9_]+))?"

    for schema, table, alias in re.findall(pattern, sql.upper()):
        full_name = f"{schema}.{table}"
        tables[full_name] = full_name

        if alias and alias not in {"WHERE", "JOIN", "ON", "ORDER", "GROUP", "LEFT", "RIGHT", "INNER", "OUTER"}:
            tables[alias] = full_name

    return tables


def validate_sql_columns(sql: str) -> bool:
    sql_upper = sql.upper()
    table_map = extract_tables(sql_upper)

    if not table_map:
        raise ValueError("No tables found in SQL.")

    qualified_columns = re.findall(r"\b([A-Z0-9_]+)\.([A-Z0-9_]+)\b", sql_upper)

    for prefix, column in qualified_columns:
        if prefix in _ALLOWED_SCHEMAS:
            continue

        if prefix not in table_map:
            continue

        full_table = table_map[prefix]
        schema, table = full_table.split(".", 1)
        columns = get_table_columns(schema, table)

        if column not in columns:
            raise ValueError(f"Invalid column: {prefix}.{column} not found in {full_table}")

    full_tables = {v for v in table_map.values()}

    if len(full_tables) == 1:
        full_table = next(iter(full_tables))
        schema, table = full_table.split(".", 1)
        columns = get_table_columns(schema, table)

        select_match = re.search(r"\bSELECT\s+(.*?)\s+FROM\b", sql_upper, flags=re.DOTALL)

        if select_match:
            selected_part = select_match.group(1)

            if "*" not in selected_part:
                selected_cols = [c.strip().split()[-1] for c in selected_part.split(",")]

                for col in selected_cols:
                    col = col.replace('"', "").strip()

                    if "." in col:
                        col = col.split(".")[-1]

                    if col and col not in columns:
                        raise ValueError(f"Invalid column: {col} not found in {full_table}")

    return True
