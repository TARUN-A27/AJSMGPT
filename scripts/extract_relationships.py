import json
import os
import sys
import textwrap
import traceback

import oracledb
from dotenv import load_dotenv

load_dotenv()

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")
ORACLE_CLIENT_LIB_DIR = os.getenv("ORACLE_CLIENT_LIB_DIR")
ORACLE_SCHEMAS = os.getenv("ORACLE_SCHEMAS", "")

DATA_FILE = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)),
    "data",
    "schema_relationships.json",
)


def init_oracle_client():
    if ORACLE_CLIENT_LIB_DIR:
        print(f"Initializing Oracle client with lib_dir={ORACLE_CLIENT_LIB_DIR}")
        oracledb.init_oracle_client(lib_dir=ORACLE_CLIENT_LIB_DIR)
    else:
        print("Initializing Oracle client with default settings")
        oracledb.init_oracle_client()


def get_connection():
    init_oracle_client()
    return oracledb.connect(
        user=ORACLE_USER,
        password=ORACLE_PASSWORD,
        dsn=ORACLE_DSN,
    )


def parse_schemas(env_value: str) -> list[str]:
    schemas = [schema.strip().upper() for schema in env_value.split(",") if schema.strip()]
    unique_schemas = []
    for schema in schemas:
        if schema not in unique_schemas:
            unique_schemas.append(schema)
    return unique_schemas


def build_query(schema_list: list[str]) -> tuple[str, dict]:
    if not schema_list:
        raise ValueError("ORACLE_SCHEMAS is empty. Provide comma-separated schema names in .env.")

    binds = {f"schema_{idx}": schema for idx, schema in enumerate(schema_list, start=1)}
    in_list = ", ".join(f":schema_{idx}" for idx in range(1, len(schema_list) + 1))

    query = textwrap.dedent(
        f"""
        SELECT
            ac.owner AS from_schema,
            ac.table_name AS from_table,
            acc.column_name AS from_column,
            rc.owner AS to_schema,
            rc.table_name AS to_table,
            rcacc.column_name AS to_column,
            ac.constraint_name AS constraint_name
        FROM all_constraints ac
        JOIN all_cons_columns acc
            ON ac.owner = acc.owner
           AND ac.constraint_name = acc.constraint_name
        JOIN all_constraints rc
            ON ac.r_owner = rc.owner
           AND ac.r_constraint_name = rc.constraint_name
        JOIN all_cons_columns rcacc
            ON rc.owner = rcacc.owner
           AND rc.constraint_name = rcacc.constraint_name
           AND rcacc.position = acc.position
        WHERE ac.constraint_type = 'R'
          AND ac.owner IN ({in_list})
        ORDER BY
            ac.owner,
            ac.table_name,
            ac.constraint_name,
            acc.position
        """
    )

    return query, binds


def extract_relationships() -> list[dict]:
    schema_list = parse_schemas(ORACLE_SCHEMAS)
    print("Schemas to scan:", schema_list)

    if not ORACLE_USER or not ORACLE_PASSWORD or not ORACLE_DSN:
        raise EnvironmentError(
            "Missing Oracle connection environment values. Check ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN."
        )

    query, binds = build_query(schema_list)
    print("Executing SELECT-only metadata query for foreign keys...")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, binds)
            columns = [col[0].lower() for col in cursor.description]
            rows = cursor.fetchall()

    print(f"Fetched {len(rows)} relationship rows from Oracle metadata")

    relationships = [
        dict(zip(columns, row))
        for row in rows
    ]

    return relationships


def save_relationships(relationships: list[dict]) -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as fp:
        json.dump(relationships, fp, indent=2, sort_keys=False)

    print(f"Saved {len(relationships)} relationship records to {DATA_FILE}")


def print_preview(relationships: list[dict], limit: int = 20) -> None:
    print("\n=== Relationship preview ===")
    for idx, relationship in enumerate(relationships[:limit], start=1):
        print(
            f"{idx:>2}. {relationship['from_schema']}.{relationship['from_table']}"
            f"({relationship['from_column']}) -> "
            f"{relationship['to_schema']}.{relationship['to_table']}"
            f"({relationship['to_column']}) [{relationship['constraint_name']}])"
        )
    print(f"\nTotal relationships extracted: {len(relationships)}")


def main() -> int:
    print("Starting Oracle schema relationship extraction")
    print("Only SELECT queries will be executed.")

    try:
        relationships = extract_relationships()
        save_relationships(relationships)
        print_preview(relationships)
        print("Extraction completed successfully")
        return 0

    except Exception as exc:
        print("ERROR: Relationship extraction failed")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
