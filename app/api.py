import json
import os
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=BASE_DIR / ".env", override=False)

from app.backend_logger import log_event, new_request_id, set_request_id
from app.query_engine import answer_question
from app.query_planner import plan_query


app = FastAPI(title="AJSMGPT API")

TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class AskRequest(BaseModel):
    question: str


class ResolveRequest(BaseModel):
    question: str
    limit: int = Field(default=10, ge=1, le=50)


class ResolveBatchRequest(BaseModel):
    questions: list[str] = Field(default_factory=list, max_length=50)
    limit: int = Field(default=5, ge=1, le=20)


@app.get("/")
def home():
    return FileResponse(TEMPLATES_DIR / "index.html")


@app.get("/health")
def health():
    """
    DB-free health endpoint.
    Safe to call even when Oracle is down.
    Does not expose passwords.
    """
    return {
        "success": True,
        "service": "AJSMGPT API",
        "status": "running",
        "project_root": str(BASE_DIR),
        "oracle": {
            "user_set": bool(os.getenv("ORACLE_USER")),
            "password_set": bool(os.getenv("ORACLE_PASSWORD")),
            "dsn": os.getenv("ORACLE_DSN"),
            "client_lib_dir": os.getenv("ORACLE_CLIENT_LIB_DIR"),
            "schemas": os.getenv("ORACLE_SCHEMAS"),
        },
        "qdrant": {
            "url": os.getenv("QDRANT_URL"),
            "collection": os.getenv("QDRANT_COLLECTION"),
        },
        "ollama": {
            "url": os.getenv("OLLAMA_URL"),
            "chat_model": os.getenv("OLLAMA_CHAT_MODEL"),
            "embed_model": os.getenv("OLLAMA_EMBED_MODEL"),
        },
        "safety": {
            "allow_only_select": os.getenv("ALLOW_ONLY_SELECT"),
            "sql_allowed_statements": os.getenv("SQL_ALLOWED_STATEMENTS"),
            "block_ddl": os.getenv("BLOCK_DDL"),
            "block_dml": os.getenv("BLOCK_DML"),
        },
    }


@app.get("/health/db")
def health_db():
    """
    Oracle health endpoint.
    Runs only safe SELECT 1 FROM DUAL.
    Returns clean DB error if Oracle/network is down.
    """
    request_id = new_request_id()
    set_request_id(request_id)
    started = time.time()

    try:
        from app.oracle_client import get_connection

        conn = get_connection()
        cur = conn.cursor()

        try:
            cur.execute("SELECT 1 AS OK FROM DUAL")
            row = cur.fetchone()

            cur.execute("SELECT SYS_CONTEXT('USERENV','CURRENT_SCHEMA') FROM DUAL")
            schema = cur.fetchone()[0]

            elapsed_ms = int((time.time() - started) * 1000)

            return {
                "success": True,
                "request_id": request_id,
                "oracle_reachable": True,
                "result": row[0] if row else None,
                "current_schema": schema,
                "elapsed_ms": elapsed_ms,
            }
        finally:
            cur.close()
            conn.close()

    except Exception as exc:
        elapsed_ms = int((time.time() - started) * 1000)

        log_event(
            request_id,
            "oracle_health_error",
            "Oracle health check failed",
            error_type=type(exc).__name__,
            error=str(exc),
            elapsed_ms=elapsed_ms,
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "request_id": request_id,
                "oracle_reachable": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "elapsed_ms": elapsed_ms,
            },
        )


def _resolve_direct(question: str, limit: int) -> dict:
    """
    Fast in-process resolver.
    Does not touch Oracle.
    Uses SQLite exact lookup + Qdrant semantic lookup directly.
    """
    started = time.time()

    try:
        from app.hybrid_value_resolver import resolve_question_values

        payload = resolve_question_values(question, final_limit=limit)
        payload["success"] = True
        payload["elapsed_ms"] = int((time.time() - started) * 1000)
        return payload

    except Exception as exc:
        return {
            "success": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "elapsed_ms": int((time.time() - started) * 1000),
        }



def _resolve_with_cli(question: str, limit: int) -> dict:
    """
    Uses the existing resolver CLI safely.
    This avoids guessing internal function names and does not touch Oracle.
    """
    started = time.time()

    cmd = [
        sys.executable,
        "-m",
        "app.hybrid_value_resolver",
        question,
        "--limit",
        str(limit),
    ]

    proc = subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    elapsed_ms = int((time.time() - started) * 1000)

    if proc.returncode != 0:
        return {
            "success": False,
            "error_type": "ResolverCliError",
            "error": proc.stderr.strip() or proc.stdout.strip(),
            "elapsed_ms": elapsed_ms,
        }

    try:
        payload = json.loads(proc.stdout)
    except Exception as exc:
        return {
            "success": False,
            "error_type": type(exc).__name__,
            "error": "Resolver returned non-JSON output.",
            "raw_output": proc.stdout[:2000],
            "elapsed_ms": elapsed_ms,
        }

    payload["success"] = True
    payload["elapsed_ms"] = elapsed_ms
    return payload


@app.post("/resolve")
def resolve(request: ResolveRequest):
    """
    Resolver-only endpoint.
    Useful when Oracle DB is down.
    Tests SQLite exact lookup + Qdrant semantic lookup.
    """
    request_id = new_request_id()
    set_request_id(request_id)

    question = request.question.strip()
    if not question:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "request_id": request_id,
                "error": "Question cannot be empty.",
            },
        )

    log_event(
        request_id,
        "resolve_start",
        "Starting resolver-only request",
        question=question,
        limit=request.limit,
    )

    result = _resolve_direct(question, request.limit)
    result["request_id"] = request_id

    log_event(
        request_id,
        "resolve_complete" if result.get("success") else "resolve_error",
        "Resolver-only request completed",
        question=question,
        success=result.get("success"),
        elapsed_ms=result.get("elapsed_ms"),
        top_result=(result.get("results") or [{}])[0] if result.get("results") else None,
        error=result.get("error"),
    )

    return result


@app.post("/resolve/batch")
def resolve_batch(request: ResolveBatchRequest):
    """
    Batch resolver-only endpoint.
    Useful for offline backend testing while Oracle DB is down.
    Does not touch Oracle.
    """
    request_id = new_request_id()
    set_request_id(request_id)

    questions = [q.strip() for q in request.questions if q and q.strip()]

    if not questions:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "request_id": request_id,
                "error": "At least one question is required.",
            },
        )

    started = time.time()

    log_event(
        request_id,
        "resolve_batch_start",
        "Starting batch resolver request",
        question_count=len(questions),
        limit=request.limit,
    )

    items = []

    for question in questions:
        result = _resolve_direct(question, request.limit)
        top = (result.get("results") or [{}])[0] if result.get("results") else None

        items.append(
            {
                "question": question,
                "success": result.get("success"),
                "elapsed_ms": result.get("elapsed_ms"),
                "exact_terms": result.get("exact_terms"),
                "semantic_queries": result.get("semantic_queries"),
                "top_result": top,
                "results": result.get("results", []),
                "error_type": result.get("error_type"),
                "error": result.get("error"),
            }
        )

    elapsed_ms = int((time.time() - started) * 1000)

    payload = {
        "success": True,
        "request_id": request_id,
        "count": len(items),
        "elapsed_ms": elapsed_ms,
        "items": items,
    }

    log_event(
        request_id,
        "resolve_batch_complete",
        "Batch resolver request completed",
        question_count=len(items),
        elapsed_ms=elapsed_ms,
    )

    return payload


@app.post("/plan")
def plan(request: AskRequest):
    """
    DB-free query planner endpoint.
    Uses question understanding + 16M+ value resolver + routers/templates.
    Does not execute Oracle.
    """
    request_id = new_request_id()
    set_request_id(request_id)

    question = request.question.strip()

    if not question:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "request_id": request_id,
                "error": "Question cannot be empty.",
            },
        )

    log_event(
        request_id,
        "api_plan_start",
        "Starting /plan request",
        question=question,
    )

    try:
        result = plan_query(question)
        result["request_id"] = request_id

        log_event(
            request_id,
            "api_plan_complete",
            "Completed /plan request",
            question=question,
            module=result.get("module"),
            preferred_path=(result.get("decision") or {}).get("preferred_path"),
            router_question=result.get("router_question"),
            elapsed_ms=result.get("elapsed_ms"),
        )

        return result

    except Exception as exc:
        log_event(
            request_id,
            "api_plan_error",
            "Planner failed",
            question=question,
            error_type=type(exc).__name__,
            error=str(exc),
        )

        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "request_id": request_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )


@app.post("/ask")
def ask(request: AskRequest):
    request_id = new_request_id()
    set_request_id(request_id)

    question = request.question.strip()

    if not question:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "request_id": request_id,
                "error": "Question cannot be empty.",
            },
        )

    log_event(
        request_id,
        "api_ask_start",
        "Starting /ask request",
        question=question,
    )

    result = answer_question(question)
    result.setdefault("request_id", request_id)

    log_event(
        request_id,
        "api_ask_complete" if result.get("success") else "api_ask_error",
        "Completed /ask request",
        question=question,
        success=result.get("success"),
        source=result.get("source"),
        intent=result.get("intent"),
        row_count=result.get("row_count"),
        elapsed_ms=result.get("elapsed_ms"),
        error=result.get("error"),
        error_type=result.get("error_type"),
    )

    if not result.get("success"):
        return JSONResponse(
            status_code=200,
            content={
                "success": False,
                "request_id": result.get("request_id"),
                "error": result.get("error"),
                "error_type": result.get("error_type"),
                "elapsed_ms": result.get("elapsed_ms"),
            },
        )

    return {
        "success": True,
        "request_id": request_id,
        "data": result,
    }
