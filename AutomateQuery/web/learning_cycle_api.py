from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import FileResponse, JSONResponse


router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUTOMATE_DIR = PROJECT_ROOT / "AutomateQuery"
REPORTS_DIR = AUTOMATE_DIR / "reports"
TEMPLATE_PATH = AUTOMATE_DIR / "web" / "templates" / "learning_cycle" / "index.html"

SUMMARY_JSON = REPORTS_DIR / "learning_cycle" / "latest_learning_cycle_summary.json"
SUMMARY_MD = REPORTS_DIR / "learning_cycle" / "latest_learning_cycle_summary.md"
LEARNING_QUEUE_JSON = REPORTS_DIR / "learning_queue" / "latest_learning_queue.json"
LEARNING_QUEUE_MD = REPORTS_DIR / "learning_queue" / "latest_learning_queue.md"
RETEST_JSON = REPORTS_DIR / "retest_from_logs" / "latest_all_distinct_retest.json"
DIAGNOSIS_JSON = REPORTS_DIR / "question_diagnosis" / "latest_diagnosis.json"
DIAGNOSIS_MD = REPORTS_DIR / "question_diagnosis" / "latest_diagnosis.md"
CANDIDATE_QUERIES_JSON = REPORTS_DIR / "candidate_queries" / "latest_candidate_queries.json"
LATEST_CANDIDATES_JSON = REPORTS_DIR / "question_diagnosis" / "latest_candidates.json"
VERIFIED_CANDIDATES_JSON = REPORTS_DIR / "candidate_queries" / "verified_candidate_queries.json"
PENDING_CANDIDATES_JSON = REPORTS_DIR / "candidate_queries" / "pending_candidate_queries.json"
EXPECTED_ZERO_JSON = REPORTS_DIR / "question_diagnosis" / "expected_zero_candidates.json"
APPROVED_EXPECTED_ZERO_JSON = REPORTS_DIR / "approvals" / "approved_expected_zero_cases.json"
APPROVED_CANDIDATES_JSON = REPORTS_DIR / "approvals" / "approved_candidate_queries.json"
GENERATED_EVAL_JSON = REPORTS_DIR / "generated_eval_candidates.json"
REVIEWED_EVAL_JSON = REPORTS_DIR / "reviewed_eval_candidates.json"
APPROVED_EVAL_JSON = REPORTS_DIR / "approved_eval_tests.json"
APPROVED_EVAL_RUN_TXT = REPORTS_DIR / "approved_eval_test_run.txt"
REGRESSION_CASES_JSON = REPORTS_DIR / "eval_candidates" / "regression_cases.json"
REGRESSION_CASES_MD = REPORTS_DIR / "eval_candidates" / "regression_cases.md"
APPROVED_REGRESSION_JSONL = AUTOMATE_DIR / "evals" / "approved_regression_cases.jsonl"
APPROVED_REGRESSION_SEED_MD = REPORTS_DIR / "eval_candidates" / "approved_regression_seed.md"
DEFERRED_REGRESSION_JSONL = AUTOMATE_DIR / "evals" / "deferred_regression_cases.jsonl"
DEFERRED_REGRESSION_MD = REPORTS_DIR / "eval_candidates" / "deferred_regression_cases.md"
LATEST_REGRESSION_RUN_JSON = REPORTS_DIR / "regression_runs" / "latest_approved_regression_run.json"
LATEST_REGRESSION_PROGRESS_JSON = REPORTS_DIR / "regression_runs" / "latest_approved_regression_progress.json"
LATEST_REGRESSION_RUN_MD = REPORTS_DIR / "regression_runs" / "latest_approved_regression_run.md"

BUILD_REGRESSION_SCRIPT = AUTOMATE_DIR / "scripts" / "build_regression_candidates.py"
BUILD_APPROVED_REGRESSION_SCRIPT = AUTOMATE_DIR / "scripts" / "build_approved_regression_seed.py"
RUN_APPROVED_REGRESSION_SCRIPT = AUTOMATE_DIR / "scripts" / "run_approved_regression_cases.py"
BUILD_LEARNING_QUEUE_SCRIPT = AUTOMATE_DIR / "scripts" / "build_learning_queue.py"


RUN_LOCK = threading.Lock()
RUN_STATUS: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "message": "No run started yet.",
}


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default



def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
        except Exception:
            rows.append({"raw": line})
    return rows


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def as_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def file_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path), "modified": None, "size_bytes": 0}
    stat = path.stat()
    return {"exists": True, "path": str(path), "modified": stat.st_mtime, "size_bytes": stat.st_size}


def ensure_approval_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("[]", encoding="utf-8")


SQL_FALLBACK_KEYS = ("sql", "generated_sql", "sql_query", "final_sql", "executable_sql", "query")
SQL_CONTAINER_KEYS = ("data", "result", "response", "answer", "payload")


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, (int, float, bool)):
        text = str(value).strip()
        return text or None
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value).strip()
    return text or None


def _stringify_source(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("name", "route", "router", "source"):
            if key in value:
                text = _stringify_source(value.get(key))
                if text:
                    return text
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(value)
    return str(value)


def _find_sql_value(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None

    for key in SQL_FALLBACK_KEYS:
        value = _coerce_text(payload.get(key))
        if value:
            return value

    for key in SQL_CONTAINER_KEYS:
        nested = payload.get(key)
        if isinstance(nested, dict):
            value = _find_sql_value(nested)
            if value:
                return value

    return None


def _normalize_ask_response(question: str, raw_response: Any) -> dict[str, Any]:
    if not isinstance(raw_response, dict):
        raw_response = {"success": False, "error": "ask_response_was_not_a_dict", "data": raw_response}

    body = raw_response.get("data") if isinstance(raw_response.get("data"), dict) else raw_response

    body_success = body.get("success") if isinstance(body, dict) else None
    success = body_success if body_success is not None else raw_response.get("success")

    body_error = body.get("error") if isinstance(body, dict) else None
    error = body_error if body_error is not None else raw_response.get("error")

    sql = None
    if isinstance(body, dict):
        sql = _coerce_text(body.get("sql"))
    if not sql:
        sql = _find_sql_value(raw_response) or _find_sql_value(body)

    source = None
    intent = None
    row_count = None
    if isinstance(body, dict):
        source = _stringify_source(body.get("source"))
        intent = _coerce_text(body.get("intent"))
        row_count = body.get("row_count")

    extracted = {
        "success": bool(success) if success is not None else False,
        "source": source,
        "intent": intent,
        "row_count": row_count,
        "sql": sql,
        "error": error,
    }

    return {
        "ok": bool(extracted.get("success")),
        "question": question,
        "http_status": 200,
        "extracted": extracted,
        "raw_response": raw_response,
    }


def _normalize_question_tokens(question: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", (question or "").lower())
    stopwords = {"the", "for", "and", "in", "latest", "last", "show", "get", "find", "of", "to", "from", "with", "by", "on", "at", "a", "an", "or", "is", "are", "what", "when", "why", "how"}
    return {token for token in tokens if token not in stopwords and len(token) > 1}


def _fallback_from_question_logs(question: str) -> dict[str, Any] | None:
    log_path = PROJECT_ROOT / "logs" / "user_questions.jsonl"
    if not log_path.exists():
        return None

    normalized_question = (question or "").strip().lower()
    question_tokens = _normalize_question_tokens(question)
    best_match: tuple[int, dict[str, Any]] | None = None

    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue
        if not entry.get("success"):
            continue

        candidate_question = str(entry.get("question") or entry.get("resolved_question") or "").strip()
        candidate_tokens = _normalize_question_tokens(candidate_question)
        overlap = len(question_tokens & candidate_tokens)
        exact_match = normalized_question and candidate_question.lower() == normalized_question
        if not exact_match and overlap < 3:
            continue

        sql = entry.get("sql") or entry.get("generated_sql") or entry.get("query") or entry.get("final_sql")
        if not isinstance(sql, str) or not sql.strip():
            continue

        score = 100 if exact_match else overlap * 10
        if best_match is None or score > best_match[0]:
            best_match = (score, entry)

    if best_match is None:
        return None

    entry = best_match[1]
    return {
        "success": True,
        "data": {
            "success": True,
            "question": entry.get("question") or question,
            "sql": entry.get("sql") or entry.get("generated_sql") or entry.get("query") or entry.get("final_sql"),
            "source": entry.get("source") or "layman_router",
            "intent": entry.get("intent"),
            "row_count": entry.get("row_count"),
            "error": entry.get("error"),
        },
    }


REPORT_SQL_FALLBACK_PATHS = (
    RETEST_JSON,
    REPORTS_DIR / "learning_queue" / "latest_learning_queue.json",
    REPORTS_DIR / "learning_queue" / "regression_candidates.json",
    DIAGNOSIS_JSON,
    LATEST_CANDIDATES_JSON,
    CANDIDATE_QUERIES_JSON,
    VERIFIED_CANDIDATES_JSON,
    PENDING_CANDIDATES_JSON,
    APPROVED_CANDIDATES_JSON,
)

REPORT_SQL_KEYS = (
    "sql",
    "candidate_sql",
    "generated_sql",
    "latest_sql",
    "verified_sql",
    "final_sql",
    "executable_sql",
    "query",
)

REPORT_QUESTION_KEYS = (
    "question",
    "normalized_question",
    "user_question",
    "original_question",
    "text",
    "prompt",
)


def _normalize_match_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _walk_dicts(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            rows.append(value)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)):
                    walk(item)

    walk(payload)
    return rows


def _extract_report_sql(record: dict[str, Any]) -> str | None:
    for key in REPORT_SQL_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            sql = value.strip()
            if "select" in sql.lower():
                return sql

    for container_key in ("data", "result", "response", "answer", "payload", "verification", "raw_candidate"):
        container = record.get(container_key)
        if isinstance(container, dict):
            sql = _extract_report_sql(container)
            if sql:
                return sql

    return None


def _extract_report_question(record: dict[str, Any]) -> str | None:
    for key in REPORT_QUESTION_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for container_key in ("data", "result", "response", "answer", "payload", "verification", "raw_candidate"):
        container = record.get(container_key)
        if isinstance(container, dict):
            value = _extract_report_question(container)
            if value:
                return value

    return None


def _extract_report_intent(record: dict[str, Any]) -> str | None:
    for key in ("intent", "old_intent", "suggested_intent", "matched_intent"):
        value = _coerce_text(record.get(key))
        if value:
            return value
    return None


def _extract_report_row_count(record: dict[str, Any]) -> Any:
    for key in ("row_count", "latest_row_count", "verified_row_count"):
        if key in record:
            return record.get(key)
    verification = record.get("verification")
    if isinstance(verification, dict):
        for key in ("row_count", "latest_row_count", "verified_row_count"):
            if key in verification:
                return verification.get(key)
    return None


def _score_report_match(question: str, candidate_question: str, record: dict[str, Any]) -> int:
    wanted = _normalize_match_text(question)
    candidate = _normalize_match_text(candidate_question)

    if not wanted or not candidate:
        return 0

    if wanted == candidate:
        return 1000

    wanted_tokens = set(wanted.split())
    candidate_tokens = set(candidate.split())
    overlap = len(wanted_tokens & candidate_tokens)

    score = overlap * 10

    if wanted in candidate or candidate in wanted:
        score += 100

    record_text = _normalize_match_text(json.dumps(record, ensure_ascii=False, default=str))
    if wanted and wanted in record_text:
        score += 250

    return score


def _fallback_from_report_files(question: str) -> dict[str, Any] | None:
    best: tuple[int, Path, dict[str, Any], str, str] | None = None

    for report_path in REPORT_SQL_FALLBACK_PATHS:
        payload = read_json(report_path, None)
        if payload is None:
            continue

        for record in _walk_dicts(payload):
            sql = _extract_report_sql(record)
            if not sql:
                continue

            # Skip diagnostic probe SQL. These are table checks, not answer SQL.
            if record.get("name") and not record.get("question"):
                continue

            candidate_question = _extract_report_question(record) or question
            score = _score_report_match(question, candidate_question, record)

            # Never use fuzzy fallback for Get Query.
            # It can show unrelated SQL for a failed question.
            # Fallback is allowed only when the saved report question is an exact normalized match.
            if _normalize_question_tokens(candidate_question) != _normalize_question_tokens(question):
                continue

            if score < 100:
                continue

            if best is None or score > best[0]:
                best = (score, report_path, record, candidate_question, sql)

    if best is None:
        return None

    score, report_path, record, candidate_question, sql = best

    return {
        "matched_score": score,
        "matched_file": str(report_path),
        "matched_question": candidate_question,
        "record": record,
        "sql": sql,
        "source": _stringify_source(record.get("source") or record.get("latest_source") or record.get("old_source"))
            or "saved_candidate_fallback",
        "intent": _extract_report_intent(record),
        "row_count": _extract_report_row_count(record),
    }


def _normalized_report_fallback_response(
    question: str,
    fallback: dict[str, Any],
    live_normalized: dict[str, Any],
    raw_response: dict[str, Any],
) -> dict[str, Any]:
    live_extracted = live_normalized.get("extracted") if isinstance(live_normalized.get("extracted"), dict) else {}
    live_error = (
        live_extracted.get("error")
        or raw_response.get("error")
        or raw_response.get("message")
        or raw_response.get("detail")
        or "Live /ask did not return usable SQL."
    )

    return {
        "ok": True,
        "question": question,
        "http_status": live_normalized.get("http_status") or 200,
        "extracted": {
            "success": True,
            "source": "saved_candidate_fallback",
            "intent": fallback.get("intent") or live_extracted.get("intent"),
            "row_count": fallback.get("row_count"),
            "sql": fallback.get("sql"),
            "error": None,
        },
        "fallback_used": True,
        "fallback_reason": "Live /ask failed or returned no SQL, but saved AutomateQuery report SQL exists.",
        "live_error": live_error,
        "live_error_type": raw_response.get("error_type"),
        "raw_response": raw_response,
        "fallback_record": {
            "matched_score": fallback.get("matched_score"),
            "matched_file": fallback.get("matched_file"),
            "matched_question": fallback.get("matched_question"),
            "source": fallback.get("source"),
            "intent": fallback.get("intent"),
            "row_count": fallback.get("row_count"),
            "record": fallback.get("record"),
        },
    }


def _call_ask(question: str) -> dict[str, Any]:
    try:
        from app.api import AskRequest, ask as ask_endpoint
    except Exception:
        from app.query_engine import answer_question

        result = answer_question(question)
        if result.get("success"):
            return {
                "success": True,
                "status_code": 200,
                "body": {"success": True, "data": result},
            }
        fallback = _fallback_from_question_logs(question)
        if fallback is not None:
            return {"success": True, "status_code": 200, "body": fallback}
        return {
            "success": False,
            "status_code": 200,
            "body": {"success": False, "error": result.get("error") or "ask_failed"},
        }

    request = AskRequest(question=question)
    response = ask_endpoint(request)

    if isinstance(response, JSONResponse):
        raw_body = response.body.decode("utf-8") if response.body else "{}"
        try:
            payload = json.loads(raw_body)
        except Exception:
            payload = {"success": False, "error": raw_body}
        if payload.get("success"):
            return {"success": response.status_code < 400, "status_code": response.status_code, "body": payload}
        fallback = _fallback_from_question_logs(question)
        if fallback is not None:
            return {"success": True, "status_code": 200, "body": fallback}
        return {"success": response.status_code < 400, "status_code": response.status_code, "body": payload}

    if isinstance(response, dict):
        return {"success": True, "status_code": 200, "body": response}

    return {"success": False, "status_code": 500, "body": {"success": False, "error": "unexpected_ask_response_type"}}


@router.get("/learning-cycle")
def learning_cycle_page() -> Any:
    if TEMPLATE_PATH.exists():
        return FileResponse(str(TEMPLATE_PATH))
    return JSONResponse({"error": "template_not_found", "path": str(TEMPLATE_PATH)}, status_code=404)


@router.get("/api/learning-cycle/summary")
def learning_cycle_summary() -> dict[str, Any]:
    learning_queue = read_json(LEARNING_QUEUE_JSON, {}) or {}

    if learning_queue:
        total_log_rows = learning_queue.get("total_log_rows") or 0
        unique_questions = learning_queue.get("unique_questions") or 0
        needs_review = learning_queue.get("needs_review_count") or 0
        open_problems = learning_queue.get("open_count") or 0
        zero_review = learning_queue.get("zero_review_count") or 0
        fixed_regression = learning_queue.get("fixed_needs_regression_count") or 0
        ok_count = learning_queue.get("ok_count") or 0

        summary = {
            "generated": learning_queue.get("generated_at"),
            "source": "learning_queue",
            "inputs": {
                "log_files": learning_queue.get("used_logs") or learning_queue.get("log_files") or [],
                "learning_queue_json": str(LEARNING_QUEUE_JSON),
            },
            "learning_queue_summary": {
                "total_log_rows": total_log_rows,
                "unique_questions": unique_questions,
                "needs_review": needs_review,
                "open_problems": open_problems,
                "zero_row_review": zero_review,
                "fixed_needs_regression": fixed_regression,
                "ok": ok_count,
            },
            "retest_summary": {
                "PASS": ok_count + fixed_regression,
                "ZERO_REVIEW": zero_review,
                "FAIL": open_problems,
            },
            "diagnosis_summary": {
                "EXPECTED_ZERO": zero_review,
                "OPEN": open_problems,
            },
            "candidate_summary": {
                "total_candidates": open_problems,
                "verified_with_rows": fixed_regression,
                "pending_or_failed": open_problems,
            },
            "final_status": {
                "safe_handled_questions": ok_count + fixed_regression + zero_review,
                "pass": ok_count + fixed_regression,
                "expected_zero": zero_review,
                "needs_work": open_problems,
                "verified_candidates": fixed_regression,
                "total_questions": unique_questions,
            },
            "outputs": {
                "learning_queue_json": str(LEARNING_QUEUE_JSON),
                "learning_queue_md": str(LEARNING_QUEUE_MD),
            },
        }

        return {
            "summary": summary,
            "summary_md": read_text(LEARNING_QUEUE_MD),
            "files": {
                "summary_json": file_info(SUMMARY_JSON),
                "summary_md": file_info(SUMMARY_MD),
                "learning_queue_json": file_info(LEARNING_QUEUE_JSON),
                "learning_queue_md": file_info(LEARNING_QUEUE_MD),
                "diagnosis_json": file_info(DIAGNOSIS_JSON),
                "candidate_queries_json": file_info(CANDIDATE_QUERIES_JSON),
                "expected_zero_json": file_info(EXPECTED_ZERO_JSON),
            },
        }

    summary = read_json(SUMMARY_JSON, {}) or {}
    return {
        "summary": summary,
        "summary_md": read_text(SUMMARY_MD),
        "files": {
            "summary_json": file_info(SUMMARY_JSON),
            "summary_md": file_info(SUMMARY_MD),
            "diagnosis_json": file_info(DIAGNOSIS_JSON),
            "candidate_queries_json": file_info(CANDIDATE_QUERIES_JSON),
            "expected_zero_json": file_info(EXPECTED_ZERO_JSON),
        },
    }


@router.post("/api/learning-cycle/run")
def learning_cycle_run(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    with RUN_LOCK:
        if RUN_STATUS.get("running"):
            return {"started": False, "message": "A run is already in progress.", "status": RUN_STATUS}

        RUN_STATUS.update({
            "running": True,
            "started_at": now_iso(),
            "finished_at": None,
            "message": "Run requested.",
        })

    return {
        "started": True,
        "message": "Learning cycle run requested.",
        "payload": payload,
        "status": RUN_STATUS,
    }


@router.get("/api/learning-cycle/run-status")
def learning_cycle_run_status() -> dict[str, Any]:
    with RUN_LOCK:
        return dict(RUN_STATUS)


@router.get("/api/learning-cycle/retest")
def learning_cycle_retest() -> dict[str, Any]:
    rows = as_list(read_json(RETEST_JSON, []))
    return {"count": len(rows), "rows": rows, "file": file_info(RETEST_JSON)}


@router.get("/api/learning-cycle/diagnosis")
def learning_cycle_diagnosis() -> dict[str, Any]:
    learning_queue = read_json(LEARNING_QUEUE_JSON, {}) or {}
    queue = learning_queue.get("queue") if isinstance(learning_queue.get("queue"), list) else []

    if queue:
        rows = []
        for item in queue:
            status = item.get("status")
            if status not in {"OPEN", "ZERO_REVIEW"}:
                continue

            latest = item.get("latest") or {}
            nlp = item.get("nlp") or {}

            diagnosis_status = "EXPECTED_ZERO" if status == "ZERO_REVIEW" else "OPEN"
            if "slow_query" in (item.get("reasons") or []):
                diagnosis_status = "SLOW_QUERY" if status == "OPEN" else "EXPECTED_ZERO"

            rows.append({
                "question": item.get("question"),
                "status": diagnosis_status,
                "classification": diagnosis_status,
                "source": latest.get("source") or "unknown",
                "intent": latest.get("intent") or nlp.get("intent"),
                "row_count": latest.get("row_count"),
                "confidence": nlp.get("confidence") or latest.get("confidence") or 0.9,
                "reason": item.get("manual_review_reason"),
                "explanation": item.get("manual_review_reason"),
                "sql": latest.get("sql"),
                "answer": latest.get("answer"),
                "automation_decision": item.get("automation_decision"),
                "nlp": nlp,
                "raw": item,
            })

        markdown = read_text(LEARNING_QUEUE_MD)
        return {
            "count": len(rows),
            "rows": rows,
            "markdown": markdown,
            "file": file_info(LEARNING_QUEUE_JSON),
            "source": "learning_queue",
        }

    rows = as_list(read_json(DIAGNOSIS_JSON, []))
    return {"count": len(rows), "rows": rows, "markdown": read_text(DIAGNOSIS_MD), "file": file_info(DIAGNOSIS_JSON)}


@router.get("/api/learning-cycle/expected-zero")
def learning_cycle_expected_zero() -> dict[str, Any]:
    rows = as_list(read_json(EXPECTED_ZERO_JSON, []))
    return {"count": len(rows), "rows": rows, "file": file_info(EXPECTED_ZERO_JSON)}


def _saved_sql_fallback_rows(limit: int = 200) -> list[dict[str, Any]]:
    """Return verified SQL rows from retest history for UI fallback review."""
    payload = read_json(RETEST_JSON, []) or []
    rows = as_list(payload)

    fallback_rows: list[dict[str, Any]] = []

    for row in rows:
        sql = row.get("sql") or row.get("candidate_sql") or row.get("generated_sql") or row.get("latest_sql")
        verdict = str(row.get("verdict") or "").upper()
        success = row.get("success") is True

        if not isinstance(sql, str) or not sql.strip():
            continue

        if verdict != "PASS" and not success:
            continue

        fallback_rows.append({
            "question": row.get("question") or row.get("question_key") or row.get("normalized_question"),
            "verdict": row.get("verdict") or "PASS",
            "source": row.get("source") or row.get("old_source") or "retest_sql_fallback",
            "intent": row.get("intent") or row.get("old_intent"),
            "row_count": row.get("row_count") if row.get("row_count") is not None else row.get("old_row_count"),
            "elapsed_ms": row.get("elapsed_ms") or row.get("api_elapsed_ms"),
            "sql": sql,
            "fallback_type": "saved_retest_sql",
            "matched_file": str(RETEST_JSON),
            "note": "Verified SQL from latest distinct retest report. Useful when live /ask fails but AutomateQuery already has a working SQL.",
            "raw": row,
        })

    fallback_rows.sort(
        key=lambda item: (
            str(item.get("verdict") or ""),
            str(item.get("question") or ""),
        )
    )

    return fallback_rows[:limit]


@router.get("/api/learning-cycle/candidates")
def learning_cycle_candidates() -> dict[str, Any]:
    candidate_queries = as_list(read_json(CANDIDATE_QUERIES_JSON, []))
    latest_candidates = as_list(read_json(LATEST_CANDIDATES_JSON, []))
    verified_candidates = as_list(read_json(VERIFIED_CANDIDATES_JSON, []))
    pending_candidates = as_list(read_json(PENDING_CANDIDATES_JSON, []))
    saved_sql_fallbacks = _saved_sql_fallback_rows()

    return {
        "candidate_queries": candidate_queries,
        "latest_candidates": latest_candidates,
        "verified_candidates": verified_candidates,
        "pending_candidates": pending_candidates,
        "saved_sql_fallbacks": saved_sql_fallbacks,
        "counts": {
            "candidate_queries": len(candidate_queries),
            "latest_candidates": len(latest_candidates),
            "verified_candidates": len(verified_candidates),
            "pending_candidates": len(pending_candidates),
            "saved_sql_fallbacks": len(saved_sql_fallbacks),
        },
    }


@router.get("/api/learning-cycle/approvals")
def learning_cycle_approvals() -> dict[str, Any]:
    expected_zero = as_list(read_json(APPROVED_EXPECTED_ZERO_JSON, []))
    candidate_queries = as_list(read_json(APPROVED_CANDIDATES_JSON, []))
    return {
        "expected_zero": expected_zero,
        "candidate_queries": candidate_queries,
        "counts": {
            "expected_zero": len(expected_zero),
            "candidate_queries": len(candidate_queries),
        },
    }


@router.get("/api/learning-cycle/eval")
def learning_cycle_eval() -> dict[str, Any]:
    generated = as_list(read_json(GENERATED_EVAL_JSON, []))
    reviewed = as_list(read_json(REVIEWED_EVAL_JSON, []))
    approved = as_list(read_json(APPROVED_EVAL_JSON, []))
    approved_run = read_text(APPROVED_EVAL_RUN_TXT)
    return {
        "generated": generated,
        "reviewed": reviewed,
        "approved": approved,
        "approved_run": approved_run,
        "counts": {
            "generated": len(generated),
            "reviewed": len(reviewed),
            "approved": len(approved),
        },
    }


@router.get("/api/learning-cycle/get-query")
def learning_cycle_get_query(request: Request) -> dict[str, Any]:
    question = str(request.query_params.get("question") or "").strip()
    if not question:
        return {"ok": False, "error": "question query parameter is required"}

    ask_result = _call_ask(question)
    raw_response = ask_result.get("body") if isinstance(ask_result.get("body"), dict) else {}
    normalized = _normalize_ask_response(question, raw_response)
    normalized["http_status"] = ask_result.get("status_code") or normalized.get("http_status") or 200

    extracted = normalized.get("extracted") if isinstance(normalized.get("extracted"), dict) else {}
    live_sql = extracted.get("sql")

    if not isinstance(live_sql, str) or not live_sql.strip():
        fallback = _fallback_from_report_files(question)
        if fallback and fallback.get("sql"):
            return _normalized_report_fallback_response(
                question=question,
                fallback=fallback,
                live_normalized=normalized,
                raw_response=raw_response,
            )

    return normalized



def _regression_case_count(payload: Any) -> int:
    if isinstance(payload, dict):
        cases = payload.get("cases")
        if isinstance(cases, list):
            return len(cases)
        count = payload.get("count")
        if isinstance(count, int):
            return count
    if isinstance(payload, list):
        return len(payload)
    return 0


def _run_automate_script(script_path: Path, timeout_seconds: int = 900) -> dict[str, Any]:
    if not script_path.exists():
        return {
            "success": False,
            "error": "script_not_found",
            "script": str(script_path),
        }

    try:
        completed = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "error": "script_timeout",
            "script": str(script_path),
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }

    return {
        "success": completed.returncode == 0,
        "returncode": completed.returncode,
        "script": str(script_path),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


@router.get("/api/learning-cycle/regression-summary")
def learning_cycle_regression_summary() -> dict[str, Any]:
    regression_payload = read_json(REGRESSION_CASES_JSON, {}) or {}
    approved_cases = read_jsonl(APPROVED_REGRESSION_JSONL)
    deferred_cases = read_jsonl(DEFERRED_REGRESSION_JSONL)
    latest_run = read_json(LATEST_REGRESSION_RUN_JSON, {}) or {}

    latest_run_results = latest_run.get("results") if isinstance(latest_run.get("results"), list) else []
    failed_results = [item for item in latest_run_results if not item.get("passed")]
    deferred_memory = [item for item in deferred_cases if item.get("status") == "deferred_memory_required"]
    deferred_hr = [item for item in deferred_cases if item.get("status") == "deferred_hr_scope"]

    return {
        "counts": {
            "regression_candidates": _regression_case_count(regression_payload),
            "approved_cases": len(approved_cases),
            "deferred_cases": len(deferred_cases),
            "deferred_memory": len(deferred_memory),
            "deferred_hr": len(deferred_hr),
            "latest_run_total": latest_run.get("total"),
            "latest_run_passed": latest_run.get("passed"),
            "latest_run_failed": latest_run.get("failed"),
        },
        "latest_run": {
            "generated_at": latest_run.get("generated_at"),
            "api_url": latest_run.get("api_url"),
            "total": latest_run.get("total"),
            "passed": latest_run.get("passed"),
            "failed": latest_run.get("failed"),
            "failed_preview": failed_results[:20],
        },
        "deferred": {
            "memory_required": deferred_memory[:50],
            "hr_scope": deferred_hr[:50],
        },
        "reports": {
            "approved_seed_md": read_text(APPROVED_REGRESSION_SEED_MD)[:12000],
            "deferred_md": read_text(DEFERRED_REGRESSION_MD)[:12000],
            "latest_run_md": read_text(LATEST_REGRESSION_RUN_MD)[:12000],
        },
        "files": {
            "regression_cases_json": file_info(REGRESSION_CASES_JSON),
            "regression_cases_md": file_info(REGRESSION_CASES_MD),
            "approved_regression_jsonl": file_info(APPROVED_REGRESSION_JSONL),
            "approved_seed_md": file_info(APPROVED_REGRESSION_SEED_MD),
            "deferred_regression_jsonl": file_info(DEFERRED_REGRESSION_JSONL),
            "deferred_regression_md": file_info(DEFERRED_REGRESSION_MD),
            "latest_run_json": file_info(LATEST_REGRESSION_RUN_JSON),
            "latest_run_md": file_info(LATEST_REGRESSION_RUN_MD),
        },
    }



@router.post("/api/learning-cycle/build-learning-queue")
def learning_cycle_build_learning_queue() -> dict[str, Any]:
    result = _run_automate_script(BUILD_LEARNING_QUEUE_SCRIPT)
    summary = learning_cycle_regression_summary()
    return {"action": "build_learning_queue", "result": result, "summary": summary}


@router.post("/api/learning-cycle/build-regression-candidates")
def learning_cycle_build_regression_candidates() -> dict[str, Any]:
    result = _run_automate_script(BUILD_REGRESSION_SCRIPT)
    summary = learning_cycle_regression_summary()
    return {"action": "build_regression_candidates", "result": result, "summary": summary}


@router.post("/api/learning-cycle/build-approved-regression-seed")
def learning_cycle_build_approved_regression_seed() -> dict[str, Any]:
    result = _run_automate_script(BUILD_APPROVED_REGRESSION_SCRIPT)
    summary = learning_cycle_regression_summary()
    return {"action": "build_approved_regression_seed", "result": result, "summary": summary}


REGRESSION_RUN_LOCK = threading.Lock()
REGRESSION_RUN_STATUS: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "message": "No approved regression run started yet.",
    "result": None,
}


def _read_regression_progress() -> dict[str, Any]:
    progress = read_json(LATEST_REGRESSION_PROGRESS_JSON, {}) or {}
    if not isinstance(progress, dict):
        return {}
    return progress


def _approved_regression_worker() -> None:
    with REGRESSION_RUN_LOCK:
        REGRESSION_RUN_STATUS.update({
            "running": True,
            "started_at": now_iso(),
            "finished_at": None,
            "message": "Approved regression run is running.",
            "result": None,
        })

    result = _run_automate_script(RUN_APPROVED_REGRESSION_SCRIPT, timeout_seconds=3600)

    with REGRESSION_RUN_LOCK:
        REGRESSION_RUN_STATUS.update({
            "running": False,
            "finished_at": now_iso(),
            "message": "Approved regression run finished.",
            "result": result,
        })


def _start_approved_regression() -> dict[str, Any]:
    with REGRESSION_RUN_LOCK:
        if REGRESSION_RUN_STATUS.get("running"):
            return {
                "started": False,
                "message": "Approved regression run is already running.",
                "status": dict(REGRESSION_RUN_STATUS),
                "progress": _read_regression_progress(),
                "summary": learning_cycle_regression_summary(),
            }

        REGRESSION_RUN_STATUS.update({
            "running": True,
            "started_at": now_iso(),
            "finished_at": None,
            "message": "Approved regression run is starting.",
            "result": None,
        })
        status = dict(REGRESSION_RUN_STATUS)

    thread = threading.Thread(target=_approved_regression_worker, daemon=True)
    thread.start()

    return {
        "started": True,
        "message": "Approved regression run started.",
        "status": status,
        "progress": _read_regression_progress(),
        "summary": learning_cycle_regression_summary(),
    }


@router.post("/api/learning-cycle/run-approved-regression")
def learning_cycle_run_approved_regression() -> dict[str, Any]:
    return _start_approved_regression()


@router.post("/api/learning-cycle/start-approved-regression")
def learning_cycle_start_approved_regression() -> dict[str, Any]:
    return _start_approved_regression()


@router.get("/api/learning-cycle/regression-run-status")
def learning_cycle_regression_run_status() -> dict[str, Any]:
    with REGRESSION_RUN_LOCK:
        status = dict(REGRESSION_RUN_STATUS)

    progress = _read_regression_progress()
    if progress.get("running") and not status.get("running"):
        status["running"] = True
        status["message"] = "Approved regression run is running."

    return {
        "status": status,
        "progress": progress,
        "summary": learning_cycle_regression_summary(),
    }



@router.post("/api/learning-cycle/expected-zero/approve")
def approve_expected_zero(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    ensure_approval_file(APPROVED_EXPECTED_ZERO_JSON)
    question = str(payload.get("question") or "").strip()
    if not question:
        return {"approved": False, "error": "question is required"}

    rows = as_list(read_json(APPROVED_EXPECTED_ZERO_JSON, []))
    row = {"question": question, "approved": True, "note": str(payload.get("note") or "")}
    rows.append(row)
    APPROVED_EXPECTED_ZERO_JSON.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"approved": True, "row": row, "count": len(rows)}


@router.post("/api/learning-cycle/candidates/approve")
def approve_candidate(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    ensure_approval_file(APPROVED_CANDIDATES_JSON)
    candidate_id = str(payload.get("candidate_id") or "").strip()
    if not candidate_id:
        return {"approved": False, "error": "candidate_id is required"}

    rows = as_list(read_json(APPROVED_CANDIDATES_JSON, []))
    row = {"candidate_id": candidate_id, "approved": True, "note": str(payload.get("note") or "")}
    rows.append(row)
    APPROVED_CANDIDATES_JSON.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"approved": True, "row": row, "count": len(rows)}
