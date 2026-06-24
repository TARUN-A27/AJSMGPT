from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


AUTOMATE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = AUTOMATE_DIR.parent

REPORTS_DIR = AUTOMATE_DIR / "reports"

USER_LOG_JSONL = PROJECT_ROOT / "logs" / "user_questions.jsonl"
QUESTIONS_ONLY_TXT = PROJECT_ROOT / "logs" / "questions_only.txt"
USER_PURCHASE_QUESTIONS_TXT = PROJECT_ROOT / "data" / "user_purchase_mrs_questions.txt"

QUESTION_BANK_JSON = REPORTS_DIR / "question_bank.json"
QUESTION_BANK_MD = REPORTS_DIR / "question_bank.md"


def normalize_question(question: str) -> str:
    q = question.lower().strip()
    q = re.sub(r"[^a-z0-9\s]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def classify_question(question: str) -> str:
    q = question.lower()
    words = set(re.findall(r"[a-z0-9]+", q))

    if "mrs" in words:
        return "mrs"

    if "attendance" in q or "empcode" in q or "authentication" in q:
        return "attendance"

    if "camera" in q or re.search(r"\bip\b", q):
        return "camera_ip"

    if "vehicle" in q:
        return "vehicle"

    if "document" in q or "voucher" in q or "party" in q:
        return "document_party"

    if (
        "received" in q
        or "receipt" in q
        or "issue" in q
        or "store" in q
        or "hold" in q
        or "approval pending" in q
    ):
        return "inventory_movement"

    if "supplier" in q or "supply" in q or "vendor" in q:
        return "supplier_purchase"

    if (
        "purchase" in q
        or "rate" in q
        or "price" in q
        or "order" in q
        or "grn" in q
        or "material" in q
        or "cost" in q
    ):
        return "purchase_analytics"

    return "review_required"


def read_questions_only(path: Path, source_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if not path.exists():
        return rows

    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        question = line.strip()
        if not question:
            continue

        rows.append(
            {
                "question": question,
                "input_source": source_name,
                "line_no": line_no,
                "success": None,
                "source": None,
                "intent": None,
                "row_count": None,
                "elapsed_ms": None,
            }
        )

    return rows


def read_user_log_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    if not path.exists():
        return rows

    with path.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            question = str(record.get("question") or "").strip()
            if not question:
                continue

            rows.append(
                {
                    "question": question,
                    "input_source": "logs/user_questions.jsonl",
                    "line_no": line_no,
                    "success": record.get("success"),
                    "source": record.get("source"),
                    "intent": record.get("intent"),
                    "row_count": record.get("row_count"),
                    "elapsed_ms": record.get("elapsed_ms"),
                }
            )

    return rows


def build_question_bank(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in raw_rows:
        key = normalize_question(row["question"])
        grouped[key].append(row)

    bank: list[dict[str, Any]] = []

    for index, (normalized, rows) in enumerate(grouped.items(), start=1):
        first = rows[0]
        category = classify_question(first["question"])

        sources = sorted({str(r.get("input_source")) for r in rows})
        router_sources = sorted({
            str(r.get("source"))
            for r in rows
            if r.get("source") not in (None, "")
        })
        intents = sorted({
            str(r.get("intent"))
            for r in rows
            if r.get("intent") not in (None, "")
        })

        fallback_count = sum(
            1
            for r in rows
            if "fallback" in str(r.get("source") or "").lower()
        )

        failed_count = sum(
            1
            for r in rows
            if r.get("success") is False
        )

        slow_count = sum(
            1
            for r in rows
            if isinstance(r.get("elapsed_ms"), (int, float)) and r.get("elapsed_ms") > 3000
        )

        bank.append(
            {
                "question_id": f"question_{index}",
                "question": first["question"],
                "normalized_question": normalized,
                "category": category,
                "occurrence_count": len(rows),
                "input_sources": sources,
                "router_sources_seen": router_sources,
                "intents_seen": intents,
                "fallback_count": fallback_count,
                "failed_count": failed_count,
                "slow_count": slow_count,
                "needs_review": (
                    category == "review_required"
                    or fallback_count > 0
                    or failed_count > 0
                    or slow_count > 0
                ),
            }
        )

    bank.sort(
        key=lambda item: (
            item["category"],
            item["question"].lower(),
        )
    )

    return bank


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_markdown(bank: list[dict[str, Any]], raw_count: int) -> None:
    lines: list[str] = []

    lines.append("# AutomateQuery Question Bank")
    lines.append("")
    lines.append(f"Generated at: `{datetime.now().isoformat(timespec='seconds')}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Raw question rows read: `{raw_count}`")
    lines.append(f"- Unique questions: `{len(bank)}`")
    lines.append("")

    category_counts: dict[str, int] = defaultdict(int)
    for item in bank:
        category_counts[item["category"]] += 1

    lines.append("## Category Counts")
    lines.append("")

    for category, count in sorted(category_counts.items()):
        lines.append(f"- `{category}`: `{count}`")

    lines.append("")
    lines.append("## Unique Questions")
    lines.append("")

    for item in bank:
        lines.append(f"### {item['question_id']}")
        lines.append("")
        lines.append(f"- Question: `{item['question']}`")
        lines.append(f"- Category: `{item['category']}`")
        lines.append(f"- Occurrence count: `{item['occurrence_count']}`")
        lines.append(f"- Router sources seen: `{item['router_sources_seen']}`")
        lines.append(f"- Intents seen: `{item['intents_seen']}`")
        lines.append(f"- Fallback count: `{item['fallback_count']}`")
        lines.append(f"- Failed count: `{item['failed_count']}`")
        lines.append(f"- Slow count: `{item['slow_count']}`")
        lines.append(f"- Needs review: `{item['needs_review']}`")
        lines.append("")

    QUESTION_BANK_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    raw_rows: list[dict[str, Any]] = []
    raw_rows.extend(read_user_log_jsonl(USER_LOG_JSONL))
    raw_rows.extend(read_questions_only(QUESTIONS_ONLY_TXT, "logs/questions_only.txt"))
    raw_rows.extend(read_questions_only(USER_PURCHASE_QUESTIONS_TXT, "data/user_purchase_mrs_questions.txt"))

    bank = build_question_bank(raw_rows)

    write_json(QUESTION_BANK_JSON, bank)
    write_markdown(bank, raw_count=len(raw_rows))

    print(f"Raw question rows read: {len(raw_rows)}")
    print(f"Unique questions: {len(bank)}")
    print(f"Wrote: {QUESTION_BANK_JSON}")
    print(f"Wrote: {QUESTION_BANK_MD}")


if __name__ == "__main__":
    main()
