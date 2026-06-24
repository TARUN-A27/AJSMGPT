from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


AUTOMATE_DIR = Path(__file__).resolve().parents[1]

REPORTS_DIR = AUTOMATE_DIR / "reports"
ROUTER_FIX_JSON = REPORTS_DIR / "router_fix_candidates.json"
GENERATED_EVAL_JSON = REPORTS_DIR / "generated_eval_candidates.json"


def read_json(path: Path) -> Any:
    if not path.exists():
        print(f"Missing file: {path}")
        return []

    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def make_dedupe_key(eval_test: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(eval_test.get("question", "")).strip().lower(),
        eval_test.get("expected_source"),
        eval_test.get("expected_intent"),
        tuple(eval_test.get("expected_sql_contains", [])),
    )


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    candidates = read_json(ROUTER_FIX_JSON)

    eval_candidates: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()

    for candidate in candidates:
        suggestion = candidate.get("suggestion", {})
        eval_test = suggestion.get("suggested_eval_test")

        if not eval_test:
            continue

        if eval_test.get("expected_source") == "REVIEW_REQUIRED":
            continue

        dedupe_key = make_dedupe_key(eval_test)

        if dedupe_key in seen_keys:
            continue

        seen_keys.add(dedupe_key)

        eval_candidates.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "status": "review_required",
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "question": eval_test.get("question"),
                "expected_source": eval_test.get("expected_source"),
                "expected_intent": eval_test.get("expected_intent"),
                "expected_sql_contains": eval_test.get("expected_sql_contains", []),
                "notes": "Generated from real user log. Human review required before adding to regression tests.",
                "auto_apply_allowed": False,
            }
        )

    write_json(GENERATED_EVAL_JSON, eval_candidates)

    print(f"Generated unique eval candidates: {len(eval_candidates)}")
    print(f"Wrote: {GENERATED_EVAL_JSON}")


if __name__ == "__main__":
    main()
