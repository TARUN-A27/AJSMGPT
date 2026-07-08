#!/usr/bin/env python3
"""
AutomateQuery Candidate Query Generator v1

Purpose:
- Read AutomateQuery diagnosis output.
- Find diagnosis rows with candidate SQL.
- Verify candidate SQL safely using app.oracle_client.run_safe_select().
- Write review-ready candidate query reports.
- Never edit production router files automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = Path(
    os.environ.get("AJSMGPT_RUNTIME_ROOT", "/home/ajsmgpt/AJSMGPT")
).resolve()

DEFAULT_INPUT = PROJECT_ROOT / "AutomateQuery/reports/question_diagnosis/latest_diagnosis.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "AutomateQuery/reports/candidate_queries"


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_env_file(RUNTIME_ROOT / ".env")
load_env_file(PROJECT_ROOT / ".env")

for path in [str(RUNTIME_ROOT), str(PROJECT_ROOT)]:
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from app.oracle_client import run_safe_select
except Exception as exc:  # noqa: BLE001
    run_safe_select = None
    IMPORT_ERROR = repr(exc)
else:
    IMPORT_ERROR = None


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def slugify(value: str, max_len: int = 80) -> str:
    value = str(value or "").lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:max_len] or "candidate"


def is_safe_candidate_sql(sql: str) -> tuple[bool, str]:
    s = str(sql or "").strip()
    upper = s.upper()

    if not upper.startswith("SELECT"):
        return False, "Candidate SQL must start with SELECT."

    blocked = [
        " INSERT ",
        " UPDATE ",
        " DELETE ",
        " DROP ",
        " ALTER ",
        " TRUNCATE ",
        " MERGE ",
        " CREATE ",
        " GRANT ",
        " REVOKE ",
        " EXEC ",
        " EXECUTE ",
        " BEGIN ",
        " DECLARE ",
        " COMMIT ",
        " ROLLBACK ",
    ]

    padded = f" {upper} "
    for keyword in blocked:
        if keyword in padded:
            return False, f"Blocked SQL keyword found: {keyword.strip()}"

    return True, "safe"


def verify_candidate_sql(sql: str, probes_enabled: bool = True) -> dict[str, Any]:
    safe, reason = is_safe_candidate_sql(sql)

    result = {
        "safe": safe,
        "safe_reason": reason,
        "verified": False,
        "row_count": None,
        "columns": [],
        "rows_preview": [],
        "elapsed_ms": None,
        "error": None,
    }

    if not safe:
        return result

    if not probes_enabled:
        result["error"] = "verification disabled"
        return result

    if run_safe_select is None:
        result["error"] = f"could not import run_safe_select: {IMPORT_ERROR}"
        return result

    try:
        db_result = run_safe_select(sql)
        rows = db_result.get("rows", []) or []
        result["verified"] = True
        result["row_count"] = db_result.get("row_count", 0)
        result["columns"] = db_result.get("columns", [])
        result["rows_preview"] = rows[:5]
        result["elapsed_ms"] = db_result.get("elapsed_ms")
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


def build_candidate_record(diagnosis: dict[str, Any], index: int, probes_enabled: bool = True) -> dict[str, Any] | None:
    candidate = diagnosis.get("candidate")
    if not candidate:
        return None

    if candidate.get("type") != "candidate_sql":
        return {
            "candidate_id": f"candidate_{index:04d}_{slugify(diagnosis.get('question'))}",
            "question": diagnosis.get("question"),
            "classification": diagnosis.get("classification"),
            "module": diagnosis.get("module"),
            "old_intent": diagnosis.get("old_intent"),
            "candidate_type": candidate.get("type"),
            "suggestion": candidate.get("suggestion"),
            "auto_apply": False,
            "needs_human_review": True,
            "verification": {
                "verified": False,
                "error": "Candidate is not SQL; route to clarification/template review.",
            },
            "raw_diagnosis": diagnosis,
        }

    sql = candidate.get("sql") or ""
    verification = verify_candidate_sql(sql, probes_enabled=probes_enabled)

    row_count = verification.get("row_count")
    verified_with_rows = bool(verification.get("verified") and row_count and row_count > 0)

    return {
        "candidate_id": f"candidate_{index:04d}_{slugify(diagnosis.get('question'))}",
        "question": diagnosis.get("question"),
        "classification": diagnosis.get("classification"),
        "confidence": diagnosis.get("confidence"),
        "reason": diagnosis.get("reason"),
        "module": diagnosis.get("module"),
        "old_verdict": diagnosis.get("old_verdict"),
        "old_source": diagnosis.get("old_source"),
        "old_intent": diagnosis.get("old_intent"),
        "suggested_intent": candidate.get("suggested_intent"),
        "entities": diagnosis.get("entities", {}),
        "candidate_sql": sql,
        "verification": verification,
        "verified_with_rows": verified_with_rows,
        "auto_apply": False,
        "needs_human_review": True,
        "review_status": "pending",
        "review_note": "",
        "raw_candidate": candidate,
    }


def build_md_report(records: list[dict[str, Any]]) -> str:
    verified = [r for r in records if r.get("verified_with_rows")]
    failed = [r for r in records if not r.get("verified_with_rows")]

    lines = []
    lines.append("# AutomateQuery Candidate Query Report")
    lines.append("")
    lines.append(f"- Generated: `{now_iso()}`")
    lines.append(f"- Candidates: `{len(records)}`")
    lines.append(f"- Verified with rows: `{len(verified)}`")
    lines.append(f"- Needs review / no rows / failed verification: `{len(failed)}`")
    lines.append("")
    lines.append("## Candidates")
    lines.append("")

    if not records:
        lines.append("No candidate SQL rows were generated from the current diagnosis report.")
        lines.append("")
        lines.append("This is okay when all non-pass questions are classified as `EXPECTED_ZERO`.")
        return "\n".join(lines)

    for record in records:
        v = record.get("verification") or {}

        lines.append(f"### {record.get('question')}")
        lines.append("")
        lines.append(f"- Candidate ID: `{record.get('candidate_id')}`")
        lines.append(f"- Classification: `{record.get('classification')}`")
        lines.append(f"- Module: `{record.get('module')}`")
        lines.append(f"- Old intent: `{record.get('old_intent')}`")
        lines.append(f"- Suggested intent: `{record.get('suggested_intent')}`")
        lines.append(f"- Verified: `{v.get('verified')}`")
        lines.append(f"- Row count: `{v.get('row_count')}`")
        lines.append(f"- Safe: `{v.get('safe')}`")
        lines.append(f"- Error: `{v.get('error')}`")
        lines.append(f"- Auto apply: `{record.get('auto_apply')}`")
        lines.append("")

        sql = record.get("candidate_sql")
        if sql:
            lines.append("```sql")
            lines.append(sql)
            lines.append("```")
            lines.append("")

        rows_preview = v.get("rows_preview") or []
        if rows_preview:
            lines.append("Rows preview:")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(rows_preview[:3], indent=2, ensure_ascii=False, default=str))
            lines.append("```")
            lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(DEFAULT_INPUT), help="latest_diagnosis.json path")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUT_DIR), help="candidate report output directory")
    ap.add_argument("--no-verify", action="store_true", help="do not execute candidate SQL verification")
    args = ap.parse_args()

    input_path = Path(args.input)
    out_dir = Path(args.output_dir)
    probes_enabled = not args.no_verify

    diagnoses = load_json(input_path)
    if not isinstance(diagnoses, list):
        raise SystemExit(f"Expected list in {input_path}")

    records = []
    for index, diagnosis in enumerate(diagnoses, start=1):
        record = build_candidate_record(diagnosis, index=index, probes_enabled=probes_enabled)
        if record:
            records.append(record)

    verified_records = [r for r in records if r.get("verified_with_rows")]
    pending_records = [r for r in records if not r.get("verified_with_rows")]

    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "latest_candidate_queries.json", records)
    write_json(out_dir / "verified_candidate_queries.json", verified_records)
    write_json(out_dir / "pending_candidate_queries.json", pending_records)
    (out_dir / "latest_candidate_queries.md").write_text(build_md_report(records), encoding="utf-8")

    print("Candidate query generation complete")
    print(f"Input: {input_path}")
    print(f"Candidates: {len(records)}")
    print(f"Verified with rows: {len(verified_records)}")
    print(f"Pending/failed/no rows: {len(pending_records)}")
    print("")
    print(f"JSON: {out_dir / 'latest_candidate_queries.json'}")
    print(f"MD  : {out_dir / 'latest_candidate_queries.md'}")
    print(f"VER : {out_dir / 'verified_candidate_queries.json'}")
    print(f"PEND: {out_dir / 'pending_candidate_queries.json'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
