from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = PROJECT_ROOT / "logs" / "user_questions.jsonl"

if not LOG_PATH.exists():
    print("No question log found:", LOG_PATH)
    raise SystemExit(0)

lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
records = []

for line in lines:
    if not line.strip():
        continue
    try:
        records.append(json.loads(line))
    except Exception:
        pass

print("LOG:", LOG_PATH)
print("TOTAL QUESTIONS:", len(records))
print("-" * 120)

for i, r in enumerate(records[-50:], start=max(1, len(records) - 49)):
    print(
        f"{i:04d}. "
        f"success={r.get('success')} | "
        f"source={r.get('source')} | "
        f"intent={r.get('intent')} | "
        f"rows={r.get('row_count')} | "
        f"{r.get('question')}"
    )
