#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

LEARNING_QUEUE_DIR = PROJECT_ROOT / "AutomateQuery" / "reports" / "learning_queue"
INPUT_PATH = LEARNING_QUEUE_DIR / "regression_candidates.json"

OUT_DIR = PROJECT_ROOT / "AutomateQuery" / "reports" / "eval_candidates"
OUT_JSONL = OUT_DIR / "regression_cases.jsonl"
OUT_JSON = OUT_DIR / "regression_cases.json"
OUT_MD = OUT_DIR / "regression_cases.md"

UNSAFE_SQL_WORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "MERGE",
    "CREATE",
    "EXEC",
    "EXECUTE",
    "BEGIN",
    "DECLARE",
}


def read_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    return json.loads(path.read_text(encoding="utf-8"))


def normalize_question(question: str) -> str:
    question = str(question or "").strip().lower()
    question = re.sub(r"\s+", " ", question)
    return question


def slugify(value: str, max_len: int = 80) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:max_len].strip("_") or "question"


def is_safe_select_sql(sql: str | None) -> bool:
    if not sql:
        return False

    compact = re.sub(r"\s+", " ", str(sql).strip()).upper()

    if not compact.startswith("SELECT"):
        return False

    tokens = set(re.findall(r"[A-Z]+", compact))
    return not bool(tokens.intersection(UNSAFE_SQL_WORDS))


def extract_sql_tables(sql: str | None) -> list[str]:
    if not sql:
        return []

    tables: list[str] = []
    for match in re.finditer(
        r"\b(?:FROM|JOIN)\s+([A-Z0-9_]+\.[A-Z0-9_]+|[A-Z0-9_]+)",
        str(sql).upper(),
    ):
        table = match.group(1)
        if table not in tables:
            tables.append(table)

    return tables


def compact_sql(sql: str | None, limit: int = 2500) -> str | None:
    if not sql:
        return None

    sql = str(sql).strip()
    if len(sql) > limit:
        return sql[:limit] + "...[truncated]"
    return sql


def build_case(item: dict[str, Any], index: int) -> dict[str, Any]:
    latest = item.get("latest") or {}
    question = str(item.get("question") or "").strip()
    latest_sql = latest.get("sql")

    observed_row_count = int(latest.get("row_count") or 0)
    expected_min_rows = 1 if observed_row_count > 0 else 0

    case_id = f"reg_{index:04d}_{slugify(question)}"

    return {
        "case_id": case_id,
        "question": question,
        "status": "needs_manual_approval",
        "approval": {
            "approved": False,
            "approved_by": None,
            "approved_at": None,
            "review_note": None,
        },
        "expected": {
            "success": True,
            "source": latest.get("source"),
            "intent": latest.get("intent"),
            "min_rows": expected_min_rows,
            "observed_row_count": observed_row_count,
            "must_be_select_only": True,
            "must_not_use_fallback": True,
            "sql_tables": extract_sql_tables(latest_sql),
        },
        "latest_observation": {
            "source": latest.get("source"),
            "intent": latest.get("intent"),
            "success": latest.get("success"),
            "row_count": latest.get("row_count"),
            "elapsed_ms": latest.get("elapsed_ms"),
            "answer": latest.get("answer"),
            "sql_is_safe_select": is_safe_select_sql(latest_sql),
            "sql_preview": compact_sql(latest_sql),
            "error": latest.get("error"),
        },
        "learning_queue_context": {
            "status": item.get("status"),
            "automation_decision": item.get("automation_decision"),
            "count": item.get("count"),
            "priority_score": item.get("max_priority_score"),
            "reasons": item.get("reasons") or [],
        },
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": "build_regression_candidates",
    }


def load_regression_items() -> list[dict[str, Any]]:
    data = read_json(INPUT_PATH)

    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    if isinstance(data, dict):
        queue = data.get("queue")
        if isinstance(queue, list):
            return [
                x for x in queue
                if isinstance(x, dict) and x.get("status") == "FIXED_NEEDS_REGRESSION"
            ]

    raise ValueError(f"Unsupported regression candidate format: {INPUT_PATH}")


def dedupe_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    for case in cases:
        key = normalize_question(case.get("question") or "")
        if not key or key in seen:
            continue

        seen.add(key)
        out.append(case)

    return out


def write_outputs(cases: list[dict[str, Any]]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    OUT_JSONL.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False, default=str) for case in cases) + ("\n" if cases else ""),
        encoding="utf-8",
    )

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input": str(INPUT_PATH),
        "count": len(cases),
        "cases": cases,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    lines: list[str] = []
    lines.append("# AJSMGPT Regression Candidates")
    lines.append("")
    lines.append(f"- Generated at: `{payload['generated_at']}`")
    lines.append(f"- Input: `{INPUT_PATH}`")
    lines.append(f"- Candidates: `{len(cases)}`")
    lines.append("")
    lines.append("These are review candidates only. Nothing is auto-approved.")
    lines.append("")

    for idx, case in enumerate(cases, start=1):
        expected = case["expected"]
        latest = case["latest_observation"]
        ctx = case["learning_queue_context"]

        lines.append(f"## {idx}. {case['question']}")
        lines.append("")
        lines.append(f"- Case ID: `{case['case_id']}`")
        lines.append(f"- Status: `{case['status']}`")
        lines.append(f"- Expected source: `{expected.get('source')}`")
        lines.append(f"- Expected intent: `{expected.get('intent')}`")
        lines.append(f"- Expected min rows: `{expected.get('min_rows')}`")
        lines.append(f"- Observed rows: `{expected.get('observed_row_count')}`")
        lines.append(f"- SQL safe SELECT: `{latest.get('sql_is_safe_select')}`")
        lines.append(f"- SQL tables: `{', '.join(expected.get('sql_tables') or [])}`")
        lines.append(f"- Learning status: `{ctx.get('status')}`")
        lines.append(f"- Learning decision: `{ctx.get('automation_decision')}`")
        lines.append(f"- Seen count: `{ctx.get('count')}`")
        lines.append(f"- Reasons: `{', '.join(ctx.get('reasons') or [])}`")
        lines.append("")

        if latest.get("answer"):
            lines.append("Latest answer:")
            lines.append("")
            lines.append(str(latest["answer"])[:1000])
            lines.append("")

        if latest.get("sql_preview"):
            lines.append("```sql")
            lines.append(str(latest["sql_preview"]))
            lines.append("```")
            lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    items = load_regression_items()

    raw_cases = []
    for index, item in enumerate(items, start=1):
        latest = item.get("latest") or {}

        if item.get("status") != "FIXED_NEEDS_REGRESSION":
            continue

        if not latest.get("success"):
            continue

        if int(latest.get("row_count") or 0) <= 0:
            continue

        raw_cases.append(build_case(item, index))

    cases = dedupe_cases(raw_cases)
    write_outputs(cases)

    print("Regression candidates generated")
    print("Input:", INPUT_PATH)
    print("Count:", len(cases))
    print("JSONL:", OUT_JSONL)
    print("JSON:", OUT_JSON)
    print("Markdown:", OUT_MD)

    print()
    print("Top 20 candidates:")
    for case in cases[:20]:
        print(
            "-",
            case["question"],
            "| source:",
            case["expected"].get("source"),
            "| intent:",
            case["expected"].get("intent"),
            "| rows:",
            case["expected"].get("observed_row_count"),
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())