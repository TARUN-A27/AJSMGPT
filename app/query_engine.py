import json
import time

from app.backend_logger import log_event, new_request_id
from app.sql_generator_v2 import generate_select_sql_v2
from app.oracle_client import run_safe_select


def answer_question(question: str) -> dict:
    request_id = new_request_id()
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
            "sql_generation_start",
            "Starting SQL generation using Qdrant context and Qwen",
        )

        sql_result = generate_select_sql_v2(question, limit=5)

        sql = sql_result["sql"]

        log_event(
            request_id,
            "sql_generation_complete",
            "SQL generated and validated",
            sql=sql,
            tables_used=sql_result.get("tables_used", []),
            relationships_used=sql_result.get("relationships_used", []),
            confidence=sql_result.get("confidence", 0.0),
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

        log_event(
            request_id,
            "error",
            "Backend failed while processing question",
            question=question,
            error_type=type(exc).__name__,
            error=str(exc),
            elapsed_ms=elapsed_ms,
        )

        raise


if __name__ == "__main__":
    result = answer_question("show supplier details")
    print(json.dumps(result, indent=2, default=str))
