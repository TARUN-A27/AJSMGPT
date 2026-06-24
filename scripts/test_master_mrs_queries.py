from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


TESTS = [
    {
        "question": "MRS details for MRS number 890330",
        "intent": "mrs_by_mrs_no",
        "contains": ["INVENTORY.MRS_TEMP", "M.MRSNO = 890330"],
    },
    {
        "question": "MRS for department EDP",
        "intent": "mrs_by_department",
        "contains": ["INVENTORY.MRS_TEMP", "UPPER(D.DEPT_NAME) LIKE '%EDP%'"],
    },
    {
        "question": "MRS for unit B-Unit",
        "intent": "mrs_by_unit",
        "contains": ["INVENTORY.MRS_TEMP", "UPPER(U.UNIT_NAME) LIKE '%B-UNIT%'"],
    },
    {
        "question": "approved MRS for keyboard",
        "intent": "approved_mrs_by_item_name",
        "contains": [
            "LIKE '%KEYBOARD%'",
            "NVL(M.APPROVALSTATUS, 0) = 1",
            "NVL(M.READYFORAPPROVAL, 0) = 1",
        ],
    },
    {
        "question": "rejected MRS for keyboard",
        "intent": "rejected_mrs_by_item_name",
        "contains": [
            "LIKE '%KEYBOARD%'",
            "NVL(M.REJECTIONSTATUS, 0) = 1",
            "NVL(M.STORESREJECTIONSTATUS, 0) = 1",
        ],
    },
    {
        "question": "MRS linked with order number 800151",
        "intent": "mrs_by_order_no",
        "contains": ["INVENTORY.MRS_TEMP", "M.ORDERNO = 800151"],
    },
    {
        "question": "MRS due in 2026",
        "intent": "mrs_due_all",
        "contains": ["INVENTORY.MRS_TEMP", "M.DUEDATE BETWEEN '20260101' AND '20261231'"],
    },
    {
        "question": "MRS due for keyboard in 2026",
        "intent": "mrs_due_by_item_name",
        "contains": ["LIKE '%KEYBOARD%'", "M.DUEDATE BETWEEN '20260101' AND '20261231'"],
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
        intent = result.get("intent")

        print("INTENT:", intent)
        print("SQL:", sql)
        print("ROW_COUNT:", result.get("row_count"))

        assert intent == case["intent"], f"Expected {case['intent']}, got {intent}"

        upper_sql = sql.upper()
        for token in case["contains"]:
            assert token.upper() in upper_sql, f"Missing {token} in SQL: {sql}"

    print("All master MRS query tests completed.")


if __name__ == "__main__":
    main()
