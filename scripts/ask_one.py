import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app.query_engine import answer_question


if len(sys.argv) < 2:
    print('Usage: python scripts/ask_one.py "your question"')
    raise SystemExit(1)

question = " ".join(sys.argv[1:])
result = answer_question(question)

if not result.get("success"):
    print("QUESTION:", question)
    print("REQUEST ID:", result.get("request_id"))
    print("ERROR:", result.get("error"))
    print("ERROR TYPE:", result.get("error_type"))
    raise SystemExit(0)

print("QUESTION:", result["question"])
print("SQL:", result["sql"])
print("TABLES:", result.get("tables_used", []))
print("COLUMNS:", result.get("columns", []))
print("ROW COUNT:", result.get("row_count", 0))

print("\nFIRST 10 ROWS:")
for row in result.get("rows", [])[:10]:
    print(row)
