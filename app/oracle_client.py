import os
import time

import oracledb
from dotenv import load_dotenv

from app.backend_logger import log_event
from app.sql_datatype_validator import validate_sql_datatypes
from app.sql_safety import add_oracle_row_limit, validate_select_only


load_dotenv()

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")
ORACLE_CLIENT_LIB_DIR = os.getenv("ORACLE_CLIENT_LIB_DIR")
SQL_MAX_ROWS = int(os.getenv("SQL_MAX_ROWS", "100"))

_oracle_client_initialized = False


def init_oracle_client_once():
    global _oracle_client_initialized

    if _oracle_client_initialized:
        return

    if ORACLE_CLIENT_LIB_DIR:
        oracledb.init_oracle_client(lib_dir=ORACLE_CLIENT_LIB_DIR)

    _oracle_client_initialized = True


def get_connection():
    init_oracle_client_once()

    return oracledb.connect(
        user=ORACLE_USER,
        password=ORACLE_PASSWORD,
        dsn=ORACLE_DSN,
    )


def run_safe_select(sql: str):
    """
    Runs SELECT-only SQL safely.
    Blocks write/DDL/PLSQL commands.
    Validates obvious datatype mistakes.
    Applies row limit.
    """

    started = time.time()

    log_event(
        None,
        "sql_validation_start",
        "Starting SQL safety and datatype validation",
        sql=sql,
    )

    validate_select_only(sql)
    validate_sql_datatypes(sql)

    log_event(
        None,
        "sql_validation_complete",
        "SQL safety and datatype validation passed",
    )

    safe_sql = add_oracle_row_limit(sql, max_rows=SQL_MAX_ROWS)

    log_event(
        None,
        "row_limit_applied",
        "Oracle row limit applied",
        max_rows=SQL_MAX_ROWS,
        final_sql=safe_sql,
    )

    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute(safe_sql)

        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

        elapsed_ms = int((time.time() - started) * 1000)

        result = {
            "sql": safe_sql,
            "columns": columns,
            "rows": [list(row) for row in rows],
            "row_count": len(rows),
            "elapsed_ms": elapsed_ms,
        }

        return result

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    test_sql = """
    SELECT ORDERNO, ORDERDATE, ITEM_CODE, QTY, STATUS
    FROM INVENTORY.PURCHASEORDER
    """

    result = run_safe_select(test_sql)

    print("Executed SQL:")
    print(result["sql"])
    print("Columns:", result["columns"])
    print("Row count:", result["row_count"])

    for row in result["rows"][:5]:
        print(row)
