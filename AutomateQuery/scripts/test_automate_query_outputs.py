from __future__ import annotations

import json
from pathlib import Path


AUTOMATE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = AUTOMATE_DIR / "reports"

REQUIRED_FILES = [
    REPORTS_DIR / "log_analysis.md",
    REPORTS_DIR / "router_fix_candidates.json",
    REPORTS_DIR / "qdrant_business_terms_candidates.json",
    REPORTS_DIR / "generated_eval_candidates.json",
]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_required_files_exist() -> None:
    missing = [str(path) for path in REQUIRED_FILES if not path.exists()]

    if missing:
        raise AssertionError(f"Missing report files: {missing}")


def test_json_files_are_valid() -> None:
    for path in REQUIRED_FILES:
        if path.suffix == ".json":
            read_json(path)


def test_generated_eval_has_no_duplicates() -> None:
    eval_path = REPORTS_DIR / "generated_eval_candidates.json"
    records = read_json(eval_path)

    seen = set()

    for record in records:
        key = (
            record.get("question", "").strip().lower(),
            record.get("expected_source"),
            record.get("expected_intent"),
            tuple(record.get("expected_sql_contains", [])),
        )

        if key in seen:
            raise AssertionError(f"Duplicate eval candidate found: {record}")

        seen.add(key)


def test_prime_compu_candidate_exists() -> None:
    eval_path = REPORTS_DIR / "generated_eval_candidates.json"
    records = read_json(eval_path)

    matched = [
        record
        for record in records
        if record.get("question") == "first supply from supplier Prime compu systems"
    ]

    if not matched:
        raise AssertionError("Prime Compu Systems eval candidate not found.")

    record = matched[0]

    assert record["expected_source"] == "purchase_analytics_router"
    assert record["expected_intent"] == "purchase_first_supply_by_supplier"

    expected_sql_parts = [
        "INVENTORY.PURCHASEORDER",
        "INVENTORY.INVITEMS",
        "SCM.PARTYMASTER",
        "UPPER(P.PARTYNAME) LIKE '%PRIME COMPU SYSTEMS%'",
        "ORDER BY PO.ORDERDATE ASC",
    ]

    for part in expected_sql_parts:
        if part not in record["expected_sql_contains"]:
            raise AssertionError(f"Missing expected SQL part: {part}")


def main() -> None:
    test_required_files_exist()
    test_json_files_are_valid()
    test_generated_eval_has_no_duplicates()
    test_prime_compu_candidate_exists()

    print("AutomateQuery output tests passed.")


if __name__ == "__main__":
    main()
