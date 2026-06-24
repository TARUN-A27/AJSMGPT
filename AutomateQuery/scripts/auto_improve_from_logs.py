from __future__ import annotations

import subprocess
import sys
from pathlib import Path


AUTOMATE_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = AUTOMATE_DIR.parent


def run_script(script_name: str) -> None:
    script_path = AUTOMATE_DIR / "scripts" / script_name

    print("=" * 100)
    print(f"Running: {script_path}")
    print("=" * 100)

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(ROOT_DIR),
        text=True,
    )

    if result.returncode != 0:
        raise SystemExit(f"Script failed: {script_name}")


def main() -> None:
    run_script("analyze_question_logs.py")
    run_script("generate_eval_from_logs.py")
    run_script("generate_question_bank.py")
    run_script("create_review_eval_file.py")

    print("=" * 100)
    print("AutomateQuery completed safely.")
    print("No Oracle SQL was executed.")
    print("No production router code was modified.")
    print("Reports refreshed for Dashboard, Question Bank, Router Candidates, and Eval Approval.")
    print("=" * 100)


if __name__ == "__main__":
    main()
