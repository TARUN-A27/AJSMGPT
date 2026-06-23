import json
import time

from app.backend_logger import log_event, new_request_id, set_request_id
from app.oracle_client import run_safe_select
from app.schema_search import search_schema_context, build_compact_context_text
from app.sql_generator_v2 import generate_select_sql_v2


def _safe_preview(value, limit=1200):
    text = json.dumps(value, default=str, ensure_ascii=False)
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def _safe_error_message(exc: Exception) -> str:
    error_text = str(exc)

    if "Invalid datatype comparison" in error_text:
        return (
            "I could not safely execute this query because the generated SQL used an invalid datatype comparison. "
            + error_text
        )

    if "Invalid LIKE comparison" in error_text:
        return (
            "I could not safely execute this query because the generated SQL used LIKE on a non-text column. "
            + error_text
        )

    if "Only SELECT statements are allowed" in error_text:
        return "This request was blocked because only SELECT queries are allowed."

    if "ORA-01722" in error_text:
        return (
            "Oracle rejected the query because of an invalid number comparison. "
            "This usually means a NUMBER column was compared with text."
        )

    if "ORA-" in error_text:
        return "Oracle could not execute the generated query. Please try a simpler wording or check the backend log."

    if "JSON" in error_text:
        return "The model returned an invalid response format. Please try again."

    return "Backend could not process this question safely. Please check the backend log."


def _summarize_retrieved_context(items):
    summary = []

    for item in items:
        if isinstance(item, dict):
            payload = item.get("payload") or item.get("metadata") or item
            score = item.get("score")
        else:
            payload = getattr(item, "payload", {}) or {}
            score = getattr(item, "score", None)

        schema = (
            payload.get("schema")
            or payload.get("owner")
            or payload.get("table_schema")
        )

        table = (
            payload.get("table")
            or payload.get("table_name")
            or payload.get("name")
        )

        full_table = (
            payload.get("full_table_name")
            or payload.get("qualified_table")
            or payload.get("table_ref")
        )

        if not schema and not table and full_table and "." in full_table:
            schema, table = full_table.split(".", 1)

        summary.append({
            "score": score,
            "type": payload.get("type", "table"),
            "schema": schema,
            "table": table,
            "title": payload.get("title"),
            "business_terms": payload.get("business_terms") or payload.get("aliases"),
            "description": (
                payload.get("description")
                or payload.get("alias_notes")
                or payload.get("text")
                or payload.get("content")
            ),
        })

    return summary


def answer_question(question: str) -> dict:
    request_id = new_request_id()
    set_request_id(request_id)

    start_time = time.time()

    log_event(
        request_id,
        "question_received",
        "User question received",
        question=question,
    )

    try:
        log_event(
            request_id,
            "qdrant_retrieval_start",
            "Searching schema/business context from Qdrant",
            question=question,
            limit=8,
        )

        retrieved_context = search_schema_context(question, limit=8)
        compact_context = build_compact_context_text(retrieved_context)

        log_event(
            request_id,
            "qdrant_retrieval_complete",
            "Qdrant context retrieved",
            retrieved_count=len(retrieved_context),
            retrieved_summary=_summarize_retrieved_context(retrieved_context),
            compact_context_preview=compact_context[:2500],
        )

        log_event(
            request_id,
            "sql_generation_start",
            "Starting SQL generation using retrieved context and Qwen",
        )

        sql_result = generate_select_sql_v2(question, limit=5)
        sql = sql_result["sql"]

        log_event(
            request_id,
            "sql_generation_complete",
            "SQL generated successfully",
            sql=sql,
            explanation=sql_result.get("explanation"),
            tables_used=sql_result.get("tables_used", []),
            relationships_used=sql_result.get("relationships_used", []),
            confidence=sql_result.get("confidence", 0.0),
            full_sql_result_preview=_safe_preview(sql_result),
        )

        log_event(
            request_id,
            "oracle_execution_start",
            "Starting safe Oracle SELECT execution",
            sql=sql,
        )

        db_result = run_safe_select(sql)

        elapsed_ms = int((time.time() - start_time) * 1000)

        log_event(
            request_id,
            "oracle_execution_complete",
            "Oracle SELECT executed successfully",
            row_count=db_result.get("row_count", 0),
            columns=db_result.get("columns", []),
            elapsed_ms=elapsed_ms,
        )

        return {
            "success": True,
            "request_id": request_id,
            "question": question,
            "sql": sql,
            "explanation": sql_result.get("explanation"),
            "tables_used": sql_result.get("tables_used", []),
            "relationships_used": sql_result.get("relationships_used", []),
            "confidence": sql_result.get("confidence", 0.0),
            "columns": db_result.get("columns", []),
            "rows": db_result.get("rows", []),
            "row_count": db_result.get("row_count", 0),
            "elapsed_ms": elapsed_ms,
        }

    except Exception as exc:
        elapsed_ms = int((time.time() - start_time) * 1000)
        safe_message = _safe_error_message(exc)

        log_event(
            request_id,
            "error",
            "Backend failed while processing question",
            question=question,
            error_type=type(exc).__name__,
            error=str(exc),
            safe_message=safe_message,
            elapsed_ms=elapsed_ms,
        )

        return {
            "success": False,
            "request_id": request_id,
            "question": question,
            "error": safe_message,
            "error_type": type(exc).__name__,
            "elapsed_ms": elapsed_ms,
        }


if __name__ == "__main__":
    result = answer_question("show supplier details")
    print(json.dumps(result, indent=2, default=str))
