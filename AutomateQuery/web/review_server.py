from __future__ import annotations

import html
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse


AUTOMATE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = AUTOMATE_DIR.parent
REPORTS_DIR = AUTOMATE_DIR / "reports"

GENERATED_EVAL_JSON = REPORTS_DIR / "generated_eval_candidates.json"
REVIEWED_EVAL_JSON = REPORTS_DIR / "reviewed_eval_candidates.json"
APPROVED_EVAL_JSON = REPORTS_DIR / "approved_eval_tests.json"
ROUTER_FIX_JSON = REPORTS_DIR / "router_fix_candidates.json"
QUESTION_BANK_JSON = REPORTS_DIR / "question_bank.json"
LAST_PIPELINE_LOG = REPORTS_DIR / "last_pipeline_run.txt"
APPROVED_TEST_RUN_LOG = REPORTS_DIR / "approved_eval_test_run.txt"

app = FastAPI(title="AutomateQuery UI")


BASE_CSS = """
:root {
    --bg: #0f0f0f;
    --panel: #171717;
    --panel-2: #1f1f1f;
    --text: #f5f5f5;
    --muted: #a3a3a3;
    --line: #2f2f2f;
    --soft-line: #262626;
    --white: #ffffff;
    --black: #000000;
    --hover: #242424;
    --danger-bg: #2a1515;
    --danger-text: #ffb4b4;
    --ok-bg: #132417;
    --ok-text: #b8f7c4;
    --warn-bg: #292313;
    --warn-text: #ffe2a3;
}

* {
    box-sizing: border-box;
}

body {
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    margin: 0;
    background: var(--bg);
    color: var(--text);
    line-height: 1.45;
}

header {
    background: #0f0f0f;
    border-bottom: 1px solid var(--line);
    padding: 20px 24px 14px 24px;
}

.header-inner {
    max-width: 1450px;
    margin: 0 auto;
}

header h1 {
    margin: 0 0 6px 0;
    font-size: 26px;
    font-weight: 700;
    letter-spacing: -0.03em;
    color: var(--white);
}

header p {
    margin: 0 0 16px 0;
    color: var(--muted);
    font-size: 14px;
}

nav {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
}

nav a {
    color: var(--text);
    text-decoration: none;
    padding: 9px 14px;
    border-radius: 999px;
    background: transparent;
    border: 1px solid var(--line);
    font-weight: 700;
    font-size: 14px;
    transition: 0.15s ease;
}

nav a:hover {
    background: var(--hover);
    border-color: #555;
}

nav a.active {
    background: var(--white);
    color: var(--black);
    border-color: var(--white);
}

.container {
    max-width: 1450px;
    margin: 28px auto;
    padding: 0 22px 40px 22px;
}

.summary {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 14px;
    margin-bottom: 24px;
}

.summary-box {
    background: linear-gradient(180deg, var(--panel-2), var(--panel));
    border: 1px solid var(--line);
    border-radius: 18px;
    padding: 18px;
    min-height: 96px;
}

.summary-box strong {
    display: block;
    font-size: 30px;
    line-height: 1;
    margin-bottom: 10px;
    color: var(--white);
    letter-spacing: -0.04em;
}

.click-filter {
    cursor: pointer;
    transition: transform 0.12s ease, border-color 0.12s ease;
}

.click-filter:hover {
    transform: translateY(-1px);
    border-color: #777 !important;
}

.card,
.candidate-card,
.dashboard-card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 20px;
    padding: 24px;
    margin-bottom: 20px;
    box-shadow: none;
}

.card:hover,
.candidate-card:hover,
.dashboard-card:hover,
.summary-box:hover {
    border-color: #4a4a4a;
}

.dashboard-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 18px;
}

.dashboard-card h2 {
    margin: 0 0 10px 0;
    color: var(--white);
    font-size: 21px;
    letter-spacing: -0.02em;
}

.dashboard-card p {
    color: var(--muted);
    margin: 8px 0;
}

.dashboard-card a {
    display: inline-block;
    background: var(--white);
    color: var(--black);
    text-decoration: none;
    padding: 10px 15px;
    border-radius: 999px;
    font-weight: 800;
    margin-top: 12px;
}

.dashboard-card a:hover {
    background: #e5e5e5;
}

.candidate-top,
.card-header {
    display: flex;
    gap: 10px;
    align-items: center;
    margin-bottom: 14px;
    flex-wrap: wrap;
}

.candidate-id,
.source,
.tag,
.badge {
    display: inline-flex;
    align-items: center;
    border-radius: 999px;
    padding: 5px 10px;
    font-size: 12px;
    font-weight: 800;
    border: 1px solid var(--line);
    background: var(--panel-2);
    color: var(--text);
}

.source {
    background: var(--danger-bg);
    color: var(--danger-text);
    border-color: #4b2424;
}

h2 {
    margin-top: 0;
    color: var(--white);
    font-size: 22px;
    letter-spacing: -0.025em;
}

h3 {
    margin-bottom: 8px;
    color: var(--white);
    font-size: 16px;
}

p {
    color: var(--text);
}

.meta-grid,
.grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 14px;
    margin: 16px 0;
}

label {
    display: block;
    font-weight: 800;
    color: var(--muted);
    margin-bottom: 6px;
    font-size: 13px;
}

code {
    background: #0b0b0b;
    color: var(--text);
    border: 1px solid var(--line);
    padding: 4px 7px;
    border-radius: 8px;
    font-size: 13px;
    word-break: break-word;
}

ul {
    padding-left: 22px;
}

li {
    margin-bottom: 8px;
}

.problem,
.pending {
    background: var(--warn-bg);
    color: var(--warn-text);
    border-color: #4f3f18;
}

.danger,
.reject-status {
    background: var(--danger-bg);
    color: var(--danger-text);
    border-color: #4b2424;
}

.approved,
.ok {
    background: var(--ok-bg);
    color: var(--ok-text);
    border-color: #244b2b;
}

.muted {
    color: var(--muted);
}

pre {
    background: #050505;
    color: var(--text);
    padding: 14px;
    border-radius: 14px;
    overflow-x: auto;
    white-space: pre-wrap;
    border: 1px solid var(--line);
}

details {
    margin-top: 14px;
    background: var(--panel-2);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 12px;
}

summary {
    cursor: pointer;
    font-weight: 800;
    color: var(--white);
}

textarea {
    width: 100%;
    box-sizing: border-box;
    padding: 12px;
    border-radius: 14px;
    border: 1px solid var(--line);
    background: #0b0b0b;
    color: var(--text);
    font-family: inherit;
    resize: vertical;
}

textarea:focus {
    outline: none;
    border-color: #777;
}

.actions,
.toolbar {
    display: flex;
    gap: 12px;
    margin-top: 16px;
    margin-bottom: 20px;
    flex-wrap: wrap;
}

button {
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 10px 16px;
    cursor: pointer;
    font-weight: 800;
    background: var(--panel-2);
    color: var(--text);
    transition: 0.15s ease;
}

button:hover {
    background: var(--hover);
    border-color: #555;
}

.approve,
.export,
.sync {
    background: var(--white);
    color: var(--black);
    border-color: var(--white);
}

.approve:hover,
.export:hover,
.sync:hover {
    background: #e5e5e5;
}

.reject {
    background: var(--danger-bg);
    color: var(--danger-text);
    border-color: #4b2424;
}

.secondary {
    background: var(--panel-2);
    color: var(--text);
}

.message {
    background: #101f14;
    color: var(--ok-text);
    padding: 13px 16px;
    border-radius: 14px;
    margin-bottom: 18px;
    border: 1px solid #244b2b;
}

.filter-toolbar {
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 18px;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 18px;
    padding: 14px;
}

.filter-label {
    color: var(--muted);
    font-size: 14px;
    font-weight: 800;
}

.active-filter-text {
    display: inline-flex;
    min-height: 34px;
    align-items: center;
    border: 1px solid var(--line);
    background: #050505;
    color: var(--text);
    border-radius: 999px;
    padding: 7px 12px;
    font-size: 13px;
    font-weight: 800;
}

.filter-clear-btn {
    background: var(--panel-2);
    color: var(--text);
    border: 1px solid var(--line);
}

.filter-hidden {
    display: none !important;
}

table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 18px;
    overflow: hidden;
}

th,
td {
    padding: 12px;
    border-bottom: 1px solid var(--soft-line);
    text-align: left;
    vertical-align: top;
    font-size: 14px;
    white-space: normal;
}

th {
    background: #050505;
    color: var(--white);
    font-weight: 800;
}

td {
    color: var(--text);
}

td:nth-child(3) {
    min-width: 260px;
}

tr:hover {
    background: #1b1b1b;
}

tr:last-child td {
    border-bottom: none;
}

.empty {
    background: var(--panel);
    border: 1px solid var(--line);
    padding: 24px;
    border-radius: 18px;
}

.safety {
    margin-top: 30px;
    font-size: 14px;
    color: var(--muted);
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 16px;
    padding: 16px;
}

.loading-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.72);
    backdrop-filter: blur(8px);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 9999;
}

.loading-box {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 22px;
    padding: 28px 34px;
    min-width: 260px;
    text-align: center;
    color: var(--text);
    box-shadow: 0 20px 80px rgba(0,0,0,0.35);
}

.spinner {
    width: 34px;
    height: 34px;
    border: 3px solid #333;
    border-top-color: var(--white);
    border-radius: 50%;
    margin: 0 auto 14px auto;
    animation: spin 0.8s linear infinite;
}

@keyframes spin {
    to {
        transform: rotate(360deg);
    }
}

.loaded-toast {
    position: fixed;
    right: 22px;
    bottom: 22px;
    background: var(--white);
    color: var(--black);
    border-radius: 999px;
    padding: 11px 16px;
    font-weight: 900;
    z-index: 10000;
    opacity: 0;
    transform: translateY(12px);
    transition: 0.22s ease;
    pointer-events: none;
}

.loaded-toast.show {
    opacity: 1;
    transform: translateY(0);
}

a {
    color: var(--white);
}

::selection {
    background: var(--white);
    color: var(--black);
}

@media (max-width: 1000px) {
    .dashboard-grid {
        grid-template-columns: 1fr;
    }

    .meta-grid,
    .grid {
        grid-template-columns: 1fr;
    }

    header {
        padding: 18px 18px 12px 18px;
    }

    .container {
        padding: 0 14px 32px 14px;
    }

    table {
        display: block;
        overflow-x: auto;
    }
}
"""


BASE_JS = """
<script>
(function () {
    function showOverlay(text) {
        var overlay = document.getElementById("loadingOverlay");
        var loadingText = document.getElementById("loadingText");
        if (loadingText) {
            loadingText.textContent = text || "Loading...";
        }
        if (overlay) {
            overlay.style.display = "flex";
        }
    }

    function hideOverlay() {
        var overlay = document.getElementById("loadingOverlay");
        if (overlay) {
            overlay.style.display = "none";
        }
    }

    function showToast(text) {
        var toast = document.getElementById("loadedToast");
        if (!toast) return;
        toast.textContent = text || "✓ Loaded";
        toast.classList.add("show");
        setTimeout(function () {
            toast.classList.remove("show");
        }, 1200);
    }

    function getFilterItems() {
        return Array.prototype.slice.call(document.querySelectorAll(".filter-item"));
    }

    function setActiveFilter(text) {
        var label = document.getElementById("activeFilterText");
        if (label) {
            label.textContent = text || "No filter";
        }
    }

    function clearFilter() {
        getFilterItems().forEach(function (item) {
            item.classList.remove("filter-hidden");
        });
        setActiveFilter("No filter");
        showToast("✓ Filter cleared");
    }

    function applyFilter(type, value, label) {
        var items = getFilterItems();
        var matched = 0;
        value = String(value || "").toLowerCase();

        items.forEach(function (item) {
            var isMatch = true;

            if (type === "category") {
                isMatch = String(item.dataset.category || "").toLowerCase() === value;
            } else if (type === "status") {
                isMatch = String(item.dataset.status || "").toLowerCase() === value;
            } else if (type === "problem") {
                isMatch = String(item.dataset.problems || "").toLowerCase().indexOf(value) !== -1;
            } else if (type === "fallback") {
                isMatch = String(item.dataset.fallback || "").toLowerCase() === value;
            } else if (type === "wrong-table") {
                isMatch = String(item.dataset.wrongTable || "").toLowerCase() === value;
            } else if (type === "review-required") {
                isMatch = String(item.dataset.reviewRequired || "").toLowerCase() === value;
            } else if (type === "text") {
                isMatch = String(item.dataset.search || item.textContent || "").toLowerCase().indexOf(value) !== -1;
            }

            item.classList.toggle("filter-hidden", !isMatch);
            if (isMatch) matched += 1;
        });

        setActiveFilter("Filter: " + label + " (" + matched + ")");
        showToast("✓ Filter applied");
    }

    window.addEventListener("load", function () {
        hideOverlay();
        showToast("✓ Loaded");
    });

    document.addEventListener("submit", function (event) {
        var form = event.target;
        var action = form.getAttribute("action") || "";

        if (action.indexOf("run-pipeline") !== -1) {
            showOverlay("Running automation pipeline...");
        } else if (action.indexOf("run-approved-tests") !== -1) {
            showOverlay("Running approved eval tests...");
        } else if (action.indexOf("export-approved") !== -1) {
            showOverlay("Exporting approved tests...");
        } else if (action.indexOf("sync") !== -1) {
            showOverlay("Syncing review file...");
        } else if (action.indexOf("approve") !== -1) {
            showOverlay("Approving candidate...");
        } else if (action.indexOf("reject") !== -1) {
            showOverlay("Rejecting candidate...");
        } else if (action.indexOf("note") !== -1) {
            showOverlay("Saving note...");
        } else {
            showOverlay("Loading...");
        }
    });

    document.addEventListener("click", function (event) {
        var clearBtn = event.target.closest("[data-filter-clear]");
        if (clearBtn) {
            clearFilter();
            return;
        }

        var filterEl = event.target.closest("[data-filter-category], [data-filter-status], [data-filter-problem], [data-filter-fallback], [data-filter-wrong-table], [data-filter-review-required], [data-filter-text]");
        if (filterEl) {
            event.preventDefault();

            if (filterEl.dataset.filterCategory) {
                applyFilter("category", filterEl.dataset.filterCategory, filterEl.dataset.filterCategory);
                return;
            }

            if (filterEl.dataset.filterStatus) {
                applyFilter("status", filterEl.dataset.filterStatus, filterEl.textContent.trim());
                return;
            }

            if (filterEl.dataset.filterProblem) {
                applyFilter("problem", filterEl.dataset.filterProblem, filterEl.dataset.filterProblem);
                return;
            }

            if (filterEl.dataset.filterFallback) {
                applyFilter("fallback", filterEl.dataset.filterFallback, "Fallback Used");
                return;
            }

            if (filterEl.dataset.filterWrongTable) {
                applyFilter("wrong-table", filterEl.dataset.filterWrongTable, "Wrong Table Detected");
                return;
            }

            if (filterEl.dataset.filterReviewRequired) {
                applyFilter("review-required", filterEl.dataset.filterReviewRequired, "Review Required");
                return;
            }

            if (filterEl.dataset.filterText) {
                applyFilter("text", filterEl.dataset.filterText, filterEl.dataset.filterText);
                return;
            }
        }

        var link = event.target.closest("a");
        if (link && link.getAttribute("href") && !link.getAttribute("href").startsWith("#")) {
            showOverlay("Loading page...");
        }
    });
})();
</script>
"""


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


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


async def read_form(request: Request) -> dict[str, str]:
    body = await request.body()
    parsed = parse_qs(body.decode("utf-8"))

    return {
        key: values[0] if values else ""
        for key, values in parsed.items()
    }


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

    reviewed_by_key = {
        make_eval_key(record): record
        for record in existing_reviewed
    }

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


def run_automation_pipeline() -> tuple[bool, str]:
    script_path = AUTOMATE_DIR / "scripts" / "auto_improve_from_logs.py"

    if not script_path.exists():
        return False, f"Pipeline script not found: {script_path}"

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, "Pipeline timed out after 180 seconds."

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    log_text = (
        "COMMAND: "
        + " ".join([sys.executable, str(script_path)])
        + "\n\nSTDOUT:\n"
        + result.stdout
        + "\n\nSTDERR:\n"
        + result.stderr
    )

    LAST_PIPELINE_LOG.write_text(log_text, encoding="utf-8")

    if result.returncode != 0:
        return False, "Pipeline failed. Check AutomateQuery/reports/last_pipeline_run.txt"

    return True, "Pipeline completed safely. Reports refreshed."



def run_approved_eval_test_script() -> tuple[bool, str]:
    script_path = AUTOMATE_DIR / "scripts" / "run_approved_eval_tests.py"

    if not script_path.exists():
        return False, f"Approved eval test script not found: {script_path}"

    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, "Approved eval tests timed out after 180 seconds."

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    log_text = (
        "COMMAND: "
        + " ".join([sys.executable, str(script_path)])
        + "\n\nSTDOUT:\n"
        + result.stdout
        + "\n\nSTDERR:\n"
        + result.stderr
    )

    APPROVED_TEST_RUN_LOG.write_text(log_text, encoding="utf-8")

    if result.returncode != 0:
        return False, "Approved eval tests failed. Check approved_eval_test_run.txt"

    return True, "Approved eval tests passed."


def page_layout(title: str, body: str, active: str = "", message: str = "") -> str:
    message_html = f"<div class='message'>{esc(message)}</div>" if message else ""

    def nav_class(name: str) -> str:
        return "active" if active == name else ""

    return f"""
    <!doctype html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>{esc(title)}</title>
        <style>{BASE_CSS}</style>
    </head>
    <body>
        <div id="loadingOverlay" class="loading-overlay">
            <div class="loading-box">
                <div class="spinner"></div>
                <div id="loadingText">Loading...</div>
            </div>
        </div>

        <div id="loadedToast" class="loaded-toast">✓ Loaded</div>

        <header>
            <div class="header-inner">
                <h1>{esc(title)}</h1>
                <p>Safe local review dashboard for AJSMGPT log improvements.</p>
                <nav>
                    <a class="{nav_class('dashboard')}" href="/">Dashboard</a>
                    <a class="{nav_class('question_bank')}" href="/question-bank">Question Bank</a>
                    <a class="{nav_class('router_candidates')}" href="/router-candidates">Router Candidates</a>
                    <a class="{nav_class('eval_review')}" href="/eval-review">Eval Approval</a>
                </nav>
            </div>
        </header>

        <div class="container">
            {message_html}
            {body}
        </div>

        {BASE_JS}
    </body>
    </html>
    """


@app.get("/health", response_class=PlainTextResponse)
async def health() -> str:
    return "AutomateQuery UI is running"


@app.post("/run-pipeline")
async def run_pipeline() -> RedirectResponse:
    ok, message = run_automation_pipeline()
    return RedirectResponse(
        url=f"/?message={quote(message)}",
        status_code=303,
    )


@app.get("/pipeline-log", response_class=PlainTextResponse)
async def pipeline_log() -> str:
    if not LAST_PIPELINE_LOG.exists():
        return "No pipeline log found yet."

    return LAST_PIPELINE_LOG.read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    question_bank = as_list(read_json(QUESTION_BANK_JSON))
    router_candidates = as_list(read_json(ROUTER_FIX_JSON))
    generated_eval = as_list(read_json(GENERATED_EVAL_JSON))
    reviewed_eval = sync_review_file()
    approved_eval = as_list(read_json(APPROVED_EVAL_JSON))

    unique_questions = len(question_bank)
    review_questions = sum(1 for item in question_bank if item.get("needs_review"))
    router_problem_count = len(router_candidates)
    wrong_table_count = sum(1 for c in router_candidates if c.get("wrong_tables_detected"))
    approved_count = sum(1 for item in reviewed_eval if item.get("approved") is True)
    pending_count = len(reviewed_eval) - approved_count

    body = f"""
    <div class="summary">
        <div class="summary-box">
            <strong>{unique_questions}</strong>
            Unique Questions
        </div>
        <div class="summary-box">
            <strong>{review_questions}</strong>
            Questions Need Review
        </div>
        <div class="summary-box">
            <strong>{router_problem_count}</strong>
            Router Problem Candidates
        </div>
        <div class="summary-box">
            <strong>{wrong_table_count}</strong>
            Wrong Table Detections
        </div>
        <div class="summary-box">
            <strong>{len(generated_eval)}</strong>
            Generated Eval Candidates
        </div>
        <div class="summary-box">
            <strong>{approved_count}</strong>
            Approved Eval Tests
        </div>
        <div class="summary-box">
            <strong>{pending_count}</strong>
            Pending Eval Reviews
        </div>
        <div class="summary-box">
            <strong>{len(approved_eval)}</strong>
            Exported Approved Tests
        </div>
    </div>

    <div class="toolbar">
        <form method="post" action="/run-pipeline">
            <button type="submit" class="sync">Run Automation Pipeline</button>
        </form>

        <form method="get" action="/pipeline-log">
            <button type="submit" class="secondary">View Last Pipeline Log</button>
        </form>
    </div>

    <div class="dashboard-grid">
        <div class="dashboard-card">
            <h2>Question Bank</h2>
            <p>View all unique user questions grouped by category.</p>
            <p>Use this to understand the full question coverage.</p>
            <a href="/question-bank">Open Question Bank</a>
        </div>

        <div class="dashboard-card">
            <h2>Router Candidates</h2>
            <p>View suspicious fallback, slow, failed, zero-row, or wrong-table questions.</p>
            <p>Use this to decide next router improvements.</p>
            <a href="/router-candidates">Open Router Candidates</a>
        </div>

        <div class="dashboard-card">
            <h2>Eval Approval</h2>
            <p>Approve generated regression test candidates after human review.</p>
            <p>Approved tests are exported to approved_eval_tests.json.</p>
            <a href="/eval-review">Open Eval Approval</a>
        </div>
    </div>

    <div class="safety">
        <strong>Safety:</strong>
        This frontend does not execute Oracle SQL and does not modify production router code.
        It only reads and writes files inside <code>AutomateQuery/reports/</code>.
    </div>
    """

    return HTMLResponse(page_layout("AutomateQuery Dashboard", body, active="dashboard"))


def render_filter_toolbar() -> str:
    return """
    <div class="filter-toolbar">
        <span class="filter-label">Click any field/tag to filter:</span>
        <span id="activeFilterText" class="active-filter-text">No filter</span>
        <button type="button" class="filter-clear-btn" data-filter-clear="1">Clear Filter</button>
    </div>
    """


def render_question_bank_row(item: dict[str, Any], index: int) -> str:
    category = str(item.get("category") or "")
    needs_review = item.get("needs_review") is True
    status = "review" if needs_review else "ok"
    review_class = "danger" if needs_review else "ok"
    review_text = "REVIEW" if needs_review else "OK"

    search_text = " ".join(
        [
            str(item.get("question") or ""),
            category,
            review_text,
            str(item.get("router_sources_seen") or ""),
            str(item.get("intents_seen") or ""),
        ]
    )

    return f"""
    <tr class="filter-item"
        data-category="{esc(category)}"
        data-status="{esc(status)}"
        data-search="{esc(search_text)}">
        <td>{index}</td>
        <td>
            <span class="tag click-filter" data-filter-category="{esc(category)}">{esc(category)}</span>
        </td>
        <td>{esc(item.get("question"))}</td>
        <td>{esc(item.get("occurrence_count"))}</td>
        <td>
            <span class="tag {review_class} click-filter" data-filter-status="{esc(status)}">{review_text}</span>
        </td>
        <td>{esc(item.get("fallback_count"))}</td>
        <td>{esc(item.get("failed_count"))}</td>
        <td>{esc(item.get("slow_count"))}</td>
        <td><code class="click-filter" data-filter-text="{esc(item.get("router_sources_seen"))}">{esc(item.get("router_sources_seen"))}</code></td>
        <td><code class="click-filter" data-filter-text="{esc(item.get("intents_seen"))}">{esc(item.get("intents_seen"))}</code></td>
    </tr>
    """


@app.get("/question-bank", response_class=HTMLResponse)
async def question_bank_page() -> HTMLResponse:
    bank = as_list(read_json(QUESTION_BANK_JSON))

    total = len(bank)
    review_count = sum(1 for item in bank if item.get("needs_review"))
    ok_count = total - review_count

    category_counts: dict[str, int] = {}
    for item in bank:
        category = str(item.get("category") or "unknown")
        category_counts[category] = category_counts.get(category, 0) + 1

    category_html = "".join(
        f"""
        <div class="summary-box click-filter" data-filter-category="{esc(category)}">
            <strong>{count}</strong>
            {esc(category)}
        </div>
        """
        for category, count in sorted(category_counts.items())
    )

    rows = "".join(
        render_question_bank_row(item, index)
        for index, item in enumerate(bank, start=1)
    )

    if not rows:
        rows = """
        <tr>
            <td colspan="10">
                No question bank found. Run:
                <pre>./venv/bin/python3 AutomateQuery/scripts/generate_question_bank.py</pre>
            </td>
        </tr>
        """

    body = f"""
    {render_filter_toolbar()}

    <div class="summary">
        <div class="summary-box click-filter" data-filter-clear="1">
            <strong>{total}</strong>
            Unique Questions
        </div>
        <div class="summary-box click-filter" data-filter-status="review">
            <strong>{review_count}</strong>
            Need Review
        </div>
        <div class="summary-box click-filter" data-filter-status="ok">
            <strong>{ok_count}</strong>
            OK
        </div>
        {category_html}
    </div>

    <table>
        <thead>
            <tr>
                <th>No</th>
                <th>Category</th>
                <th>Question</th>
                <th>Count</th>
                <th>Status</th>
                <th>Fallback</th>
                <th>Failed</th>
                <th>Slow</th>
                <th>Sources Seen</th>
                <th>Intents Seen</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
    """

    return HTMLResponse(page_layout("AutomateQuery Question Bank", body, active="question_bank"))


def render_router_candidate_card(candidate: dict[str, Any]) -> str:
    suggestion = candidate.get("suggestion", {}) or {}

    problems = candidate.get("problem_types", []) or []
    wrong_tables = candidate.get("wrong_tables_detected", []) or []

    fallback = "fallback_used" in problems
    wrong_table = bool(wrong_tables)
    review_required = suggestion.get("suggested_intent_name") == "REVIEW_REQUIRED"

    problems_text = " ".join(str(p) for p in problems)
    search_text = " ".join(
        [
            str(candidate.get("candidate_id") or ""),
            str(candidate.get("question") or ""),
            str(candidate.get("source") or ""),
            problems_text,
            " ".join(str(t) for t in wrong_tables),
            str(suggestion.get("target_router_file") or ""),
            str(suggestion.get("suggested_intent_name") or ""),
        ]
    )

    problem_html = "".join(
        f"<span class='tag problem click-filter' data-filter-problem='{esc(problem)}'>{esc(problem)}</span>"
        for problem in problems
    )

    wrong_table_html = "".join(
        f"<span class='tag danger click-filter' data-filter-text='{esc(table)}'>{esc(table)}</span>"
        for table in wrong_tables
    )

    if not wrong_table_html:
        wrong_table_html = "<span class='muted'>None</span>"

    sql_text = candidate.get("sql") or ""

    return f"""
    <div class="candidate-card filter-item"
        data-fallback="{str(fallback).lower()}"
        data-wrong-table="{str(wrong_table).lower()}"
        data-review-required="{str(review_required).lower()}"
        data-problems="{esc(problems_text)}"
        data-search="{esc(search_text)}">

        <div class="candidate-top">
            <span class="candidate-id click-filter" data-filter-text="{esc(candidate.get("candidate_id"))}">{esc(candidate.get("candidate_id"))}</span>
            <span class="source click-filter" data-filter-text="{esc(candidate.get("source"))}">{esc(candidate.get("source"))}</span>
        </div>

        <h2>{esc(candidate.get("question"))}</h2>

        <div class="meta-grid">
            <div>
                <label>Success</label>
                <code>{esc(candidate.get("success"))}</code>
            </div>
            <div>
                <label>Rows</label>
                <code>{esc(candidate.get("row_count"))}</code>
            </div>
            <div>
                <label>Elapsed MS</label>
                <code>{esc(candidate.get("elapsed_ms"))}</code>
            </div>
            <div>
                <label>Similar Count</label>
                <code>{esc(candidate.get("similar_question_count"))}</code>
            </div>
        </div>

        <h3>Problems</h3>
        <div>{problem_html}</div>

        <h3>Wrong Tables Detected</h3>
        <div>{wrong_table_html}</div>

        <h3>Suggested Fix</h3>
        <div class="meta-grid">
            <div>
                <label>Target Router</label>
                <code>{esc(suggestion.get("target_router_file"))}</code>
            </div>
            <div>
                <label>Suggested Intent</label>
                <code>{esc(suggestion.get("suggested_intent_name"))}</code>
            </div>
        </div>

        <p><strong>SQL Template Pattern:</strong></p>
        <pre>{esc(suggestion.get("suggested_sql_template_pattern"))}</pre>

        <details>
            <summary>Show logged SQL / fallback SQL</summary>
            <pre>{esc(sql_text)}</pre>
        </details>
    </div>
    """


@app.get("/router-candidates", response_class=HTMLResponse)
async def router_candidates_page() -> HTMLResponse:
    candidates = as_list(read_json(ROUTER_FIX_JSON))

    total = len(candidates)
    review_required = sum(
        1
        for c in candidates
        if ((c.get("suggestion") or {}).get("suggested_intent_name") == "REVIEW_REQUIRED")
    )
    wrong_table_count = sum(1 for c in candidates if c.get("wrong_tables_detected"))
    fallback_count = sum(
        1
        for c in candidates
        if "fallback_used" in (c.get("problem_types") or [])
    )

    cards = "".join(render_router_candidate_card(c) for c in candidates)

    if not cards:
        cards = """
        <div class="empty">
            No router candidates found. Run:
            <pre>./venv/bin/python3 AutomateQuery/scripts/auto_improve_from_logs.py</pre>
        </div>
        """

    body = f"""
    {render_filter_toolbar()}

    <div class="summary">
        <div class="summary-box click-filter" data-filter-clear="1">
            <strong>{total}</strong>
            Total Candidates
        </div>
        <div class="summary-box click-filter" data-filter-review-required="true">
            <strong>{review_required}</strong>
            Review Required
        </div>
        <div class="summary-box click-filter" data-filter-wrong-table="true">
            <strong>{wrong_table_count}</strong>
            Wrong Table Detected
        </div>
        <div class="summary-box click-filter" data-filter-fallback="true">
            <strong>{fallback_count}</strong>
            Fallback Used
        </div>
    </div>

    {cards}
    """

    return HTMLResponse(page_layout("AutomateQuery Router Candidates", body, active="router_candidates"))


def render_eval_candidate_card(record: dict[str, Any]) -> str:
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
            <span class="badge {status_class}">{esc(status_label)}</span>
            <span class="candidate-id">{esc(candidate_id)}</span>
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


@app.get("/eval-review", response_class=HTMLResponse)
async def eval_review_page(request: Request) -> HTMLResponse:
    message = request.query_params.get("message", "")
    reviewed = sync_review_file()

    total_count = len(reviewed)
    approved_count = sum(1 for record in reviewed if record.get("approved") is True)
    pending_count = total_count - approved_count

    cards_html = "".join(render_eval_candidate_card(record) for record in reviewed)

    if not cards_html:
        cards_html = """
        <div class="empty">
            No review candidates found. Run:
            <pre>./venv/bin/python3 AutomateQuery/scripts/auto_improve_from_logs.py</pre>
        </div>
        """

    body = f"""
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

        <form method="post" action="/run-approved-tests">
            <button type="submit" class="sync">Run Approved Tests</button>
        </form>

        <form method="get" action="/approved-test-log">
            <button type="submit" class="secondary">View Approved Test Log</button>
        </form>
    </div>

    {cards_html}

    <div class="safety">
        <strong>Safety:</strong>
        This UI does not execute Oracle SQL and does not modify production router code.
        It only updates files inside <code>AutomateQuery/reports/</code>.
    </div>
    """

    return HTMLResponse(page_layout("AutomateQuery Eval Approval", body, active="eval_review", message=message))


@app.get("/approval", response_class=HTMLResponse)
async def approval_alias(request: Request) -> HTMLResponse:
    return await eval_review_page(request)



@app.post("/run-approved-tests")
async def run_approved_tests() -> RedirectResponse:
    ok, message = run_approved_eval_test_script()
    return RedirectResponse(
        url=f"/eval-review?message={quote(message)}",
        status_code=303,
    )


@app.get("/approved-test-log", response_class=PlainTextResponse)
async def approved_test_log() -> str:
    if not APPROVED_TEST_RUN_LOG.exists():
        return "No approved eval test run log found yet."

    return APPROVED_TEST_RUN_LOG.read_text(encoding="utf-8")


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
    review_note = form.get("review_note", "")

    found = update_candidate(
        candidate_id,
        review_note=review_note,
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
