import json

from app.sql_generator_v2 import generate_select_sql_v2
from app.oracle_client import run_safe_select


def answer_question(question: str) -> dict:
    sql_result = generate_select_sql_v2(question, limit=5)

    sql = sql_result["sql"]

    db_result = run_safe_select(sql)

    return {
        "question": question,
        "sql": sql,
        "explanation": sql_result.get("explanation"),
        "tables_used": sql_result.get("tables_used", []),
        "relationships_used": sql_result.get("relationships_used", []),
        "confidence": sql_result.get("confidence", 0.0),
        "columns": db_result.get("columns", []),
        "rows": db_result.get("rows", []),
        "row_count": db_result.get("row_count", 0),
    }


if __name__ == "__main__":
    result = answer_question("show pending purchase orders")
    print(json.dumps(result, indent=2, default=str))
