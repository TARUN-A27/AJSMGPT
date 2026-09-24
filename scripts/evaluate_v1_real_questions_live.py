"""
V1 real-question evaluation harness -- LIVE ORACLE VARIANT.

DANGER: unlike scripts/evaluate_v1_real_questions.py (which this file reuses
question selection and classification logic from, but never imports Oracle-
adjacent state from), this script executes against the REAL company Oracle
database through the REAL, unmodified production seams:
  - entity resolution: app.nlp_execution._default_resolve_entities
    (-> resolve_plan_entities -> oracle_entity_lookup -> run_safe_select)
  - business execution: app.nlp_execution._default_runner
    (-> run_safe_select)
Neither is overridden here. This is Step 6 (CLAUDE.md order of work) -- run
it ONLY on the server, with the ajsmgpt_ro read-only account
(docs/ORACLE_READONLY_ACCOUNT.md), NEVER on the laptop (CLAUDE.md §9).

CLAUDE.md §3: "Never expose credentials, DSNs, or ERP result rows in code,
logs, tests, or chat." A real execution's response DOES carry real business
row data (NLPExecuteResponse.rows/columns/summary). This script deliberately
NEVER writes those three fields to disk or stdout -- only row_count (an
integer, like the COUNT(*) queries already used throughout this project's
Oracle study) and structural metadata (classification, stage, elapsed time,
the query plan/grounding/SQL shapes, which never contain literal business
values since they are parameterized).

Run (on the server only):
    ./venv/bin/python scripts/evaluate_v1_real_questions_live.py

Requires the server's local Ollama (OLLAMA_URL/OLLAMA_CHAT_MODEL already
correct in the server's own environment) and the ajsmgpt_ro Oracle account
already configured in .env (never read or written by this script directly;
app.oracle_client reads it).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("OLLAMA_CHAT_MODEL", "qwen3:14b")

import app.grounded_sql_generator as grounded_sql_generator_module  # noqa: E402
import app.query_plan_extractor as query_plan_extractor_module  # noqa: E402
from app.grounded_sql_generator import (  # noqa: E402
    GroundedSqlModelUnavailableError,
    GroundedSqlResponseError,
    generate_grounded_sql,
)
from app.grounded_sql_validator import GroundedSqlValidationError  # noqa: E402
from app.nlp_execution import (  # noqa: E402
    ExecutionRejectedError,
    NLPExecutionDependencies,
    execute_nlp_query,
)
from app.ollama_client import chat_with_qwen as _real_chat_with_qwen  # noqa: E402
from app.oracle_client import OracleExecutionError, OracleUnavailableError  # noqa: E402
from app.query_plan_extractor import (  # noqa: E402
    QueryPlanExtractionError,
    QueryPlanResponseError,
    QueryPlanValidationError,
    extract_query_plan,
)
from app.schema_grounding import ground_query_plan  # noqa: E402
from app.spacy_nlp import NLPAnalysisError  # noqa: E402
from app.text_correction import TextCorrectionError  # noqa: E402

# Pure, non-Oracle helpers reused verbatim from the offline evaluator so
# question selection and classification logic never drifts between the two.
# Nothing Oracle-adjacent is imported from that module.
from scripts.evaluate_v1_real_questions import (  # noqa: E402
    CATALOG_LIMITATION_PHRASES,
    KNOWN_UNSUPPORTED_DOMAINS,
    OUT_OF_SCOPE_HINTS,
    _ENTITY_UNRESOLVED_MARKER,
    _cause_is_connection_error,
    _expected_unsupported,
    _is_genuine_multi_candidate_ambiguity,
    _select_questions,
)

RESULTS_PATH = PROJECT_ROOT / "scripts" / "v1_real_question_eval_results_live.json"


def _classify(question: str, exc: BaseException | None) -> dict:
    """Identical logic to the offline evaluator's _classify, plus the two
    live-only Oracle exception types that never occur off Oracle."""
    if exc is None:
        return {"classification": "PASS_PIPELINE", "stage": "NONE", "reason": ""}

    if isinstance(exc, TextCorrectionError):
        return {"classification": "CORRECTION_FAILURE", "stage": "CORRECTION", "reason": str(exc)}
    if isinstance(exc, NLPAnalysisError):
        return {"classification": "SPACY_FAILURE", "stage": "SPACY", "reason": str(exc)}
    if isinstance(exc, (QueryPlanResponseError, QueryPlanValidationError)):
        return {"classification": "QUERY_PLAN_FAILURE", "stage": "QUERY_PLAN", "reason": str(exc)}
    if isinstance(exc, QueryPlanExtractionError):
        if _cause_is_connection_error(exc):
            return {"classification": "ENVIRONMENT_FAILURE", "stage": "QUERY_PLAN", "reason": str(exc)}
        return {"classification": "QUERY_PLAN_FAILURE", "stage": "QUERY_PLAN", "reason": str(exc)}
    if isinstance(exc, ExecutionRejectedError):
        response = exc.response
        reasons = "; ".join(response.reject_reasons) or "; ".join(response.ambiguities)
        if response.grounding is None:
            if response.reject_reasons:
                domain = (response.query_plan.domain if response.query_plan else "").lower()
                if domain in KNOWN_UNSUPPORTED_DOMAINS or domain in OUT_OF_SCOPE_HINTS:
                    return {"classification": "UNSUPPORTED_EXPECTED", "stage": "CAPABILITY",
                            "reason": f"domain={domain!r}: {reasons}"}
                return {"classification": "CAPABILITY_FAILURE", "stage": "CAPABILITY",
                        "reason": f"domain={domain!r}: {reasons}"}
            entity_ambiguities = [a for a in response.ambiguities if _ENTITY_UNRESOLVED_MARKER in a]
            if entity_ambiguities:
                if len(entity_ambiguities) == len(response.ambiguities) and all(
                    _is_genuine_multi_candidate_ambiguity(a) for a in entity_ambiguities
                ):
                    return {"classification": "ENTITY_AMBIGUOUS_DEFERRED", "stage": "ENTITY_RESOLUTION",
                            "reason": reasons}
                return {"classification": "ENTITY_RESOLUTION_REJECTION", "stage": "ENTITY_RESOLUTION",
                        "reason": reasons}
            return {"classification": "QUERY_PLAN_FAILURE", "stage": "QUERY_PLAN",
                    "reason": f"QueryPlan flagged clarification-required: {reasons}"}
        limitation = next((p for p in CATALOG_LIMITATION_PHRASES if p in reasons), None)
        return {"classification": "GROUNDING_FAILURE", "stage": "GROUNDING",
                "reason": reasons, "catalog_limitation": limitation}
    if isinstance(exc, GroundedSqlModelUnavailableError):
        return {"classification": "ENVIRONMENT_FAILURE", "stage": "SQL_GENERATION", "reason": str(exc)}
    if isinstance(exc, GroundedSqlResponseError):
        return {"classification": "SQL_GENERATION_FAILURE", "stage": "SQL_GENERATION", "reason": str(exc)}
    if isinstance(exc, GroundedSqlValidationError):
        return {"classification": "SQL_VALIDATION_FAILURE", "stage": "SQL_VALIDATION", "reason": str(exc)}
    if isinstance(exc, OracleUnavailableError):
        return {"classification": "ENVIRONMENT_FAILURE", "stage": "ORACLE", "reason": str(exc)}
    if isinstance(exc, OracleExecutionError):
        return {"classification": "SQL_EXECUTION_FAILURE", "stage": "ORACLE", "reason": str(exc)}
    return {"classification": "HARNESS_FAILURE", "stage": "OTHER", "reason": f"{type(exc).__name__}: {exc}"}


def _run_one(question: str) -> dict:
    """Real V1 pipeline, real Oracle. Never records rows/columns/summary --
    only row_count and structural metadata (see module docstring)."""
    capture: dict = {"raw_model_calls": [], "full_query_plan": None, "full_grounding": None, "full_sql_result": None}

    def _recording_chat(caller_label):
        def wrapper(*args, **kwargs):
            raw = _real_chat_with_qwen(*args, **kwargs)
            capture["raw_model_calls"].append({"caller": caller_label, "response": raw})
            return raw
        return wrapper

    def _recording_extract_plan(*args, **kwargs):
        plan = extract_query_plan(*args, **kwargs)
        capture["full_query_plan"] = plan.model_dump(mode="json")
        return plan

    def _recording_ground_plan(plan):
        grounding = ground_query_plan(plan)
        capture["full_grounding"] = grounding.model_dump(mode="json")
        return grounding

    def _recording_generate_sql(plan, grounding):
        result = generate_grounded_sql(plan, grounding)
        capture["full_sql_result"] = result.model_dump(mode="json")
        return result

    # No resolve_entities= / runner= overrides: both take their real,
    # Oracle-backed defaults (_default_resolve_entities / _default_runner).
    deps = NLPExecutionDependencies(
        extract_plan=_recording_extract_plan,
        ground_plan=_recording_ground_plan,
        generate_sql=_recording_generate_sql,
    )
    record: dict = {"question": question}
    started = time.monotonic()

    original_qpe_chat = query_plan_extractor_module.chat_with_qwen
    original_gsg_chat = grounded_sql_generator_module.chat_with_qwen
    query_plan_extractor_module.chat_with_qwen = _recording_chat("query_plan_extractor")
    grounded_sql_generator_module.chat_with_qwen = _recording_chat("grounded_sql_generator")
    try:
        try:
            response = execute_nlp_query(question, dependencies=deps)
            record["corrected_question"] = response.correction.corrected_question
            record["query_plan"] = response.query_plan.model_dump(mode="json")
            record["grounding"] = response.grounding.model_dump(mode="json")
            record["sql_preview"] = response.sql_preview
            # Deliberately NOT record["rows"]/["columns"]/["summary"] -- see
            # module docstring. row_count is an aggregate integer, not row data.
            record["row_count"] = response.row_count
            record["truncated"] = response.truncated
            record["report_type"] = response.report_type.value if hasattr(response.report_type, "value") else str(response.report_type)
            record.update(_classify(question, None))
        except Exception as exc:  # noqa: BLE001
            classification = _classify(question, exc)
            record.update(classification)
            if isinstance(exc, ExecutionRejectedError):
                resp = exc.response
                record["query_plan"] = resp.query_plan.model_dump(mode="json") if resp.query_plan else None
                record["grounding"] = resp.grounding.model_dump(mode="json") if resp.grounding else None
            if record["classification"] == "HARNESS_FAILURE":
                record["traceback"] = traceback.format_exc(limit=6)
            if exc.__cause__ is not None:
                record["cause"] = f"{type(exc.__cause__).__name__}: {exc.__cause__}"
    finally:
        query_plan_extractor_module.chat_with_qwen = original_qpe_chat
        grounded_sql_generator_module.chat_with_qwen = original_gsg_chat

    record["raw_model_calls"] = capture["raw_model_calls"]
    record["full_query_plan"] = capture["full_query_plan"]
    record["full_grounding"] = capture["full_grounding"]
    record["full_sql_result"] = capture["full_sql_result"]
    record["elapsed_s"] = round(time.monotonic() - started, 1)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split", choices=("all", "train", "test"), default="all",
        help="'test' = the held-out half for the depth-bar measurement "
             "(never used to tune prompts/catalog); 'train' = everything "
             "else; 'all' = today's default, no split applied.",
    )
    args = parser.parse_args()

    questions = _select_questions(args.split)
    results = []
    print(f"LIVE ORACLE RUN. Selected {len(questions)} real questions (split={args.split}). "
          f"Ollama: {os.environ['OLLAMA_URL']} model={os.environ['OLLAMA_CHAT_MODEL']}")
    print("Entity resolution and execution are REAL Oracle calls (ajsmgpt_ro, read-only).")

    for index, item in enumerate(questions, start=1):
        question = item["question"]
        expected_unsupported = _expected_unsupported(item.get("category", ""), question)
        record = _run_one(question)
        record["category"] = item.get("category")
        record["occurrence_count"] = item.get("occurrence_count")
        record["expected_unsupported"] = expected_unsupported
        results.append(record)
        RESULTS_PATH.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        row_note = f" rows={record['row_count']}" if record.get("classification") == "PASS_PIPELINE" else ""
        print(f"[{index}/{len(questions)}] {record['classification']:<24} "
              f"({record['elapsed_s']:>5}s){row_note} {question[:60]}")

    print(f"\nWrote {len(results)} results to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
