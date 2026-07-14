#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import request, error


PROJECT_ROOT = Path(__file__).resolve().parents[2]

CASES_PATH = PROJECT_ROOT / "AutomateQuery" / "evals" / "approved_regression_cases.jsonl"
OUT_DIR = PROJECT_ROOT / "AutomateQuery" / "reports" / "regression_runs"
PROGRESS_PATH = OUT_DIR / "latest_approved_regression_progress.json"
API_URL = "http://127.0.0.1:8000/ask"

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


def load_cases(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Approved regression file not found: {path}")

    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def post_question(question: str, timeout_seconds: int = 90) -> tuple[bool, dict[str, Any] | None, str | None, float]:
    started = time.time()
    payload = json.dumps({"question": question}).encode("utf-8")

    req = request.Request(
        API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout_seconds) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            elapsed = time.time() - started
            return True, json.loads(body), None, elapsed
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        elapsed = time.time() - started
        return False, None, f"HTTP {exc.code}: {body[:1000]}", elapsed
    except Exception as exc:
        elapsed = time.time() - started
        return False, None, f"{type(exc).__name__}: {exc}", elapsed


def unwrap_response(response: dict[str, Any] | None) -> dict[str, Any]:
    if not response:
        return {}

    data = response.get("data")
    if isinstance(data, dict):
        return data

    return response


def is_safe_select_sql(sql: str | None) -> bool:
    if not sql:
        return False

    compact = re.sub(r"\s+", " ", str(sql).strip()).upper()

    if not compact.startswith("SELECT"):
        return False

    tokens = set(re.findall(r"[A-Z]+", compact))
    return not bool(tokens.intersection(UNSAFE_SQL_WORDS))


def check_case(case: dict[str, Any], response: dict[str, Any] | None, transport_ok: bool, transport_error: str | None) -> dict[str, Any]:
    expected = case.get("expected") or {}
    actual = unwrap_response(response)

    question = case.get("question")
    sql = actual.get("sql")
    row_count = int(actual.get("row_count") or 0)

    expected_source = expected.get("source")
    expected_intent = expected.get("intent")
    expected_min_rows = int(expected.get("min_rows") or 0)
    must_not_use_fallback = bool(expected.get("must_not_use_fallback", True))

    checks = {
        "transport_ok": transport_ok,
        "api_success": bool(actual.get("success")),
        "sql_safe_select": is_safe_select_sql(sql),
        "min_rows_ok": row_count >= expected_min_rows,
        "source_ok": actual.get("source") == expected_source if expected_source else True,
        "intent_ok": actual.get("intent") == expected_intent if expected_intent else True,
        "fallback_ok": not (
            must_not_use_fallback
            and str(actual.get("source") or "").lower() in {"qwen_schema_fallback", "fallback", "schema_fallback"}
        ),
    }

    passed = all(checks.values())

    return {
        "case_id": case.get("case_id"),
        "question": question,
        "passed": passed,
        "checks": checks,
        "expected": {
            "source": expected_source,
            "intent": expected_intent,
            "min_rows": expected_min_rows,
        },
        "actual": {
            "success": actual.get("success"),
            "source": actual.get("source"),
            "intent": actual.get("intent"),
            "row_count": row_count,
            "elapsed_ms": actual.get("elapsed_ms"),
            "sql_safe_select": is_safe_select_sql(sql),
            "sql_preview": str(sql or "")[:2500],
            "answer": actual.get("answer"),
            "error": actual.get("error") or transport_error,
        },
    }


def write_progress(
    *,
    total: int,
    completed: int,
    passed: int,
    failed: int,
    current_question: str | None,
    running: bool,
    started_at: str | None,
    finished_at: str | None = None,
) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    percent = 0
    if total:
        percent = round((completed / total) * 100, 2)

    payload = {
        "running": running,
        "started_at": started_at,
        "finished_at": finished_at,
        "total": total,
        "completed": completed,
        "passed": passed,
        "failed": failed,
        "percent": percent,
        "current_question": current_question,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

    PROGRESS_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def progress_counts(results: list[dict[str, Any]]) -> tuple[int, int]:
    passed = sum(1 for item in results if item.get("passed"))
    failed = sum(1 for item in results if not item.get("passed"))
    return passed, failed


def write_reports(results: list[dict[str, Any]]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    latest_json = OUT_DIR / "latest_approved_regression_run.json"
    latest_md = OUT_DIR / "latest_approved_regression_run.md"
    stamped_json = OUT_DIR / f"approved_regression_run_{ts}.json"
    stamped_md = OUT_DIR / f"approved_regression_run_{ts}.md"

    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "cases_path": str(CASES_PATH),
        "api_url": API_URL,
        "total": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "results": results,
    }

    json_text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    latest_json.write_text(json_text, encoding="utf-8")
    stamped_json.write_text(json_text, encoding="utf-8")

    lines = []
    lines.append("# AJSMGPT Approved Regression Run")
    lines.append("")
    lines.append(f"- Generated at: `{payload['generated_at']}`")
    lines.append(f"- API: `{API_URL}`")
    lines.append(f"- Total: `{payload['total']}`")
    lines.append(f"- Passed: `{payload['passed']}`")
    lines.append(f"- Failed: `{payload['failed']}`")
    lines.append("")

    if failed:
        lines.append("## Failed")
        lines.append("")
        for idx, result in enumerate(failed, start=1):
            lines.append(f"### {idx}. {result['question']}")
            lines.append("")
            lines.append(f"- Case ID: `{result['case_id']}`")
            lines.append(f"- Expected source: `{result['expected']['source']}`")
            lines.append(f"- Actual source: `{result['actual']['source']}`")
            lines.append(f"- Expected intent: `{result['expected']['intent']}`")
            lines.append(f"- Actual intent: `{result['actual']['intent']}`")
            lines.append(f"- Expected min rows: `{result['expected']['min_rows']}`")
            lines.append(f"- Actual rows: `{result['actual']['row_count']}`")
            lines.append(f"- Checks: `{json.dumps(result['checks'], ensure_ascii=False)}`")
            if result["actual"].get("error"):
                lines.append(f"- Error: `{result['actual']['error']}`")
            if result["actual"].get("sql_preview"):
                lines.append("")
                lines.append("```sql")
                lines.append(result["actual"]["sql_preview"])
                lines.append("```")
            lines.append("")

    lines.append("## Passed")
    lines.append("")
    for idx, result in enumerate(passed, start=1):
        lines.append(
            f"- {idx}. `{result['case_id']}` | {result['question']} | "
            f"{result['actual']['source']} | {result['actual']['intent']} | rows={result['actual']['row_count']}"
        )

    md_text = "\n".join(lines)
    latest_md.write_text(md_text, encoding="utf-8")
    stamped_md.write_text(md_text, encoding="utf-8")

    print("Approved regression run complete")
    print("Total:", payload["total"])
    print("Passed:", payload["passed"])
    print("Failed:", payload["failed"])
    print("JSON:", latest_json)
    print("Markdown:", latest_md)


def main() -> int:
    cases = load_cases(CASES_PATH)
    results = []
    started_at = datetime.now().isoformat(timespec="seconds")

    write_progress(
        total=len(cases),
        completed=0,
        passed=0,
        failed=0,
        current_question=None,
        running=True,
        started_at=started_at,
    )

    for idx, case in enumerate(cases, start=1):
        question = str(case.get("question") or "").strip()
        passed_count, failed_count = progress_counts(results)

        write_progress(
            total=len(cases),
            completed=len(results),
            passed=passed_count,
            failed=failed_count,
            current_question=question,
            running=True,
            started_at=started_at,
        )

        print(f"[{idx}/{len(cases)}] {question}")

        transport_ok, response, transport_error, elapsed = post_question(question)
        result = check_case(case, response, transport_ok, transport_error)
        result["transport_elapsed_seconds"] = round(elapsed, 4)
        results.append(result)

        passed_count, failed_count = progress_counts(results)
        write_progress(
            total=len(cases),
            completed=len(results),
            passed=passed_count,
            failed=failed_count,
            current_question=question,
            running=True,
            started_at=started_at,
        )

        status = "PASS" if result["passed"] else "FAIL"
        actual = result["actual"]
        print(
            " ",
            status,
            "| source:",
            actual.get("source"),
            "| intent:",
            actual.get("intent"),
            "| rows:",
            actual.get("row_count"),
        )

    write_reports(results)

    passed_count, failed_count = progress_counts(results)
    write_progress(
        total=len(cases),
        completed=len(results),
        passed=passed_count,
        failed=failed_count,
        current_question=None,
        running=False,
        started_at=started_at,
        finished_at=datetime.now().isoformat(timespec="seconds"),
    )

    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
