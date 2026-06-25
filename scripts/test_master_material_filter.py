import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.purchase_analytics_router import match_purchase_analytics_template


questions = [
    'WHO are the suppliers for the item "BARCODE LABEL"',
    'WHO are the suppliers for the item "BARCODE CHROMO LABEL"',
]

failed = 0

for q in questions:
    res = match_purchase_analytics_template(q)
    sql = res.get("sql") if res else ""
    intent = res.get("intent") if res else ""

    print("=" * 100)
    print("QUESTION:", q)
    print("INTENT:", intent)
    print("SQL:", sql)

    required = [
        "purchase_suppliers_by_material",
        "BARCODE",
        "BAR CODE",
        "LABEL",
        "LABLE",
        "SCM.PARTYMASTER",
    ]

    for item in required:
        if item not in (intent + "\n" + sql):
            print("FAILED missing:", item)
            failed += 1

    if "INVENTORY.SUPPLIER" in sql:
        print("FAILED wrong supplier table used")
        failed += 1

if failed:
    raise SystemExit(f"FAILED: {failed} checks failed")

print("Master material filter tests passed.")
