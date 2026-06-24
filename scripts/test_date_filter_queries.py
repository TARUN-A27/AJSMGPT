from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

TESTS = [
    {
        "question": "pending MRS for keyboard in 2026",
        "expected_table": "INVENTORY.MRS_TEMP",
        "contains": ["LIKE '%KEYBOARD%'", "M.MRSDATE BETWEEN '20260101' AND '20261231'"],
    },
    {
        "question": "document details for party 911378 in 2012",
        "expected_table": "ADMIN.DOCUMENT",
        "contains": ["UPPER(PARTYCODE) = '911378'", "DOCDATE BETWEEN 20120101 AND 20121231"],
    },
    {
        "question": "current attendance for empcode 165224 on 20260212",
        "expected_table": "HRDNEW.CURRENTATTENDANCE",
        "contains": ["EMPCODE = 165224", "INDATE = 20260212"],
    },
    {
        "question": "latest purchase order for supplier 800967 in 2026",
        "expected_table": "INVENTORY.PURCHASEORDER",
        "contains": ["UPPER(PO.SUP_CODE) = '800967'", "PO.ORDERDATE BETWEEN '20260101' AND '20261231'"],
    },
    {
        "question": "GRN for supplier 800967 in 2025",
        "expected_table": "INVENTORY.GRN",
        "contains": ["UPPER(G.SUP_CODE) = '800967'", "G.GRNDATE BETWEEN '20250101' AND '20251231'"],
    },
    {
        "question": "latest issue for yarn in 2024",
        "expected_table": "INVENTORY.ISSUE",
        "contains": ["LIKE '%YARN%'", "I.ISSUEDATE BETWEEN '20240101' AND '20241231'"],
    },
]


def main():
    from app.query_engine import answer_question

    for case in TESTS:
        print("-" * 100)
        print("QUESTION:", case["question"])

        result = answer_question(case["question"])
        assert result.get("success") is True, result

        sql = result.get("sql") or ""
        print("SOURCE:", result.get("source"))
        print("SQL:", sql)
        print("ROW_COUNT:", result.get("row_count"))

        upper_sql = sql.upper()
        assert case["expected_table"] in upper_sql, f"Missing table {case['expected_table']} in SQL: {sql}"

        for token in case["contains"]:
            assert token.upper() in upper_sql, f"Missing {token} in SQL: {sql}"

    print("All date filter tests completed.")


if __name__ == "__main__":
    main()
