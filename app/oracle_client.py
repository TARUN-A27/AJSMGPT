import os
import oracledb
from dotenv import load_dotenv

from app.sql_safety import add_oracle_row_limit

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

    oracledb.init_oracle_client(lib_dir=ORACLE_CLIENT_LIB_DIR)
    _oracle_client_initialized = True


def get_connection():
    init_oracle_client_once()

    return oracledb.connect(
        user=ORACLE_USER,
        password=ORACLE_PASSWORD,
        dsn=ORACLE_DSN
    )


def run_safe_select(sql: str):
    """
    Runs SELECT-only SQL safely.
    Blocks all write/DDL/PLSQL commands.
    Applies row limit.
    """

    safe_sql = add_oracle_row_limit(sql, max_rows=SQL_MAX_ROWS)

    conn = get_connection()
    cur = conn.cursor()

    try:
        cur.execute(safe_sql)

        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

        result = {
            "sql": safe_sql,
            "columns": columns,
            "rows": [list(row) for row in rows],
            "row_count": len(rows)
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
