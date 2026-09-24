"""
V1 real-question evaluation harness (evaluation-only; not a unit test).

Exercises the REAL V1 pipeline end to end, using the actual production
functions (correct_question_text, analyze_question_with_spacy,
extract_query_plan, evaluate_capability [via execute_nlp_query's own gate],
ground_query_plan, generate_grounded_sql), against a real local Ollama/Qwen
model. Two seams are substituted to keep this off Oracle entirely: the
runner (a stub that never connects) and entity resolution (the same
offline, fixture-backed lookup used in Phase 9C, via
scripts.test_entity_resolution's fake_lookup/SUPPLIER_ROWS/MATERIAL_ROWS --
no oracle_entity_lookup call is ever reachable from this harness). Everything
else is the real, unmodified V1 code path (app/nlp_execution.execute_nlp_query).

Run:
    ./venv/bin/python scripts/evaluate_v1_real_questions.py

Requires a local Ollama server with a chat-capable model (defaults to
localhost:11434 / qwen3:8b, overridable via OLLAMA_URL / OLLAMA_CHAT_MODEL
env vars set before this script runs, e.g. from the shell).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# The repo's own .env points OLLAMA_URL at a remote office-network host that
# is unreachable from this laptop. A real local Ollama instance is running
# instead; point the client at it WITHOUT touching the .env file. These must
# be set before any app.* module is imported, since app/ollama_client.py
# reads them as module-level constants at import time.
os.environ.setdefault("OLLAMA_URL", "http://localhost:11434")
os.environ.setdefault("OLLAMA_CHAT_MODEL", "qwen3:8b")

import app.grounded_sql_generator as grounded_sql_generator_module  # noqa: E402
import app.query_plan_extractor as query_plan_extractor_module  # noqa: E402
from app.entity_resolution import resolve_plan_entities  # noqa: E402
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
    selected_fields_from_sql,
)
from app.ollama_client import chat_with_qwen as _real_chat_with_qwen  # noqa: E402
from app.query_plan_extractor import (  # noqa: E402
    QueryPlanExtractionError,
    QueryPlanResponseError,
    QueryPlanValidationError,
    extract_query_plan,
)
from app.schema_grounding import ground_query_plan  # noqa: E402
from app.spacy_nlp import NLPAnalysisError  # noqa: E402
from app.text_correction import TextCorrectionError  # noqa: E402
from scripts.test_entity_resolution import MATERIAL_ROWS, SUPPLIER_ROWS, fake_lookup  # noqa: E402

# Entity resolution must stay off Oracle for this evaluation (measurement-only,
# no Oracle/network calls). This is the SAME offline fixture-backed lookup
# contract Phase 9C used: a small, known-verified set of supplier/material
# rows. Any entity value not in it resolves UNRESOLVED, deterministically --
# an honest "no verified offline data" signal rather than a fabricated match
# or a real Oracle round trip.
_OFFLINE_ENTITY_LOOKUP = fake_lookup({**SUPPLIER_ROWS, **MATERIAL_ROWS})


def _offline_resolve_entities(plan):
    return resolve_plan_entities(plan, _OFFLINE_ENTITY_LOOKUP)

QUESTION_BANK = PROJECT_ROOT / "data" / "question_bank_v1.json"
RESULTS_PATH = PROJECT_ROOT / "scripts" / "v1_real_question_eval_results.json"

# Per-family selection cap. Every one of the 7 v1.0 families (CLAUDE.md §6)
# gets a real floor -- material_lookup and consumption/grn had none before
# this (2026-09-24) beyond a keyword-matched slice of a smaller, uncategorized
# bank. purchase keeps a higher cap since it's already the deepest-tested
# family and the pool (84) would otherwise dominate the run.
FAMILY_CAPS = {
    "material_lookup": 10,
    "consumption": 10,
    "grn": 10,
    "stock": 10,
    "supplier_lookup": 10,
    "mrs": 10,
    "purchase": 20,
}
OUT_OF_SCOPE_CAP = 8

KNOWN_UNSUPPORTED_DOMAINS: set[str] = set()
# stock/grn/inventory/"goods receipt"(+" note") were here until 2026-09-23
# (fix.md #13): both are now supported families (aggregate-only for stock,
# by design -- ITEMSTOCK holds 1-31 rows per item). Leaving them in this set
# would mislabel a genuine CAPABILITY_FAILURE (e.g. "keyboard stock" asking
# for operation=detail, which stock deliberately does not support) as
# UNSUPPORTED_EXPECTED -- "working as intended" when it is really a model
# operation-choice failure, the more useful signal for Step 4-style work.
# Domains the V1 catalog never claims to cover at all (HR/security/etc.);
# the model is explicitly instructed to emit domain="unknown" for these.
OUT_OF_SCOPE_HINTS = {"unknown", ""}


def _quote_normalised(question: str) -> str:
    """Collapse quoted entity values so near-duplicate questions dedupe."""
    return re.sub(r'"[^"]*"', '"X"', question).strip().lower()


def _select_questions() -> list[dict]:
    # data/user_purchase_mrs_questions.txt used to be topped up here for extra
    # phrasing variety; verified 2026-09-24 that all 24 of its lines are
    # already present in this bank (it was one of the bank's own sources), so
    # the top-up is now a no-op and was dropped.
    bank = json.loads(QUESTION_BANK.read_text(encoding="utf-8"))["questions"]

    def bucket(predicate, limit):
        seen = set()
        picked = []
        candidates = [q for q in bank if predicate(q)]
        candidates.sort(key=lambda q: -q.get("occurrences", 0))
        for q in candidates:
            key = _quote_normalised(q["question"])
            if key in seen:
                continue
            seen.add(key)
            picked.append({
                "question": q["question"],
                "category": q["proposed_family"],
                "occurrence_count": q.get("occurrences", 0),
            })
            if len(picked) >= limit:
                break
        return picked

    selected = []
    for family, limit in FAMILY_CAPS.items():
        selected += bucket(lambda q, f=family: q.get("proposed_family") == f, limit)
    selected += bucket(
        lambda q: q.get("proposed_family", "").startswith("out_of_scope"), OUT_OF_SCOPE_CAP,
    )
    return selected


def _expected_unsupported(category: str, question: str) -> bool:
    # stock and grn are supported families (fix.md #13); a question naming
    # them is no longer expected to be refused by design -- whether it
    # actually passes end to end is now a genuine measurement.
    return category.startswith("out_of_scope")


CATALOG_LIMITATION_PHRASES = (
    "No verified V1 column supports this required concept",
    "Domain is outside the V1 business schema catalog",
    "Required table has no verified relationship path",
)

# Marks a QueryPlan-stage blocking ambiguity that came from the entity
# resolution gate (nlp_execution.py's `unresolved` list), not from the model
# producing an invalid/malformed plan. Distinguishing this (and the
# catalog/capability ambiguities below) from genuine QUERY_PLAN_FAILURE is
# the classification fix requested for this phase: the model extracted a
# valid plan and the deterministic gate correctly rejected it -- that is
# success of the safety architecture, not a model defect.
_ENTITY_UNRESOLVED_MARKER = "requires verified resolution before execution"


def _cause_is_connection_error(exc: BaseException) -> bool:
    cause = exc.__cause__
    if cause is None:
        return False
    name = type(cause).__name__
    return "Connection" in name or "Timeout" in name


def _classify(question: str, exc: BaseException | None) -> dict:
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
            # This gate runs before grounding, and capability.reject_reasons
            # and the blocking-ambiguities list are independent -- a plan can
            # carry both at once (e.g. an unsupported measure AND a low
            # confidence score). A capability/domain rejection is the more
            # fundamental verdict (the question is out of scope regardless of
            # entity resolution), so it takes priority when present.
            if response.reject_reasons:
                domain = (response.query_plan.domain if response.query_plan else "").lower()
                if domain in KNOWN_UNSUPPORTED_DOMAINS or domain in OUT_OF_SCOPE_HINTS:
                    return {
                        "classification": "UNSUPPORTED_EXPECTED", "stage": "CAPABILITY",
                        "reason": f"domain={domain!r}: {reasons}",
                    }
                return {
                    "classification": "CAPABILITY_FAILURE", "stage": "CAPABILITY",
                    "reason": f"domain={domain!r}: {reasons}",
                }
            if any(_ENTITY_UNRESOLVED_MARKER in a for a in response.ambiguities):
                return {
                    "classification": "ENTITY_RESOLUTION_REJECTION", "stage": "ENTITY_RESOLUTION",
                    "reason": reasons,
                }
            # Whatever remains (low confidence, a QueryPlan-level blocking
            # ambiguity) is a genuine model-quality signal, not a
            # deterministic capability/entity check -- keep it as a
            # QueryPlan-stage failure.
            return {
                "classification": "QUERY_PLAN_FAILURE", "stage": "QUERY_PLAN",
                "reason": f"QueryPlan flagged clarification-required: {reasons}",
            }
        limitation = next((p for p in CATALOG_LIMITATION_PHRASES if p in reasons), None)
        return {
            "classification": "GROUNDING_FAILURE", "stage": "GROUNDING",
            "reason": reasons, "catalog_limitation": limitation,
        }

    if isinstance(exc, GroundedSqlModelUnavailableError):
        return {"classification": "ENVIRONMENT_FAILURE", "stage": "SQL_GENERATION", "reason": str(exc)}

    if isinstance(exc, GroundedSqlResponseError):
        return {"classification": "SQL_GENERATION_FAILURE", "stage": "SQL_GENERATION", "reason": str(exc)}

    if isinstance(exc, GroundedSqlValidationError):
        return {"classification": "SQL_VALIDATION_FAILURE", "stage": "SQL_VALIDATION", "reason": str(exc)}

    return {
        "classification": "HARNESS_FAILURE", "stage": "OTHER",
        "reason": f"{type(exc).__name__}: {exc}",
    }


def _fake_runner(sql, binds):
    """Never touches Oracle. Returns a zero-row, contract-shaped result."""
    columns = selected_fields_from_sql(sql)
    return {"columns": columns, "rows": [], "row_count": 0, "elapsed_ms": 0}


def _run_one(question: str) -> dict:
    """
    Runs the real pipeline for one question.

    In addition to the existing behavior, this instruments the model-calling
    seams (never the model call's own arguments/options) so that for every
    record -- including QUERY_PLAN_FAILURE -- we attach the raw model
    response text(s) and the full parsed QueryPlan/GroundedSchemaPlan/SQL
    result objects that were produced before the eventual failure point.
    Nothing about the production prompts, schemas, or call parameters is
    changed -- the wrappers only forward arguments and record return values.
    """
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

    deps = NLPExecutionDependencies(
        extract_plan=_recording_extract_plan,
        resolve_entities=_offline_resolve_entities,
        ground_plan=_recording_ground_plan,
        generate_sql=_recording_generate_sql,
        runner=_fake_runner,
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
            record.update(_classify(question, None))
        except Exception as exc:  # noqa: BLE001 - classification handles specificity
            classification = _classify(question, exc)
            record.update(classification)
            if isinstance(exc, ExecutionRejectedError):
                resp = exc.response
                record["query_plan"] = resp.query_plan.model_dump(mode="json") if resp.query_plan else None
                record["grounding"] = resp.grounding.model_dump(mode="json") if resp.grounding else None
            if record["classification"] == "HARNESS_FAILURE":
                record["traceback"] = traceback.format_exc(limit=6)
            # QueryPlanValidationError is raised `from` the pydantic error;
            # the failing-field detail lives only in __cause__.
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
    questions = _select_questions()
    results = []
    print(f"Selected {len(questions)} real questions. Ollama: {os.environ['OLLAMA_URL']} "
          f"model={os.environ['OLLAMA_CHAT_MODEL']}")

    for index, item in enumerate(questions, start=1):
        question = item["question"]
        expected_unsupported = _expected_unsupported(item.get("category", ""), question)
        record = _run_one(question)
        record["category"] = item.get("category")
        record["occurrence_count"] = item.get("occurrence_count")
        record["expected_unsupported"] = expected_unsupported
        results.append(record)
        RESULTS_PATH.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print(
            f"[{index}/{len(questions)}] {record['classification']:<24} "
            f"({record['elapsed_s']:>5}s) {question[:70]}"
        )

    print(f"\nWrote {len(results)} results to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
