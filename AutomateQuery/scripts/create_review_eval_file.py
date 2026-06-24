from __future__ import annotations

import json
from pathlib import Path
from typing import Any


AUTOMATE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = AUTOMATE_DIR / "reports"

GENERATED_EVAL_JSON = REPORTS_DIR / "generated_eval_candidates.json"
REVIEWED_EVAL_JSON = REPORTS_DIR / "reviewed_eval_candidates.json"


def read_json(path: Path) -> Any:
    if not path.exists():
        return []

    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def make_key(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(record.get("question", "")).strip().lower(),
        record.get("expected_source"),
        record.get("expected_intent"),
        tuple(record.get("expected_sql_contains", [])),
    )


def main() -> None:
    generated_candidates = read_json(GENERATED_EVAL_JSON)
    existing_reviewed = read_json(REVIEWED_EVAL_JSON)

    reviewed_by_key = {
        make_key(record): record
        for record in existing_reviewed
    }

    final_reviewed: list[dict[str, Any]] = []

    for candidate in generated_candidates:
        key = make_key(candidate)

        if key in reviewed_by_key:
            # Preserve previous human approval/review note.
            final_reviewed.append(reviewed_by_key[key])
            continue

        final_reviewed.append(
            {
                "approved": False,
                "review_note": "",
                "candidate_id": candidate.get("candidate_id"),
                "question": candidate.get("question"),
                "expected_source": candidate.get("expected_source"),
                "expected_intent": candidate.get("expected_intent"),
                "expected_sql_contains": candidate.get("expected_sql_contains", []),
                "auto_apply_allowed": False,
                "status": "waiting_for_human_review",
            }
        )

    write_json(REVIEWED_EVAL_JSON, final_reviewed)

    print(f"Review candidates available: {len(final_reviewed)}")
    print(f"Wrote: {REVIEWED_EVAL_JSON}")
    print("Existing human approvals were preserved.")


if __name__ == "__main__":
    main()
