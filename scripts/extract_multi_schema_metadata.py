import os
import json
from pathlib import Path

import oracledb
from dotenv import load_dotenv

load_dotenv(".env")

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")
ORACLE_CLIENT_LIB_DIR = os.getenv("ORACLE_CLIENT_LIB_DIR")

SCHEMAS = [
    schema.strip().upper()
    for schema in os.getenv("ORACLE_SCHEMAS", "").split(",")
    if schema.strip()
]

OUTPUT_FILE = Path("data/multi_schema_metadata.json")


def get_connection():
    oracledb.init_oracle_client(lib_dir=ORACLE_CLIENT_LIB_DIR)
    return oracledb.connect(
        user=ORACLE_USER,
        password=ORACLE_PASSWORD,
        dsn=ORACLE_DSN
    )


def fetch_tables(cursor):
    placeholders = ",".join([f":s{i}" for i in range(len(SCHEMAS))])
    params = {f"s{i}": schema for i, schema in enumerate(SCHEMAS)}

    cursor.execute(f"""
        SELECT OWNER, TABLE_NAME
        FROM ALL_TABLES
        WHERE OWNER IN ({placeholders})
        ORDER BY OWNER, TABLE_NAME
    """, params)

    return cursor.fetchall()


def fetch_columns(cursor, owner, table_name):
    cursor.execute("""
        SELECT COLUMN_ID, COLUMN_NAME, DATA_TYPE, DATA_LENGTH, NULLABLE
        FROM ALL_TAB_COLUMNS
        WHERE OWNER = :owner
          AND TABLE_NAME = :table_name
        ORDER BY COLUMN_ID
    """, {
        "owner": owner,
        "table_name": table_name
    })

    return [
        {
            "column_id": row[0],
            "column_name": row[1],
            "data_type": row[2],
            "data_length": row[3],
            "nullable": row[4],
        }
        for row in cursor.fetchall()
    ]


def build_text(owner, table_name, columns):
    col_text = ", ".join(
        f"{c['column_name']} {c['data_type']} nullable={c['nullable']}"
        for c in columns
    )

    return f"""
Schema: {owner}
Table: {owner}.{table_name}
Purpose: Oracle schema metadata for AJSMGPT textile ERP assistant.
Columns: {col_text}
Rules: SELECT only. Never generate INSERT, UPDATE, DELETE, MERGE, DROP, ALTER, TRUNCATE, CREATE, COMMIT, ROLLBACK, EXEC, CALL, BEGIN, or DECLARE.
""".strip()


def main():
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    cursor = conn.cursor()

    tables = fetch_tables(cursor)
    print(f"Visible target tables: {len(tables)}")

    metadata = []

    for index, (owner, table_name) in enumerate(tables, start=1):
        columns = fetch_columns(cursor, owner, table_name)

        if not columns:
            continue

        item = {
            "schema": owner,
            "table": table_name,
            "full_table_name": f"{owner}.{table_name}",
            "columns": columns,
            "text": build_text(owner, table_name, columns)
        }

        metadata.append(item)

        if index % 25 == 0:
            print(f"Processed {index}/{len(tables)} tables...")

    cursor.close()
    conn.close()

    OUTPUT_FILE.write_text(
        json.dumps(metadata, indent=2),
        encoding="utf-8"
    )

    print("=" * 80)
    print(f"Saved metadata records: {len(metadata)}")
    print(f"Output file: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
