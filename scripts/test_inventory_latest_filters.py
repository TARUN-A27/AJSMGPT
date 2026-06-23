from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

TESTS = [
    {
        "question": "latest pending MRS for keyboard",
        "intent": "pending_mrs_by_item_name",
        "contains": [
            "INVENTORY.MRS_TEMP",
            "LIKE '%KEYBOARD%'",
            "ORDER BY M.MRSDATE DESC",
        ],
    },
    {
        "question": "latest purchase order for supplier 800967",
        "intent": "pending_po_by_supplier_code",
        "contains": [
            "INVENTORY.PURCHASEORDER",
            "UPPER(PO.SUP_CODE) = '800967'",
            "ORDER BY PO.ORDERDATE DESC",
        ],
    },
    {
        "question": "latest GRN for supplier 800967",
        "intent": "grn_by_supplier_code",
        "contains": [
            "INVENTORY.GRN",
            "UPPER(G.SUP_CODE) = '800967'",
            "ORDER BY G.GRNDATE DESC",
        ],
    },
    {
        "question": "latest issue for yarn",
        "intent": "issue_by_item_name",
        "contains": [
            "INVENTORY.ISSUE",
            "LIKE '%YARN%'",
            "ORDER BY I.ISSUEDATE DESC",
        ],
    },
    {
        "question": "latest keyboard stock",
        "intent": "stock_availability_by_item_name",
        "contains": [
            "INVENTORY.INVITEMS",
            "INVENTORY.STOCK",
            "LIKE '%KEYBOARD%'",
            "ORDER BY INV.ITEM_NAME",
        ],
    },
]


def main():
    from app.business_template_engine import match_business_template

    for case in TESTS:
        print("-" * 100)
        print("QUESTION:", case["question"])

        result = match_business_template(case["question"])
        assert result is not None, f"No template matched: {case['question']}"

        sql = result.get("sql") or result.get("sql_template") or ""
        intent = result.get("intent")

        print("INTENT:", intent)
        print("SQL:", sql)

        assert intent == case["intent"], f"Expected {case['intent']}, got {intent}"

        upper_sql = sql.upper()
        for token in case["contains"]:
            assert token.upper() in upper_sql, f"Missing {token} in SQL: {sql}"

    print("All inventory latest filter tests completed.")


if __name__ == "__main__":
    main()
