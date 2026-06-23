from __future__ import annotations

import json
import time
from typing import Any

from app.backend_logger import log_event, new_request_id, set_request_id
from app.business_template_engine import match_business_template
from app.oracle_client import run_safe_select
from app.question_understanding import understand_question
from app.answer_formatter import format_answer
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

    if "Invalid column" in error_text:
        return error_text

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


def answer_question(question: str) -> dict[str, Any]:
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
        understanding = understand_question(question)

        log_event(
            request_id,
            "question_understanding",
            "Question understanding generated",
            understanding=understanding,
        )

        template_result = match_business_template(question)

        if template_result:
            sql_result = template_result
            sql = sql_result["sql"]

            log_event(
                request_id,
                "business_template_matched",
                "Business template matched; skipping Qdrant and Qwen",
                source=sql_result.get("source"),
                intent=sql_result.get("intent"),
                confidence=sql_result.get("confidence"),
                parameters=sql_result.get("parameters"),
                sql=sql,
                tables_used=sql_result.get("tables_used", []),
                relationships_used=sql_result.get("relationships_used", []),
                full_sql_result_preview=_safe_preview(sql_result),
            )

        else:
            log_event(
                request_id,
                "sql_generation_start",
                "No business template matched; starting Qdrant/Qwen SQL generation",
            )

            sql_result = generate_select_sql_v2(question, limit=5, understanding=understanding)
            sql = sql_result["sql"]

        log_event(
            request_id,
            "sql_generation_complete",
            "SQL generated successfully",
            source=sql_result.get("source", "qwen"),
            intent=sql_result.get("intent"),
            parameters=sql_result.get("parameters"),
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

        result = {
            "success": True,
            "request_id": request_id,
            "question": question,
            "sql": sql,
            "explanation": sql_result.get("explanation"),
            "tables_used": sql_result.get("tables_used", []),
            "relationships_used": sql_result.get("relationships_used", []),
            "confidence": sql_result.get("confidence", 0.0),
            "source": sql_result.get("source", "qwen"),
            "intent": sql_result.get("intent"),
            "parameters": sql_result.get("parameters"),
            "columns": db_result.get("columns", []),
            "rows": db_result.get("rows", []),
            "row_count": db_result.get("row_count", 0),
            "elapsed_ms": elapsed_ms,
            "understanding": understanding,
        }

        result["answer"] = format_answer(result)

        return result

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
