import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app.query_engine import answer_question

if len(sys.argv) < 2:
    print("Usage: python scripts/ask_one.py \"your question\"")
    sys.exit(1)

question = " ".join(sys.argv[1:])
result = answer_question(question)

print("QUESTION:", result["question"])
print("SQL:", result["sql"])
print("TABLES:", result["tables_used"])
print("COLUMNS:", result["columns"])
print("ROW COUNT:", result["row_count"])
print("\nFIRST 10 ROWS:")
for row in result["rows"][:10]:
    print(row)
