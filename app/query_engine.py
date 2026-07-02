from __future__ import annotations

import json
import time
from typing import Any

from app.backend_logger import log_event, new_request_id, set_request_id
from app.business_template_engine import match_business_template
from app.purchase_analytics_router import match_purchase_analytics_template
from app.mrs_template_router import match_mrs_template
from app.date_filter_engine import apply_date_filter_to_sql, strip_date_filter_phrases
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


def _resolver_enriched_question(question: str, resolved_values: dict[str, Any]) -> str:
    """
    Stage 2 resolver integration.

    This does not generate SQL directly.
    It only replaces a user phrase with the best real DB value before router matching.

    Example:
        barcode chromo label last purchase
        -> BARCODE CHROMO LABLES 40 X 25MM last purchase
    """
    import re

    q = question or ""
    q_upper = q.upper()

    business_words = {
        "PURCHASE", "SUPPLY", "SUPPLIER", "MATERIAL", "ITEM",
        "QTY", "QUANTITY", "GRN", "MRS", "ISSUE", "STOCK",
        "DOCUMENT", "CASHBANK", "VOUCHER", "PARTY", "EMPLOYEE",
        "EMPCODE", "ATTENDANCE", "VEHICLE", "CAMERA",
    }

    results = resolved_values.get("results") or []
    semantic_queries = resolved_values.get("semantic_queries") or []

    if not results:
        return q

    # For exact code questions like ODUT001, keep original question.
    # Existing fallbacks already use the exact code safely.
    if resolved_values.get("exact_terms"):
        return q

    # Choose the best high-confidence real DB value.
    best = None
    for item in results:
        entity_type = item.get("entity_type")
        score = float(item.get("score") or 0)
        value = str(item.get("resolved_value") or "").strip()

        if not value:
            continue

        if entity_type in {"material", "supplier_or_party", "department", "unit", "vehicle", "employee"} and score >= 80:
            best = item
            break

    if not best:
        return q

    resolved_value = str(best.get("resolved_value") or "").strip()
    if not resolved_value:
        return q

    # Try replacing the clean semantic phrase first.
    for phrase in semantic_queries:
        phrase = str(phrase or "").strip()
        if not phrase:
            continue

        phrase_words = set(phrase.upper().split())
        if phrase_words and phrase_words.issubset(business_words):
            continue

        pattern = re.compile(re.escape(phrase), flags=re.IGNORECASE)
        if pattern.search(q):
            return pattern.sub(resolved_value, q, count=1)

    # Fallback: append resolved value as a router hint.
    return f"{q} resolved value {resolved_value}"




def _best_resolved_material(resolved_values: dict[str, Any]) -> str | None:
    """
    Pick the best material value found by hybrid resolver.
    """
    for item in resolved_values.get("results", []) or []:
        if item.get("entity_type") != "material":
            continue

        score = float(item.get("score") or 0)
        value = str(item.get("resolved_value") or "").strip()

        if value and score >= 80:
            return value

    return None


def _sql_text_literal(value: str) -> str:
    return str(value or "").replace("'", "''").strip()


def _apply_resolved_material_to_sql(sql: str, resolved_values: dict[str, Any]) -> str:
    """
    If resolver found an exact material value, preserve that value in router SQL.

    Example:
        Router produced:
            UPPER(INV.ITEM_NAME) LIKE '%BARCODE CHROMO LABLE 40 X 25MM%'

        Resolver found:
            BARCODE CHROMO LABLES 40 X 25MM

        Final SQL:
            UPPER(INV.ITEM_NAME) LIKE '%BARCODE CHROMO LABLES 40 X 25MM%'
    """
    import re

    if not sql:
        return sql

    material = _best_resolved_material(resolved_values)
    if not material:
        return sql

    sql_upper = sql.upper()

    # Only patch purchase item-name filters. Do not touch other modules.
    if "INV.ITEM_NAME" not in sql_upper:
        return sql

    material_sql = _sql_text_literal(material).upper()

    pattern = re.compile(
        r"UPPER\s*\(\s*INV\.ITEM_NAME\s*\)\s+LIKE\s+'%[^']*%'",
        flags=re.IGNORECASE,
    )

    patched_sql = pattern.sub(
        f"UPPER(INV.ITEM_NAME) LIKE '%{material_sql}%'",
        sql,
        count=1,
    )

    return patched_sql


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
                + " ORDER BY V.ENTRYDATE DESC NULLS LAST, V.ID DESC"
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
                + " ORDER BY INDATE DESC, INTIME DESC NULLS LAST"
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
                + " ORDER BY DOCDATE DESC NULLS LAST, ID DESC"
            ),
            "explanation": "Document details are stored in ADMIN.DOCUMENT.",
            "tables_used": ["ADMIN.DOCUMENT"],
            "relationships_used": [],
            "confidence": 0.9,
            "retrieved_schema": ["ADMIN.DOCUMENT"],
            "source": "qwen_schema_fallback",
        }

    return None


def _answer_question_core(question: str) -> dict[str, Any]:
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
        sql_question = strip_date_filter_phrases(question)

        resolved_values = {
            "question": sql_question,
            "exact_terms": [],
            "semantic_queries": [],
            "elapsed_seconds": 0,
            "results": [],
            "resolver_status": "not_run",
        }

        try:
            from app.hybrid_value_resolver import resolve_question_values

            resolved_values = resolve_question_values(sql_question, final_limit=10)
            resolved_values["resolver_status"] = "ok"

            log_event(
                request_id,
                "hybrid_value_resolver_complete",
                "Hybrid SQLite/Qdrant value resolver completed",
                resolved_values_preview=_safe_preview(resolved_values),
                elapsed_seconds=resolved_values.get("elapsed_seconds"),
                result_count=len(resolved_values.get("results", [])),
            )

        except Exception as resolver_exc:
            resolved_values = {
                "question": sql_question,
                "exact_terms": [],
                "semantic_queries": [],
                "elapsed_seconds": 0,
                "results": [],
                "resolver_status": "error",
                "error": f"{type(resolver_exc).__name__}: {resolver_exc}",
            }

            log_event(
                request_id,
                "hybrid_value_resolver_failed",
                "Hybrid value resolver failed; continuing without resolver",
                error=resolved_values["error"],
            )

        log_event(
            request_id,
            "question_understanding",
            "Question understanding generated",
            understanding=understanding,
            resolved_values_preview=_safe_preview(resolved_values),
        )

        router_question = _resolver_enriched_question(sql_question, resolved_values)

        if router_question != sql_question:
            log_event(
                request_id,
                "resolver_enriched_router_question",
                "Router question enriched using resolved database value",
                original_question=sql_question,
                router_question=router_question,
                resolved_values_preview=_safe_preview(resolved_values),
            )

        template_result = match_purchase_analytics_template(router_question) or match_mrs_template(router_question) or match_business_template(router_question)

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
                sql_result = _known_schema_fallback_sql(sql_question)
                if sql_result is None:
                    sql_result = generate_select_sql_v2(sql_question, limit=5, understanding=understanding)

                # Ensure fallback source label
                sql_result["source"] = sql_result.get("source") or "qwen_schema_fallback"
                sql = sql_result.get("sql")
                sql = apply_date_filter_to_sql(
                    sql,
                    question,
                    intent=sql_result.get("intent"),
                    tables_used=sql_result.get("tables_used"),
                )
                sql_result["sql"] = sql
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

        sql_before_resolved_material_patch = sql
        sql = _apply_resolved_material_to_sql(sql, resolved_values)
        sql_result["sql"] = sql

        if sql != sql_before_resolved_material_patch:
            log_event(
                request_id,
                "resolved_material_sql_patch_applied",
                "Applied exact resolved material value to generated SQL",
                before_sql=sql_before_resolved_material_patch,
                after_sql=sql,
                resolved_values_preview=_safe_preview(resolved_values),
            )

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

        sql = apply_date_filter_to_sql(
            sql,
            question,
            intent=sql_result.get("intent"),
            tables_used=sql_result.get("tables_used"),
        )
        sql_result["sql"] = sql

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
            "resolved_values": resolved_values,
            "router_question": router_question,
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


def answer_question(question: str, *args, **kwargs):
    """
    Public wrapper around the real query engine.
    Logs every user question to logs/user_questions.jsonl for future tuning.
    """
    import time
    from app.question_logger import log_question_event

    session_id = kwargs.pop("session_id", None) or "default"
    user_id = kwargs.pop("user_id", None)
    client_ip = kwargs.pop("client_ip", None)

    start = time.perf_counter()
    result = None

    try:
        result = _answer_question_core(question, *args, **kwargs)
        return result
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        log_question_event(
            question=question,
            result=None,
            elapsed_ms=elapsed_ms,
            session_id=session_id,
            user_id=user_id,
            client_ip=client_ip,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        if result is not None:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            try:
                log_question_event(
                    question=question,
                    result=result,
                    elapsed_ms=elapsed_ms,
                    session_id=session_id,
                    user_id=user_id,
                    client_ip=client_ip,
                )
            except Exception:
                # Logging must never break the main answer flow.
                pass

