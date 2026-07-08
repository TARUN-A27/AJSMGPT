#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []

    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue

        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                obj["_line_no"] = line_no
                rows.append(obj)
        except Exception:
            pass

    return rows


def unique_questions(rows: list[dict[str, Any]], only_failed_or_zero: bool) -> list[str]:
    latest: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()

    for row in rows:
        q = str(row.get("question") or "").strip()
        if not q:
            continue

        key = " ".join(q.lower().split())
        counts[key] += 1
        latest[key] = row

    selected = []

    for key, row in latest.items():
        q = str(row.get("question") or "").strip()

        success = bool(row.get("success"))
        row_count = row.get("row_count")
        source = str(row.get("source") or "").lower()
        error = row.get("error")

        is_problem = (
            not success
            or error
            or row_count in (None, 0)
            or "fallback" in source
            or "qwen" in source
        )

        if only_failed_or_zero and not is_problem:
            continue

        selected.append((counts[key], q))

    selected.sort(key=lambda x: (-x[0], x[1].lower()))
    return [q for _, q in selected]


def ask_api(api_url: str, question: str, timeout: int) -> dict[str, Any]:
    payload = json.dumps({"question": question}).encode("utf-8")
    req = urllib.request.Request(
        api_url.rstrip("/") + "/ask",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    started = time.time()

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw)
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    data = payload.get("data", payload)
    if not isinstance(data, dict):
        data = {"success": False, "error": "Unexpected API payload", "raw": payload}

    data["_api_elapsed_ms"] = int((time.time() - started) * 1000)
    return data


def verdict(result: dict[str, Any]) -> str:
    success = bool(result.get("success"))
    row_count = result.get("row_count")
    source = str(result.get("source") or "").lower()
    error = result.get("error")

    if not success or error:
        return "FAIL"

    if "fallback" in source or "qwen" in source:
        if row_count and int(row_count) > 0:
            return "WORKING_FALLBACK"
        return "REVIEW_FALLBACK"

    if row_count is None:
        return "REVIEW_NO_ROWCOUNT"

    try:
        rc = int(row_count)
    except Exception:
        return "REVIEW_BAD_ROWCOUNT"

    if rc > 0:
        return "PASS"

    return "ZERO_REVIEW"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--api-url", default="http://172.16.90.1:8000")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only-failed-or-zero", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    log_path = Path(args.log_file)
    if not log_path.exists():
        raise SystemExit(f"Log file not found: {log_path}")

    rows = read_jsonl(log_path)
    questions = unique_questions(rows, only_failed_or_zero=args.only_failed_or_zero)

    if args.limit and args.limit > 0:
        questions = questions[: args.limit]

    out_dir = Path("AutomateQuery/reports/retest_from_logs")
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"retest_results_{ts}.json"
    csv_path = out_dir / f"retest_results_{ts}.csv"
    md_path = out_dir / f"retest_results_{ts}.md"

    results = []

    print("Questions to retest:", len(questions))
    print("API:", args.api_url)
    print()

    for i, q in enumerate(questions, start=1):
        print(f"[{i}/{len(questions)}] {q}")
        result = ask_api(args.api_url, q, args.timeout)
        v = verdict(result)

        item = {
            "question": q,
            "verdict": v,
            "success": result.get("success"),
            "source": result.get("source"),
            "intent": result.get("intent"),
            "row_count": result.get("row_count"),
            "elapsed_ms": result.get("elapsed_ms"),
            "api_elapsed_ms": result.get("_api_elapsed_ms"),
            "answer": result.get("answer"),
            "sql": result.get("sql"),
            "error": result.get("error"),
        }
        results.append(item)

        print("  verdict:", v)
        print("  source:", item["source"])
        print("  intent:", item["intent"])
        print("  row_count:", item["row_count"])
        print()

    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "verdict",
                "question",
                "success",
                "source",
                "intent",
                "row_count",
                "elapsed_ms",
                "api_elapsed_ms",
                "answer",
                "error",
                "sql",
            ],
        )
        writer.writeheader()
        for row in results:
            writer.writerow(row)

    counts = Counter(r["verdict"] for r in results)

    lines = []
    lines.append("# AJSMGPT Retest From Logs")
    lines.append("")
    lines.append(f"- Generated: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append(f"- Log file: `{log_path}`")
    lines.append(f"- API: `{args.api_url}`")
    lines.append(f"- Questions tested: `{len(results)}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    for k, v in sorted(counts.items()):
        lines.append(f"- {k}: `{v}`")
    lines.append("")
    lines.append("## Failed / review questions")
    lines.append("")

    for r in results:
        if r["verdict"] in {"FAIL", "REVIEW_FALLBACK", "REVIEW_NO_ROWCOUNT", "REVIEW_BAD_ROWCOUNT", "ZERO_REVIEW"}:
            lines.append(f"### {r['question']}")
            lines.append("")
            lines.append(f"- Verdict: `{r['verdict']}`")
            lines.append(f"- Source: `{r.get('source')}`")
            lines.append(f"- Intent: `{r.get('intent')}`")
            lines.append(f"- Row count: `{r.get('row_count')}`")
            if r.get("error"):
                lines.append(f"- Error: `{r.get('error')}`")
            if r.get("answer"):
                lines.append(f"- Answer: {r.get('answer')}")
            if r.get("sql"):
                lines.append("")
                lines.append("```sql")
                lines.append(str(r["sql"])[:3000])
                lines.append("```")
            lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")

    print("Saved JSON:", json_path)
    print("Saved CSV :", csv_path)
    print("Saved MD  :", md_path)
    print()
    print("Summary:")
    for k, v in sorted(counts.items()):
        print(f"{k}: {v}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
