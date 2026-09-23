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
#
# Scoped to the 5 schemas AJSMGPT could ever touch (same filter as "VISIBLE
# SCHEMAS" above) -- this instance turned out to have ~28,000 PUBLIC grants
# database-wide (evidently from old cross-database migration tooling: the
# MICROSOFTDTPROPERTIES/MICROSOFTSEQDTPROPERTIES pattern repeats in schemas
# AJSMGPT has never heard of, e.g. ACCSHARES/ACCTEX), and dumping all of them
# every run would bury the one question that actually matters below.
print("\n" + "=" * 60)
print("PUBLIC GRANTS in AJSMGPT's 5 schemas (informational, not pass/fail)")
print("=" * 60)

cur.execute("""
SELECT TABLE_SCHEMA, PRIVILEGE, COUNT(*) FROM ALL_TAB_PRIVS
WHERE GRANTEE = 'PUBLIC' AND TABLE_SCHEMA IN ('ADMIN', 'HRDNEW', 'INSUR', 'INVENTORY', 'SCM')
GROUP BY TABLE_SCHEMA, PRIVILEGE ORDER BY TABLE_SCHEMA, PRIVILEGE
""")
by_schema = cur.fetchall()
if not by_schema:
    print("None.")
else:
    for schema, privilege, count in by_schema:
        print(f"  {schema:<10} {privilege:<12} {count}")

# The only question that actually matters for V1: does this touch a table
# AJSMGPT itself reads? Checked directly, not asserted.
cur.execute("""
SELECT TABLE_SCHEMA, TABLE_NAME, PRIVILEGE FROM ALL_TAB_PRIVS
WHERE GRANTEE = 'PUBLIC' AND PRIVILEGE != 'SELECT'
  AND (TABLE_SCHEMA, TABLE_NAME) IN (
    ('INVENTORY','PURCHASEORDER'), ('INVENTORY','INVITEMS'), ('INVENTORY','MRS_TEMP'),
    ('INVENTORY','ISSUE'), ('INVENTORY','DEPT'), ('SCM','PARTYMASTER'),
    ('INVENTORY','MRS'), ('INVENTORY','GRN'), ('INVENTORY','INVOICEGRN'),
    ('INVENTORY','ITEMSTOCK'), ('INVENTORY','UNIT'), ('SCM','PLACE'),
    ('SCM','STATE'), ('SCM','COUNTRY')
  )
""")
hits = cur.fetchall()
print()
if hits:
    print(f"WARNING: {len(hits)} non-SELECT PUBLIC grant(s) on a table AJSMGPT itself reads:")
    for schema, table, privilege in hits:
        print(f"  {schema}.{table} {privilege}")
else:
    print("OK: none of AJSMGPT's own 14 tables carry a PUBLIC grant beyond SELECT.")
print("(PUBLIC grants elsewhere in ADMIN/HRDNEW/INSUR are a pre-existing, database-wide")
print("condition this account did not create and AJSMGPT cannot revoke -- see")
print("docs/ORACLE_READONLY_ACCOUNT.md for the full picture.)")

cur.close()
conn.close()
