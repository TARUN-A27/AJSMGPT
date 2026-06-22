import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app.sql_generator_v2 import generate_select_sql_v2

question = " ".join(sys.argv[1:])
result = generate_select_sql_v2(question, limit=5)

print(json.dumps(result, indent=2))
