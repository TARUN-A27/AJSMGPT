from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


AUTOMATE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = AUTOMATE_DIR.parent
REPORTS_DIR = AUTOMATE_DIR / "reports"
NLP_DIR = AUTOMATE_DIR / "NLP"
TEMPLATES_DIR = AUTOMATE_DIR / "web" / "templates"
STATIC_DIR = AUTOMATE_DIR / "web" / "static"

LATEST_LEARNING_CYCLE_SUMMARY_JSON = REPORTS_DIR / "learning_cycle" / "latest_learning_cycle_summary.json"
LATEST_LEARNING_CYCLE_SUMMARY_MD = REPORTS_DIR / "learning_cycle" / "latest_learning_cycle_summary.md"
LATEST_RETEST_JSON = REPORTS_DIR / "retest_from_logs" / "latest_all_distinct_retest.json"
LATEST_DIAGNOSIS_JSON = REPORTS_DIR / "question_diagnosis" / "latest_diagnosis.json"
LATEST_DIAGNOSIS_MD = REPORTS_DIR / "question_diagnosis" / "latest_diagnosis.md"
LATEST_CANDIDATE_QUERIES_JSON = REPORTS_DIR / "candidate_queries" / "latest_candidate_queries.json"
LATEST_CANDIDATES_JSON = REPORTS_DIR / "question_diagnosis" / "latest_candidates.json"
VERIFIED_CANDIDATES_JSON = REPORTS_DIR / "candidate_queries" / "verified_candidate_queries.json"
PENDING_CANDIDATES_JSON = REPORTS_DIR / "candidate_queries" / "pending_candidate_queries.json"
EXPECTED_ZERO_JSON = REPORTS_DIR / "question_diagnosis" / "expected_zero_candidates.json"
APPROVED_EXPECTED_ZERO_JSON = REPORTS_DIR / "approvals" / "approved_expected_zero_cases.json"
APPROVED_CANDIDATES_JSON = REPORTS_DIR / "approvals" / "approved_candidate_queries.json"
LATEST_LEARNING_QUEUE_JSON = REPORTS_DIR / "learning_queue" / "latest_learning_queue.json"
OPEN_QUESTIONS_JSON = REPORTS_DIR / "learning_queue" / "open_questions.json"
REGRESSION_CANDIDATES_JSON = REPORTS_DIR / "learning_queue" / "regression_candidates.json"
GENERATED_EVAL_JSON = REPORTS_DIR / "generated_eval_candidates.json"
REVIEWED_EVAL_JSON = REPORTS_DIR / "reviewed_eval_candidates.json"
APPROVED_EVAL_JSON = REPORTS_DIR / "approved_eval_tests.json"
APPROVED_EVAL_RUN_TXT = REPORTS_DIR / "approved_eval_test_run.txt"

NLU_YML = NLP_DIR / "rasa_nlu" / "data" / "nlu.yml"
NLU_PRIORITY_YML = NLP_DIR / "rasa_nlu" / "data" / "nlu_priority.yml"
NLU_GENERATED_YML = NLP_DIR / "rasa_nlu" / "data" / "nlu_generated.yml"
INTENT_TEMPLATES_JSON = NLP_DIR / "training_generator" / "intent_templates.json"
NLP_REVIEW_FEEDBACK_JSONL = REPORTS_DIR / "nlp_review_feedback.jsonl"

app = FastAPI(title="AutomateQuery UI")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def serve_html_template(page_name: str) -> Any:
    template_path = TEMPLATES_DIR / page_name / "index.html"
    if not template_path.exists():
        return JSONResponse({"error": "template_not_found", "path": str(template_path)}, status_code=404)
    return FileResponse(str(template_path))


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
        if not line.strip():
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


def file_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path), "modified": None, "size_bytes": 0}
    stat = path.stat()
    return {"exists": True, "path": str(path), "modified": stat.st_mtime, "size_bytes": stat.st_size}


def as_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def ensure_approval_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("[]", encoding="utf-8")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "project_root": str(PROJECT_ROOT)}


@app.get("/")
@app.get("/dashboard")
def dashboard_page() -> Any:
    return serve_html_template("dashboard")


@app.get("/question-bank")
def question_bank_page() -> Any:
    return serve_html_template("question_bank")


@app.get("/api/question-bank")
def api_question_bank() -> dict[str, Any]:
    queue = as_list(read_json(LATEST_LEARNING_QUEUE_JSON, []))
    open_questions = as_list(read_json(OPEN_QUESTIONS_JSON, []))
    regression_candidates = as_list(read_json(REGRESSION_CANDIDATES_JSON, []))
    retest = as_list(read_json(LATEST_RETEST_JSON, []))
    diagnosis = as_list(read_json(LATEST_DIAGNOSIS_JSON, []))
    expected_zero = as_list(read_json(EXPECTED_ZERO_JSON, []))
    approved_expected_zero = as_list(read_json(APPROVED_EXPECTED_ZERO_JSON, []))

    return {
        "queue": queue,
        "open_questions": open_questions,
        "regression_candidates": regression_candidates,
        "retest": retest,
        "diagnosis": diagnosis,
        "expected_zero": expected_zero,
        "approved_expected_zero": approved_expected_zero,
        "counts": {
            "queue": len(queue),
            "open_questions": len(open_questions),
            "regression_candidates": len(regression_candidates),
            "retest": len(retest),
            "diagnosis": len(diagnosis),
            "expected_zero": len(expected_zero),
            "approved_expected_zero": len(approved_expected_zero),
        },
    }


@app.get("/api/dashboard/summary")
def api_dashboard_summary() -> dict[str, Any]:
    summary = read_json(LATEST_LEARNING_CYCLE_SUMMARY_JSON, {}) or {}
    diagnosis = as_list(read_json(LATEST_DIAGNOSIS_JSON, []))
    candidate_queries = as_list(read_json(LATEST_CANDIDATE_QUERIES_JSON, []))
    expected_zero = as_list(read_json(EXPECTED_ZERO_JSON, []))
    approved_expected_zero = as_list(read_json(APPROVED_EXPECTED_ZERO_JSON, []))
    approved_candidate_queries = as_list(read_json(APPROVED_CANDIDATES_JSON, []))
    learning_queue = as_list(read_json(LATEST_LEARNING_QUEUE_JSON, []))

    return {
        "summary": summary,
        "diagnosis": diagnosis,
        "candidate_queries": candidate_queries,
        "expected_zero": expected_zero,
        "approved_expected_zero": approved_expected_zero,
        "approved_candidate_queries": approved_candidate_queries,
        "learning_queue": learning_queue,
        "counts": {
            "diagnosis": len(diagnosis),
            "candidate_queries": len(candidate_queries),
            "expected_zero": len(expected_zero),
            "approved_expected_zero": len(approved_expected_zero),
            "approved_candidate_queries": len(approved_candidate_queries),
            "learning_queue": len(learning_queue),
        },
        "files": {
            "summary_json": file_info(LATEST_LEARNING_CYCLE_SUMMARY_JSON),
            "summary_md": file_info(LATEST_LEARNING_CYCLE_SUMMARY_MD),
            "diagnosis_json": file_info(LATEST_DIAGNOSIS_JSON),
            "candidate_queries_json": file_info(LATEST_CANDIDATE_QUERIES_JSON),
            "expected_zero_json": file_info(EXPECTED_ZERO_JSON),
            "approved_expected_zero_json": file_info(APPROVED_EXPECTED_ZERO_JSON),
            "approved_candidate_queries_json": file_info(APPROVED_CANDIDATES_JSON),
            "learning_queue_json": file_info(LATEST_LEARNING_QUEUE_JSON),
        },
    }


@app.get("/api/nlp-review/questions")
def api_nlp_review_questions() -> dict[str, Any]:
    return {
        "nlu": read_text(NLU_YML),
        "nlu_priority": read_text(NLU_PRIORITY_YML),
        "nlu_generated": read_text(NLU_GENERATED_YML),
        "intent_templates": read_text(INTENT_TEMPLATES_JSON),
        "feedback": read_jsonl(NLP_REVIEW_FEEDBACK_JSONL),
        "files": {
            "nlu": file_info(NLU_YML),
            "nlu_priority": file_info(NLU_PRIORITY_YML),
            "nlu_generated": file_info(NLU_GENERATED_YML),
            "intent_templates": file_info(INTENT_TEMPLATES_JSON),
            "feedback": file_info(NLP_REVIEW_FEEDBACK_JSONL),
        },
    }


@app.post("/api/nlp-review/candidate")
async def api_nlp_review_candidate(request: Request) -> dict[str, Any]:
    body = await request.json()
    question = str(body.get("question") or "").strip()
    if not question:
        return {"success": False, "error": "question_required"}
    return {
        "success": True,
        "question": question,
        "mode": "demo",
        "message": "Candidate generation is not wired into this build yet.",
    }


@app.post("/api/nlp-review/save-feedback")
async def api_nlp_review_save_feedback(request: Request) -> dict[str, Any]:
    body = await request.json()
    question = str(body.get("question") or "").strip()
    is_correct = bool(body.get("is_correct"))
    note = str(body.get("note") or "")

    if not question:
        return {"success": False, "error": "question_required"}

    NLP_REVIEW_FEEDBACK_JSONL.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "question": question,
        "is_correct": is_correct,
        "note": note,
        "saved_at": os.path.getmtime(__file__) if False else None,
    }
    existing = read_jsonl(NLP_REVIEW_FEEDBACK_JSONL)
    existing.append(row)
    NLP_REVIEW_FEEDBACK_JSONL.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in existing) + "\n",
        encoding="utf-8",
    )
    return {"success": True, "row": row, "saved_to": str(NLP_REVIEW_FEEDBACK_JSONL)}


@app.get("/nlp-review")
def nlp_review_page() -> Any:
    return serve_html_template("nlp_review")


try:
    from AutomateQuery.web.learning_cycle_api import router as learning_cycle_router
except Exception:  # pragma: no cover
    from learning_cycle_api import router as learning_cycle_router

app.include_router(learning_cycle_router)
