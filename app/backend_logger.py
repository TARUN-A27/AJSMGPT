import json
import logging
import time
import uuid
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path


LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / f"backend_trace_{datetime.now().strftime('%Y%m%d')}.log"

_current_request_id: ContextVar[str | None] = ContextVar(
    "current_request_id",
    default=None,
)

logger = logging.getLogger("ajsmgpt_backend")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def new_request_id() -> str:
    return str(uuid.uuid4())[:8]


def set_request_id(request_id: str):
    _current_request_id.set(request_id)


def get_request_id() -> str:
    request_id = _current_request_id.get()
    return request_id or "no-request"


def now_ms() -> int:
    return int(time.time() * 1000)


def log_event(
    request_id: str | None,
    stage: str,
    message: str,
    **extra,
):
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "request_id": request_id or get_request_id(),
        "stage": stage,
        "message": message,
        **extra,
    }

    logger.info(json.dumps(payload, default=str, ensure_ascii=False))
