import os
import json
from pathlib import Path

import oracledb
from dotenv import load_dotenv

load_dotenv()

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")
ORACLE_CLIENT_LIB_DIR = os.getenv("ORACLE_CLIENT_LIB_DIR")

OUTPUT_FILE = Path("data/inventory_schema_metadata.json")


IMPORTANT_TABLES = [
    "PURCHASEORDER",
    "INVITEMS",
    "ITEMSTOCK",
    "GRN",
    "GATEINWARD",
    "GATEINWARDRDC",
    "MRS",
    "MRS_TEMP",
    "MRS_TEMP_DETAILS",
    "ISSUE",
    "TRANSFERSTOCK",
    "SUPPLIER",
    "UNIT",
    "UOM",
    "DEPT",
    "HOD",
    "RAWUSER",
    "MACHINE",
    "BREAKDOWNS",
    "BREAKDOWNEVENTS",
    "YEARLYBUDGET",
]


def get_connection():
    oracledb.init_oracle_client(lib_dir=ORACLE_CLIENT_LIB_DIR)

    return oracledb.connect(
        user=ORACLE_USER,
        password=ORACLE_PASSWORD,
        dsn=ORACLE_DSN
    )


def fetch_columns(cursor, table_name):
    cursor.execute("""
        SELECT
            COLUMN_ID,
            COLUMN_NAME,
            DATA_TYPE,
            DATA_LENGTH,
            NULLABLE
        FROM USER_TAB_COLUMNS
        WHERE TABLE_NAME = :table_name
        ORDER BY COLUMN_ID
    """, {"table_name": table_name})

    columns = []

    for column_id, column_name, data_type, data_length, nullable in cursor.fetchall():
        columns.append({
            "column_id": column_id,
            "column_name": column_name,
            "data_type": data_type,
            "data_length": data_length,
            "nullable": nullable,
        })

    return columns


def build_metadata_text(table_name, columns):
    column_lines = []

    for col in columns:
        column_lines.append(
            f"{col['column_name']} {col['data_type']} nullable={col['nullable']}"
        )

    columns_text = ", ".join(column_lines)

    return f"""
Schema: INVENTORY
Table: INVENTORY.{table_name}
Purpose: Oracle INVENTORY schema table used by AJSMGPT for textile inventory business queries.
Columns: {columns_text}
Allowed access rule: SELECT only. Do not generate INSERT, UPDATE, DELETE, MERGE, DROP, ALTER, TRUNCATE, or CREATE.
""".strip()


def main():
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    cursor = conn.cursor()

    metadata = []

    for table_name in IMPORTANT_TABLES:
        columns = fetch_columns(cursor, table_name)

        if not columns:
            print(f"Skipping {table_name}: table not found or no visible columns")
            continue

        item = {
            "schema": "INVENTORY",
            "table": table_name,
            "full_table_name": f"INVENTORY.{table_name}",
            "columns": columns,
            "text": build_metadata_text(table_name, columns)
        }

        metadata.append(item)
        print(f"Extracted: INVENTORY.{table_name} ({len(columns)} columns)")

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
