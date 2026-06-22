import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from app.sql_generator_v2 import generate_select_sql_v2

question = "show pending purchase orders"

result = generate_select_sql_v2(question)

print(json.dumps(result, indent=2))
