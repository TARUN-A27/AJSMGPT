#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from app.nlp_router_bridge import build_nlp_router_candidate
    NLP_IMPORT_ERROR = None
except Exception as exc:
    build_nlp_router_candidate = None
    NLP_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


LOG_CANDIDATES = [
    Path("/home/ajsmgpt/AJSMGPT/logs/user_questions.jsonl"),
    PROJECT_ROOT / "logs" / "user_questions.jsonl",
]

OUT_DIR = PROJECT_ROOT / "AutomateQuery" / "reports" / "learning_queue"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if not path.exists():
        return rows

    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                obj["_log_file"] = str(path)
                obj["_line_no"] = line_no
                rows.append(obj)
        except Exception:
            rows.append({
                "_log_file": str(path),
                "_line_no": line_no,
                "success": False,
                "error": "Invalid JSON log line",
                "raw": line[:1000],
            })

    return rows


def get_record(raw: dict[str, Any]) -> dict[str, Any]:
    data = raw.get("data")
    if isinstance(data, dict):
        merged = dict(raw)
        merged.update(data)
        return merged
    return raw


def norm_question(q: str) -> str:
    q = (q or "").strip().lower()
    q = re.sub(r"\s+", " ", q)
    return q


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def compact_json_value(value: Any, limit: int = 700) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def learning_queue_nlp_enabled() -> bool:
    value = os.getenv("AJSMGPT_LEARNING_QUEUE_NLP", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def build_nlp_observation(question: str) -> dict[str, Any]:
    if not learning_queue_nlp_enabled():
        return {
            "available": False,
            "status": "skipped",
            "error": "disabled_by_AJSMGPT_LEARNING_QUEUE_NLP",
        }

    if build_nlp_router_candidate is None:
        return {
            "available": False,
            "status": "import_error",
            "error": NLP_IMPORT_ERROR,
        }

    try:
        candidate = build_nlp_router_candidate(
            question,
            min_confidence=0.80,
            allow_apply=False,
        )
    except Exception as exc:
        return {
            "available": False,
            "status": "runtime_error",
            "error": f"{type(exc).__name__}: {exc}",
        }

    return {
        "available": True,
        "status": "ok",
        "success": bool(candidate.success),
        "intent": candidate.intent,
        "confidence": candidate.confidence,
        "module": candidate.module,
        "router_candidate": candidate.router_candidate,
        "entities": candidate.entities,
        "required_entities": candidate.required_entities,
        "missing_entities": candidate.missing_entities,
        "required_entities_ok": candidate.required_entities_ok,
        "safe_to_apply": False,
        "reason": candidate.reason,
        "nlp_observation": candidate.nlp_observation,
    }


def decide_automation(group: dict[str, Any]) -> dict[str, Any]:
    status = str(group.get("status") or "")
    latest = group.get("latest") or {}
    latest_source = str(latest.get("source") or "").lower()
    latest_error = str(latest.get("error") or "")
    latest_success = bool(latest.get("success"))
    latest_reasons = set(latest.get("reasons") or [])
    nlp = group.get("nlp") or {}

    nlp_understood = bool(
        nlp.get("available")
        and nlp.get("success")
        and nlp.get("intent")
        and nlp.get("required_entities_ok")
    )

    fallback_now = "fallback" in latest_source or "qwen" in latest_source
    wrong_router_now = any(str(reason).startswith("wrong_") for reason in latest_reasons)

    decision = "manual_review_only"
    manual_reason = "Needs manual review before any router/template change."
    should_create_router_candidate = False
    should_create_regression_candidate = False

    if status == "ZERO_REVIEW":
        decision = "manual_data_absence_review"
        manual_reason = (
            "Latest SELECT returned zero rows. Keep it for manual table/date/entity verification; "
            "do not create a router fix or regression case until approved."
        )

    elif status == "FIXED_NEEDS_REGRESSION":
        decision = "regression_test_candidate"
        manual_reason = (
            "Latest run succeeded after earlier failures. Review once, then save as a regression case."
        )
        should_create_regression_candidate = True

    elif not nlp.get("available"):
        decision = "manual_review_only"
        manual_reason = "NLP observation is unavailable, so classify manually from SQL/source/error."

    elif not nlp.get("success"):
        reason = str(nlp.get("reason") or nlp.get("error") or "")
        if "confidence_below_threshold" in reason:
            decision = "rasa_training_candidate"
            manual_reason = "Rasa detected a low-confidence intent; review as a possible NLU training example."
        elif "missing_required_entities" in reason:
            decision = "entity_extraction_candidate"
            manual_reason = "Intent was detected but required entities are missing; review entity extraction."
        else:
            decision = "nlp_gap_candidate"
            manual_reason = "NLP did not produce an actionable router candidate; review NLU intent/entities."

    elif status == "OPEN" and fallback_now and nlp_understood:
        decision = "planner_router_gap_candidate"
        manual_reason = (
            "NLP understood the intent/entities, but the live route used fallback/Qwen. "
            "Review planner/router handoff before creating a candidate fix."
        )
        should_create_router_candidate = True

    elif status == "OPEN" and (wrong_router_now or latest_error or not latest_success):
        decision = "router_template_candidate"
        manual_reason = (
            "Latest route has an error or wrong-router signal. "
            "Review before creating a safe SELECT template."
        )
        should_create_router_candidate = True

    return {
        "automation_decision": decision,
        "nlp_understood": nlp_understood,
        "should_create_router_candidate": should_create_router_candidate,
        "should_create_regression_candidate": should_create_regression_candidate,
        "requires_manual_approval": True,
        "manual_review_reason": manual_reason,
    }


def classify(record: dict[str, Any]) -> tuple[list[str], int]:
    q = str(record.get("question") or "")
    q_l = q.lower()

    source = str(record.get("source") or "").lower()
    intent = str(record.get("intent") or "").lower()
    sql = str(record.get("sql") or "")
    sql_u = sql.upper()

    success = bool(record.get("success"))
    row_count = to_int(record.get("row_count"), 0)
    elapsed_ms = to_int(record.get("elapsed_ms"), 0)
    error = str(record.get("error") or "")

    reasons: list[str] = []
    score = 0

    if not success:
        reasons.append("failed")
        score += 10

    if error:
        reasons.append("has_error")
        score += 8

    if row_count == 0:
        reasons.append("zero_rows")
        score += 3

    if "fallback" in source or "qwen" in source:
        reasons.append("fallback_router")
        score += 5

    if elapsed_ms >= 10000:
        reasons.append("slow_query")
        score += 2

    if "last supply" in q_l and "mouse" in q_l and "PARTYNAME" in sql_u and "INV.ITEM_NAME" not in sql_u:
        reasons.append("wrong_router_material_treated_as_supplier")
        score += 10

    if "cash" in q_l and "bank" in q_l and "ADMIN.CASHBANK" not in sql_u:
        reasons.append("wrong_router_cashbank_not_cashbank_table")
        score += 10

    if "attendance" in q_l and "CURRENTATTENDANCE" not in sql_u:
        reasons.append("wrong_router_attendance_not_currentattendance")
        score += 10

    if intent.startswith("stock") and "FROM INVENTORY.STOCK " in sql_u:
        reasons.append("wrong_stock_table_should_be_itemstock")
        score += 10

    if "ORA-" in error.upper():
        reasons.append("oracle_error")
        score += 10

    if "invalid datatype" in error.lower() or "invalid number" in error.lower():
        reasons.append("datatype_error")
        score += 10

    if not reasons:
        reasons.append("ok")
        score = 0

    return reasons, score


def compact_record(raw: dict[str, Any]) -> dict[str, Any]:
    r = get_record(raw)
    reasons, score = classify(r)

    return {
        "question": r.get("question"),
        "success": bool(r.get("success")),
        "source": r.get("source"),
        "intent": r.get("intent"),
        "row_count": to_int(r.get("row_count"), 0),
        "elapsed_ms": to_int(r.get("elapsed_ms"), 0),
        "answer": r.get("answer"),
        "sql": r.get("sql"),
        "error": r.get("error"),
        "reasons": reasons,
        "priority_score": score,
        "log_file": raw.get("_log_file"),
        "line_no": raw.get("_line_no"),
    }


def final_status(group: dict[str, Any]) -> str:
    latest = group.get("latest") or {}
    latest_reasons = set(latest.get("reasons") or [])
    all_reasons = set(group.get("reasons") or [])

    latest_success = bool(latest.get("success"))
    latest_row_count = to_int(latest.get("row_count"), 0)
    latest_source = str(latest.get("source") or "").lower()
    latest_error = str(latest.get("error") or "")

    hard_problem_now = (
        not latest_success
        or bool(latest_error)
        or "fallback" in latest_source
        or "qwen" in latest_source
        or any(r.startswith("wrong_") for r in latest_reasons)
        or "oracle_error" in latest_reasons
        or "datatype_error" in latest_reasons
    )

    if hard_problem_now:
        return "OPEN"

    if latest_success and latest_row_count > 0:
        if any(
            r in all_reasons
            for r in {"failed", "has_error", "fallback_router", "oracle_error", "datatype_error", "zero_rows"}
        ):
            return "FIXED_NEEDS_REGRESSION"
        return "OK"

    if latest_success and latest_row_count == 0:
        return "ZERO_REVIEW"

    return "OPEN"


def build_queue(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    for raw in raw_rows:
        item = compact_record(raw)
        q = str(item.get("question") or "").strip()
        key = norm_question(q) or f"missing_question:{item.get('log_file')}:{item.get('line_no')}"

        if key not in grouped:
            grouped[key] = {
                "question": q,
                "count": 0,
                "max_priority_score": 0,
                "needs_review": False,
                "reasons": [],
                "latest": item,
                "examples": [],
            }

        g = grouped[key]
        g["count"] += 1
        g["max_priority_score"] = max(int(g["max_priority_score"]), int(item["priority_score"]))

        for reason in item["reasons"]:
            if reason not in g["reasons"]:
                g["reasons"].append(reason)

        g["latest"] = item

        if len(g["examples"]) < 3:
            g["examples"].append(item)

    for g in grouped.values():
        g["status"] = final_status(g)
        g["needs_review"] = g["status"] in {"OPEN", "ZERO_REVIEW"}

        if g["status"] in {"OPEN", "ZERO_REVIEW"}:
            g["nlp"] = build_nlp_observation(str(g.get("question") or ""))
        else:
            g["nlp"] = {
                "available": False,
                "status": "skipped",
                "error": f"not_needed_for_status:{g['status']}",
            }

        g.update(decide_automation(g))

    return sorted(
        grouped.values(),
        key=lambda x: (
            {"OPEN": 0, "ZERO_REVIEW": 1, "FIXED_NEEDS_REGRESSION": 2, "OK": 3}.get(str(x.get("status")), 9),
            -int(x["max_priority_score"]),
            -int(x["count"]),
            str(x["question"] or ""),
        ),
    )


def write_outputs(queue: list[dict[str, Any]], raw_rows: list[dict[str, Any]], used_logs: list[str]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    open_questions = [x for x in queue if x.get("status") == "OPEN"]
    zero_review = [x for x in queue if x.get("status") == "ZERO_REVIEW"]
    regression_candidates = [x for x in queue if x.get("status") == "FIXED_NEEDS_REGRESSION"]

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "used_logs": used_logs,
        "total_log_rows": len(raw_rows),
        "unique_questions": len(queue),
        "needs_review_count": len(open_questions) + len(zero_review),
        "open_count": len(open_questions),
        "zero_review_count": len(zero_review),
        "fixed_needs_regression_count": len(regression_candidates),
        "ok_count": sum(1 for x in queue if x.get("status") == "OK"),
        "queue": queue,
    }

    json_text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)

    (OUT_DIR / f"learning_queue_{ts}.json").write_text(json_text, encoding="utf-8")
    (OUT_DIR / "latest_learning_queue.json").write_text(json_text, encoding="utf-8")

    (OUT_DIR / "open_questions.json").write_text(
        json.dumps(open_questions + zero_review, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    (OUT_DIR / "regression_candidates.json").write_text(
        json.dumps(regression_candidates, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    lines: list[str] = [
        "# AJSMGPT Learning Queue",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Total log rows: `{payload['total_log_rows']}`",
        f"- Unique questions: `{payload['unique_questions']}`",
        f"- Needs review: `{payload['needs_review_count']}`",
        f"- Open problems: `{payload['open_count']}`",
        f"- Zero-row review: `{payload['zero_review_count']}`",
        f"- Fixed, needs regression test: `{payload['fixed_needs_regression_count']}`",
        f"- OK: `{payload['ok_count']}`",
        "",
        "## Top open / zero-row review candidates",
        "",
    ]

    for idx, item in enumerate((open_questions + zero_review)[:50], start=1):
        latest = item["latest"]
        nlp = item.get("nlp") or {}

        lines.extend([
            f"### {idx}. {item['question']}",
            "",
            f"- Status: `{item.get('status')}`",
            f"- Count: `{item['count']}`",
            f"- Priority score: `{item['max_priority_score']}`",
            f"- Reasons: `{', '.join(item['reasons'])}`",
            f"- Latest source: `{latest.get('source')}`",
            f"- Latest intent: `{latest.get('intent')}`",
            f"- Latest row_count: `{latest.get('row_count')}`",
            f"- Latest elapsed_ms: `{latest.get('elapsed_ms')}`",
            f"- Automation decision: `{item.get('automation_decision')}`",
            f"- NLP understood: `{item.get('nlp_understood')}`",
            f"- Create router candidate: `{item.get('should_create_router_candidate')}`",
            f"- Create regression candidate: `{item.get('should_create_regression_candidate')}`",
            f"- Manual review required: `{item.get('requires_manual_approval')}`",
            f"- Manual review reason: {item.get('manual_review_reason')}",
            f"- NLP status: `{nlp.get('status')}`",
        ])

        if nlp.get("available"):
            lines.extend([
                f"- NLP intent: `{nlp.get('intent')}`",
                f"- NLP confidence: `{nlp.get('confidence')}`",
                f"- NLP module: `{nlp.get('module')}`",
                f"- NLP missing entities: `{', '.join(nlp.get('missing_entities') or [])}`",
                f"- NLP reason: `{nlp.get('reason')}`",
                f"- NLP entities: `{compact_json_value(nlp.get('entities'), 700)}`",
            ])
        else:
            lines.append(f"- NLP error: `{nlp.get('error')}`")

        lines.append("")

        if latest.get("sql"):
            lines.extend(["```sql", str(latest["sql"])[:3000], "```", ""])

        if latest.get("answer"):
            lines.extend([f"Answer: {latest.get('answer')}", ""])

    lines.extend(["", "## Top fixed questions needing regression tests", ""])

    for idx, item in enumerate(regression_candidates[:50], start=1):
        latest = item["latest"]
        lines.extend([
            f"### {idx}. {item['question']}",
            "",
            f"- Status: `{item.get('status')}`",
            f"- Count: `{item['count']}`",
            f"- Previous reasons: `{', '.join(item['reasons'])}`",
            f"- Latest source: `{latest.get('source')}`",
            f"- Latest intent: `{latest.get('intent')}`",
            f"- Latest row_count: `{latest.get('row_count')}`",
            f"- Automation decision: `{item.get('automation_decision')}`",
            f"- Create regression candidate: `{item.get('should_create_regression_candidate')}`",
            "",
        ])

    md_text = "\n".join(lines)

    (OUT_DIR / f"learning_queue_{ts}.md").write_text(md_text, encoding="utf-8")
    (OUT_DIR / "latest_learning_queue.md").write_text(md_text, encoding="utf-8")

    print("Learning queue generated")
    print("Used logs:")
    for path in used_logs:
        print(" -", path)
    print("Total log rows:", payload["total_log_rows"])
    print("Unique questions:", payload["unique_questions"])
    print("Needs review:", payload["needs_review_count"])
    print("Open problems:", payload["open_count"])
    print("Zero-row review:", payload["zero_review_count"])
    print("Fixed needs regression:", payload["fixed_needs_regression_count"])
    print("OK:", payload["ok_count"])
    print("JSON:", OUT_DIR / "latest_learning_queue.json")
    print("Markdown:", OUT_DIR / "latest_learning_queue.md")
    print("Open questions:", OUT_DIR / "open_questions.json")
    print("Regression candidates:", OUT_DIR / "regression_candidates.json")

    print()
    print("Top 10 open/zero review questions:")
    for item in (open_questions + zero_review)[:10]:
        print(
            "-",
            item["question"],
            "| status:",
            item.get("status"),
            "| decision:",
            item.get("automation_decision"),
            "| reasons:",
            ",".join(item["reasons"]),
            "| count:",
            item["count"],
        )

    print()
    print("Top 10 fixed questions needing regression tests:")
    for item in regression_candidates[:10]:
        latest = item["latest"]
        print(
            "-",
            item["question"],
            "| decision:",
            item.get("automation_decision"),
            "| latest source:",
            latest.get("source"),
            "| latest row_count:",
            latest.get("row_count"),
            "| count:",
            item["count"],
        )


def main() -> int:
    raw_rows: list[dict[str, Any]] = []
    used_logs: list[str] = []

    for path in LOG_CANDIDATES:
        rows = read_jsonl(path)
        if rows:
            raw_rows = rows
            used_logs = [str(path)]
            break

    queue = build_queue(raw_rows)
    write_outputs(queue, raw_rows, used_logs)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
