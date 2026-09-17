"""Development-only API for offline spaCy question analysis."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.grounded_sql_generator import (
    GroundedSqlModelUnavailableError,
    GroundedSqlResponseError,
    GroundedSqlResult,
    generate_grounded_sql,
)
from app.grounded_sql_validator import GroundedSqlValidationError
from app.query_plan import QueryPlan
from app.query_plan_extractor import (
    QueryPlanExtractionError,
    QueryPlanResponseError,
    QueryPlanValidationError,
    extract_query_plan,
)
from app.schema_grounding import GroundedSchemaPlan, ground_query_plan
from app.spacy_nlp import NLPAnalysis, NLPAnalysisError, analyze_question_with_spacy
from app.text_correction import TextCorrectionError, TextCorrectionResult, correct_question_text


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
    correction: TextCorrectionResult
    analysis: NLPAnalysis


class NLPUnderstandResponse(BaseModel):
    correction: TextCorrectionResult
    analysis: NLPAnalysis
    query_plan: QueryPlan
    requires_clarification: bool


class NLPGroundResponse(BaseModel):
    correction: TextCorrectionResult
    analysis: NLPAnalysis
    query_plan: QueryPlan
    grounding: GroundedSchemaPlan
    requires_clarification: bool


class NLPSqlPreviewResponse(BaseModel):
    correction: TextCorrectionResult
    analysis: NLPAnalysis
    query_plan: QueryPlan
    grounding: GroundedSchemaPlan
    sql_preview: GroundedSqlResult
    requires_clarification: bool


def _enabled() -> bool:
    return os.getenv("NLP_ANALYSIS_API_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _query_plan_enabled() -> bool:
    return os.getenv("NLP_QUERY_PLAN_API_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _grounding_enabled() -> bool:
    return os.getenv("NLP_GROUNDING_API_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _sql_preview_enabled() -> bool:
    return os.getenv("NLP_SQL_PREVIEW_API_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


@router.post("/v1/nlp/analyze", response_model=NLPAnalyzeResponse)
def analyze(request: NLPAnalyzeRequest) -> NLPAnalyzeResponse:
    if not _enabled():
        raise HTTPException(status_code=404, detail="Not found")
    try:
        correction = correct_question_text(request.question)
        return NLPAnalyzeResponse(analysis=analyze_question_with_spacy(correction.corrected_question), correction=correction)
    except (NLPAnalysisError, TextCorrectionError) as exc:
        raise HTTPException(status_code=422, detail="Question could not be analyzed.") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="NLP analysis is unavailable.") from exc


@router.post("/v1/nlp/understand", response_model=NLPUnderstandResponse)
def understand(request: NLPAnalyzeRequest) -> NLPUnderstandResponse:
    if not _query_plan_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    try:
        correction = correct_question_text(request.question)
        analysis = analyze_question_with_spacy(correction.corrected_question)
        plan = extract_query_plan(
            correction.corrected_question,
            nlp_analysis=analysis,
            original_question=correction.original_question,
        )
        return NLPUnderstandResponse(
            correction=correction,
            analysis=analysis,
            query_plan=plan,
            requires_clarification=plan.requires_clarification,
        )
    except (NLPAnalysisError, TextCorrectionError) as exc:
        raise HTTPException(status_code=422, detail="Question could not be analyzed.") from exc
    except (QueryPlanResponseError, QueryPlanValidationError) as exc:
        raise HTTPException(status_code=422, detail="Model output could not be validated.") from exc
    except QueryPlanExtractionError as exc:
        raise HTTPException(status_code=502, detail="Question understanding model is unavailable.") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="NLP understanding is unavailable.") from exc


@router.post("/v1/nlp/ground", response_model=NLPGroundResponse)
def ground(request: NLPAnalyzeRequest) -> NLPGroundResponse:
    if not _grounding_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    try:
        correction = correct_question_text(request.question)
        analysis = analyze_question_with_spacy(correction.corrected_question)
        plan = extract_query_plan(
            correction.corrected_question,
            nlp_analysis=analysis,
            original_question=correction.original_question,
        )
        grounding = ground_query_plan(plan)
        return NLPGroundResponse(
            correction=correction,
            analysis=analysis,
            query_plan=plan,
            grounding=grounding,
            requires_clarification=plan.requires_clarification or not grounding.is_grounded,
        )
    except (NLPAnalysisError, TextCorrectionError) as exc:
        raise HTTPException(status_code=422, detail="Question could not be analyzed.") from exc
    except (QueryPlanResponseError, QueryPlanValidationError) as exc:
        raise HTTPException(status_code=422, detail="Model output could not be validated.") from exc
    except QueryPlanExtractionError as exc:
        raise HTTPException(status_code=502, detail="Question understanding model is unavailable.") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="NLP grounding is unavailable.") from exc


@router.post("/v1/nlp/sql-preview", response_model=NLPSqlPreviewResponse)
def sql_preview(request: NLPAnalyzeRequest) -> NLPSqlPreviewResponse:
    if not _sql_preview_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    try:
        correction = correct_question_text(request.question)
        analysis = analyze_question_with_spacy(correction.corrected_question)
        plan = extract_query_plan(
            correction.corrected_question,
            nlp_analysis=analysis,
            original_question=correction.original_question,
        )
        if plan.requires_clarification:
            raise GroundedSqlValidationError("QueryPlan requires clarification.")
        grounding = ground_query_plan(plan)
        if not grounding.is_grounded:
            raise GroundedSqlValidationError("QueryPlan could not be grounded safely.")
        preview = generate_grounded_sql(plan, grounding)
        return NLPSqlPreviewResponse(
            correction=correction,
            analysis=analysis,
            query_plan=plan,
            grounding=grounding,
            sql_preview=preview,
            requires_clarification=False,
        )
    except (NLPAnalysisError, TextCorrectionError) as exc:
        raise HTTPException(status_code=422, detail="Question could not be analyzed.") from exc
    except (QueryPlanResponseError, QueryPlanValidationError) as exc:
        raise HTTPException(status_code=422, detail="Model output could not be validated.") from exc
    except QueryPlanExtractionError as exc:
        raise HTTPException(status_code=502, detail="Question understanding model is unavailable.") from exc
    except GroundedSqlModelUnavailableError as exc:
        raise HTTPException(status_code=502, detail="SQL preview model is unavailable.") from exc
    except (GroundedSqlResponseError, GroundedSqlValidationError) as exc:
        raise HTTPException(status_code=422, detail="SQL preview could not be validated.") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="SQL preview is unavailable.") from exc
