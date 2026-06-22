from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

from app.query_engine import answer_question

app = FastAPI(title="AJSMGPT")

BASE_DIR = Path(__file__).resolve().parents[1]

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class AskRequest(BaseModel):
    question: str


@app.get("/", response_class=HTMLResponse)
def home():
    html_path = BASE_DIR / "templates" / "index.html"
    return html_path.read_text(encoding="utf-8")


@app.post("/ask")
def ask_question(request: AskRequest):
    try:
        result = answer_question(request.question)
        return {
            "success": True,
            "data": result,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
        }
