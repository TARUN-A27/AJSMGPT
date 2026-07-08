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
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import FileResponse, JSONResponse


router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = Path(os.environ.get("AJSMGPT_RUNTIME_ROOT", "/home/ajsmgpt/AJSMGPT")).resolve()

TEMPLATE_PATH = PROJECT_ROOT / "AutomateQuery/web/templates/learning_cycle.html"

SUMMARY_JSON = PROJECT_ROOT / "AutomateQuery/reports/learning_cycle/latest_learning_cycle_summary.json"
SUMMARY_MD = PROJECT_ROOT / "AutomateQuery/reports/learning_cycle/latest_learning_cycle_summary.md"

DIAGNOSIS_JSON = PROJECT_ROOT / "AutomateQuery/reports/question_diagnosis/latest_diagnosis.json"
DIAGNOSIS_MD = PROJECT_ROOT / "AutomateQuery/reports/question_diagnosis/latest_diagnosis.md"

CANDIDATES_JSON = PROJECT_ROOT / "AutomateQuery/reports/candidate_queries/latest_candidate_queries.json"
CANDIDATES_MD = PROJECT_ROOT / "AutomateQuery/reports/candidate_queries/latest_candidate_queries.md"

EXPECTED_ZERO_JSON = PROJECT_ROOT / "AutomateQuery/reports/question_diagnosis/expected_zero_candidates.json"

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
            "error": "learning_cycle.html not found",
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
