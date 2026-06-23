from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

"""Run layman evaluation questions against the query engine.

Usage: ./venv/bin/python3 scripts/test_layman_questions.py
"""
import json
from pathlib import Path

from app import query_engine

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_FILE = ROOT / "data" / "evaluation_questions.json"


def run():
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        cases = json.load(f)

    total = 0
    passed = 0
    for case in cases:
        q = case.get("question")
        expected = case.get("expected_intent")
        must_contain = case.get("must_contain", [])
        execute = case.get("execute", False)

        print("\n----")
        print(f"Question: {q}")
        total += 1
        try:
            resp = query_engine.answer_question(q)
        except Exception as e:
            print("Error calling answer_question:", e)
            continue

        intent = resp.get("intent") or resp.get("matched_intent") or resp.get("template", {}).get("intent")
        sql = resp.get("sql") or resp.get("generated_sql") or ""
        rows = resp.get("rows")

        ok_intent = expected is None or (intent and expected.lower() in intent.lower())
        ok_contains = all((token.upper() in sql.upper()) for token in must_contain)

        passed_case = ok_intent and ok_contains
        if passed_case:
            passed += 1
            status = "PASS"
        else:
            status = "FAIL"

        print(f"Matched intent: {intent}")
        print(f"Expected intent: {expected}")
        print(f"SQL present: {bool(sql)}")
        if sql:
            print("SQL:\n" + sql)
        if rows is not None:
            print(f"Rows returned: {len(rows)}")
        print(f"Result: {status}")

    print("\n==== Summary ====")
    print(f"Passed {passed}/{total} cases")


if __name__ == "__main__":
    run()
