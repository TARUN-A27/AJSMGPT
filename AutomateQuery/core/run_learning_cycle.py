#!/usr/bin/env python3
"""
AutomateQuery Universal Learning Cycle Runner v1

Runs the complete safe learning cycle:

1. Retest all distinct logged questions against /ask
2. Diagnose FAIL / ZERO_REVIEW / fallback / problem rows
3. Generate verified candidate SQL for fixable diagnosis rows
4. Write one final learning-cycle summary

No production router files are edited.
No SQL is executed except through existing safe SELECT execution.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_RUNTIME_ROOT = Path(os.environ.get("AJSMGPT_RUNTIME_ROOT", "/home/ajsmgpt/AJSMGPT"))
DEFAULT_LOG_FILE = DEFAULT_RUNTIME_ROOT / "logs/user_questions.jsonl"
DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_OUT_DIR = PROJECT_ROOT / "AutomateQuery/reports/learning_cycle"


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def run_cmd(cmd: list[str], env: dict[str, str] | None = None) -> None:
    print("=" * 100)
    print("RUN:", " ".join(cmd))
    print("=" * 100)

    completed = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        text=True,
    )

    if completed.returncode != 0:
        raise SystemExit(f"Command failed with exit code {completed.returncode}: {' '.join(cmd)}")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def safe_read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return load_json(path)


def build_summary_md(summary: dict[str, Any]) -> str:
    lines = []

    lines.append("# AutomateQuery Learning Cycle Summary")
    lines.append("")
    lines.append(f"- Generated: `{summary['generated']}`")
    lines.append(f"- Log file: `{summary['inputs']['log_file']}`")
    lines.append(f"- API URL: `{summary['inputs']['api_url']}`")
    lines.append(f"- Runtime root: `{summary['inputs']['runtime_root']}`")
    lines.append("")

    lines.append("## Retest Summary")
    lines.append("")
    for key, value in summary["retest_summary"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")

    lines.append("## Diagnosis Summary")
    lines.append("")
    for key, value in summary["diagnosis_summary"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")

    lines.append("## Candidate Summary")
    lines.append("")
    for key, value in summary["candidate_summary"].items():
        lines.append(f"- {key}: `{value}`")
    lines.append("")

    lines.append("## Final Status")
    lines.append("")
    lines.append(f"- Safe handled questions: `{summary['final_status']['safe_handled_questions']}`")
    lines.append(f"- Questions needing router/code work: `{summary['final_status']['needs_work']}`")
    lines.append(f"- Expected zero/no-data cases: `{summary['final_status']['expected_zero']}`")
    lines.append(f"- Verified candidate fixes: `{summary['final_status']['verified_candidates']}`")
    lines.append("")

    lines.append("## Output Files")
    lines.append("")
    for name, path in summary["outputs"].items():
        lines.append(f"- {name}: `{path}`")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE))
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--timeout", default="25")
    parser.add_argument("--skip-retest", action="store_true", help="Use existing latest retest report")
    parser.add_argument("--skip-diagnosis", action="store_true", help="Use existing latest diagnosis report")
    parser.add_argument("--skip-candidates", action="store_true", help="Use existing latest candidate report")
    parser.add_argument("--no-probes", action="store_true", help="Disable Oracle probes in diagnosis")
    parser.add_argument("--no-verify", action="store_true", help="Disable candidate SQL verification")

    args = parser.parse_args()

    runtime_root = Path(args.runtime_root).resolve()
    log_file = Path(args.log_file).resolve()
    out_dir = DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["AJSMGPT_RUNTIME_ROOT"] = str(runtime_root)

    python = str(runtime_root / "venv/bin/python3")

    retest_json = PROJECT_ROOT / "AutomateQuery/reports/retest_from_logs/latest_all_distinct_retest.json"
    diagnosis_json = PROJECT_ROOT / "AutomateQuery/reports/question_diagnosis/latest_diagnosis.json"
    candidates_json = PROJECT_ROOT / "AutomateQuery/reports/candidate_queries/latest_candidate_queries.json"
    verified_candidates_json = PROJECT_ROOT / "AutomateQuery/reports/candidate_queries/verified_candidate_queries.json"

    if not args.skip_retest:
        raw_retest_results = PROJECT_ROOT / "AutomateQuery/reports/retest_from_logs/all_distinct_retest_results.jsonl"
        if raw_retest_results.exists():
            raw_retest_results.unlink()

        run_cmd(
            [
                python,
                "AutomateQuery/scripts/retest_all_distinct_questions.py",
                "--log-file",
                str(log_file),
                "--api-url",
                args.api_url,
                "--timeout",
                str(args.timeout),
                "--resume",
            ],
            env=env,
        )

    if not args.skip_diagnosis:
        diagnosis_cmd = [
            python,
            "AutomateQuery/core/diagnose_questions.py",
            "--input",
            str(retest_json),
        ]

        if args.no_probes:
            diagnosis_cmd.append("--no-probes")

        run_cmd(diagnosis_cmd, env=env)

    if not args.skip_candidates:
        candidate_cmd = [
            python,
            "AutomateQuery/core/generate_candidate_queries.py",
            "--input",
            str(diagnosis_json),
        ]

        if args.no_verify:
            candidate_cmd.append("--no-verify")

        run_cmd(candidate_cmd, env=env)

    retest_rows = safe_read_json(retest_json, [])
    diagnosis_rows = safe_read_json(diagnosis_json, [])
    candidate_rows = safe_read_json(candidates_json, [])
    verified_candidate_rows = safe_read_json(verified_candidates_json, [])

    retest_counts = Counter(row.get("verdict", "UNKNOWN") for row in retest_rows)
    diagnosis_counts = Counter(row.get("classification", "UNKNOWN") for row in diagnosis_rows)

    expected_zero = diagnosis_counts.get("EXPECTED_ZERO", 0)
    pass_count = retest_counts.get("PASS", 0)
    fail_count = retest_counts.get("FAIL", 0)
    zero_review_count = retest_counts.get("ZERO_REVIEW", 0)

    candidate_verified_count = len([r for r in candidate_rows if r.get("verified_with_rows")])

    needs_work = (
        fail_count
        + len([d for d in diagnosis_rows if d.get("classification") not in {"PASS", "EXPECTED_ZERO"}])
        - candidate_verified_count
    )
    needs_work = max(needs_work, 0)

    summary = {
        "generated": now_iso(),
        "inputs": {
            "log_file": str(log_file),
            "api_url": args.api_url,
            "runtime_root": str(runtime_root),
        },
        "retest_summary": dict(sorted(retest_counts.items())),
        "diagnosis_summary": dict(sorted(diagnosis_counts.items())),
        "candidate_summary": {
            "total_candidates": len(candidate_rows),
            "verified_with_rows": candidate_verified_count,
            "pending_or_failed": len(candidate_rows) - candidate_verified_count,
        },
        "final_status": {
            "safe_handled_questions": pass_count + expected_zero,
            "pass": pass_count,
            "zero_review": zero_review_count,
            "expected_zero": expected_zero,
            "fail": fail_count,
            "verified_candidates": candidate_verified_count,
            "needs_work": needs_work,
        },
        "outputs": {
            "retest_json": str(retest_json),
            "diagnosis_json": str(diagnosis_json),
            "candidate_json": str(candidates_json),
            "verified_candidate_json": str(verified_candidates_json),
            "summary_json": str(out_dir / "latest_learning_cycle_summary.json"),
            "summary_md": str(out_dir / "latest_learning_cycle_summary.md"),
        },
    }

    write_json(out_dir / "latest_learning_cycle_summary.json", summary)
    (out_dir / "latest_learning_cycle_summary.md").write_text(build_summary_md(summary), encoding="utf-8")

    print("")
    print("=" * 100)
    print("AUTOMATEQUERY LEARNING CYCLE COMPLETE")
    print("=" * 100)
    print(f"PASS: {pass_count}")
    print(f"EXPECTED_ZERO: {expected_zero}")
    print(f"FAIL: {fail_count}")
    print(f"VERIFIED_CANDIDATES: {candidate_verified_count}")
    print(f"NEEDS_WORK: {needs_work}")
    print("")
    print(f"SUMMARY: {out_dir / 'latest_learning_cycle_summary.md'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
