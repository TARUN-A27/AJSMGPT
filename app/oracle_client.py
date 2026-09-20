import os
import time
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import TypeAlias

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
SQL_MAX_ROWS = max(1, min(int(os.getenv("SQL_MAX_ROWS", "100")), 1000))
ORACLE_QUERY_TIMEOUT_MS = max(
    1000,
    min(int(os.getenv("ORACLE_QUERY_TIMEOUT_MS", "30000")), 120000),
)
ORACLE_CONNECT_TIMEOUT_SECONDS = max(
    1,
    min(int(os.getenv("ORACLE_CONNECT_TIMEOUT_SECONDS", "10")), 60),
)

OracleBindValue: TypeAlias = str | int | float | bool | Decimal | date | datetime | None


class OracleUnavailableError(RuntimeError):
    """The database could not be reached within the configured boundary."""


class OracleExecutionError(RuntimeError):
    """Oracle rejected or could not complete the safe SELECT."""

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
        tcp_connect_timeout=ORACLE_CONNECT_TIMEOUT_SECONDS,
        retry_count=0,
    )


def _safe_database_error(exc: oracledb.DatabaseError) -> RuntimeError:
    error = exc.args[0] if exc.args else None
    code = getattr(error, "code", None)
    unavailable_codes = {
        1012, 1033, 1034, 1089, 1090, 1092, 12154, 12505, 12514,
        12516, 12518, 12520, 12521, 12528, 12537, 12541, 12543,
        12545, 12547, 12560, 12570, 12571, 3135,
    }
    if code in unavailable_codes:
        return OracleUnavailableError("Oracle is unavailable.")
    return OracleExecutionError("Oracle could not execute the safe query.")


def run_safe_select(
    sql: str,
    binds: Mapping[str, OracleBindValue] | None = None,
    *,
    enforce_row_limit: bool = True,
    validate_datatypes: bool = True,
):
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
        sql_fingerprint=sha256(sql.encode("utf-8")).hexdigest()[:16],
    )

    validate_select_only(sql)
    if validate_datatypes:
        validate_sql_datatypes(sql, binds)

    log_event(
        None,
        "sql_validation_complete",
        "SQL safety and datatype validation passed",
    )

    safe_sql = add_oracle_row_limit(sql, max_rows=SQL_MAX_ROWS) if enforce_row_limit else sql

    log_event(
        None,
        "row_limit_applied",
        "Oracle row limit applied",
        max_rows=SQL_MAX_ROWS,
        sql_fingerprint=sha256(safe_sql.encode("utf-8")).hexdigest()[:16],
    )

    conn = None
    cur = None
    try:
        conn = get_connection()
        conn.call_timeout = ORACLE_QUERY_TIMEOUT_MS
        cur = conn.cursor()
        cur.execute(safe_sql, dict(binds or {}))

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

    except oracledb.DatabaseError as exc:
        raise _safe_database_error(exc) from None

    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
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
