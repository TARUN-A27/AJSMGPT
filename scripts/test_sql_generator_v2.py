import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app.sql_generator_v2 import generate_select_sql_v2

QUESTIONS = [
    "show pending purchase orders",
    "show supplier details",
    "show employee department",
    "show insurance claim status",
    "show current stock of item",
    "show material requisition pending approval",
    "show vehicle movement details",
]


def main():
    for question in QUESTIONS:
        print("\n" + "=" * 100)
        print("QUESTION:", question)
        print("=" * 100)

        try:
            result = generate_select_sql_v2(question)
            print(json.dumps(result, indent=2))
        except Exception as exc:
            print("ERROR:", str(exc))


if __name__ == "__main__":
    main()
