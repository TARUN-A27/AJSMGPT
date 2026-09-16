"""Development-only API for offline spaCy question analysis."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.spacy_nlp import NLPAnalysis, NLPAnalysisError, analyze_question_with_spacy


router = APIRouter()


class NLPAnalyzeRequest(BaseModel):
    question: str

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Question cannot be empty.")
        return value


class NLPAnalyzeResponse(BaseModel):
    analysis: NLPAnalysis


def _enabled() -> bool:
    return os.getenv("NLP_ANALYSIS_API_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


@router.post("/v1/nlp/analyze", response_model=NLPAnalyzeResponse)
def analyze(request: NLPAnalyzeRequest) -> NLPAnalyzeResponse:
    if not _enabled():
        raise HTTPException(status_code=404, detail="Not found")
    try:
        return NLPAnalyzeResponse(analysis=analyze_question_with_spacy(request.question))
    except NLPAnalysisError as exc:
        raise HTTPException(status_code=422, detail="Question could not be analyzed.") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="NLP analysis is unavailable.") from exc
