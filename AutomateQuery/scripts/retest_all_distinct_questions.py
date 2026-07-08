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
            continue
    return rows


def normalize_question(q: str) -> str:
    return " ".join(str(q or "").strip().lower().split())


def extract_distinct_questions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest = {}
    counts = Counter()

    for row in rows:
        q = str(row.get("question") or "").strip()
        if not q:
            continue

        key = normalize_question(q)
        counts[key] += 1
        latest[key] = row

    items = []
    for key, row in latest.items():
        q = str(row.get("question") or "").strip()
        items.append({
            "question_key": key,
            "question": q,
            "old_count": counts[key],
            "old_success": row.get("success"),
            "old_source": row.get("source"),
            "old_intent": row.get("intent"),
            "old_row_count": row.get("row_count"),
            "old_error": row.get("error"),
        })

    items.sort(key=lambda x: (-int(x["old_count"]), x["question"].lower()))
    return items


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
            body = json.loads(raw)
    except Exception as exc:
        return {
            "success": False,
            "source": None,
            "intent": None,
            "row_count": None,
            "answer": None,
            "sql": None,
            "error": str(exc),
            "api_elapsed_ms": int((time.time() - started) * 1000),
        }

    data = body.get("data", body)
    if not isinstance(data, dict):
        data = {
            "success": False,
            "source": None,
            "intent": None,
            "row_count": None,
            "answer": None,
            "sql": None,
            "error": "Unexpected API response",
        }

    data["api_elapsed_ms"] = int((time.time() - started) * 1000)
    return data


def verdict(result: dict[str, Any]) -> str:
    success = bool(result.get("success"))
    source = str(result.get("source") or "").lower()
    error = result.get("error")
    row_count = result.get("row_count")

    if not success or error:
        return "FAIL"

    if "fallback" in source or "qwen" in source:
        try:
            if row_count is not None and int(row_count) > 0:
                return "WORKING_FALLBACK"
        except Exception:
            pass
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


def load_done(jsonl_path: Path) -> set[str]:
    done = set()
    if not jsonl_path.exists():
        return done

    for line in jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            obj = json.loads(line)
            key = obj.get("question_key")
            if key:
                done.add(str(key))
        except Exception:
            continue

    return done


def write_reports(results_jsonl: Path, out_dir: Path) -> None:
    rows = []
    for line in results_jsonl.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue

    latest_json = out_dir / "latest_all_distinct_retest.json"
    latest_csv = out_dir / "latest_all_distinct_retest.csv"
    latest_md = out_dir / "latest_all_distinct_retest.md"

    latest_json.write_text(json.dumps(rows, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    with latest_csv.open("w", newline="", encoding="utf-8") as f:
        fields = [
            "verdict",
            "question",
            "old_count",
            "old_success",
            "old_source",
            "old_intent",
            "old_row_count",
            "success",
            "source",
            "intent",
            "row_count",
            "elapsed_ms",
            "api_elapsed_ms",
            "answer",
            "error",
            "sql",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k) for k in fields})

    counts = Counter(r.get("verdict") for r in rows)

    lines = []
    lines.append("# AJSMGPT All Distinct Question Retest")
    lines.append("")
    lines.append(f"- Generated: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append(f"- Questions retested: `{len(rows)}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    for k, v in sorted(counts.items()):
        lines.append(f"- {k}: `{v}`")

    lines.append("")
    lines.append("## Needs work")
    lines.append("")

    bad_verdicts = {"FAIL", "REVIEW_FALLBACK", "ZERO_REVIEW", "REVIEW_NO_ROWCOUNT", "REVIEW_BAD_ROWCOUNT"}

    for r in rows:
        if r.get("verdict") not in bad_verdicts:
            continue

        lines.append(f"### {r.get('question')}")
        lines.append("")
        lines.append(f"- Verdict: `{r.get('verdict')}`")
        lines.append(f"- Old count: `{r.get('old_count')}`")
        lines.append(f"- Current source: `{r.get('source')}`")
        lines.append(f"- Current intent: `{r.get('intent')}`")
        lines.append(f"- Current row_count: `{r.get('row_count')}`")
        if r.get("error"):
            lines.append(f"- Error: `{r.get('error')}`")
        if r.get("answer"):
            lines.append(f"- Answer: {r.get('answer')}")
        if r.get("sql"):
            lines.append("")
            lines.append("```sql")
            lines.append(str(r.get("sql"))[:2500])
            lines.append("```")
        lines.append("")

    latest_md.write_text("\n".join(lines), encoding="utf-8")

    print()
    print("Reports updated:")
    print("JSON:", latest_json)
    print("CSV :", latest_csv)
    print("MD  :", latest_md)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--api-url", default="http://172.16.90.1:8000")
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    log_file = Path(args.log_file)
    if not log_file.exists():
        raise SystemExit(f"Log file not found: {log_file}")

    out_dir = Path("AutomateQuery/reports/retest_from_logs")
    out_dir.mkdir(parents=True, exist_ok=True)

    results_jsonl = out_dir / "all_distinct_retest_results.jsonl"

    rows = read_jsonl(log_file)
    questions = extract_distinct_questions(rows)

    if args.limit and args.limit > 0:
        questions = questions[: args.limit]

    done = load_done(results_jsonl) if args.resume else set()

    print("Total log rows:", len(rows))
    print("Distinct questions:", len(questions))
    print("Already done:", len(done))
    print("API:", args.api_url)
    print("Timeout per question:", args.timeout)
    print("Results JSONL:", results_jsonl)
    print()

    total = len(questions)

    with results_jsonl.open("a", encoding="utf-8") as f:
        for idx, item in enumerate(questions, start=1):
            key = item["question_key"]
            q = item["question"]

            if key in done:
                continue

            print(f"[{idx}/{total}] {q}")

            result = ask_api(args.api_url, q, args.timeout)
            v = verdict(result)

            row = {
                **item,
                "tested_at": datetime.now().isoformat(timespec="seconds"),
                "verdict": v,
                "success": result.get("success"),
                "source": result.get("source"),
                "intent": result.get("intent"),
                "row_count": result.get("row_count"),
                "elapsed_ms": result.get("elapsed_ms"),
                "api_elapsed_ms": result.get("api_elapsed_ms"),
                "answer": result.get("answer"),
                "sql": result.get("sql"),
                "error": result.get("error"),
            }

            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            f.flush()

            print("  verdict:", v)
            print("  source:", row.get("source"))
            print("  intent:", row.get("intent"))
            print("  row_count:", row.get("row_count"))
            print()

            time.sleep(args.sleep)

    write_reports(results_jsonl, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
