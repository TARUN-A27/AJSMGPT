from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]

APPROVED_PATCHES_PATH = PROJECT_ROOT / "AutomateQuery" / "reports" / "approved_router_patches.json"
REPORTS_DIR = PROJECT_ROOT / "AutomateQuery" / "reports"
BACKUP_DIR = REPORTS_DIR / "backups"
APPLY_REPORT_PATH = REPORTS_DIR / "apply_approved_router_patches_report.json"

ROUTER_PATH = PROJECT_ROOT / "app" / "purchase_analytics_router.py"
TEST_PATH = PROJECT_ROOT / "scripts" / "test_hod_purchase_questions.py"

SUPPORTED_PATCH_IDS = {
    "patch_purchase_last_supply_by_material",
    "patch_purchase_last_n_purchases_by_material",
}


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def write_report(report: dict[str, Any]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    APPLY_REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def run_command(cmd: list[str], cwd: Path) -> tuple[bool, str]:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    return completed.returncode == 0, completed.stdout


def create_backup(path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    backup_path = BACKUP_DIR / f"{path.name}.bak_{now_stamp()}"
    shutil.copy2(path, backup_path)

    return backup_path


def restore_backup(backup_path: Path, target_path: Path) -> None:
    shutil.copy2(backup_path, target_path)


def ensure_helpers(text: str) -> tuple[str, bool]:
    if "def _last_n_limit(" in text:
        return text, False

    marker = "def _item_filter("
    if marker not in text:
        raise RuntimeError("Could not find marker: def _item_filter(")

    helper_code = r'''

def _last_n_limit(q: str, default: int = 1) -> int:
    m = re.search(r"\blast\s+(\d+)\b", q)
    if not m:
        return default

    try:
        value = int(m.group(1))
    except ValueError:
        return default

    return max(1, min(value, 50))


def _clean_material_name(value: str | None) -> str | None:
    if not value:
        return None

    item = value.strip(" .,-_/\\")
    item = re.sub(r"\s+", " ", item).strip()

    # Remove common trailing words if captured accidentally.
    item = re.sub(
        r"\b(qty|quantity|purchase|purchases|rate|price|details)\b.*$",
        "",
        item,
        flags=re.I,
    ).strip()

    if not item:
        return None

    return item.upper()


def _material_from_item_phrase(q: str) -> str | None:
    patterns = [
        r"\bof\s+the\s+item\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\bitem\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            return _clean_material_name(m.group(1))

    return None


def _material_from_last_supply(q: str) -> str | None:
    patterns = [
        r"\blast\s+supply\s+(?:of|for)\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\blast\s+purchase\s+(?:of|for)\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            return _clean_material_name(m.group(1))

    return _material_from_item_phrase(q)


def _material_from_last_purchase_qty(q: str) -> str | None:
    item = _material_from_item_phrase(q)
    if item:
        return item

    patterns = [
        r"\blast\s+(?:\d+\s+)?purchase\s+(?:qty|quantity)\s+(?:purchase\s+)?(?:of|for)\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            return _clean_material_name(m.group(1))

    return None
'''

    return text.replace(marker, helper_code + "\n" + marker, 1), True


def insert_rule_after_clean_question(text: str, rule_code: str) -> str:
    func_marker = "def match_purchase_analytics_template(question: str) -> dict[str, Any] | None:"
    func_pos = text.find(func_marker)

    if func_pos == -1:
        raise RuntimeError("Could not find match_purchase_analytics_template function.")

    q_marker = "q = _clean(question)"
    q_pos = text.find(q_marker, func_pos)

    if q_pos == -1:
        raise RuntimeError("Could not find q = _clean(question) inside match function.")

    insert_pos = text.find("\n", q_pos)

    if insert_pos == -1:
        raise RuntimeError("Could not find insert position after q = _clean(question).")

    return text[: insert_pos + 1] + rule_code + text[insert_pos + 1 :]


def ensure_last_n_purchase_rule(text: str) -> tuple[str, bool]:
    if "purchase_last_n_purchases_by_material" in text:
        return text, False

    rule_code = r'''
    # Approved patch: Last N purchase quantity/details by material
    # Example: "Last 3 purchase qty purchase of the item Keyboard"
    material_for_last_n = _material_from_last_purchase_qty(q)
    if (
        "last" in q
        and "purchase" in q
        and ("qty" in q or "quantity" in q)
        and material_for_last_n
    ):
        sql = _po_base_select(
            where=_item_filter("INV", material_for_last_n),
            order_by="PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
            limit=_last_n_limit(q, default=1),
        )
        return _result("purchase_last_n_purchases_by_material", sql)

'''

    return insert_rule_after_clean_question(text, rule_code), True


def ensure_last_supply_material_rule(text: str) -> tuple[str, bool]:
    if "purchase_last_supply_by_material" in text:
        return text, False

    rule_code = r'''
    # Approved patch: Last supply by material
    # Example: "last supply of mouse"
    material_for_last_supply = _material_from_last_supply(q)
    if (
        "last" in q
        and "supply" in q
        and material_for_last_supply
    ):
        sql = _po_base_select(
            where=_item_filter("INV", material_for_last_supply),
            order_by="PO.ORDERDATE DESC NULLS LAST, PO.ORDERNO DESC",
            limit=1,
        )
        return _result("purchase_last_supply_by_material", sql)

'''

    return insert_rule_after_clean_question(text, rule_code), True


def apply_supported_patches(approved_patches: list[dict[str, Any]]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "success": False,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "approved_patch_count": len(approved_patches),
        "supported_patch_ids": sorted(SUPPORTED_PATCH_IDS),
        "applied_patch_ids": [],
        "skipped_patch_ids": [],
        "changed": False,
        "backup_path": None,
        "compile_output": "",
        "test_output": "",
        "error": None,
    }

    if not ROUTER_PATH.exists():
        raise RuntimeError(f"Router file not found: {ROUTER_PATH}")

    supported_patches: list[dict[str, Any]] = []

    for patch in approved_patches:
        patch_id = str(patch.get("patch_id") or "")
        target_router_file = str(patch.get("target_router_file") or "")

        if patch_id not in SUPPORTED_PATCH_IDS:
            report["skipped_patch_ids"].append(
                {
                    "patch_id": patch_id,
                    "reason": "unsupported patch_id",
                }
            )
            continue

        if target_router_file != "app/purchase_analytics_router.py":
            report["skipped_patch_ids"].append(
                {
                    "patch_id": patch_id,
                    "reason": f"unexpected target router: {target_router_file}",
                }
            )
            continue

        supported_patches.append(patch)

    print(f"Read approved patches: {len(approved_patches)}")
    print(f"Supported patches found: {len(supported_patches)}")

    if not supported_patches:
        report["success"] = True
        report["error"] = "No supported approved patches to apply."
        return report

    backup_path = create_backup(ROUTER_PATH)
    report["backup_path"] = str(backup_path.relative_to(PROJECT_ROOT))
    print(f"Backup created: {backup_path}")

    original_text = ROUTER_PATH.read_text(encoding="utf-8")
    text = original_text

    helpers_changed = False
    text, helpers_changed = ensure_helpers(text)

    applied_ids: list[str] = []

    approved_ids = {str(patch.get("patch_id") or "") for patch in supported_patches}

    if "patch_purchase_last_n_purchases_by_material" in approved_ids:
        text, changed = ensure_last_n_purchase_rule(text)
        if changed:
            applied_ids.append("patch_purchase_last_n_purchases_by_material")

    if "patch_purchase_last_supply_by_material" in approved_ids:
        text, changed = ensure_last_supply_material_rule(text)
        if changed:
            applied_ids.append("patch_purchase_last_supply_by_material")

    changed_anything = helpers_changed or text != original_text
    report["changed"] = changed_anything
    report["applied_patch_ids"] = applied_ids

    if not changed_anything:
        print("Router already contains supported approved patch code. No code change needed.")
    else:
        ROUTER_PATH.write_text(text, encoding="utf-8")
        print(f"Applied patch code: {applied_ids or ['helpers only']}")

    compile_ok, compile_output = run_command(
        [sys.executable, "-m", "py_compile", str(ROUTER_PATH)],
        PROJECT_ROOT,
    )
    report["compile_output"] = compile_output

    if not compile_ok:
        restore_backup(backup_path, ROUTER_PATH)
        report["error"] = "Compile failed. Rolled back router file."
        return report

    print("Compile passed.")

    if TEST_PATH.exists():
        test_ok, test_output = run_command(
            [sys.executable, str(TEST_PATH)],
            PROJECT_ROOT,
        )
    else:
        test_ok, test_output = run_command(
            [
                sys.executable,
                "-c",
                (
                    "from app.query_engine import answer_question\n"
                    "questions = [\n"
                    "    'last supply of mouse',\n"
                    "    'Last 3 purchase qty purchase of the item Keyboard',\n"
                    "    'Last purchase qty purchase of the item Keyboard',\n"
                    "]\n"
                    "for q in questions:\n"
                    "    res = answer_question(q)\n"
                    "    print(q, res.get('success'), res.get('source'), res.get('intent'), res.get('row_count'))\n"
                    "    assert res.get('success') is True\n"
                    "    assert res.get('source') == 'purchase_analytics_router'\n"
                ),
            ],
            PROJECT_ROOT,
        )

    report["test_output"] = test_output

    if not test_ok:
        restore_backup(backup_path, ROUTER_PATH)
        report["error"] = "Tests failed. Rolled back router file."
        return report

    print("Tests passed.")

    report["success"] = True
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")

    return report


def main() -> int:
    approved_patches = read_json(APPROVED_PATCHES_PATH, [])

    if not isinstance(approved_patches, list):
        print("approved_router_patches.json must contain a list.")
        return 1

    try:
        report = apply_supported_patches(approved_patches)
    except Exception as exc:
        report = {
            "success": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        write_report(report)
        print(f"FAILED: {exc}")
        return 1

    write_report(report)

    if not report.get("success"):
        print("Apply approved router patches failed.")
        print(report.get("error"))
        return 1

    print("Apply approved router patches completed successfully.")
    print(f"Report: {APPLY_REPORT_PATH.relative_to(PROJECT_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
