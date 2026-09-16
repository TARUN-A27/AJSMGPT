"""Development-only API for offline spaCy question analysis."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.query_plan import QueryPlan
from app.query_plan_extractor import (
    QueryPlanExtractionError,
    QueryPlanResponseError,
    QueryPlanValidationError,
    extract_query_plan,
)
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


class NLPUnderstandResponse(BaseModel):
    analysis: NLPAnalysis
    query_plan: QueryPlan
    requires_clarification: bool


def _enabled() -> bool:
    return os.getenv("NLP_ANALYSIS_API_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _query_plan_enabled() -> bool:
    return os.getenv("NLP_QUERY_PLAN_API_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


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


@router.post("/v1/nlp/understand", response_model=NLPUnderstandResponse)
def understand(request: NLPAnalyzeRequest) -> NLPUnderstandResponse:
    if not _query_plan_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    try:
        analysis = analyze_question_with_spacy(request.question)
        plan = extract_query_plan(request.question, nlp_analysis=analysis)
        return NLPUnderstandResponse(
            analysis=analysis, query_plan=plan, requires_clarification=plan.requires_clarification
        )
    except (QueryPlanResponseError, QueryPlanValidationError) as exc:
        raise HTTPException(status_code=422, detail="Model output could not be validated.") from exc
    except QueryPlanExtractionError as exc:
        raise HTTPException(status_code=502, detail="Question understanding model is unavailable.") from exc
    except NLPAnalysisError as exc:
        raise HTTPException(status_code=422, detail="Question could not be analyzed.") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="NLP understanding is unavailable.") from exc
