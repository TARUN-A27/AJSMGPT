from pathlib import Path
import csv
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.query_engine import answer_question

questions_path = PROJECT_ROOT / "data" / "user_purchase_mrs_questions.txt"
report_path = PROJECT_ROOT / "reports" / "user_purchase_mrs_question_baseline.csv"

questions = [
    line.strip()
    for line in questions_path.read_text().splitlines()
    if line.strip()
]

rows = []

for i, q in enumerate(questions, start=1):
    print("-" * 120)
    print(f"{i}. QUESTION: {q}")

    start = time.perf_counter()
    try:
        result = answer_question(q)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

        success = result.get("success")
        source = result.get("source")
        intent = result.get("intent")
        row_count = result.get("row_count")
        sql = result.get("sql") or ""
        error = result.get("error") or ""

        print("SUCCESS:", success)
        print("SOURCE:", source)
        print("INTENT:", intent)
        print("ROW_COUNT:", row_count)
        print("TIME_MS:", elapsed_ms)
        print("SQL:", sql[:1000])

        status = "OK"
        if not success:
            status = "FAILED"
        elif not sql:
            status = "NO_SQL"
        elif source in ("qwen_schema_fallback", "llm_fallback", "fallback"):
            status = "FALLBACK_CHECK"
        elif row_count == 0:
            status = "ZERO_ROWS_CHECK"

        rows.append({
            "no": i,
            "question": q,
            "status": status,
            "success": success,
            "source": source,
            "intent": intent,
            "row_count": row_count,
            "elapsed_ms": elapsed_ms,
            "sql": sql.replace("\n", " "),
            "error": error,
        })

    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        print("ERROR:", type(exc).__name__, str(exc))
        rows.append({
            "no": i,
            "question": q,
            "status": "ERROR",
            "success": False,
            "source": "",
            "intent": "",
            "row_count": "",
            "elapsed_ms": elapsed_ms,
            "sql": "",
            "error": f"{type(exc).__name__}: {exc}",
        })

report_path.parent.mkdir(exist_ok=True)

with report_path.open("w", newline="") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "no", "question", "status", "success", "source", "intent",
            "row_count", "elapsed_ms", "sql", "error"
        ],
    )
    writer.writeheader()
    writer.writerows(rows)

print("-" * 120)
print("REPORT:", report_path)

print("\nSUMMARY:")
for r in rows:
    source = str(r.get("source") or "-")
    intent = str(r.get("intent") or "-")
    row_count = str(r.get("row_count") if r.get("row_count") is not None else "-")
    print(f"{r['no']:02d}. {r['status']:<16} | {source:<22} | {intent} | rows={row_count} | {r['question']}")
