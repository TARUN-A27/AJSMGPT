from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.rasa_nlu_client import understand_with_rasa_duckling

try:
    from app.query_planner import plan_query
except Exception as exc:
    print("ERROR: Could not import app.query_planner.plan_query")
    print(type(exc).__name__, exc)
    raise


QUESTIONS = [
    "last supplier for mouse",
    "mouse last purchased supplier name",
    "pending MRS for keyboard",
    "latest GRN for supplier 800967 in 2025",
    "show goods receipt for supplier 800967",
    "cash bank entries for party ODUT001",
    "camera details",
    "show camera ip details",
    "attendance for empcode 165224",
    "last 3 purchases of keyboard in 2025",
    "barcode chromo label last purchase",
]


def safe_plan(question: str):
    try:
        return plan_query(question)
    except TypeError:
        return plan_query(question=question)


def main() -> int:
    failed = 0

    for q in QUESTIONS:
        print("-" * 120)
        print("QUESTION:", q)

        nlp = understand_with_rasa_duckling(q)
        print("RASA_SUCCESS:", nlp.success)
        print("RASA_INTENT:", nlp.intent)
        print("RASA_CONFIDENCE:", round(nlp.confidence, 4))
        print("RASA_ENTITIES:", json.dumps(nlp.entities, ensure_ascii=False))

        try:
            plan = safe_plan(q)
            print("PLANNER_TYPE:", type(plan).__name__)

            if isinstance(plan, dict):
                print("PLANNER_SOURCE:", plan.get("source"))
                print("PLANNER_INTENT:", plan.get("intent"))
                print("PLANNER_MODULE:", plan.get("module"))
                print("PLANNER_ENTITIES:", json.dumps(plan.get("entities", {}), ensure_ascii=False))
                print("PLANNER_SUCCESS:", plan.get("success"))
            else:
                print("PLANNER_RESULT:", plan)

        except Exception as exc:
            failed += 1
            print("PLANNER_ERROR:", type(exc).__name__, exc)

    print("=" * 120)
    print("TOTAL:", len(QUESTIONS))
    print("FAILED:", failed)
    print("PASSED:", len(QUESTIONS) - failed)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
