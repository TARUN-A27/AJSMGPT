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


def build_understanding_summary(understanding: dict | None) -> str:
    if not understanding:
        return ""

    requested_outputs = understanding.get("requested_outputs") or []
    outputs_text = ", ".join(requested_outputs) if requested_outputs else "none"
    entities = understanding.get("entities") or {}
    entities_text = ", ".join(f"{k}: {v}" for k, v in entities.items()) if entities else "none"

    return f"""
Question understanding summary:
- domain: {understanding.get('domain')}
- intent_hint: {understanding.get('intent_hint')}
- entities: {entities_text}
- requested_outputs: {outputs_text}
- detail_level: {understanding.get('detail_level')}
- needs_template: {understanding.get('needs_template')}
- needs_qwen: {understanding.get('needs_qwen')}
""".strip()


def _match_table_name(full_table_name: str, patterns: list[str]) -> bool:
    name = (full_table_name or "").upper()
    return any(re.search(pattern, name) for pattern in patterns)


def build_table_preference_text(question: str, context_results: list[dict]) -> str:
    ql = question.lower()
    hints = []
    if "document" in ql:
        document_tables = [item["full_table_name"] for item in context_results if item.get("full_table_name") and _match_table_name(item["full_table_name"], [r"DOCUMENT", r"ENQUIRYDOCUMENT"])]
        if document_tables:
            hints.append(
                "For document questions, prefer the document tables shown in context: "
                + ", ".join(document_tables)
                + ". Do not default to SCM.PARTYMASTER unless the user explicitly asks for supplier or party master details."
            )
        else:
            hints.append(
                "For document questions, prefer document-related tables and do not use SCM.PARTYMASTER unless explicitly asked for supplier or party master details."
            )

    if "vehicle movement" in ql:
        movement_tables = [item["full_table_name"] for item in context_results if item.get("full_table_name") and _match_table_name(item["full_table_name"], [r"TRN_VEHICLEMOVEMENT"])]
        if movement_tables:
            hints.append(
                "For vehicle movement questions, prefer these vehicle movement tables if available: "
                + ", ".join(movement_tables)
                + ". If a supplier or party code is present in the question, only apply it if a matching supplier/party column is clearly available in the retrieved schema context."
            )
        else:
            hints.append(
                "For vehicle movement questions, prefer vehicle movement tables and do not invent a supplier party filter unless a relevant column exists in the schema context."
            )

    if "current attendance" in ql:
        current_tables = [item["full_table_name"] for item in context_results if item.get("full_table_name") and _match_table_name(item["full_table_name"], [r"CURRENTATTENDANCE"])]
        if current_tables:
            hints.append(
                "For current attendance questions, prefer HRDNEW.CURRENTATTENDANCE over HRDNEW.CANTEENATTENDANCE when it is available in context."
            )

    if "department authentication" in ql or "authentication" in ql:
        hints.append(
            "For authentication questions, prefer HRDNEW.HODDEPTAUTHENTICATION when it is shown in the retrieved schema context."
        )

    return "\n".join(hints)


def prioritize_context_results(question: str, context_results: list[dict]) -> list[dict]:
    ql = question.lower()
    preferred_patterns = []

    if "document" in ql:
        preferred_patterns.extend([r"DOCUMENT", r"ENQUIRYDOCUMENT"])
    if "vehicle movement" in ql:
        preferred_patterns.append(r"TRN_VEHICLEMOVEMENT")
    if "current attendance" in ql:
        preferred_patterns.append(r"CURRENTATTENDANCE")
    if "department authentication" in ql or "authentication" in ql:
        preferred_patterns.append(r"HODDEPTAUTHENTICATION")

    if not preferred_patterns:
        return context_results

    def score(item: dict) -> tuple[int, float]:
        name = (item.get("full_table_name") or "").upper()
        bonus = 0
        for pattern in preferred_patterns:
            if re.search(pattern, name):
                bonus += 100
        return (bonus, item.get("score", 0) or 0)

    return sorted(context_results, key=score, reverse=True)


def build_prompt(question: str, context_text: str, understanding: dict | None = None, context_results: list[dict] | None = None) -> str:
    understanding_text = build_understanding_summary(understanding)
    preferred_text = build_table_preference_text(question, context_results or [])
    return f"""
User question:
{question}

Relevant schema context:
{context_text}

{understanding_text}

{preferred_text}

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
- If requested_outputs contains supplier_company_name, include supplier/company name only if it exists in context.
- For document questions, prefer document-related tables shown in the retrieved schema context and do not use SCM.PARTYMASTER unless explicitly asked for supplier or party master details.
- For vehicle movement questions, prefer vehicle movement tables and only use supplier/party filters if the exact supplier/party column exists in the context.
- For current attendance questions, prefer HRDNEW.CURRENTATTENDANCE over HRDNEW.CANTEENATTENDANCE when current attendance is available.
- For department authentication questions, prefer HRDNEW.HODDEPTAUTHENTICATION when available in the retrieved schema context.
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


def generate_select_sql_v2(question: str, limit: int = 6, understanding: dict | None = None) -> dict:
    if not question or not question.strip():
        raise ValueError("Question cannot be empty.")

    template_result = match_business_template(question)
    if template_result:
        validated_sql = validate_select_only(template_result["sql"])
        validate_sql_columns(validated_sql)
        template_result["sql"] = validated_sql
        return template_result

    collections = None
    if understanding:
        collections = understanding.get("collections")

    context_results = search_schema_context(question, limit=limit, collections=collections)
    context_text = build_compact_context_text(context_results, max_tables=6)

    user_prompt = build_prompt(question, context_text, understanding)
    raw_response = chat_with_qwen(SYSTEM_PROMPT, user_prompt)

    # Normalize model text: strip common markdown fences before extracting JSON
    raw_response_clean = re.sub(r"`{3,}.*?\n", "", raw_response, flags=re.DOTALL)
    raw_response_clean = raw_response_clean.strip()

    parsed = extract_json(raw_response_clean)
    result = normalize_response(parsed)

    if not result["sql"]:
        raise ValueError(f"Model did not return SQL. Response: {raw_response_clean}")

    # Clean SQL: remove markdown fences and trailing semicolon
    def _clean_sql_text(s: str) -> str:
        if not s:
            return s
        s = s.strip()
        # remove ``` fences
        s = re.sub(r"^```(?:sql)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```$", "", s)
        # remove any leading/trailing backticks
        s = s.strip('`')
        s = s.strip()
        # remove trailing semicolon
        if s.endswith(";"):
            s = s[:-1].strip()
        return s

    cleaned_sql = _clean_sql_text(result["sql"])

    # Ensure query begins with SELECT or WITH
    if not cleaned_sql.upper().lstrip().startswith(("SELECT ", "WITH ")):
        raise ValueError(f"Model returned non-SELECT SQL. SQL: {cleaned_sql}")

    validated_sql = validate_select_only(cleaned_sql)
    validate_sql_columns(validated_sql)
    result["sql"] = validated_sql

    # include retrieved schema info
    result["retrieved_schema"] = [item.get("full_table_name") for item in context_results]
    # mark source
    result["source"] = result.get("source") or "qwen_schema_fallback"

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
