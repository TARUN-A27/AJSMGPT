from __future__ import annotations

import json
import os
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
    return {
        "ok": True,
        "question": question,
        "message": "Query generation is not wired into this build yet.",
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
