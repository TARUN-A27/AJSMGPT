import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.query_engine import answer_question

CASES = [
    {
        "question": "last supply of mouse",
        "intent": "purchase_last_supply_by_material",
        "sql_contains": [
            "INVENTORY.PURCHASEORDER",
            "INVENTORY.INVITEMS",
            "SCM.PARTYMASTER",
            "UPPER(INV.ITEM_NAME) LIKE '%MOUSE%'",
            "ROWNUM <= 1",
        ],
    },
    {
        "question": "Last 3 purchase qty purchase of the item Keyboard",
        "intent": "purchase_last_n_purchases_by_material",
        "sql_contains": [
            "INVENTORY.PURCHASEORDER",
            "INVENTORY.INVITEMS",
            "SCM.PARTYMASTER",
            "UPPER(INV.ITEM_NAME) LIKE '%KEYBOARD%'",
            "ROWNUM <= 3",
        ],
    },
    {
        "question": "Last purchase qty purchase of the item Keyboard",
        "intent": "purchase_last_n_purchases_by_material",
        "sql_contains": [
            "UPPER(INV.ITEM_NAME) LIKE '%KEYBOARD%'",
            "ROWNUM <= 1",
        ],
    },
]

failed = 0

for case in CASES:
    res = answer_question(case["question"])

    print("-" * 100)
    print("QUESTION:", case["question"])
    print("SUCCESS:", res.get("success"))
    print("SOURCE:", res.get("source"))
    print("INTENT:", res.get("intent"))
    print("ROWS:", res.get("row_count"))

    sql = res.get("sql") or ""

    if res.get("source") != "purchase_analytics_router":
        print("FAILED: wrong source")
        failed += 1

    if res.get("intent") != case["intent"]:
        print("FAILED: wrong intent")
        failed += 1

    for expected in case["sql_contains"]:
        if expected not in sql:
            print("FAILED: SQL missing:", expected)
            failed += 1

if failed:
    raise SystemExit(f"FAILED: {failed} checks failed")

print("All HOD purchase question tests passed.")
