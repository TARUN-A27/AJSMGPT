from __future__ import annotations

import json
import os
import re
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
    rows = as_list(read_json(DIAGNOSIS_JSON, []))
    return {"count": len(rows), "rows": rows, "markdown": read_text(DIAGNOSIS_MD), "file": file_info(DIAGNOSIS_JSON)}


@router.get("/api/learning-cycle/expected-zero")
def learning_cycle_expected_zero() -> dict[str, Any]:
    rows = as_list(read_json(EXPECTED_ZERO_JSON, []))
    return {"count": len(rows), "rows": rows, "file": file_info(EXPECTED_ZERO_JSON)}


@router.get("/api/learning-cycle/candidates")
def learning_cycle_candidates() -> dict[str, Any]:
    candidate_queries = as_list(read_json(CANDIDATE_QUERIES_JSON, []))
    latest_candidates = as_list(read_json(LATEST_CANDIDATES_JSON, []))
    verified_candidates = as_list(read_json(VERIFIED_CANDIDATES_JSON, []))
    pending_candidates = as_list(read_json(PENDING_CANDIDATES_JSON, []))
    return {
        "candidate_queries": candidate_queries,
        "latest_candidates": latest_candidates,
        "verified_candidates": verified_candidates,
        "pending_candidates": pending_candidates,
        "counts": {
            "candidate_queries": len(candidate_queries),
            "latest_candidates": len(latest_candidates),
            "verified_candidates": len(verified_candidates),
            "pending_candidates": len(pending_candidates),
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
    return normalized


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
