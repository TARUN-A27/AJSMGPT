import os
import oracledb
from dotenv import load_dotenv

load_dotenv()

ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASSWORD = os.getenv("ORACLE_PASSWORD")
ORACLE_DSN = os.getenv("ORACLE_DSN")
ORACLE_CLIENT_LIB_DIR = os.getenv("ORACLE_CLIENT_LIB_DIR")


def main():
    oracledb.init_oracle_client(lib_dir=ORACLE_CLIENT_LIB_DIR)

    conn = oracledb.connect(
        user=ORACLE_USER,
        password=ORACLE_PASSWORD,
        dsn=ORACLE_DSN
    )

    cur = conn.cursor()

    cur.execute("""
        SELECT TABLE_NAME
        FROM USER_TABLES
        ORDER BY TABLE_NAME
    """)

    rows = cur.fetchall()

    print(f"Total INVENTORY tables: {len(rows)}")
    print("-" * 60)

    for row in rows[:100]:
        print(row[0])

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
