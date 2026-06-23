import json
import os
import re
from dotenv import load_dotenv

from app.ollama_client import chat_with_qwen
from app.schema_search import build_compact_context_text, search_schema_context
from app.sql_column_validator import validate_sql_columns
from app.sql_safety import validate_select_only
from app.business_template_engine import match_business_template

load_dotenv()

ORACLE_SCHEMAS = [
    schema.strip().upper()
    for schema in os.getenv("ORACLE_SCHEMAS", "INVENTORY").split(",")
    if schema.strip()
]

ALLOWED_SCHEMA_LIST = ", ".join(ORACLE_SCHEMAS)

SYSTEM_PROMPT = f"""
You are AJSMGPT SQL Generator V2.

CRITICAL DATABASE SAFETY RULES:
1. Generate Oracle SELECT queries only.
2. Never generate INSERT, UPDATE, DELETE, MERGE, DROP, ALTER, TRUNCATE, CREATE, COMMIT, ROLLBACK, EXEC, CALL, BEGIN, DECLARE, or any PLSQL block.
3. Use only schemas from the allowed set: {ALLOWED_SCHEMA_LIST}.
4. Always use full schema.table names.
5. Avoid SELECT * unless the user explicitly asks for all columns.
6. Use relationship metadata for joins when available.
7. Return only valid JSON with the required fields.

CRITICAL COLUMN RULES:
8. Use ONLY table names and column names that are shown in the provided schema context.
9. Never invent columns.
10. Never assume common columns like ID, ORDERID, SUPPLIERID, TOTALAMOUNT unless they are explicitly present in the schema context.
11. If unsure about available columns, select only clearly available columns from the context.
12. For INVENTORY.PURCHASEORDER, prefer known columns like ORDERNO, ORDERDATE, ITEM_CODE, SUP_CODE, QTY, STATUS when available.
"""

JSON_TEMPLATE = {
    "question": "",
    "sql": "",
    "explanation": "",
    "tables_used": [],
    "relationships_used": [],
    "confidence": 0.0,
}

BLOCKED_KEYWORDS = [
    "INSERT",
    "UPDATE",
    "DELETE",
    "MERGE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "CREATE",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT",
    "EXEC",
    "EXECUTE",
    "CALL",
    "BEGIN",
    "DECLARE",
]


def extract_json(text: str) -> dict:
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"No JSON object found in model response: {text}")
        return json.loads(match.group(0))


def parse_tables_from_sql(sql: str) -> list[str]:
    pattern = r"\b(?:" + "|".join(re.escape(schema) for schema in ORACLE_SCHEMAS) + r")\.[A-Z0-9_]+\b"
    matches = re.findall(pattern, sql.upper())
    unique = []
    for match in matches:
        if match not in unique:
            unique.append(match)
    return unique


def derive_relationships_used(sql: str, context_results: list[dict]) -> list[str]:
    upper_sql = sql.upper()
    relationships = []

    for item in context_results:
        if item.get("type") != "relationship":
            continue

        constraint_name = item.get("constraint_name")
        from_schema = item.get("schema")
        from_table = item.get("table")
        to_table = None
        to_schema = None

        text = (item.get("text") or "").upper()

        # Attempt to infer the referenced tables from relationship text.
        full_refs = re.findall(r"\b([A-Z]+\.[A-Z0-9_]+)\b", text)
        if full_refs:
            if any(ref in upper_sql for ref in full_refs):
                if constraint_name and constraint_name not in relationships:
                    relationships.append(constraint_name)
                continue

        if from_schema and from_table and f"{from_schema}.{from_table}".upper() in upper_sql:
            if constraint_name and constraint_name not in relationships:
                relationships.append(constraint_name)

    return relationships


def build_prompt(question: str, context_text: str) -> str:
    return f"""
User question:
{question}

Relevant schema context:
{context_text}

Required output:
Return only valid JSON with the following fields:
- question: the original user question
- sql: the generated Oracle SELECT statement
- explanation: why this SQL was generated and which context items were used
- tables_used: list of full schema.table names used in the SQL
- relationships_used: list of foreign key constraint names used in the SQL
- confidence: a number from 0.0 to 1.0 reflecting generation confidence

Important instructions:
- Only generate one SELECT statement.
- Use full schema.table names from the allowed schemas.
- Do not use SELECT * unless the user explicitly asks for all columns.
- Use relationship metadata to connect tables when appropriate.
- If a join relationship is not shown in context, do not invent the join.
- Use ONLY table names and column names shown in the schema context.
- Never invent columns.
- Do not assume common columns like ID, ORDERID, SUPPLIERID, TOTALAMOUNT, NAME, ADDRESS, CONTACT, EMAIL unless they are explicitly shown in context.
- If exact columns are unclear, select only clearly available columns from the context.
- Do not include any SQL outside the SELECT statement.
- Do not include commentary outside the required JSON.
""".strip()

def normalize_response(parsed: dict) -> dict:
    result = {
        "question": parsed.get("question", ""),
        "sql": parsed.get("sql", ""),
        "explanation": parsed.get("explanation", ""),
        "tables_used": parsed.get("tables_used") or [],
        "relationships_used": parsed.get("relationships_used") or [],
        "confidence": parsed.get("confidence") or 0.0,
    }

    if not isinstance(result["tables_used"], list):
        result["tables_used"] = []
    if not isinstance(result["relationships_used"], list):
        result["relationships_used"] = []

    return result


def generate_select_sql_v2(question: str, limit: int = 6) -> dict:
    if not question or not question.strip():
        raise ValueError("Question cannot be empty.")

    template_result = match_business_template(question)
    if template_result:
        validated_sql = validate_select_only(template_result["sql"])
        validate_sql_columns(validated_sql)
        template_result["sql"] = validated_sql
        return template_result

    context_results = search_schema_context(question, limit=limit)
    context_text = build_compact_context_text(context_results, max_tables=6)

    user_prompt = build_prompt(question, context_text)
    raw_response = chat_with_qwen(SYSTEM_PROMPT, user_prompt)
    parsed = extract_json(raw_response)
    result = normalize_response(parsed)

    if not result["sql"]:
        raise ValueError(f"Model did not return SQL. Response: {raw_response}")

    validated_sql = validate_select_only(result["sql"])
    validate_sql_columns(validated_sql)
    result["sql"] = validated_sql

    if not result["question"]:
        result["question"] = question

    if not result["tables_used"]:
        result["tables_used"] = parse_tables_from_sql(validated_sql)

    if not result["relationships_used"]:
        result["relationships_used"] = derive_relationships_used(validated_sql, context_results)

    if not isinstance(result["confidence"], (int, float)):
        result["confidence"] = 0.0

    if result["confidence"] == 0.0:
        result["confidence"] = 0.65

    return result


if __name__ == "__main__":
    question = "show pending purchase orders"
    output = generate_select_sql_v2(question)
    print(json.dumps(output, indent=2))
