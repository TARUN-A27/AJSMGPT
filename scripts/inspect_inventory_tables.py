import os
import oracledb
from dotenv import load_dotenv

load_dotenv()

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")
ORACLE_CLIENT_LIB_DIR = os.getenv("ORACLE_CLIENT_LIB_DIR")

TABLES = [
    "PURCHASEORDER",
    "INVITEMS",
    "ITEMSTOCK",
    "GRN",
    "MRS",
    "ISSUE",
    "TRANSFERSTOCK",
    "SUPPLIER",
    "DEPT",
    "UNIT"
]


def get_connection():
    oracledb.init_oracle_client(lib_dir=ORACLE_CLIENT_LIB_DIR)

    return oracledb.connect(
        user=ORACLE_USER,
        password=ORACLE_PASSWORD,
        dsn=ORACLE_DSN
    )


def main():
    conn = get_connection()
    cur = conn.cursor()

    for table_name in TABLES:
        print("\n" + "=" * 100)
        print(f"TABLE: INVENTORY.{table_name}")
        print("=" * 100)

        cur.execute("""
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

        rows = cur.fetchall()

        if not rows:
            print("Table not found or no columns visible.")
            continue

        for column_id, column_name, data_type, data_length, nullable in rows:
            print(
                f"{column_id:>3}. "
                f"{column_name:<35} "
                f"{data_type:<15} "
                f"LEN={data_length:<5} "
                f"NULLABLE={nullable}"
            )

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
