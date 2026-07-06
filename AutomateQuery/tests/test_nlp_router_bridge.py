from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.nlp_router_bridge import build_nlp_router_candidate


TESTS = [
    ("last supplier for mouse", "purchase", "purchase_last_supplier_by_material", {"item_name": "mouse"}),
    ("pending MRS for keyboard", "inventory_mrs", "pending_mrs_by_item_name", {"item_name": "keyboard"}),
    ("latest GRN for supplier 800967 in 2025", "purchase", "grn_by_supplier_code", {"supplier_code": "800967", "year": "2025"}),
    ("cash bank entries for party ODUT001", "admin_cashbank", "cashbank_voucher_details", {"party_code": "ODUT001"}),
    ("camera details", "admin_camera", "camera_details", {}),
    ("attendance for empcode 165224", "hrd", "hrd_employee_or_attendance", {"empcode": "165224"}),
    ("last 3 purchases of keyboard in 2025", "purchase", "purchase_last_n_purchases_by_material", {"item_name": "keyboard", "number": 3, "year": "2025"}),
]


def value_matches(actual, expected) -> bool:
    if isinstance(actual, list):
        return any(str(x).upper() == str(expected).upper() for x in actual)
    return str(actual).upper() == str(expected).upper()


def main() -> int:
    failed = 0

    for question, expected_module, expected_intent, expected_entities in TESTS:
        result = build_nlp_router_candidate(question)

        print("-" * 100)
        print("QUESTION:", question)
        print("SUCCESS:", result.success)
        print("INTENT:", result.intent)
        print("MODULE:", result.module)
        print("ENTITIES:", result.entities)
        print("REQUIRED_OK:", result.required_entities_ok)
        print("SAFE_TO_APPLY:", result.safe_to_apply)
        print("REASON:", result.reason)

        if not result.success:
            print("FAIL: result unsuccessful")
            failed += 1

        if result.module != expected_module:
            print("FAIL: module expected", expected_module, "got", result.module)
            failed += 1

        if result.intent != expected_intent:
            print("FAIL: intent expected", expected_intent, "got", result.intent)
            failed += 1

        if result.safe_to_apply:
            print("FAIL: safe_to_apply should remain False in observe/review mode")
            failed += 1

        for key, expected_value in expected_entities.items():
            actual_value = result.entities.get(key)
            if not value_matches(actual_value, expected_value):
                print("FAIL:", key, "expected", expected_value, "got", actual_value)
                failed += 1

    print("=" * 100)
    print("TOTAL:", len(TESTS))
    print("FAILED:", failed)
    print("PASSED:", len(TESTS) - failed)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
