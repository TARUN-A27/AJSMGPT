#!/usr/bin/env python3
"""
AutomateQuery Learning Cycle Dashboard API

Adds browser endpoints for:
- Learning cycle summary
- Run learning cycle
- Diagnosis report
- Candidate queries
- Expected-zero cases

This does NOT edit production routers.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import FileResponse, JSONResponse


router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = Path(os.environ.get("AJSMGPT_RUNTIME_ROOT", "/home/ajsmgpt/AJSMGPT")).resolve()

TEMPLATE_PATH = PROJECT_ROOT / "AutomateQuery/web/templates/learning_cycle/index.html"

SUMMARY_JSON = PROJECT_ROOT / "AutomateQuery/reports/learning_cycle/latest_learning_cycle_summary.json"
SUMMARY_MD = PROJECT_ROOT / "AutomateQuery/reports/learning_cycle/latest_learning_cycle_summary.md"

DIAGNOSIS_JSON = PROJECT_ROOT / "AutomateQuery/reports/question_diagnosis/latest_diagnosis.json"
DIAGNOSIS_MD = PROJECT_ROOT / "AutomateQuery/reports/question_diagnosis/latest_diagnosis.md"

CANDIDATES_JSON = PROJECT_ROOT / "AutomateQuery/reports/candidate_queries/latest_candidate_queries.json"
CANDIDATES_MD = PROJECT_ROOT / "AutomateQuery/reports/candidate_queries/latest_candidate_queries.md"

EXPECTED_ZERO_JSON = PROJECT_ROOT / "AutomateQuery/reports/question_diagnosis/expected_zero_candidates.json"
APPROVED_EXPECTED_ZERO_JSON = PROJECT_ROOT / "AutomateQuery/reports/approvals/approved_expected_zero_cases.json"
APPROVED_CANDIDATES_JSON = PROJECT_ROOT / "AutomateQuery/reports/approvals/approved_candidate_queries.json"

RUN_LOCK = threading.Lock()
RUN_STATUS: dict[str, Any] = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "output": "",
    "error": None,
}


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Failed to read {path}: {exc}"}


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def file_info(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "exists": False,
            "path": str(path),
            "modified": None,
            "size_bytes": 0,
        }

    stat = path.stat()
    return {
        "exists": True,
        "path": str(path),
        "modified": datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat(),
        "size_bytes": stat.st_size,
    }


def run_learning_cycle_background(api_url: str, timeout: int, log_file: str) -> None:
    global RUN_STATUS

    python_bin = RUNTIME_ROOT / "venv/bin/python3"
    script = PROJECT_ROOT / "AutomateQuery/core/run_learning_cycle.py"

    cmd = [
        str(python_bin),
        str(script),
        "--log-file",
        log_file,
        "--api-url",
        api_url,
        "--timeout",
        str(timeout),
    ]

    env = os.environ.copy()
    env["AJSMGPT_RUNTIME_ROOT"] = str(RUNTIME_ROOT)

    output_chunks: list[str] = []

    try:
        with RUN_LOCK:
            RUN_STATUS.update(
                {
                    "running": True,
                    "started_at": now_iso(),
                    "finished_at": None,
                    "returncode": None,
                    "output": "",
                    "error": None,
                    "cmd": " ".join(cmd),
                }
            )

        process = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        assert process.stdout is not None

        for line in process.stdout:
            output_chunks.append(line)
            with RUN_LOCK:
                RUN_STATUS["output"] = "".join(output_chunks)[-20000:]

        returncode = process.wait()

        with RUN_LOCK:
            RUN_STATUS.update(
                {
                    "running": False,
                    "finished_at": now_iso(),
                    "returncode": returncode,
                    "output": "".join(output_chunks)[-20000:],
                    "error": None if returncode == 0 else f"Learning cycle failed with return code {returncode}",
                }
            )

    except Exception as exc:  # noqa: BLE001
        with RUN_LOCK:
            RUN_STATUS.update(
                {
                    "running": False,
                    "finished_at": now_iso(),
                    "returncode": -1,
                    "output": "".join(output_chunks)[-20000:],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )


@router.get("/learning-cycle")
def learning_cycle_page():
    if TEMPLATE_PATH.exists():
        return FileResponse(str(TEMPLATE_PATH))

    return JSONResponse(
        {
            "error": "learning_cycle index.html not found",
            "expected_path": str(TEMPLATE_PATH),
        },
        status_code=404,
    )


@router.get("/api/learning-cycle/summary")
def learning_cycle_summary():
    summary = read_json(SUMMARY_JSON, {})
    return {
        "summary": summary,
        "summary_md": read_text(SUMMARY_MD),
        "files": {
            "summary_json": file_info(SUMMARY_JSON),
            "summary_md": file_info(SUMMARY_MD),
            "diagnosis_json": file_info(DIAGNOSIS_JSON),
            "candidates_json": file_info(CANDIDATES_JSON),
            "expected_zero_json": file_info(EXPECTED_ZERO_JSON),
        },
    }


@router.post("/api/learning-cycle/run")
def learning_cycle_run(payload: dict[str, Any] = Body(default={})):
    with RUN_LOCK:
        if RUN_STATUS.get("running"):
            return {
                "started": False,
                "message": "Learning cycle is already running.",
                "status": RUN_STATUS,
            }

    api_url = str(payload.get("api_url") or "http://127.0.0.1:8000")
    timeout = int(payload.get("timeout") or 25)
    log_file = str(payload.get("log_file") or (RUNTIME_ROOT / "logs/user_questions.jsonl"))

    thread = threading.Thread(
        target=run_learning_cycle_background,
        args=(api_url, timeout, log_file),
        daemon=True,
    )
    thread.start()

    return {
        "started": True,
        "message": "Learning cycle started.",
        "api_url": api_url,
        "timeout": timeout,
        "log_file": log_file,
    }


@router.get("/api/learning-cycle/run-status")
def learning_cycle_run_status():
    with RUN_LOCK:
        return dict(RUN_STATUS)


@router.get("/api/learning-cycle/diagnosis")
def learning_cycle_diagnosis():
    rows = read_json(DIAGNOSIS_JSON, [])
    return {
        "count": len(rows) if isinstance(rows, list) else 0,
        "rows": rows,
        "markdown": read_text(DIAGNOSIS_MD),
        "file": file_info(DIAGNOSIS_JSON),
    }


@router.get("/api/learning-cycle/candidates")
def learning_cycle_candidates():
    rows = read_json(CANDIDATES_JSON, [])
    return {
        "count": len(rows) if isinstance(rows, list) else 0,
        "rows": rows,
        "markdown": read_text(CANDIDATES_MD),
        "file": file_info(CANDIDATES_JSON),
    }


@router.get("/api/learning-cycle/expected-zero")
def learning_cycle_expected_zero():
    rows = read_json(EXPECTED_ZERO_JSON, [])
    return {
        "count": len(rows) if isinstance(rows, list) else 0,
        "rows": rows,
        "file": file_info(EXPECTED_ZERO_JSON),
    }


# --- Approval memory endpoints ---

def _norm_key(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().split())


def _load_list(path: Path) -> list[dict[str, Any]]:
    data = read_json(path, [])
    return data if isinstance(data, list) else []


def _write_list(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _upsert_by_key(path: Path, key_name: str, key_value: str, record: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _load_list(path)
    wanted = _norm_key(key_value)
    updated = False

    for idx, row in enumerate(rows):
        if _norm_key(row.get(key_name)) == wanted:
            rows[idx] = {**row, **record, "updated_at": now_iso()}
            updated = True
            break

    if not updated:
        rows.append({**record, "created_at": now_iso(), "updated_at": now_iso()})

    _write_list(path, rows)
    return rows


@router.get("/api/learning-cycle/approved-expected-zero")
def approved_expected_zero_cases():
    rows = _load_list(APPROVED_EXPECTED_ZERO_JSON)
    return {
        "count": len(rows),
        "rows": rows,
        "file": file_info(APPROVED_EXPECTED_ZERO_JSON),
    }


@router.post("/api/learning-cycle/expected-zero/approve")
def approve_expected_zero_case(payload: dict[str, Any] = Body(default={})):
    question = str(payload.get("question") or "").strip()
    note = str(payload.get("note") or "").strip()

    if not question:
        return JSONResponse({"approved": False, "error": "question is required"}, status_code=400)

    source_rows = _load_list(EXPECTED_ZERO_JSON)
    source = None
    wanted = _norm_key(question)

    for row in source_rows:
        if _norm_key(row.get("question")) == wanted:
            source = row
            break

    record = {
        "question": question,
        "approved": True,
        "approval_type": "expected_zero",
        "note": note,
        "source": source,
    }

    rows = _upsert_by_key(APPROVED_EXPECTED_ZERO_JSON, "question", question, record)

    return {
        "approved": True,
        "question": question,
        "count": len(rows),
        "file": str(APPROVED_EXPECTED_ZERO_JSON),
    }


@router.get("/api/learning-cycle/approved-candidates")
def approved_candidate_queries():
    rows = _load_list(APPROVED_CANDIDATES_JSON)
    return {
        "count": len(rows),
        "rows": rows,
        "file": file_info(APPROVED_CANDIDATES_JSON),
    }


@router.post("/api/learning-cycle/candidates/approve")
def approve_candidate_query(payload: dict[str, Any] = Body(default={})):
    candidate_id = str(payload.get("candidate_id") or "").strip()
    note = str(payload.get("note") or "").strip()

    if not candidate_id:
        return JSONResponse({"approved": False, "error": "candidate_id is required"}, status_code=400)

    source_rows = _load_list(CANDIDATES_JSON)
    source = None
    wanted = _norm_key(candidate_id)

    for row in source_rows:
        if _norm_key(row.get("candidate_id")) == wanted:
            source = row
            break

    if source is None:
        return JSONResponse(
            {
                "approved": False,
                "error": f"Candidate not found: {candidate_id}",
            },
            status_code=404,
        )

    record = {
        "candidate_id": candidate_id,
        "question": source.get("question"),
        "approved": True,
        "approval_type": "candidate_sql",
        "note": note,
        "source": source,
    }

    rows = _upsert_by_key(APPROVED_CANDIDATES_JSON, "candidate_id", candidate_id, record)

    return {
        "approved": True,
        "candidate_id": candidate_id,
        "count": len(rows),
        "file": str(APPROVED_CANDIDATES_JSON),
    }

# --- End approval memory endpoints ---


# --- Manual Get Query / Test Query endpoint ---

def _find_first_value(obj: Any, keys: list[str]) -> Any:
    if isinstance(obj, dict):
        for key in keys:
            if key in obj and obj[key] not in (None, ""):
                return obj[key]

        for value in obj.values():
            found = _find_first_value(value, keys)
            if found not in (None, ""):
                return found

    if isinstance(obj, list):
        for item in obj:
            found = _find_first_value(item, keys)
            if found not in (None, ""):
                return found

    return None


def _find_sql(obj: Any) -> str | None:
    found = _find_first_value(
        obj,
        ["sql", "generated_sql", "final_sql", "query", "executed_sql"],
    )

    if isinstance(found, str) and "select" in found.lower():
        return found

    return None


def _find_rows_preview(obj: Any) -> list[Any]:
    rows = _find_first_value(obj, ["rows", "data", "results"])

    if isinstance(rows, list):
        return rows[:10]

    return []


def _post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        status = response.getcode()

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = {"raw_text": body}

    return {
        "http_status": status,
        "body": parsed,
    }


@router.get("/api/learning-cycle/get-query")
@router.get("/api/learning-cycle/test-query")
def manual_get_query(question: str, api_url: str = "http://127.0.0.1:8000", timeout: int = 25):
    question = str(question or "").strip()

    if not question:
        return JSONResponse(
            {"ok": False, "error": "question query parameter is required"},
            status_code=400,
        )

    ask_url = api_url.rstrip("/") + "/ask"

    try:
        response = _post_json(
            ask_url,
            {"question": question},
            timeout=int(timeout),
        )
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body_text = ""

        try:
            body_json = json.loads(body_text) if body_text else None
        except json.JSONDecodeError:
            body_json = None

        return {
            "ok": False,
            "question": question,
            "api_url": api_url,
            "ask_url": ask_url,
            "http_status": exc.code,
            "error": f"/ask API returned HTTP {exc.code}",
            "raw": body_json if body_json is not None else body_text,
        }

    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "question": question,
            "api_url": api_url,
            "ask_url": ask_url,
            "http_status": None,
            "error": f"Could not call /ask API: {exc}",
            "raw": None,
        }

    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "question": question,
            "api_url": api_url,
            "ask_url": ask_url,
            "http_status": None,
            "error": f"{type(exc).__name__}: {exc}",
            "raw": None,
        }

    body = response.get("body", {})

    extracted = {
        "success": _find_first_value(body, ["success"]),
        "source": _find_first_value(body, ["source"]),
        "intent": _find_first_value(body, ["intent", "matched_intent"]),
        "row_count": _find_first_value(body, ["row_count", "rows_count", "count"]),
        "sql": _find_sql(body),
        "rows_preview": _find_rows_preview(body),
    }

    return {
        "ok": True,
        "question": question,
        "api_url": api_url,
        "ask_url": ask_url,
        "http_status": response.get("http_status"),
        "extracted": extracted,
        "raw": body,
    }

# --- End Manual Get Query / Test Query endpoint ---
