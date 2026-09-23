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

# docs/ORACLE_READONLY_ACCOUNT.md promises this account is a DATABASE
# guarantee, not just an application-enforced one -- that claim is only true
# if it is checked, not assumed. These two dictionary views answer "can this
# account do anything beyond SELECT", independent of which tables happen to
# be visible above.
print("\n" + "=" * 60)
print("OBJECT PRIVILEGES (expect SELECT only)")
print("=" * 60)

cur.execute("SELECT DISTINCT PRIVILEGE FROM USER_TAB_PRIVS ORDER BY PRIVILEGE")
privileges = [row[0] for row in cur.fetchall()]
if privileges == ["SELECT"]:
    print("OK: SELECT only.")
else:
    print(f"WARNING: non-SELECT object privileges present: {privileges}")

print("\n" + "=" * 60)
print("SYSTEM PRIVILEGES (expect CREATE SESSION only)")
print("=" * 60)

cur.execute("SELECT PRIVILEGE FROM USER_SYS_PRIVS ORDER BY PRIVILEGE")
sys_privileges = [row[0] for row in cur.fetchall()]
if sys_privileges == ["CREATE SESSION"]:
    print("OK: CREATE SESSION only.")
else:
    print(f"WARNING: unexpected system privileges: {sys_privileges}")

# USER_TAB_PRIVS above only shows grants made TO THIS NAMED USER. A GRANT ...
# TO PUBLIC applies to every account on the instance and would not show up
# there -- checked directly here so "SELECT only" above is not overstated.
# This is a database-wide condition this account did not create and cannot
# fix (AJSMGPT never runs GRANT/REVOKE); it is reported, not resolved, here.
print("\n" + "=" * 60)
print("PUBLIC GRANTS this account also inherits (informational, not pass/fail)")
print("=" * 60)

cur.execute("""
SELECT TABLE_SCHEMA, TABLE_NAME, PRIVILEGE FROM ALL_TAB_PRIVS
WHERE GRANTEE = 'PUBLIC' ORDER BY TABLE_SCHEMA, TABLE_NAME, PRIVILEGE
""")
public_grants = cur.fetchall()
if not public_grants:
    print("None.")
else:
    non_select = [row for row in public_grants if row[2] != "SELECT"]
    print(f"{len(public_grants)} PUBLIC object grants visible ({len(non_select)} beyond SELECT):")
    for schema, table, privilege in public_grants:
        print(f"  {schema}.{table:<28} {privilege}")
    if non_select:
        print("\nNote: PUBLIC grants beyond SELECT are a database-level condition, not")
        print("something this account's creation introduced or something AJSMGPT can")
        print("revoke. AJSMGPT's own catalog never references these tables, so this")
        print("does not affect V1's own read-only behaviour, but it is worth the")
        print("database owner's attention independently of AJSMGPT.")

cur.close()
conn.close()
