import os
import oracledb
from dotenv import load_dotenv

load_dotenv(".env")

oracledb.init_oracle_client(
    lib_dir=os.getenv("ORACLE_CLIENT_LIB_DIR")
)

conn = oracledb.connect(
    user=os.getenv("ORACLE_USER"),
    password=os.getenv("ORACLE_PASSWORD"),
    dsn=os.getenv("ORACLE_DSN")
)

cur = conn.cursor()

print("=" * 60)
print("CURRENT LOGIN USER")
print("=" * 60)

cur.execute("SELECT USER FROM DUAL")
print("User:", cur.fetchone()[0])

print("\n" + "=" * 60)
print("VISIBLE SCHEMAS")
print("=" * 60)

cur.execute("""
SELECT OWNER, COUNT(*) AS TABLE_COUNT
FROM ALL_TABLES
WHERE OWNER IN ('ADMIN', 'HRDNEW', 'INSUR', 'INVENTORY', 'SCM')
GROUP BY OWNER
ORDER BY OWNER
""")

rows = cur.fetchall()

if not rows:
    print("No target schemas visible.")
else:
    for owner, count in rows:
        print(f"{owner:<15} {count}")

cur.close()
conn.close()
