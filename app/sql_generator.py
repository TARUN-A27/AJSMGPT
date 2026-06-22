import json
import re

from app.ollama_client import chat_with_qwen
from app.schema_search import search_schema_context, build_compact_context_text
from app.sql_safety import validate_select_only, add_oracle_row_limit


SYSTEM_PROMPT = """
You are AJSMGPT, a production Oracle SQL assistant for a textile company.

CRITICAL DATABASE SAFETY RULES:
1. Generate SELECT queries only.
2. Never generate INSERT, UPDATE, DELETE, MERGE, DROP, ALTER, TRUNCATE, CREATE, COMMIT, ROLLBACK, EXEC, CALL, BEGIN, or DECLARE.
3. Use only INVENTORY schema.
4. Always use full table names like INVENTORY.PURCHASEORDER.
5. Do not use ADMIN, HRDNEW, INSUR, or SCM.
6. Do not modify data.
7. Do not explain outside JSON.
8. If the user asks for data modification, return an error message in JSON.
9. Prefer simple SQL.
10. Use only columns that exist in the provided schema context.

Return only valid JSON in this format:
{
  "sql": "SELECT ...",
  "reason": "short reason"
}
"""


def extract_json(text: str) -> dict:
    """
    Extract JSON object from Qwen response.
    """

    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)

    if not match:
        raise ValueError(f"No JSON object found in model response: {text}")

    return json.loads(match.group(0))


def generate_select_sql(question: str) -> dict:
    """
    Generate SELECT SQL using Qdrant schema context + Qwen.
    Does not execute Oracle SQL.
    """

    schema_results = search_schema_context(question, limit=5)
    context_text = build_compact_context_text(schema_results)

    user_prompt = f"""
User question:
{question}

Relevant INVENTORY schema context:
{context_text}

Generate one Oracle SELECT query only.

Important:
- Use INVENTORY schema only.
- Use full table names.
- Do not use SELECT * unless necessary.
- If the meaning of "pending" is unclear, use available status/pending columns from context.
- Return only JSON.
"""

    raw_response = chat_with_qwen(SYSTEM_PROMPT, user_prompt)
    parsed = extract_json(raw_response)

    sql = parsed.get("sql")
    reason = parsed.get("reason", "")

    if not sql:
        raise ValueError(f"Model did not return SQL. Response: {parsed}")

    validated_sql = validate_select_only(sql)
    limited_sql = add_oracle_row_limit(validated_sql)

    return {
        "question": question,
        "sql": validated_sql,
        "limited_sql": limited_sql,
        "reason": reason,
        "schema_tables": [
            item.get("full_table_name")
            for item in schema_results
        ],
        "raw_model_response": raw_response
    }


if __name__ == "__main__":
    question = "show pending purchase orders"

    result = generate_select_sql(question)

    print("Question:")
    print(result["question"])

    print("\nRelevant tables:")
    for table in result["schema_tables"]:
        print("-", table)

    print("\nGenerated SQL:")
    print(result["sql"])

    print("\nSafe limited SQL:")
    print(result["limited_sql"])

    print("\nReason:")
    print(result["reason"])
