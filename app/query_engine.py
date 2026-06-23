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



def _known_schema_fallback_sql(question: str) -> dict[str, Any] | None:
    """
    Deterministic fallback hints for common admin/HR questions.
    These still go through run_safe_select(), so SQL safety is preserved.
    """
    import re

    q = question.lower()

    def extract_code(patterns):
        for pattern in patterns:
            m = re.search(pattern, question, flags=re.IGNORECASE)
            if m:
                return m.group(1).strip().upper()
        return None

    party_code = extract_code([
        r"\bparty\s+code\s+([A-Za-z0-9_-]+)",
        r"\bparty\s+([A-Za-z0-9_-]+)",
        r"\bsupplier\s+([A-Za-z0-9_-]+)",
        r"\bsupplier\s+code\s+([A-Za-z0-9_-]+)",
        r"\bvendor\s+([A-Za-z0-9_-]+)",
        r"\bvendor\s+code\s+([A-Za-z0-9_-]+)",
    ])

    empcode = extract_code([
        r"\bempcode\s+([0-9]+)",
        r"\bemployee\s+([0-9]+)",
        r"\bstaff\s+([0-9]+)",
    ])

    if "camera" in q and "ip" in q:
        return {
            "question": question,
            "sql": (
                "SELECT ID, CAMERAIP, CAMERA_NAME, MILLCODE, ACTIVESTATUS, UNIT, HO_GODOWN, ANPR_STATUS "
                "FROM ADMIN.CAMERAIP"
            ),
            "explanation": "Camera IP details are stored in ADMIN.CAMERAIP.",
            "tables_used": ["ADMIN.CAMERAIP"],
            "relationships_used": [],
            "confidence": 0.95,
            "retrieved_schema": ["ADMIN.CAMERAIP"],
            "source": "qwen_schema_fallback",
        }

    if "cashbank" in q or "voucher" in q:
        where_clause = f" WHERE UPPER(PARTYCODE) = '{party_code}'" if party_code else ""
        return {
            "question": question,
            "sql": (
                "SELECT VOCNO, VOCDATE, VOCTYPE, PARTYCODE, GSTPARTYCODE, ACCODE, CTCODE, "
                "AMOUNT, DEBIT, CREDIT, NARR, REF, BILLNO, BILLDATE, USERCODE, CREATIONDATETIME "
                "FROM ADMIN.CASHBANK"
                + where_clause
            ),
            "explanation": "Cashbank/voucher details are stored in ADMIN.CASHBANK.",
            "tables_used": ["ADMIN.CASHBANK"],
            "relationships_used": [],
            "confidence": 0.9,
            "retrieved_schema": ["ADMIN.CASHBANK"],
            "source": "qwen_schema_fallback",
        }

    if "vehicle" in q and "movement" in q:
        where_clause = f" WHERE UPPER(V.SUP_CODE) = '{party_code}'" if party_code else ""
        return {
            "question": question,
            "sql": (
                "SELECT V.ID, V.VEHICLENO, V.DRIVERNAME, V.SUP_CODE, V.MILLCODE, V.ENTRYDATE, "
                "V.USED_STATUS, V.USEDTABLE, V.USEDTABLE_ID, V.STATUS, V.USED_DATE, "
                "D.RECORDTIME, D.STARTTIME, D.ENDTIME, D.FILENAME, D.IMG_FILENAME "
                "FROM ADMIN.TRN_VEHICLEMOVEMENT V "
                "LEFT JOIN ADMIN.TRN_VEHICLEMOVEMENT_DETAILS D ON D.TRANSID = V.ID"
                + where_clause
            ),
            "explanation": "Vehicle movement header is in ADMIN.TRN_VEHICLEMOVEMENT and image/time details are in ADMIN.TRN_VEHICLEMOVEMENT_DETAILS.",
            "tables_used": ["ADMIN.TRN_VEHICLEMOVEMENT", "ADMIN.TRN_VEHICLEMOVEMENT_DETAILS"],
            "relationships_used": ["ADMIN.TRN_VEHICLEMOVEMENT_DETAILS.TRANSID = ADMIN.TRN_VEHICLEMOVEMENT.ID"],
            "confidence": 0.9,
            "retrieved_schema": ["ADMIN.TRN_VEHICLEMOVEMENT", "ADMIN.TRN_VEHICLEMOVEMENT_DETAILS"],
            "source": "qwen_schema_fallback",
        }

    if "current attendance" in q:
        where_clause = f" WHERE EMPCODE = {empcode}" if empcode else ""
        return {
            "question": question,
            "sql": (
                "SELECT EMPCODE, INDATE, INTIME, OUTDATE, OUTTIME, LATETIME, STATUS, SHIFT, "
                "SALARYDAY, DEPTCODE, UNITCODE, WORKEDHRS, AUTHSTATUS, APPROVESTATUS, "
                "AOAUTHSTATUS, ONDUTYSTATUS, CAMERAFLAG "
                "FROM HRDNEW.CURRENTATTENDANCE"
                + where_clause
            ),
            "explanation": "Current attendance details are stored in HRDNEW.CURRENTATTENDANCE.",
            "tables_used": ["HRDNEW.CURRENTATTENDANCE"],
            "relationships_used": [],
            "confidence": 0.95,
            "retrieved_schema": ["HRDNEW.CURRENTATTENDANCE"],
            "source": "qwen_schema_fallback",
        }

    if "department authentication" in q or "dept authentication" in q:
        where_clause = f" WHERE EMPCODE = {empcode}" if empcode else ""
        return {
            "question": question,
            "sql": (
                "SELECT EMPCODE, DEPTCODE, UNITCODE, AOSTATUS, AUTHSTATUS, COMPADJUSTSTATUS, OLDEMPCODE "
                "FROM HRDNEW.HODDEPTAUTHENTICATION"
                + where_clause
            ),
            "explanation": "Department authentication details are stored in HRDNEW.HODDEPTAUTHENTICATION.",
            "tables_used": ["HRDNEW.HODDEPTAUTHENTICATION"],
            "relationships_used": [],
            "confidence": 0.95,
            "retrieved_schema": ["HRDNEW.HODDEPTAUTHENTICATION"],
            "source": "qwen_schema_fallback",
        }

    if "document" in q:
        where_clause = f" WHERE UPPER(PARTYCODE) = '{party_code}'" if party_code else ""
        return {
            "question": question,
            "sql": (
                "SELECT ID, PARTYCODE, DEPTCODE, CATECODE, REFNO, DOCDATE, DOCNAME, FILEFORMAT, "
                "ENTRYDATE, ENTRYUSER, IAAUTH, APPROVEUSER, ENQUIRYDOCUMENTSTATUS, BLNUMBER, "
                "BLDATE, INVNO, INVDATE "
                "FROM ADMIN.DOCUMENT"
                + where_clause
            ),
            "explanation": "Document details are stored in ADMIN.DOCUMENT.",
            "tables_used": ["ADMIN.DOCUMENT"],
            "relationships_used": [],
            "confidence": 0.9,
            "retrieved_schema": ["ADMIN.DOCUMENT"],
            "source": "qwen_schema_fallback",
        }

    return None


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

            try:
                sql_result = _known_schema_fallback_sql(question)
                if sql_result is None:
                    sql_result = generate_select_sql_v2(question, limit=5, understanding=understanding)

                # Ensure fallback source label
                sql_result["source"] = sql_result.get("source") or "qwen_schema_fallback"
                sql = sql_result.get("sql")
            except Exception as gen_exc:
                # Do not crash; return a safe user-facing message when SQL cannot be generated safely
                log_event(
                    request_id,
                    "sql_generation_failed",
                    "Qwen/schema fallback could not produce a safe SELECT",
                    error=str(gen_exc),
                )

                return {
                    "success": False,
                    "request_id": request_id,
                    "question": question,
                    "error": "I could not safely create a SELECT query for this question. Please ask with a table/module name or more details.",
                    "error_type": type(gen_exc).__name__,
                    "source": "qwen_schema_fallback",
                }

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
            "retrieved_schema": sql_result.get("retrieved_schema", []),
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
