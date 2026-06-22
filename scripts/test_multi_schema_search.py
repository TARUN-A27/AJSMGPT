import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app.schema_search import search_schema_context

QUESTIONS = [
    "show pending purchase orders",
    "show supplier details",
    "show employee department",
    "show insurance claim status",
    "show current stock of item",
    "show material requisition pending approval",
    "show vehicle movement details",
]

def label(item):
    if item.get("full_table_name"):
        return f"TABLE {item.get('full_table_name')}"
    payload_text = item.get("text") or ""
    return f"RELATIONSHIP {payload_text[:120]}"

def main():
    for question in QUESTIONS:
        print("\n" + "=" * 100)
        print("QUESTION:", question)
        print("=" * 100)

        results = search_schema_context(question, limit=8)

        for i, item in enumerate(results, start=1):
            print(f"{i}. {label(item)} | score={item.get('score'):.4f}")

if __name__ == "__main__":
    main()
