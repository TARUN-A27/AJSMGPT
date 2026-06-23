import json
import logging
import time
import uuid
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path


_current_request_id: ContextVar[str | None] = ContextVar(
    "current_request_id",
    default=None,
)


def new_request_id() -> str:
    return str(uuid.uuid4())[:8]


def set_request_id(request_id: str):
    _current_request_id.set(request_id)


def get_request_id() -> str:
    request_id = _current_request_id.get()
    return request_id or "no-request"


def now_ms() -> int:
    return int(time.time() * 1000)


def _today_log_dir() -> Path:
    now = datetime.now()

    log_dir = (
        Path("logs")
        / "backend"
        / now.strftime("%Y")
        / now.strftime("%m")
        / now.strftime("%d")
    )

    (log_dir / "requests").mkdir(parents=True, exist_ok=True)

    return log_dir


def _write_jsonl(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")


def log_event(
    request_id: str | None,
    stage: str,
    message: str,
    **extra,
):
    final_request_id = request_id or get_request_id()
    log_dir = _today_log_dir()

    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "request_id": final_request_id,
        "stage": stage,
        "message": message,
        **extra,
    }

    # 1. Daily full backend trace
    _write_jsonl(log_dir / "backend_trace.log", payload)

    # 2. Per-request trace
    if final_request_id and final_request_id != "no-request":
        _write_jsonl(
            log_dir / "requests" / f"{final_request_id}.jsonl",
            payload,
        )

    # 3. Error-only log
    if stage.lower() == "error" or "error" in stage.lower():
        _write_jsonl(log_dir / "errors.log", payload)
