from __future__ import annotations

import json
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


MASTER_ITEM_FILTER = r"""def _item_filter(alias: str, material: str | None) -> str:
    material = _clean_material_name(material)

    if not material:
        return ""

    column = f"UPPER({alias}.ITEM_NAME)"
    phrase = material.upper().replace("'", "''")

    raw_tokens = [
        token.replace("'", "''").upper()
        for token in re.split(r"[^A-Z0-9]+", phrase)
        if len(token.strip()) >= 2
    ]

    if not raw_tokens:
        return ""

    tokens: list[str] = []
    i = 0
    while i < len(raw_tokens):
        if raw_tokens[i] == "BAR" and i + 1 < len(raw_tokens) and raw_tokens[i + 1] == "CODE":
            tokens.append("BARCODE")
            i += 2
        else:
            tokens.append(raw_tokens[i])
            i += 1

    def token_condition(token: str) -> str:
        if token == "BARCODE":
            return (
                f"({column} LIKE '%BARCODE%' "
                f"OR {column} LIKE '%BAR CODE%' "
                f"OR ({column} LIKE '%BAR%' AND {column} LIKE '%CODE%'))"
            )

        if token in {"LABEL", "LABELS", "LABLE", "LABLES"}:
            return f"({column} LIKE '%LABEL%' OR {column} LIKE '%LABLE%')"

        return f"{column} LIKE '%{token}%'"

    exact_condition = f"{column} LIKE '%{phrase}%'"
    token_conditions = " AND ".join(token_condition(token) for token in tokens)

    return f\"\"\"  AND (
    {exact_condition}
    OR ({token_conditions})
  )\"\"\"
"""


SUPPORTED_PATCH_IDS = {
    "patch_purchase_last_supply_by_material",
    "patch_purchase_last_n_purchases_by_material",
    "patch_purchase_latest_order_by_material",
    "patch_purchase_suppliers_by_material",
}


SUPPLIER_HELPERS = r'''
def _supplier_material_clean(value: str | None) -> str | None:
    if not value:
        return None

    item = value.strip(" .,-_/\\")
    item = item.strip('"').strip("'")
    item = re.sub(r"\s+", " ", item).strip()

    item = re.sub(
        r"\b(supplier|suppliers|item|material|purchase|order|details|detail|qty|quantity)\b.*$",
        "",
        item,
        flags=re.I,
    ).strip()

    if not item:
        return None

    return item.upper()


def _material_from_supplier_question(q: str) -> str | None:
    # Example: WHO are the suppliers for the item "barcode label"
    quoted = re.search(r'"([^"]+)"', q)
    if quoted:
        return _supplier_material_clean(quoted.group(1))

    patterns = [
        r"\bsuppliers?\s+(?:for|of)\s+(?:the\s+)?(?:item|material)\s+([a-zA-Z0-9&.\-_/ ]+?)(?:\s+in\s+\d{4}|$)",
        r"\b(?:item|material)\s+([a-zA-Z0-9&.\-_/ ]+?)\s+suppliers?\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, q, flags=re.I)
        if m:
            return _supplier_material_clean(m.group(1))

    return None


def _suppliers_by_material_select(material: str) -> str:
    where = _item_filter("INV", material)

    return f"""SELECT DISTINCT
    PO.SUP_CODE,
    P.PARTYNAME AS SUPPLIER_NAME,
    INV.ITEM_NAME
FROM INVENTORY.PURCHASEORDER PO
JOIN INVENTORY.INVITEMS INV ON PO.ITEM_CODE = INV.ITEM_CODE
LEFT JOIN SCM.PARTYMASTER P ON PO.SUP_CODE = P.PARTYCODE
WHERE 1 = 1
 {where}
  AND P.PARTYNAME IS NOT NULL
ORDER BY P.PARTYNAME"""
'''


SUPPLIER_RULE = r'''
    # Approved patch: Suppliers by material/item
    material_for_suppliers = _material_from_supplier_question(q)
    if (
        ("supplier" in q or "suppliers" in q)
        and ("item" in q or "material" in q)
        and material_for_suppliers
        and "purchase order" not in q
    ):
        sql = _suppliers_by_material_select(material_for_suppliers)
        return _result("purchase_suppliers_by_material", sql)

'''


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


def run_command(cmd: list[str]) -> tuple[bool, str]:
    completed = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
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


def insert_before_item_filter(text: str, code: str) -> str:
    marker = "def _item_filter("
    if marker not in text:
        raise RuntimeError("Could not find def _item_filter marker in purchase_analytics_router.py")
    return text.replace(marker, code + "\n\n" + marker, 1)


def insert_after_clean_question(text: str, code: str) -> str:
    func_marker = "def match_purchase_analytics_template(question: str) -> dict[str, Any] | None:"
    func_pos = text.find(func_marker)
    if func_pos == -1:
        raise RuntimeError("Could not find match_purchase_analytics_template function.")

    q_marker = "q = _clean(question)"
    q_pos = text.find(q_marker, func_pos)
    if q_pos == -1:
        raise RuntimeError("Could not find q = _clean(question) inside match_purchase_analytics_template.")

    insert_pos = text.find("\n", q_pos)
    return text[:insert_pos + 1] + code + text[insert_pos + 1:]



def ensure_master_item_filter(text: str) -> tuple[str, bool]:
    import re

    pattern = r'(?ms)^def _item_filter\(.*?\n(?=^def |\Z)'
    matches = list(re.finditer(pattern, text))

    if not matches:
        return text, False

    current = matches[0].group(0)

    if "BAR CODE" in current and "LABLE" in current:
        return text, False

    m = matches[0]
    text = text[:m.start()] + MASTER_ITEM_FILTER + "\n\n" + text[m.end():]
    return text, True

def ensure_supplier_patch(text: str) -> tuple[str, bool]:
    original = text

    if "def _material_from_supplier_question(" not in text:
        text = insert_before_item_filter(text, SUPPLIER_HELPERS)

    if "purchase_suppliers_by_material" not in text:
        text = insert_after_clean_question(text, SUPPLIER_RULE)

    return text, text != original


def supplier_router_unit_test() -> tuple[bool, str]:
    code = r'''
from app.purchase_analytics_router import match_purchase_analytics_template

q = 'WHO are the suppliers for the item "barcode label"'
res = match_purchase_analytics_template(q)

if not res:
    raise SystemExit("Router returned None")

sql = res.get("sql") or ""

print("SOURCE_ROUTER_RESULT:", res)
print("SQL:", sql)

if res.get("intent") != "purchase_suppliers_by_material":
    raise SystemExit(f"Wrong intent: {res.get('intent')}")

required = [
    "INVENTORY.PURCHASEORDER",
    "INVENTORY.INVITEMS",
    "SCM.PARTYMASTER",
    "UPPER(INV.ITEM_NAME) LIKE '%BARCODE LABEL%'",
    "SELECT DISTINCT",
]

for item in required:
    if item not in sql:
        raise SystemExit(f"SQL missing: {item}")

if "INVENTORY.SUPPLIER" in sql:
    raise SystemExit("Wrong supplier table used: INVENTORY.SUPPLIER")

print("Supplier by material router test passed.")
'''
    return run_command([sys.executable, "-c", code])


def apply_supported_patches(approved_patches: list[dict[str, Any]]) -> dict[str, Any]:
    approved_patch_ids = {
        str(p.get("patch_id") or "")
        for p in approved_patches
        if isinstance(p, dict)
    }

    supported_approved = sorted(approved_patch_ids & SUPPORTED_PATCH_IDS)
    skipped = sorted(approved_patch_ids - SUPPORTED_PATCH_IDS)

    report: dict[str, Any] = {
        "success": False,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "approved_patch_count": len(approved_patches),
        "supported_patch_ids": sorted(SUPPORTED_PATCH_IDS),
        "supported_approved_patch_ids": supported_approved,
        "applied_patch_ids": [],
        "skipped_patch_ids": [
            {"patch_id": patch_id, "reason": "unsupported patch_id"}
            for patch_id in skipped
            if patch_id
        ],
        "changed": False,
        "backup_path": None,
        "compile_output": "",
        "test_output": "",
        "error": None,
    }

    if not supported_approved:
        report["success"] = True
        report["error"] = "No supported approved patches to apply."
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        return report

    backup_path = create_backup(ROUTER_PATH)
    report["backup_path"] = str(backup_path.relative_to(PROJECT_ROOT))

    original = ROUTER_PATH.read_text(encoding="utf-8")
    text = original

    text, master_filter_changed = ensure_master_item_filter(text)

    applied: list[str] = []

    if "patch_purchase_suppliers_by_material" in approved_patch_ids:
        text, changed = ensure_supplier_patch(text)
        if changed:
            applied.append("patch_purchase_suppliers_by_material")

    changed_anything = text != original

    if changed_anything:
        ROUTER_PATH.write_text(text, encoding="utf-8")

    report["changed"] = changed_anything
    report["applied_patch_ids"] = applied

    compile_ok, compile_output = run_command([sys.executable, "-m", "py_compile", str(ROUTER_PATH)])
    report["compile_output"] = compile_output

    if not compile_ok:
        restore_backup(backup_path, ROUTER_PATH)
        report["error"] = "Compile failed. Rolled back router file."
        report["finished_at"] = datetime.now().isoformat(timespec="seconds")
        return report

    test_outputs: list[str] = []

    hod_test = PROJECT_ROOT / "scripts" / "test_hod_purchase_questions.py"
    if hod_test.exists():
        ok, output = run_command([sys.executable, str(hod_test)])
        test_outputs.append("===== test_hod_purchase_questions.py =====\n" + output)
        if not ok:
            restore_backup(backup_path, ROUTER_PATH)
            report["test_output"] = "\n".join(test_outputs)
            report["error"] = "HOD purchase test failed. Rolled back router file."
            report["finished_at"] = datetime.now().isoformat(timespec="seconds")
            return report

    if "patch_purchase_suppliers_by_material" in approved_patch_ids:
        ok, output = supplier_router_unit_test()
        test_outputs.append("===== supplier_router_unit_test =====\n" + output)
        if not ok:
            restore_backup(backup_path, ROUTER_PATH)
            report["test_output"] = "\n".join(test_outputs)
            report["error"] = "Supplier router unit test failed. Rolled back router file."
            report["finished_at"] = datetime.now().isoformat(timespec="seconds")
            return report

    report["test_output"] = "\n".join(test_outputs)
    report["success"] = True
    report["finished_at"] = datetime.now().isoformat(timespec="seconds")
    return report


def main() -> int:
    approved_patches = read_json(APPROVED_PATCHES_PATH, [])

    if not isinstance(approved_patches, list):
        print("approved_router_patches.json must contain a list.")
        return 1

    report = apply_supported_patches(approved_patches)
    write_report(report)

    if not report.get("success"):
        print("Apply approved router patches failed.")
        print(report.get("error"))
        return 1

    print("Apply approved router patches completed successfully.")
    print(f"Supported approved patches: {report.get('supported_approved_patch_ids')}")
    print(f"Applied patch IDs: {report.get('applied_patch_ids')}")
    print(f"Changed: {report.get('changed')}")
    print(f"Report: {APPLY_REPORT_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
