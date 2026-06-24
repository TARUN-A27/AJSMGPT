from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


AUTOMATE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = AUTOMATE_DIR.parent

sys.path.insert(0, str(PROJECT_ROOT))

from app.query_engine import answer_question  # noqa: E402


APPROVED_EVAL_JSON = AUTOMATE_DIR / "reports" / "approved_eval_tests.json"


def read_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Approved eval file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, list):
        raise ValueError(f"Expected list in {path}")

    return data


def main() -> None:
    tests = read_json(APPROVED_EVAL_JSON)

    if not tests:
        raise AssertionError("No approved eval tests found.")

    passed = 0
    failed = 0

    for index, test in enumerate(tests, start=1):
        question = str(test.get("question", "")).strip()
        expected_source = test.get("expected_source")
        expected_intent = test.get("expected_intent")
        expected_sql_contains = test.get("expected_sql_contains", [])

        print("-" * 120)
        print(f"{index}. QUESTION: {question}")

        if not question:
            failed += 1
            print("FAILED: Empty question.")
            continue

        try:
            result = answer_question(question)
            sql = result.get("sql") or ""

            print("SUCCESS:", result.get("success"))
            print("SOURCE:", result.get("source"))
            print("INTENT:", result.get("intent"))
            print("ROWS:", result.get("row_count"))
            print("SQL:", sql)

            assert result.get("success") is True, "Expected success=True"
            assert result.get("source") == expected_source, (
                f"Expected source {expected_source}, got {result.get('source')}"
            )
            assert result.get("intent") == expected_intent, (
                f"Expected intent {expected_intent}, got {result.get('intent')}"
            )

            for token in expected_sql_contains:
                assert token in sql, f"Missing SQL token: {token}"

            passed += 1
            print("PASSED")

        except Exception as exc:
            failed += 1
            print("FAILED:", type(exc).__name__, str(exc))

    print("-" * 120)
    print(f"Approved eval tests finished. Passed={passed}, Failed={failed}")

    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
