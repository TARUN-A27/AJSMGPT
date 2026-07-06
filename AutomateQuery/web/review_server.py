from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from AutomateQuery.scripts.sql_query_generator import generate_sql_for_patch_question
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


AUTOMATE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = AUTOMATE_DIR.parent
REPORTS_DIR = AUTOMATE_DIR / "reports"
TEMPLATES_DIR = AUTOMATE_DIR / "web" / "templates"
STATIC_DIR = AUTOMATE_DIR / "web" / "static"

GENERATED_EVAL_JSON = REPORTS_DIR / "generated_eval_candidates.json"
REVIEWED_EVAL_JSON = REPORTS_DIR / "reviewed_eval_candidates.json"
APPROVED_EVAL_JSON = REPORTS_DIR / "approved_eval_tests.json"
ROUTER_FIX_JSON = REPORTS_DIR / "router_fix_candidates.json"
QUESTION_BANK_JSON = REPORTS_DIR / "question_bank.json"
ROUTER_PATCH_JSON = REPORTS_DIR / "router_patch_candidates.json"
REVIEWED_ROUTER_PATCH_JSON = REPORTS_DIR / "reviewed_router_patch_candidates.json"
APPROVED_ROUTER_PATCH_JSON = REPORTS_DIR / "approved_router_patches.json"
LAST_PIPELINE_LOG = REPORTS_DIR / "last_pipeline_run.txt"
APPROVED_TEST_RUN_LOG = REPORTS_DIR / "approved_eval_test_run.txt"
ROUTER_PATCH_VERIFY_JSON = REPORTS_DIR / "router_patch_verification_results.json"

app = FastAPI(title="AutomateQuery UI")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def to_pretty_json(value: Any) -> str:
    return json.dumps(value or {}, indent=2, ensure_ascii=False)


templates.env.filters["to_pretty_json"] = to_pretty_json


def read_json(path: Path) -> Any:
    if not path.exists():
        return []

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def write_json(path: Path, data: Any) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


async def read_form(request: Request) -> dict[str, str]:
    body = await request.body()
    parsed = parse_qs(body.decode("utf-8"))
    return {key: values[0] if values else "" for key, values in parsed.items()}


def make_eval_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(record.get("question", "")).strip().lower(),
        record.get("expected_source"),
        record.get("expected_intent"),
        tuple(record.get("expected_sql_contains", [])),
    )


def sync_review_file() -> list[dict[str, Any]]:
    generated_candidates = as_list(read_json(GENERATED_EVAL_JSON))
    existing_reviewed = as_list(read_json(REVIEWED_EVAL_JSON))

    reviewed_by_key = {make_eval_key(record): record for record in existing_reviewed}
    final_reviewed: list[dict[str, Any]] = []

    for candidate in generated_candidates:
        key = make_eval_key(candidate)

        if key in reviewed_by_key:
            final_reviewed.append(reviewed_by_key[key])
            continue

        final_reviewed.append(
            {
                "approved": False,
                "review_note": "",
                "candidate_id": candidate.get("candidate_id"),
                "question": candidate.get("question"),
                "expected_source": candidate.get("expected_source"),
                "expected_intent": candidate.get("expected_intent"),
                "expected_sql_contains": candidate.get("expected_sql_contains", []),
                "auto_apply_allowed": False,
                "status": "waiting_for_human_review",
            }
        )

    write_json(REVIEWED_EVAL_JSON, final_reviewed)
    return final_reviewed


def export_approved_tests() -> list[dict[str, Any]]:
    reviewed = as_list(read_json(REVIEWED_EVAL_JSON))
    approved_tests: list[dict[str, Any]] = []

    for record in reviewed:
        if record.get("approved") is not True:
            continue

        approved_tests.append(
            {
                "question": record.get("question"),
                "expected_source": record.get("expected_source"),
                "expected_intent": record.get("expected_intent"),
                "expected_sql_contains": record.get("expected_sql_contains", []),
                "review_note": record.get("review_note", ""),
            }
        )

    write_json(APPROVED_EVAL_JSON, approved_tests)
    return approved_tests


def update_candidate(
    candidate_id: str,
    *,
    approved: bool | None = None,
    status: str | None = None,
    review_note: str | None = None,
) -> bool:
    reviewed = sync_review_file()
    found = False

    for record in reviewed:
        if str(record.get("candidate_id")) != candidate_id:
            continue

        if approved is not None:
            record["approved"] = approved

        if status is not None:
            record["status"] = status

        if review_note is not None:
            record["review_note"] = review_note

        record["auto_apply_allowed"] = False
        found = True

    write_json(REVIEWED_EVAL_JSON, reviewed)
    return found


def sync_router_patch_review_file() -> list[dict[str, Any]]:
    patch_candidates = as_list(read_json(ROUTER_PATCH_JSON))
    existing_reviewed = as_list(read_json(REVIEWED_ROUTER_PATCH_JSON))

    reviewed_by_patch_id = {
        item.get("patch_id"): item
        for item in existing_reviewed
        if item.get("patch_id")
    }

    final_reviewed: list[dict[str, Any]] = []

    for patch in patch_candidates:
        patch_id = patch.get("patch_id")

        if patch_id in reviewed_by_patch_id:
            existing = reviewed_by_patch_id[patch_id]
            patch["approved"] = existing.get("approved", False)
            patch["review_note"] = existing.get("review_note", "")
            patch["review_status"] = existing.get("review_status", "waiting_for_human_review")
        else:
            patch["approved"] = False
            patch["review_note"] = ""
            patch["review_status"] = "waiting_for_human_review"

        patch["auto_apply_allowed"] = False
        final_reviewed.append(patch)

    write_json(REVIEWED_ROUTER_PATCH_JSON, final_reviewed)
    return final_reviewed


def export_approved_router_patches() -> list[dict[str, Any]]:
    reviewed = as_list(read_json(REVIEWED_ROUTER_PATCH_JSON))
    approved: list[dict[str, Any]] = []

    for patch in reviewed:
        if patch.get("approved") is not True:
            continue

        approved.append(
            {
                "patch_id": patch.get("patch_id"),
                "title": patch.get("title"),
                "target_router_file": patch.get("target_router_file"),
                "intent_name": patch.get("intent_name"),
                "question_patterns": patch.get("question_patterns", []),
                "suggested_extraction": patch.get("suggested_extraction", {}),
                "suggested_sql_logic": patch.get("suggested_sql_logic", []),
                "expected_sql_contains": patch.get("expected_sql_contains", []),
                "review_note": patch.get("review_note", ""),
                "auto_apply_allowed": False,
            }
        )

    write_json(APPROVED_ROUTER_PATCH_JSON, approved)
    return approved


def update_router_patch(
    patch_id: str,
    *,
    approved: bool | None = None,
    review_status: str | None = None,
    review_note: str | None = None,
) -> bool:
    reviewed = sync_router_patch_review_file()
    found = False

    for patch in reviewed:
        if str(patch.get("patch_id")) != patch_id:
            continue

        if approved is not None:
            patch["approved"] = approved

        if review_status is not None:
            patch["review_status"] = review_status

        if review_note is not None:
            patch["review_note"] = review_note

        patch["auto_apply_allowed"] = False
        found = True

    write_json(REVIEWED_ROUTER_PATCH_JSON, reviewed)
    return found


def run_script(script_name: str, log_path: Path, timeout: int = 180) -> tuple[bool, str]:
    script_path = AUTOMATE_DIR / "scripts" / script_name

    if not script_path.exists():
        return False, f"Script not found: {script_path}"

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"{script_name} timed out after {timeout} seconds."

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    log_text = (
        "COMMAND: "
        + " ".join([sys.executable, str(script_path)])
        + "\n\nSTDOUT:\n"
        + result.stdout
        + "\n\nSTDERR:\n"
        + result.stderr
    )

    log_path.write_text(log_text, encoding="utf-8")

    if result.returncode != 0:
        return False, f"{script_name} failed. Check {log_path.name}"

    return True, f"{script_name} completed successfully."



def first_list_value(value: Any, default: str = "") -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return default


def clean_sql_token(value: str) -> str:
    value = str(value or "").strip().upper()
    return value.replace("'", "''")


def material_filter_sql(material_value: str) -> str:
    material_value = clean_sql_token(material_value)
    if material_value:
        return f"AND UPPER(INV.ITEM_NAME) LIKE '%{material_value}%'"
    return "-- material filter pending human review"


def supplier_filter_sql(supplier_value: str) -> str:
    supplier_value = clean_sql_token(supplier_value)
    if supplier_value.isdigit():
        return f"AND PO.SUP_CODE = '{supplier_value}'"
    if supplier_value:
        return f"AND UPPER(P.PARTYNAME) LIKE '%{supplier_value}%'"
    return "-- supplier filter pending human review"


def build_router_patch_sql_preview(patch: dict[str, Any]) -> str:
    intent = str(patch.get("intent_name") or "")
    extraction = patch.get("suggested_extraction") or {}

    material_value = first_list_value(extraction.get("material_name"), "MOUSE")
    supplier_value = first_list_value(
        extraction.get("supplier_name_or_code") or extraction.get("party_code"),
        "PRIME COMPU SYSTEMS",
    )
    year_value = first_list_value(extraction.get("year"), "")

    if intent == "purchase_last_supply_by_material":
        return f"""SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE,
        PO.SUP_CODE,
        P.PARTYNAME AS SUPPLIER_NAME,
        PO.ITEM_CODE,
        INV.ITEM_NAME,
        PO.QTY,
        PO.RATE,
        PO.NET,
        PO.INVQTY,
        (NVL(PO.QTY, 0) - NVL(PO.INVQTY, 0)) AS RECEIPT_PENDING_QTY,
        PO.STATUS
    FROM INVENTORY.PURCHASEORDER PO
    JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE 1 = 1
      {material_filter_sql(material_value)}
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= 1"""

    if intent == "purchase_first_supply_by_supplier":
        order_line = "ORDER BY PO.ORDERDATE ASC NULLS LAST, PO.ORDERNO ASC"
        supplier_filter = supplier_filter_sql(supplier_value)
    elif intent == "purchase_last_supply_by_supplier":
        order_line = "ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC"
        supplier_filter = supplier_filter_sql(supplier_value)
    else:
        order_line = ""
        supplier_filter = ""

    if intent in {"purchase_first_supply_by_supplier", "purchase_last_supply_by_supplier"}:
        return f"""SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE,
        PO.SUP_CODE,
        P.PARTYNAME AS SUPPLIER_NAME,
        PO.ITEM_CODE,
        INV.ITEM_NAME,
        PO.QTY,
        PO.RATE,
        PO.NET,
        PO.INVQTY,
        (NVL(PO.QTY, 0) - NVL(PO.INVQTY, 0)) AS RECEIPT_PENDING_QTY,
        PO.STATUS
    FROM INVENTORY.PURCHASEORDER PO
    JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE 1 = 1
      {supplier_filter}
    {order_line}
)
WHERE ROWNUM <= 1"""

    if intent == "purchase_last_n_purchases_by_material":
        limit_value = "3"

        raw_limits = extraction.get("limit") or []
        if isinstance(raw_limits, list):
            for value in raw_limits:
                value = str(value)
                if value.startswith("N="):
                    limit_value = value.replace("N=", "").strip()
                    break

        if not str(limit_value).isdigit():
            limit_value = "3"

        return f"""SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE,
        PO.ITEM_CODE,
        INV.ITEM_NAME,
        PO.QTY,
        PO.RATE,
        PO.NET,
        PO.SUP_CODE,
        P.PARTYNAME AS SUPPLIER_NAME,
        PO.STATUS
    FROM INVENTORY.PURCHASEORDER PO
    JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE 1 = 1
      {material_filter_sql(material_value)}
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= {limit_value}"""


    if intent == "purchase_rate_by_material":
        year_filter = ""
        if str(year_value).isdigit():
            year_filter = f"AND EXTRACT(YEAR FROM PO.ORDERDATE) = {year_value}"

        return f"""SELECT *
FROM (
    SELECT
        PO.ORDERNO,
        PO.ORDERDATE,
        PO.SUP_CODE,
        P.PARTYNAME AS SUPPLIER_NAME,
        PO.ITEM_CODE,
        INV.ITEM_NAME,
        PO.QTY,
        PO.RATE,
        PO.NET,
        PO.STATUS
    FROM INVENTORY.PURCHASEORDER PO
    JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
    LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
    WHERE 1 = 1
      {material_filter_sql(material_value)}
      {year_filter}
    ORDER BY PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC
)
WHERE ROWNUM <= 20"""

    return "-- SQL preview requires table verification before code generation."


def clean_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): clean_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [clean_for_json(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def read_router_patch_verifications() -> dict[str, Any]:
    data = read_json(ROUTER_PATCH_VERIFY_JSON)
    return data if isinstance(data, dict) else {}


def write_router_patch_verifications(data: dict[str, Any]) -> None:
    write_json(ROUTER_PATCH_VERIFY_JSON, data)


def enrich_router_patches_for_ui(patches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    verifications = read_router_patch_verifications()
    enriched = []

    for patch in patches:
        patch_copy = dict(patch)
        patch_id = str(patch_copy.get("patch_id") or "")
        patch_copy["generated_sql_preview"] = build_router_patch_sql_preview(patch_copy)
        patch_copy["verify_result"] = verifications.get(patch_id)
        enriched.append(patch_copy)

    return enriched


def verify_router_patch(patch_id: str) -> tuple[bool, str]:
    patches = sync_router_patch_review_file()
    selected_patch = None

    for patch in patches:
        if str(patch.get("patch_id")) == patch_id:
            selected_patch = patch
            break

    if selected_patch is None:
        return False, "Router patch not found."

    question_patterns = selected_patch.get("question_patterns") or []
    if not question_patterns:
        return False, "No question available for verification."

    question = str(question_patterns[0]).strip()

    sys.path.insert(0, str(PROJECT_ROOT))
    from app.query_engine import answer_question  # noqa: E402

    try:
        result = clean_for_json(answer_question(question))
    except Exception as exc:
        result = {
            "success": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }

    verifications = read_router_patch_verifications()
    verifications[patch_id] = {
        "patch_id": patch_id,
        "question": question,
        "success": result.get("success"),
        "source": result.get("source"),
        "intent": result.get("intent"),
        "row_count": result.get("row_count"),
        "sql": result.get("sql"),
        "answer": result.get("answer"),
        "error": result.get("error"),
        "raw_result": result,
    }

    write_router_patch_verifications(verifications)
    return True, "Verify output loaded."


@app.get("/health", response_class=PlainTextResponse)
async def health() -> str:
    return "AutomateQuery UI is running"


@app.get("/")
async def dashboard(request: Request):
    question_bank = as_list(read_json(QUESTION_BANK_JSON))
    router_candidates = as_list(read_json(ROUTER_FIX_JSON))
    generated_eval = as_list(read_json(GENERATED_EVAL_JSON))
    reviewed_eval = sync_review_file()
    approved_eval = as_list(read_json(APPROVED_EVAL_JSON))
    router_patches = sync_router_patch_review_file()

    approved_count = sum(1 for item in reviewed_eval if item.get("approved") is True)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "title": "AutomateQuery Dashboard",
            "active": "dashboard",
            "message": request.query_params.get("message", ""),
            "unique_questions": len(question_bank),
            "review_questions": sum(1 for item in question_bank if item.get("needs_review")),
            "router_problem_count": len(router_candidates),
            "wrong_table_count": sum(1 for c in router_candidates if c.get("wrong_tables_detected")),
            "router_patch_count": len(router_patches),
            "generated_eval_count": len(generated_eval),
            "approved_count": approved_count,
            "pending_count": len(reviewed_eval) - approved_count,
            "approved_eval_count": len(approved_eval),
        },
    )


@app.get("/question-bank")
async def question_bank_page(request: Request):
    bank = as_list(read_json(QUESTION_BANK_JSON))

    category_counts: dict[str, int] = {}
    for item in bank:
        category = str(item.get("category") or "unknown")
        category_counts[category] = category_counts.get(category, 0) + 1

    return templates.TemplateResponse(
        request,
        "question_bank.html",
        {
            "title": "AutomateQuery Question Bank",
            "active": "question_bank",
            "items": bank,
            "total": len(bank),
            "review_count": sum(1 for item in bank if item.get("needs_review")),
            "ok_count": sum(1 for item in bank if not item.get("needs_review")),
            "category_counts": sorted(category_counts.items()),
        },
    )


@app.get("/router-candidates")
async def router_candidates_page(request: Request):
    candidates = as_list(read_json(ROUTER_FIX_JSON))

    return templates.TemplateResponse(
        request,
        "router_candidates.html",
        {
            "title": "AutomateQuery Router Candidates",
            "active": "router_candidates",
            "candidates": candidates,
            "total": len(candidates),
            "review_required": sum(
                1
                for c in candidates
                if ((c.get("suggestion") or {}).get("suggested_intent_name") == "REVIEW_REQUIRED")
            ),
            "wrong_table_count": sum(1 for c in candidates if c.get("wrong_tables_detected")),
            "fallback_count": sum(
                1
                for c in candidates
                if "fallback_used" in (c.get("problem_types") or [])
            ),
        },
    )


@app.get("/router-patches")
async def router_patches_page(request: Request):
    patches = enrich_patch_questions_for_ui(enrich_router_patches_for_ui(sync_router_patch_review_file()))

    return templates.TemplateResponse(
        request,
        "router_patches.html",
        {
            "title": "AutomateQuery Router Patches",
            "active": "router_patches",
            "message": request.query_params.get("message", ""),
            "patches": patches,
            "total": len(patches),
            "approved_count": sum(1 for p in patches if p.get("approved") is True),
            "review_count": sum(1 for p in patches if p.get("approved") is not True),
            "high_count": sum(1 for p in patches if p.get("priority") == "high"),
            "medium_count": sum(1 for p in patches if p.get("priority") == "medium"),
        },
    )


@app.get("/eval-review")
async def eval_review_page(request: Request):
    reviewed = sync_review_file()

    approved_count = sum(1 for record in reviewed if record.get("approved") is True)

    return templates.TemplateResponse(
        request,
        "eval_review.html",
        {
            "title": "AutomateQuery Eval Approval",
            "active": "eval_review",
            "message": request.query_params.get("message", ""),
            "records": reviewed,
            "total_count": len(reviewed),
            "approved_count": approved_count,
            "pending_count": len(reviewed) - approved_count,
        },
    )


@app.get("/approval")
async def approval_alias(request: Request):
    return await eval_review_page(request)


@app.post("/run-pipeline")
async def run_pipeline() -> RedirectResponse:
    ok, message = run_script(
        "auto_improve_from_logs.py",
        LAST_PIPELINE_LOG,
    )
    return RedirectResponse(url=f"/?message={quote(message)}", status_code=303)


@app.get("/pipeline-log", response_class=PlainTextResponse)
async def pipeline_log() -> str:
    if not LAST_PIPELINE_LOG.exists():
        return "No pipeline log found yet."

    return LAST_PIPELINE_LOG.read_text(encoding="utf-8")


@app.get("/approved-test-log", response_class=PlainTextResponse)
async def approved_test_log() -> str:
    if not APPROVED_TEST_RUN_LOG.exists():
        return "No approved eval test run log found yet."

    return APPROVED_TEST_RUN_LOG.read_text(encoding="utf-8")


@app.post("/router-patch/sync")
async def router_patch_sync() -> RedirectResponse:
    patches = sync_router_patch_review_file()
    return RedirectResponse(
        url=f"/router-patches?message=Router patch review file synced. Patches available: {len(patches)}",
        status_code=303,
    )



def verify_router_patch_question(patch_id: str, question_index: int) -> tuple[bool, str, dict[str, Any]]:
    patches = sync_router_patch_review_file()
    selected_patch = None

    for patch in patches:
        if str(patch.get("patch_id")) == patch_id:
            selected_patch = patch
            break

    if selected_patch is None:
        return False, "Router patch not found.", {}

    questions = selected_patch.get("question_patterns") or []

    if question_index < 0 or question_index >= len(questions):
        return False, "Question index not found.", {}

    question = str(questions[question_index]).strip()

    if not question:
        return False, "Question is empty.", {}

    sys.path.insert(0, str(PROJECT_ROOT))
    from app.query_engine import answer_question  # noqa: E402

    try:
        result = clean_for_json(answer_question(question))
    except Exception as exc:
        result = {
            "success": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }

    verify_record = {
        "patch_id": patch_id,
        "question_index": question_index,
        "question": question,
        "success": result.get("success"),
        "source": result.get("source"),
        "intent": result.get("intent"),
        "row_count": result.get("row_count"),
        "sql": result.get("sql"),
        "answer": result.get("answer"),
        "error": result.get("error"),
        "raw_result": result,
    }

    verifications = read_router_patch_verifications()
    key = f"{patch_id}::question::{question_index}"
    verifications[key] = verify_record
    write_router_patch_verifications(verifications)

    return bool(result.get("success")), "Question verification completed.", verify_record



def build_question_sql_preview(patch: dict[str, Any], question: str) -> str:
    return generate_sql_for_patch_question(patch, question)

def enrich_patch_questions_for_ui(patches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    verifications = read_router_patch_verifications()
    enriched_patches: list[dict[str, Any]] = []

    for patch in patches:
        patch_copy = dict(patch)
        patch_id = str(patch_copy.get("patch_id") or "")
        questions = patch_copy.get("question_patterns") or []

        question_items = []

        for index, question in enumerate(questions):
            key = f"{patch_id}::question::{index}"
            question_items.append(
                {
                    "index": index,
                    "question": question,
                    "verify_result": verifications.get(key),
                    "expected_sql_preview": build_question_sql_preview(patch_copy, str(question)),
                }
            )

        patch_copy["question_items"] = question_items
        enriched_patches.append(patch_copy)

    return enriched_patches


@app.post("/router-patch/verify/{patch_id}")
async def router_patch_verify(patch_id: str) -> RedirectResponse:
    ok, message = verify_router_patch(patch_id)
    return RedirectResponse(
        url=f"/router-patches?message={quote(message)}",
        status_code=303,
    )



@app.post("/api/router-patch/verify/{patch_id}")
async def api_router_patch_verify(patch_id: str) -> JSONResponse:
    ok, message = verify_router_patch(patch_id)
    results = read_router_patch_verifications()
    result = results.get(patch_id, {})

    return JSONResponse(
        {
            "ok": ok,
            "message": message,
            "result": result,
        }
    )



@app.post("/api/router-patch/verify-question/{patch_id}/{question_index}")
async def api_router_patch_verify_question(patch_id: str, question_index: int) -> JSONResponse:
    ok, message, result = verify_router_patch_question(patch_id, question_index)

    return JSONResponse(
        {
            "ok": ok,
            "message": message,
            "result": result,
        }
    )


@app.post("/router-patch/export-approved")
async def router_patch_export_approved() -> RedirectResponse:
    approved = export_approved_router_patches()
    return RedirectResponse(
        url=f"/router-patches?message=Approved router patches exported: {len(approved)}",
        status_code=303,
    )


@app.post("/router-patch/approve/{patch_id}")
async def router_patch_approve(patch_id: str) -> RedirectResponse:
    found = update_router_patch(
        patch_id,
        approved=True,
        review_status="approved",
    )

    message = "Router patch approved." if found else "Router patch not found."
    return RedirectResponse(url=f"/router-patches?message={quote(message)}", status_code=303)


@app.post("/router-patch/reject/{patch_id}")
async def router_patch_reject(patch_id: str) -> RedirectResponse:
    found = update_router_patch(
        patch_id,
        approved=False,
        review_status="rejected",
    )

    message = "Router patch rejected." if found else "Router patch not found."
    return RedirectResponse(url=f"/router-patches?message={quote(message)}", status_code=303)


@app.post("/router-patch/note/{patch_id}")
async def router_patch_save_note(patch_id: str, request: Request) -> RedirectResponse:
    form = await read_form(request)
    found = update_router_patch(
        patch_id,
        review_note=form.get("review_note", ""),
    )

    message = "Router patch note saved." if found else "Router patch not found."
    return RedirectResponse(url=f"/router-patches?message={quote(message)}", status_code=303)


@app.post("/sync")
async def sync() -> RedirectResponse:
    reviewed = sync_review_file()
    return RedirectResponse(
        url=f"/eval-review?message=Review file synced. Candidates available: {len(reviewed)}",
        status_code=303,
    )


@app.post("/approve/{candidate_id}")
async def approve(candidate_id: str) -> RedirectResponse:
    found = update_candidate(
        candidate_id,
        approved=True,
        status="approved",
    )

    message = "Candidate approved." if found else "Candidate not found."
    return RedirectResponse(url=f"/eval-review?message={quote(message)}", status_code=303)


@app.post("/reject/{candidate_id}")
async def reject(candidate_id: str) -> RedirectResponse:
    found = update_candidate(
        candidate_id,
        approved=False,
        status="rejected",
    )

    message = "Candidate rejected." if found else "Candidate not found."
    return RedirectResponse(url=f"/eval-review?message={quote(message)}", status_code=303)


@app.post("/note/{candidate_id}")
async def save_note(candidate_id: str, request: Request) -> RedirectResponse:
    form = await read_form(request)
    found = update_candidate(
        candidate_id,
        review_note=form.get("review_note", ""),
    )

    message = "Review note saved." if found else "Candidate not found."
    return RedirectResponse(url=f"/eval-review?message={quote(message)}", status_code=303)


@app.post("/export-approved")
async def export_approved() -> RedirectResponse:
    approved_tests = export_approved_tests()
    return RedirectResponse(
        url=f"/eval-review?message=Approved eval tests exported: {len(approved_tests)}",
        status_code=303,
    )


@app.post("/run-approved-tests")
async def run_approved_tests() -> RedirectResponse:
    ok, message = run_script(
        "run_approved_eval_tests.py",
        APPROVED_TEST_RUN_LOG,
    )
    return RedirectResponse(
        url=f"/eval-review?message={quote(message)}",
        status_code=303,
    )


@app.post("/router-patch/apply-approved")
async def apply_approved_router_patches() -> RedirectResponse:
    script_path = PROJECT_ROOT / "AutomateQuery" / "scripts" / "apply_approved_router_patches.py"

    subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(PROJECT_ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    return RedirectResponse("/router-patches", status_code=303)


@app.get("/router-patch/apply-report")
async def router_patch_apply_report() -> PlainTextResponse:
    report_path = PROJECT_ROOT / "AutomateQuery" / "reports" / "apply_approved_router_patches_report.json"

    if not report_path.exists():
        return PlainTextResponse("No apply report found yet.")

    return PlainTextResponse(report_path.read_text(encoding="utf-8"))




# -------------------------------------------------------------------------------------------------
# NLP Review UI
# -------------------------------------------------------------------------------------------------

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
import json as _nlp_json
import os as _nlp_os
import sys as _nlp_sys
from pathlib import Path as _NlpPath
from collections import Counter as _NlpCounter


_NLP_PROJECT_ROOT = _NlpPath(
    _nlp_os.getenv(
        "AJSMGPT_PROJECT_ROOT",
        str(_NlpPath(__file__).resolve().parents[2]),
    )
).resolve()

if str(_NLP_PROJECT_ROOT) not in _nlp_sys.path:
    _nlp_sys.path.insert(0, str(_NLP_PROJECT_ROOT))


def _nlp_review_log_path() -> _NlpPath:
    return _NLP_PROJECT_ROOT / "logs" / "user_questions.jsonl"


def _nlp_review_read_questions(limit: int = 200):
    path = _nlp_review_log_path()
    counter = _NlpCounter()

    if not path.exists():
        return []

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                row = _nlp_json.loads(line)
            except Exception:
                continue

            question = (
                row.get("question")
                or row.get("user_question")
                or row.get("q")
                or ""
            ).strip()

            if question:
                counter[question] += 1

    rows = [
        {"question": question, "count": count}
        for question, count in counter.most_common(limit)
    ]
    return rows


@app.get("/api/nlp-review/questions")
def api_nlp_review_questions(limit: int = 200):
    """
    Returns unique real logged questions for NLP review.
    """
    limit = max(1, min(int(limit or 200), 1000))
    return {
        "success": True,
        "project_root": str(_NLP_PROJECT_ROOT),
        "log_path": str(_nlp_review_log_path()),
        "questions": _nlp_review_read_questions(limit=limit),
    }


@app.post("/api/nlp-review/candidate")
async def api_nlp_review_candidate(request: Request):
    """
    Returns NLP router candidate for one question.
    Safe review mode only.
    """
    from app.nlp_router_bridge import build_nlp_router_candidate

    body = await request.json()
    question = (body.get("question") or "").strip()

    if not question:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": "question_required",
            },
        )

    result = build_nlp_router_candidate(question)
    return result.to_dict()




@app.post("/api/nlp-review/save-feedback")
async def api_nlp_review_save_feedback(request: Request):
    """
    Save human review feedback for NLP result.
    This is append-only JSONL. No production code is changed.
    """
    import datetime as _dt

    body = await request.json()

    question = (body.get("question") or "").strip()
    candidate = body.get("candidate") or {}
    is_correct = bool(body.get("is_correct"))
    note = (body.get("note") or "").strip()

    if not question:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "question_required"},
        )

    out_dir = _NLP_PROJECT_ROOT / "AutomateQuery" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "nlp_review_feedback.jsonl"

    row = {
        "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "question": question,
        "is_correct": is_correct,
        "note": note,
        "candidate": candidate,
    }

    with out_path.open("a", encoding="utf-8") as f:
        f.write(_nlp_json.dumps(row, ensure_ascii=False) + "\n")

    return {
        "success": True,
        "saved_to": str(out_path),
        "row": row,
    }


@app.get("/nlp-review", response_class=HTMLResponse)
def nlp_review_page():
    """
    Simple review UI for Rasa + Duckling NLP router candidates.
    """
    return HTMLResponse("""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>AJSMGPT NLP Review</title>
  <style>
    body {
      margin: 0;
      background: #0f0f10;
      color: #f4f4f5;
      font-family: Arial, sans-serif;
    }
    header {
      padding: 18px 24px;
      border-bottom: 1px solid #2a2a2d;
      background: #151518;
      position: sticky;
      top: 0;
      z-index: 10;
    }
    h1 {
      margin: 0;
      font-size: 22px;
    }
    .sub {
      margin-top: 6px;
      color: #a1a1aa;
      font-size: 13px;
    }
    main {
      padding: 20px 24px;
      max-width: 1280px;
      margin: 0 auto;
    }
    .toolbar {
      display: flex;
      gap: 12px;
      align-items: center;
      margin-bottom: 16px;
      flex-wrap: wrap;
    }
    input, select {
      background: #18181b;
      color: #f4f4f5;
      border: 1px solid #3f3f46;
      border-radius: 8px;
      padding: 10px 12px;
    }
    input {
      min-width: 360px;
      flex: 1;
    }
    button {
      background: #f4f4f5;
      color: #09090b;
      border: 0;
      border-radius: 8px;
      padding: 10px 14px;
      cursor: pointer;
      font-weight: 600;
    }
    button.secondary {
      background: #27272a;
      color: #f4f4f5;
      border: 1px solid #3f3f46;
    }
    button:disabled {
      opacity: 0.55;
      cursor: not-allowed;
    }
    .card {
      background: #18181b;
      border: 1px solid #2f2f33;
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 14px;
    }
    .question-row {
      display: grid;
      grid-template-columns: 1fr auto auto;
      gap: 12px;
      align-items: center;
    }
    .question {
      font-size: 15px;
      line-height: 1.4;
    }
    .badge {
      display: inline-block;
      border-radius: 999px;
      padding: 4px 9px;
      background: #27272a;
      color: #d4d4d8;
      font-size: 12px;
    }
    .result {
      margin-top: 14px;
      border-top: 1px solid #2f2f33;
      padding-top: 14px;
      display: none;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .field {
      background: #0f0f10;
      border: 1px solid #2f2f33;
      border-radius: 10px;
      padding: 10px;
      overflow-wrap: anywhere;
    }
    .label {
      color: #a1a1aa;
      font-size: 12px;
      margin-bottom: 6px;
    }
    .value {
      font-size: 14px;
      white-space: pre-wrap;
    }
    .ok {
      color: #86efac;
    }
    .warn {
      color: #fde68a;
    }
    .bad {
      color: #fca5a5;
    }
    pre {
      background: #0f0f10;
      border: 1px solid #2f2f33;
      border-radius: 10px;
      padding: 12px;
      overflow-x: auto;
      color: #e4e4e7;
    }
    .empty {
      color: #a1a1aa;
      padding: 30px;
      text-align: center;
    }
    @media (max-width: 900px) {
      .grid {
        grid-template-columns: 1fr;
      }
      .question-row {
        grid-template-columns: 1fr;
      }
      input {
        min-width: 0;
      }
    }
  </style>
</head>
<body>
  <header>
    <h1>AJSMGPT NLP Review</h1>
    <div class="sub">
      Review Rasa + Duckling intent, entities, module, confidence and safe_to_apply for real logged questions.
    </div>
  </header>

  <main>
    <div class="toolbar">
      <input id="searchBox" placeholder="Filter questions..." oninput="render()" />
      <select id="limitSelect" onchange="loadQuestions()">
        <option value="100">100 questions</option>
        <option value="200" selected>200 questions</option>
        <option value="500">500 questions</option>
        <option value="1000">1000 questions</option>
      </select>
      <button onclick="loadQuestions()">Reload</button>
      <button class="secondary" onclick="checkVisible()">Check visible</button>
    </div>

    <div id="status" class="sub">Loading...</div>
    <div id="list"></div>
  </main>

<script>
let QUESTIONS = [];

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fmtJson(obj) {
  return escapeHtml(JSON.stringify(obj ?? {}, null, 2));
}

function shortNum(value) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toFixed(4) : "0.0000";
}

async function loadQuestions() {
  const limit = document.getElementById("limitSelect").value;
  const status = document.getElementById("status");
  const list = document.getElementById("list");

  status.textContent = "Loading questions...";
  list.innerHTML = "";

  try {
    const res = await fetch(`/api/nlp-review/questions?limit=${limit}`);
    const data = await res.json();

    if (!data.success) {
      status.textContent = "Failed to load questions.";
      return;
    }

    QUESTIONS = data.questions || [];
    status.textContent = `Loaded ${QUESTIONS.length} unique questions from ${data.log_path}`;
    render();

  } catch (err) {
    status.textContent = "Error loading questions: " + err;
  }
}

function render() {
  const list = document.getElementById("list");
  const search = document.getElementById("searchBox").value.toLowerCase().trim();

  const filtered = QUESTIONS.filter(q => {
    return !search || q.question.toLowerCase().includes(search);
  });

  if (!filtered.length) {
    list.innerHTML = `<div class="empty">No questions found.</div>`;
    return;
  }

  list.innerHTML = filtered.map((row, idx) => {
    const id = "q_" + idx + "_" + Math.random().toString(36).slice(2);
    row._domId = id;

    return `
      <div class="card" data-question="${escapeHtml(row.question)}">
        <div class="question-row">
          <div class="question">${escapeHtml(row.question)}</div>
          <div class="badge">count: ${row.count}</div>
          <button onclick="checkOne(this)">Check NLP</button>
        </div>
        <div class="result"></div>
      </div>
    `;
  }).join("");
}

async function checkOne(button) {
  const card = button.closest(".card");
  const question = card.getAttribute("data-question");
  const resultDiv = card.querySelector(".result");

  button.disabled = true;
  button.textContent = "Checking...";
  resultDiv.style.display = "block";
  resultDiv.innerHTML = `<div class="sub">Running Rasa + Duckling...</div>`;

  try {
    const res = await fetch("/api/nlp-review/candidate", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question})
    });

    const d = await res.json();

    const okClass = d.success ? "ok" : "bad";
    const safeClass = d.safe_to_apply ? "warn" : "ok";

    resultDiv.innerHTML = `
      <div class="grid">
        <div class="field">
          <div class="label">Success</div>
          <div class="value ${okClass}">${escapeHtml(d.success)}</div>
        </div>
        <div class="field">
          <div class="label">Intent</div>
          <div class="value">${escapeHtml(d.intent)}</div>
        </div>
        <div class="field">
          <div class="label">Confidence</div>
          <div class="value">${shortNum(d.confidence)}</div>
        </div>
        <div class="field">
          <div class="label">Module</div>
          <div class="value">${escapeHtml(d.module)}</div>
        </div>
        <div class="field">
          <div class="label">Required entities OK</div>
          <div class="value ${d.required_entities_ok ? "ok" : "bad"}">${escapeHtml(d.required_entities_ok)}</div>
        </div>
        <div class="field">
          <div class="label">safe_to_apply</div>
          <div class="value ${safeClass}">${escapeHtml(d.safe_to_apply)}</div>
        </div>
        <div class="field">
          <div class="label">Reason</div>
          <div class="value">${escapeHtml(d.reason)}</div>
        </div>
        <div class="field">
          <div class="label">Missing entities</div>
          <div class="value">${escapeHtml((d.missing_entities || []).join(", ") || "-")}</div>
        </div>
        <div class="field">
          <div class="label">Required entities</div>
          <div class="value">${escapeHtml((d.required_entities || []).join(", ") || "-")}</div>
        </div>
      </div>

      <div style="margin-top:12px">
        <div class="label">Entities</div>
        <pre>${fmtJson(d.entities)}</pre>
      </div>

      <details style="margin-top:12px">
        <summary class="sub">Full JSON</summary>
        <pre>${fmtJson(d)}</pre>
      </details>

      <div style="display:flex; gap:10px; margin-top:12px; flex-wrap:wrap">
        <button class="secondary" onclick='saveNlpFeedback(this, true, ${JSON.stringify(question)}, ${JSON.stringify(d)})'>
          Save Correct
        </button>
        <button class="secondary" onclick='saveNlpFeedback(this, false, ${JSON.stringify(question)}, ${JSON.stringify(d)})'>
          Save Wrong
        </button>
        <input style="min-width:260px" placeholder="Optional note..." class="feedback-note" />
      </div>
    `;

  } catch (err) {
    resultDiv.innerHTML = `<div class="bad">Error: ${escapeHtml(err)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = "Check NLP";
  }
}

async function checkVisible() {
  const buttons = Array.from(document.querySelectorAll(".card button"));
  for (const btn of buttons) {
    await checkOne(btn);
  }
}


async function saveNlpFeedback(button, isCorrect, question, candidate) {
  const wrap = button.parentElement;
  const noteInput = wrap.querySelector(".feedback-note");
  const note = noteInput ? noteInput.value : "";

  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Saving...";

  try {
    const res = await fetch("/api/nlp-review/save-feedback", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        question,
        candidate,
        is_correct: isCorrect,
        note
      })
    });

    const data = await res.json();

    if (!data.success) {
      button.textContent = "Save failed";
      return;
    }

    button.textContent = isCorrect ? "Saved Correct" : "Saved Wrong";

  } catch (err) {
    button.textContent = "Error";
  } finally {
    setTimeout(() => {
      button.disabled = false;
      button.textContent = original;
    }, 1500);
  }
}

loadQuestions();
</script>
</body>
</html>
    """)
