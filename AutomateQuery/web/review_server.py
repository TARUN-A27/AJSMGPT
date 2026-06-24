from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse


AUTOMATE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = AUTOMATE_DIR / "reports"

GENERATED_EVAL_JSON = REPORTS_DIR / "generated_eval_candidates.json"
REVIEWED_EVAL_JSON = REPORTS_DIR / "reviewed_eval_candidates.json"
APPROVED_EVAL_JSON = REPORTS_DIR / "approved_eval_tests.json"


app = FastAPI(title="AutomateQuery Review UI")


def read_json(path: Path) -> Any:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def make_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(record.get("question", "")).strip().lower(),
        record.get("expected_source"),
        record.get("expected_intent"),
        tuple(record.get("expected_sql_contains", [])),
    )


def sync_review_file() -> list[dict[str, Any]]:
    """
    Creates/updates reviewed_eval_candidates.json from generated_eval_candidates.json.

    Important:
    Existing human approvals and notes are preserved.
    """
    generated_candidates = read_json(GENERATED_EVAL_JSON)
    existing_reviewed = read_json(REVIEWED_EVAL_JSON)

    reviewed_by_key = {
        make_key(record): record
        for record in existing_reviewed
    }

    final_reviewed: list[dict[str, Any]] = []

    for candidate in generated_candidates:
        key = make_key(candidate)

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
    reviewed = read_json(REVIEWED_EVAL_JSON)

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


async def read_form(request: Request) -> dict[str, str]:
    """
    Reads normal HTML form data without requiring python-multipart.
    """
    body = await request.body()
    parsed = parse_qs(body.decode("utf-8"))

    return {
        key: values[0] if values else ""
        for key, values in parsed.items()
    }


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def render_candidate_card(record: dict[str, Any]) -> str:
    candidate_id = str(record.get("candidate_id", ""))
    approved = record.get("approved") is True
    status = str(record.get("status", "waiting_for_human_review"))

    status_class = "approved" if approved else "pending"
    status_label = "APPROVED" if approved else status.upper()

    sql_parts = record.get("expected_sql_contains", [])
    sql_html = "".join(f"<li><code>{esc(part)}</code></li>" for part in sql_parts)

    approve_path = f"/approve/{quote(candidate_id)}"
    reject_path = f"/reject/{quote(candidate_id)}"
    note_path = f"/note/{quote(candidate_id)}"

    return f"""
    <div class="card">
        <div class="card-header">
            <div>
                <span class="badge {status_class}">{esc(status_label)}</span>
                <span class="candidate-id">{esc(candidate_id)}</span>
            </div>
        </div>

        <h2>{esc(record.get("question"))}</h2>

        <div class="grid">
            <div>
                <label>Expected Source</label>
                <code>{esc(record.get("expected_source"))}</code>
            </div>
            <div>
                <label>Expected Intent</label>
                <code>{esc(record.get("expected_intent"))}</code>
            </div>
        </div>

        <h3>Expected SQL Contains</h3>
        <ul>{sql_html}</ul>

        <form method="post" action="{note_path}" class="note-form">
            <label>Review Note</label>
            <textarea name="review_note" rows="3">{esc(record.get("review_note", ""))}</textarea>
            <button type="submit" class="secondary">Save Note</button>
        </form>

        <div class="actions">
            <form method="post" action="{approve_path}">
                <button type="submit" class="approve">Approve</button>
            </form>

            <form method="post" action="{reject_path}">
                <button type="submit" class="reject">Reject</button>
            </form>
        </div>
    </div>
    """


def render_page(message: str = "") -> str:
    reviewed = sync_review_file()

    total_count = len(reviewed)
    approved_count = sum(1 for record in reviewed if record.get("approved") is True)
    pending_count = total_count - approved_count

    cards_html = "".join(render_candidate_card(record) for record in reviewed)

    if not cards_html:
        cards_html = """
        <div class="empty">
            No review candidates found.
            Run AutomateQuery pipeline first:
            <pre>./venv/bin/python3 AutomateQuery/scripts/auto_improve_from_logs.py</pre>
        </div>
        """

    message_html = f"<div class='message'>{esc(message)}</div>" if message else ""

    return f"""
    <!doctype html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>AutomateQuery Review UI</title>
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 0;
                background: #f4f6f8;
                color: #222;
            }}

            header {{
                background: #111827;
                color: white;
                padding: 20px 32px;
            }}

            header h1 {{
                margin: 0 0 8px 0;
                font-size: 26px;
            }}

            header p {{
                margin: 0;
                color: #cbd5e1;
            }}

            .container {{
                max-width: 1100px;
                margin: 24px auto;
                padding: 0 20px;
            }}

            .summary {{
                display: flex;
                gap: 16px;
                margin-bottom: 20px;
            }}

            .summary-box {{
                background: white;
                border-radius: 10px;
                padding: 16px;
                min-width: 150px;
                box-shadow: 0 1px 4px rgba(0,0,0,0.08);
            }}

            .summary-box strong {{
                display: block;
                font-size: 24px;
                margin-bottom: 4px;
            }}

            .toolbar {{
                display: flex;
                gap: 12px;
                margin-bottom: 20px;
            }}

            .card {{
                background: white;
                border-radius: 12px;
                padding: 22px;
                margin-bottom: 20px;
                box-shadow: 0 1px 6px rgba(0,0,0,0.10);
                border-left: 6px solid #64748b;
            }}

            .card-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 14px;
            }}

            .candidate-id {{
                color: #64748b;
                margin-left: 8px;
                font-size: 14px;
            }}

            h2 {{
                margin-top: 0;
                font-size: 22px;
            }}

            h3 {{
                margin-bottom: 8px;
            }}

            code {{
                background: #eef2ff;
                padding: 3px 6px;
                border-radius: 5px;
            }}

            .grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 16px;
                margin: 16px 0;
            }}

            label {{
                display: block;
                font-weight: bold;
                margin-bottom: 6px;
                color: #334155;
            }}

            textarea {{
                width: 100%;
                box-sizing: border-box;
                padding: 10px;
                border-radius: 8px;
                border: 1px solid #cbd5e1;
                font-family: Arial, sans-serif;
            }}

            .actions {{
                display: flex;
                gap: 12px;
                margin-top: 16px;
            }}

            button {{
                border: none;
                border-radius: 8px;
                padding: 10px 16px;
                cursor: pointer;
                font-weight: bold;
            }}

            .approve {{
                background: #16a34a;
                color: white;
            }}

            .reject {{
                background: #dc2626;
                color: white;
            }}

            .secondary {{
                background: #334155;
                color: white;
                margin-top: 8px;
            }}

            .export {{
                background: #2563eb;
                color: white;
            }}

            .sync {{
                background: #7c3aed;
                color: white;
            }}

            .badge {{
                display: inline-block;
                padding: 6px 10px;
                border-radius: 999px;
                font-size: 12px;
                font-weight: bold;
            }}

            .badge.approved {{
                background: #dcfce7;
                color: #166534;
            }}

            .badge.pending {{
                background: #fef3c7;
                color: #92400e;
            }}

            .message {{
                background: #ecfdf5;
                color: #065f46;
                padding: 12px 16px;
                border-radius: 8px;
                margin-bottom: 16px;
                border: 1px solid #a7f3d0;
            }}

            .empty {{
                background: white;
                padding: 24px;
                border-radius: 12px;
            }}

            pre {{
                background: #111827;
                color: #e5e7eb;
                padding: 12px;
                border-radius: 8px;
                overflow-x: auto;
            }}

            .safety {{
                margin-top: 30px;
                font-size: 14px;
                color: #475569;
            }}
        </style>
    </head>

    <body>
        <header>
            <h1>AutomateQuery Review UI</h1>
            <p>Review and approve generated regression test candidates safely.</p>
        </header>

        <div class="container">
            {message_html}

            <div class="summary">
                <div class="summary-box">
                    <strong>{total_count}</strong>
                    Total Candidates
                </div>
                <div class="summary-box">
                    <strong>{approved_count}</strong>
                    Approved
                </div>
                <div class="summary-box">
                    <strong>{pending_count}</strong>
                    Pending / Rejected
                </div>
            </div>

            <div class="toolbar">
                <form method="post" action="/sync">
                    <button type="submit" class="sync">Sync Review File</button>
                </form>

                <form method="post" action="/export-approved">
                    <button type="submit" class="export">Export Approved Tests</button>
                </form>
            </div>

            {cards_html}

            <div class="safety">
                <strong>Safety:</strong>
                This UI does not execute Oracle SQL and does not modify production router code.
                It only updates files inside <code>AutomateQuery/reports/</code>.
            </div>
        </div>
    </body>
    </html>
    """


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    message = request.query_params.get("message", "")
    return HTMLResponse(render_page(message))


@app.get("/health", response_class=PlainTextResponse)
async def health() -> str:
    return "AutomateQuery Review UI is running"


@app.post("/sync")
async def sync() -> RedirectResponse:
    reviewed = sync_review_file()
    return RedirectResponse(
        url=f"/?message=Review file synced. Candidates available: {len(reviewed)}",
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
    return RedirectResponse(url=f"/?message={quote(message)}", status_code=303)


@app.post("/reject/{candidate_id}")
async def reject(candidate_id: str) -> RedirectResponse:
    found = update_candidate(
        candidate_id,
        approved=False,
        status="rejected",
    )

    message = "Candidate rejected." if found else "Candidate not found."
    return RedirectResponse(url=f"/?message={quote(message)}", status_code=303)


@app.post("/note/{candidate_id}")
async def save_note(candidate_id: str, request: Request) -> RedirectResponse:
    form = await read_form(request)
    review_note = form.get("review_note", "")

    found = update_candidate(
        candidate_id,
        review_note=review_note,
    )

    message = "Review note saved." if found else "Candidate not found."
    return RedirectResponse(url=f"/?message={quote(message)}", status_code=303)


@app.post("/export-approved")
async def export_approved() -> RedirectResponse:
    approved_tests = export_approved_tests()

    return RedirectResponse(
        url=f"/?message=Approved eval tests exported: {len(approved_tests)}",
        status_code=303,
    )
