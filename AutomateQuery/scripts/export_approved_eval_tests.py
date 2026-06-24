from __future__ import annotations

import json
from pathlib import Path
from typing import Any


AUTOMATE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = AUTOMATE_DIR / "reports"

REVIEWED_EVAL_JSON = REPORTS_DIR / "reviewed_eval_candidates.json"
APPROVED_EVAL_JSON = REPORTS_DIR / "approved_eval_tests.json"


def read_json(path: Path) -> Any:
    if not path.exists():
        print(f"Missing file: {path}")
        return []

    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    reviewed = read_json(REVIEWED_EVAL_JSON)

    approved_tests: list[dict[str, Any]] = []

    for record in reviewed:
        if record.get("approved") is not True:
            continue

        approved_tests.append(
            {
                "question": record.get("question"),
                "expected_source": record.get("expected_source"),
                "expected_intent": record.get("expected_intent"),
                "expected_sql_contains": record.get("expected_sql_contains", []),
                "review_note": record.get("review_note", ""),
            }
        )

    write_json(APPROVED_EVAL_JSON, approved_tests)

    print(f"Approved eval tests exported: {len(approved_tests)}")
    print(f"Wrote: {APPROVED_EVAL_JSON}")


if __name__ == "__main__":
    main()
