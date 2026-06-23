import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.sql_generator_v2 import generate_select_sql_v2
from app.oracle_client import run_safe_select


EVAL_PATH = Path("data/evaluation_questions.json")


def fail(message: str) -> None:
    print(f"❌ {message}")


def ok(message: str) -> None:
    print(f"✅ {message}")


def main() -> int:
    if not EVAL_PATH.exists():
        print(f"Evaluation file not found: {EVAL_PATH}")
        return 1

    cases = json.loads(EVAL_PATH.read_text(encoding="utf-8"))

    total = len(cases)
    passed = 0

    print(f"\nRunning {total} evaluation cases...\n")

    for index, case in enumerate(cases, start=1):
        question = case["question"]
        expected_intent = case.get("expected_intent")
        must_contain = case.get("must_contain", [])
        should_execute = case.get("execute", False)

        print("=" * 100)
        print(f"CASE {index}: {question}")

        try:
            generated = generate_select_sql_v2(question)
            sql = generated.get("sql", "")
            intent = generated.get("intent")

            print(f"INTENT: {intent}")
            print(f"SQL: {sql}")

            case_ok = True

            if expected_intent and intent != expected_intent:
                fail(f"Expected intent '{expected_intent}', got '{intent}'")
                case_ok = False
            else:
                ok("Intent matched")

            sql_upper = sql.upper()

            for token in must_contain:
                if token.upper() not in sql_upper:
                    fail(f"SQL missing required token: {token}")
                    case_ok = False
                else:
                    ok(f"SQL contains: {token}")

            if should_execute:
                result = run_safe_select(sql)
                row_count = result.get("row_count", 0)
                print(f"ROW COUNT: {row_count}")
                ok("Oracle execution succeeded")

            if case_ok:
                passed += 1
                ok(f"CASE {index} PASSED")
            else:
                fail(f"CASE {index} FAILED")

        except Exception as exc:
            fail(f"CASE {index} ERROR: {type(exc).__name__}: {exc}")

    print("\n" + "=" * 100)
    print(f"RESULT: {passed}/{total} cases passed")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
