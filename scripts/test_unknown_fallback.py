from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

FORBIDDEN = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "MERGE",
    "CREATE", "COMMIT", "ROLLBACK", "EXEC", "CALL", "BEGIN", "DECLARE",
]

TESTS = [
    ("show camera ip details", "ADMIN.CAMERAIP"),
    ("vehicle movement details for supplier 800967", "ADMIN.TRN_VEHICLEMOVEMENT"),
    ("cashbank voucher details for party ODUT001", "ADMIN.CASHBANK"),
    ("show current attendance for empcode 165224", "HRDNEW.CURRENTATTENDANCE"),
    ("show department authentication for empcode 165224", "HRDNEW.HODDEPTAUTHENTICATION"),
    ("document details for party ODUT001", "ADMIN.DOCUMENT"),
]


def main():
    from app.query_engine import answer_question

    for question, expected_table in TESTS:
        print("---")
        print("QUESTION:", question)
        result = answer_question(question)

        print("SOURCE:", result.get("source"))
        if result.get("error"):
            print("ERROR:", result.get("error"))

        sql = result.get("sql") or ""
        print("SQL:", sql)
        print("ROW_COUNT:", result.get("row_count"))

        assert result.get("success") is True, result
        assert result.get("source") == "qwen_schema_fallback", result
        assert sql.strip().upper().startswith("SELECT"), sql
        assert expected_table in sql.upper(), f"Expected {expected_table}, got {sql}"

        upper_sql = sql.upper()

        if "CURRENT ATTENDANCE" in question.upper():
            assert "ORDER BY INDATE DESC" in upper_sql, sql

        if "DOCUMENT DETAILS" in question.upper():
            assert "ORDER BY DOCDATE DESC" in upper_sql, sql

        if "VEHICLE MOVEMENT" in question.upper():
            assert "ORDER BY V.ENTRYDATE DESC" in upper_sql, sql
        for token in FORBIDDEN:
            assert token not in upper_sql, f"Unsafe token {token} found in SQL: {sql}"

    print("All fallback tests completed.")


if __name__ == "__main__":
    main()
