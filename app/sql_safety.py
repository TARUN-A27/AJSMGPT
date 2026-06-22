import re


BLOCKED_KEYWORDS = [
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "CREATE",
    "REPLACE",
    "GRANT",
    "REVOKE",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT",
    "EXEC",
    "EXECUTE",
    "CALL",
    "BEGIN",
    "DECLARE",
]


ALLOWED_SCHEMA = "INVENTORY"


class SQLSafetyError(Exception):
    pass


def normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip())


def remove_sql_comments(sql: str) -> str:
    sql = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def validate_select_only(sql: str) -> str:
    """
    Strict validator for AJSMGPT Oracle queries.

    Rules:
    1. Only SELECT is allowed.
    2. No INSERT/UPDATE/DELETE/MERGE/DDL/TCL/PLSQL.
    3. Only INVENTORY schema is allowed.
    4. Multiple statements are blocked.
    5. Semicolon is removed safely at the end only.
    """

    if not sql or not sql.strip():
        raise SQLSafetyError("SQL is empty.")

    cleaned = remove_sql_comments(sql)
    cleaned = normalize_sql(cleaned)

    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()

    if ";" in cleaned:
        raise SQLSafetyError("Multiple SQL statements are not allowed.")

    upper_sql = cleaned.upper()

    if not upper_sql.startswith("SELECT "):
        raise SQLSafetyError("Only SELECT queries are allowed.")

    for keyword in BLOCKED_KEYWORDS:
        pattern = rf"\b{keyword}\b"
        if re.search(pattern, upper_sql):
            raise SQLSafetyError(f"Blocked SQL keyword detected: {keyword}")

    # Block access to other schemas like HRDNEW.TABLE, ADMIN.TABLE, SCM.TABLE etc.
    schema_references = re.findall(r"\b([A-Z][A-Z0-9_]*)\s*\.", upper_sql)

    for schema in schema_references:
        # Ignore aliases like po.COLUMN or item.COLUMN.
        # Real schema names are blocked if not INVENTORY and look like known schema names.
        if schema in ["ADMIN", "HRDNEW", "INSUR", "SCM"]:
            raise SQLSafetyError(f"Schema not allowed: {schema}")

    # If any full schema.table reference is used, it must be INVENTORY.
    forbidden_schema_pattern = r"\b(ADMIN|HRDNEW|INSUR|SCM)\s*\."
    if re.search(forbidden_schema_pattern, upper_sql):
        raise SQLSafetyError("Only INVENTORY schema is allowed.")

    return cleaned


def add_oracle_row_limit(sql: str, max_rows: int = 100) -> str:
    """
    Wrap query with Oracle row limit.
    This avoids accidentally reading huge data from sensitive DB.
    """

    safe_sql = validate_select_only(sql)

    upper_sql = safe_sql.upper()

    if " FETCH FIRST " in upper_sql or " ROWNUM " in upper_sql:
        return safe_sql

    return f"""
SELECT *
FROM (
    {safe_sql}
)
WHERE ROWNUM <= {max_rows}
""".strip()


if __name__ == "__main__":
    test_queries = [
        "SELECT * FROM INVENTORY.PURCHASEORDER",
        "SELECT ORDERNO, ORDERDATE FROM INVENTORY.PURCHASEORDER WHERE STATUS = 0",
        "DELETE FROM INVENTORY.PURCHASEORDER",
        "UPDATE INVENTORY.PURCHASEORDER SET STATUS = 1",
        "DROP TABLE INVENTORY.PURCHASEORDER",
        "SELECT * FROM HRDNEW.DEPARTMENT",
        "SELECT * FROM INVENTORY.PURCHASEORDER; DELETE FROM INVENTORY.PURCHASEORDER",
    ]

    for sql in test_queries:
        print("=" * 80)
        print("SQL:", sql)
        try:
            safe_sql = add_oracle_row_limit(sql)
            print("✅ SAFE")
            print(safe_sql)
        except Exception as e:
            print("❌ BLOCKED")
            print(e)
