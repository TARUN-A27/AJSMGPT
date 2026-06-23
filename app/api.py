from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.query_engine import answer_question


app = FastAPI(title="AJSMGPT API")

BASE_DIR = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class AskRequest(BaseModel):
    question: str


@app.get("/")
def home():
    return FileResponse(TEMPLATES_DIR / "index.html")


@app.post("/ask")
def ask(request: AskRequest):
    question = request.question.strip()

    if not question:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "Question cannot be empty.",
            },
        )

    result = answer_question(question)

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
        "data": result,
    }
