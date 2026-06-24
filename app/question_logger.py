from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

QUESTION_LOG_PATH = LOG_DIR / "user_questions.jsonl"

_LOCK = threading.Lock()


def _safe_value(value: Any) -> Any:
    try:
        json.dumps(value, default=str)
        return value
    except Exception:
        return str(value)


def log_question_event(
    *,
    question: str,
    result: dict[str, Any] | None = None,
    elapsed_ms: float | None = None,
    session_id: str = "default",
    user_id: str | None = None,
    client_ip: str | None = None,
    error: str | None = None,
) -> None:
    """
    Append one user question event to logs/user_questions.jsonl.

    This log is for:
    - later tuning
    - finding failed questions
    - building templates/routers
    - checking fallback usage
    """

    result = result or {}

    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id,
        "user_id": user_id,
        "client_ip": client_ip,
        "question": question,
        "resolved_question": result.get("resolved_question") or result.get("question") or question,
        "success": result.get("success"),
        "source": result.get("source"),
        "intent": result.get("intent"),
        "row_count": result.get("row_count"),
        "elapsed_ms": elapsed_ms if elapsed_ms is not None else result.get("elapsed_ms"),
        "sql": result.get("sql"),
        "answer": result.get("answer"),
        "error": error or result.get("error"),
        "tables_used": result.get("tables_used"),
        "parameters": result.get("parameters"),
    }

    record = {k: _safe_value(v) for k, v in record.items()}

    line = json.dumps(record, ensure_ascii=False, default=str)

    with _LOCK:
        with QUESTION_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
