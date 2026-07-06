from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.rasa_nlu_client import understand_with_rasa_duckling


TESTS = [
    {
        "question": "last supplier for mouse",
        "intent": "purchase_last_supplier_by_material",
        "entities": {"item_name": "mouse"},
    },
    {
        "question": "pending MRS for keyboard",
        "intent": "pending_mrs_by_item_name",
        "entities": {"item_name": "keyboard"},
    },
    {
        "question": "latest GRN for supplier 800967 in 2025",
        "intent": "grn_by_supplier_code",
        "entities": {"supplier_code": "800967", "year": "2025"},
    },
    {
        "question": "cash bank entries for party ODUT001",
        "intent": "cashbank_voucher_details",
        "entities": {"party_code": "ODUT001"},
    },
    {
        "question": "camera details",
        "intent": "camera_details",
        "entities": {},
    },
    {
        "question": "attendance for empcode 165224",
        "intent": "hrd_employee_or_attendance",
        "entities": {"empcode": "165224"},
    },
    {
        "question": "last 3 purchases of keyboard in 2025",
        "intent": "purchase_last_n_purchases_by_material",
        "entities": {"item_name": "keyboard", "number": 3, "year": "2025"},
    },
]


def values_match(actual, expected) -> bool:
    if isinstance(actual, list):
        return any(str(x).upper() == str(expected).upper() for x in actual)
    return str(actual).upper() == str(expected).upper()


def main() -> int:
    failed = 0

    for test in TESTS:
        question = test["question"]
        result = understand_with_rasa_duckling(question)

        print("-" * 100)
        print("QUESTION:", question)
        print("SUCCESS:", result.success)
        print("INTENT:", result.intent)
        print("CONFIDENCE:", round(result.confidence, 4))
        print("ENTITIES:", result.entities)

        if not result.success:
            print("FAIL: NLP result unsuccessful:", result.error)
            failed += 1
            continue

        if result.intent != test["intent"]:
            print("FAIL: expected intent", test["intent"], "got", result.intent)
            failed += 1

        for key, expected_value in test["entities"].items():
            actual_value = result.entities.get(key)
            if not values_match(actual_value, expected_value):
                print("FAIL:", key, "expected", expected_value, "got", actual_value)
                failed += 1

    print("=" * 100)
    print("TOTAL:", len(TESTS))
    print("FAILED:", failed)
    print("PASSED:", len(TESTS) - failed)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
