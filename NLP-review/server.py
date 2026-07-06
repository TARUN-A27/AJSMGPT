from __future__ import annotations

import datetime as dt
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse


PROJECT_ROOT = Path(
    os.getenv("AJSMGPT_PROJECT_ROOT", str(Path(__file__).resolve().parents[1]))
).resolve()

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


app = FastAPI(title="AJSMGPT NLP Review")


def log_path() -> Path:
    return PROJECT_ROOT / "logs" / "user_questions.jsonl"


def feedback_path() -> Path:
    return PROJECT_ROOT / "AutomateQuery" / "reports" / "nlp_review_feedback.jsonl"


def read_questions(limit: int = 200):
    path = log_path()
    counter = Counter()

    if not path.exists():
        return []

    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                row = json.loads(line)
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

    return [
        {"question": question, "count": count}
        for question, count in counter.most_common(limit)
    ]


@app.get("/health")
def health():
    return {
        "success": True,
        "service": "NLP-review",
        "project_root": str(PROJECT_ROOT),
        "log_path": str(log_path()),
        "feedback_path": str(feedback_path()),
    }


@app.get("/api/questions")
def api_questions(limit: int = 200):
    limit = max(1, min(int(limit or 200), 1000))
    return {
        "success": True,
        "project_root": str(PROJECT_ROOT),
        "log_path": str(log_path()),
        "questions": read_questions(limit=limit),
    }


@app.post("/api/candidate")
async def api_candidate(request: Request):
    from app.nlp_router_bridge import build_nlp_router_candidate

    body = await request.json()
    question = (body.get("question") or "").strip()

    if not question:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "question_required"},
        )

    result = build_nlp_router_candidate(question)
    return result.to_dict()


@app.post("/api/save-feedback")
async def api_save_feedback(request: Request):
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

    out_path = feedback_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    row: Dict[str, Any] = {
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "question": question,
        "is_correct": is_correct,
        "note": note,
        "candidate": candidate,
        "source": "NLP-review",
    }

    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    return {
        "success": True,
        "saved_to": str(out_path),
        "row": row,
    }


@app.get("/", response_class=HTMLResponse)
@app.get("/nlp-review", response_class=HTMLResponse)
def page():
    return HTMLResponse("""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>AJSMGPT NLP Review</title>
  <style>
    body { margin:0; background:#0f0f10; color:#f4f4f5; font-family:Arial,sans-serif; }
    header { padding:18px 24px; border-bottom:1px solid #2a2a2d; background:#151518; position:sticky; top:0; z-index:10; }
    h1 { margin:0; font-size:22px; }
    .sub { margin-top:6px; color:#a1a1aa; font-size:13px; }
    main { padding:20px 24px; max-width:1280px; margin:0 auto; }
    .toolbar { display:flex; gap:12px; align-items:center; margin-bottom:16px; flex-wrap:wrap; }
    input, select { background:#18181b; color:#f4f4f5; border:1px solid #3f3f46; border-radius:8px; padding:10px 12px; }
    input { min-width:360px; flex:1; }
    button { background:#f4f4f5; color:#09090b; border:0; border-radius:8px; padding:10px 14px; cursor:pointer; font-weight:600; }
    button.secondary { background:#27272a; color:#f4f4f5; border:1px solid #3f3f46; }
    button:disabled { opacity:.55; cursor:not-allowed; }
    .card { background:#18181b; border:1px solid #2f2f33; border-radius:12px; padding:16px; margin-bottom:14px; }
    .question-row { display:grid; grid-template-columns:1fr auto auto; gap:12px; align-items:center; }
    .question { font-size:15px; line-height:1.4; }
    .badge { display:inline-block; border-radius:999px; padding:4px 9px; background:#27272a; color:#d4d4d8; font-size:12px; }
    .result { margin-top:14px; border-top:1px solid #2f2f33; padding-top:14px; display:none; }
    .grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }
    .field { background:#0f0f10; border:1px solid #2f2f33; border-radius:10px; padding:10px; overflow-wrap:anywhere; }
    .label { color:#a1a1aa; font-size:12px; margin-bottom:6px; }
    .value { font-size:14px; white-space:pre-wrap; }
    .ok { color:#86efac; } .warn { color:#fde68a; } .bad { color:#fca5a5; }
    pre { background:#0f0f10; border:1px solid #2f2f33; border-radius:10px; padding:12px; overflow-x:auto; color:#e4e4e7; }
    .empty { color:#a1a1aa; padding:30px; text-align:center; }
    @media (max-width:900px) { .grid { grid-template-columns:1fr; } .question-row { grid-template-columns:1fr; } input { min-width:0; } }
  </style>
</head>
<body>
<header>
  <h1>AJSMGPT NLP Review</h1>
  <div class="sub">Separated NLP-review module. Review Rasa + Duckling intent, entities, module, confidence and save feedback.</div>
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
    .replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;")
    .replaceAll('"',"&quot;").replaceAll("'","&#039;");
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
    const res = await fetch(`/api/questions?limit=${limit}`);
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
  const filtered = QUESTIONS.filter(q => !search || q.question.toLowerCase().includes(search));

  if (!filtered.length) {
    list.innerHTML = `<div class="empty">No questions found.</div>`;
    return;
  }

  list.innerHTML = filtered.map(row => `
    <div class="card" data-question="${escapeHtml(row.question)}">
      <div class="question-row">
        <div class="question">${escapeHtml(row.question)}</div>
        <div class="badge">count: ${row.count}</div>
        <button class="check-btn" onclick="checkOne(this)">Check NLP</button>
      </div>
      <div class="result"></div>
    </div>
  `).join("");
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
    const res = await fetch("/api/candidate", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({question})
    });

    const d = await res.json();
    const okClass = d.success ? "ok" : "bad";
    const safeClass = d.safe_to_apply ? "warn" : "ok";

    resultDiv.innerHTML = `
      <div class="grid">
        <div class="field"><div class="label">Success</div><div class="value ${okClass}">${escapeHtml(d.success)}</div></div>
        <div class="field"><div class="label">Intent</div><div class="value">${escapeHtml(d.intent)}</div></div>
        <div class="field"><div class="label">Confidence</div><div class="value">${shortNum(d.confidence)}</div></div>
        <div class="field"><div class="label">Module</div><div class="value">${escapeHtml(d.module)}</div></div>
        <div class="field"><div class="label">Required entities OK</div><div class="value ${d.required_entities_ok ? "ok" : "bad"}">${escapeHtml(d.required_entities_ok)}</div></div>
        <div class="field"><div class="label">safe_to_apply</div><div class="value ${safeClass}">${escapeHtml(d.safe_to_apply)}</div></div>
        <div class="field"><div class="label">Reason</div><div class="value">${escapeHtml(d.reason)}</div></div>
        <div class="field"><div class="label">Missing entities</div><div class="value">${escapeHtml((d.missing_entities || []).join(", ") || "-")}</div></div>
        <div class="field"><div class="label">Required entities</div><div class="value">${escapeHtml((d.required_entities || []).join(", ") || "-")}</div></div>
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
        <button class="secondary" onclick='saveFeedback(this, true, ${JSON.stringify(question)}, ${JSON.stringify(d)})'>Save Correct</button>
        <button class="secondary" onclick='saveFeedback(this, false, ${JSON.stringify(question)}, ${JSON.stringify(d)})'>Save Wrong</button>
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

async function saveFeedback(button, isCorrect, question, candidate) {
  const wrap = button.parentElement;
  const noteInput = wrap.querySelector(".feedback-note");
  const note = noteInput ? noteInput.value : "";
  const original = button.textContent;

  button.disabled = true;
  button.textContent = "Saving...";

  try {
    const res = await fetch("/api/save-feedback", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({question, candidate, is_correct: isCorrect, note})
    });
    const data = await res.json();
    button.textContent = data.success ? (isCorrect ? "Saved Correct" : "Saved Wrong") : "Save failed";
  } catch (err) {
    button.textContent = "Error";
  } finally {
    setTimeout(() => {
      button.disabled = false;
      button.textContent = original;
    }, 1500);
  }
}

async function checkVisible() {
  const buttons = Array.from(document.querySelectorAll(".check-btn"));
  for (const btn of buttons) {
    await checkOne(btn);
  }
}

loadQuestions();
</script>
</body>
</html>
    """)
