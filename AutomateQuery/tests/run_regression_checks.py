#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(os.environ.get("AJSMGPT_PROJECT_ROOT") or Path.cwd()).resolve()
sys.path.insert(0, str(PROJECT_ROOT))

REPORT_DIR = PROJECT_ROOT / "AutomateQuery" / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

BANNED_SQL_WORDS = [
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "TRUNCATE",
    "MERGE",
    "CREATE",
    "REPLACE",
    "EXEC",
    "EXECUTE",
    "BEGIN",
    "DECLARE",
]


def norm(value: Any) -> str:
    return str(value or "").upper()


def sql_norm(sql: Any) -> str:
    return re.sub(r"\s+", " ", str(sql or "").upper()).strip()


def is_safe_select(sql: Any) -> bool:
    s = sql_norm(sql)
    if not s:
        return True
    if not s.startswith("SELECT"):
        return False
    return not any(re.search(rf"\b{word}\b", s) for word in BANNED_SQL_WORDS)


def plan_template(plan: dict[str, Any]) -> dict[str, Any]:
    return plan.get("template") or {}


def plan_fallback(plan: dict[str, Any]) -> dict[str, Any]:
    return plan.get("fallback") or {}


def plan_decision(plan: dict[str, Any]) -> dict[str, Any]:
    return plan.get("decision") or {}


def plan_intent(plan: dict[str, Any]) -> str | None:
    template = plan_template(plan)
    fallback = plan_fallback(plan)
    return template.get("intent") or fallback.get("intent_hint")


def plan_sql(plan: dict[str, Any]) -> str:
    return str(plan_template(plan).get("sql_preview") or "")


def top_result(resolved_values: dict[str, Any] | None) -> dict[str, Any]:
    resolved_values = resolved_values or {}
    rows = resolved_values.get("top_results") or resolved_values.get("results") or []
    return rows[0] if rows else {}


class CheckRunner:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    def add(self, group: str, question: str, check: str, ok: bool, details: str = "") -> None:
        self.results.append(
            {
                "group": group,
                "question": question,
                "check": check,
                "ok": bool(ok),
                "details": details,
            }
        )

    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r["ok"])

    def print_summary(self) -> None:
        total = len(self.results)
        failed = self.fail_count()
        passed = total - failed

        print("=" * 120)
        print("REGRESSION SUMMARY")
        print("=" * 120)
        print("PROJECT_ROOT:", PROJECT_ROOT)
        print("TOTAL:", total)
        print("PASSED:", passed)
        print("FAILED:", failed)

        if failed:
            print()
            print("FAILED CHECKS")
            print("=" * 120)
            for r in self.results:
                if not r["ok"]:
                    print("-" * 120)
                    print("GROUP:", r["group"])
                    print("QUESTION:", r["question"])
                    print("CHECK:", r["check"])
                    print("DETAILS:", r["details"])

    def write_report(self, mode: str) -> Path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = REPORT_DIR / f"regression_checks_{mode}_{ts}.json"
        path.write_text(
            json.dumps(
                {
                    "mode": mode,
                    "project_root": str(PROJECT_ROOT),
                    "total": len(self.results),
                    "passed": len(self.results) - self.fail_count(),
                    "failed": self.fail_count(),
                    "results": self.results,
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )
        return path


def check_eq(r: CheckRunner, group: str, q: str, name: str, actual: Any, expected: Any) -> None:
    r.add(group, q, name, actual == expected, f"actual={actual!r}, expected={expected!r}")


def check_contains(r: CheckRunner, group: str, q: str, name: str, text: Any, needle: str) -> None:
    r.add(group, q, name, norm(needle) in norm(text), f"needle={needle!r}, text={str(text)[:500]!r}")


def check_not_contains(r: CheckRunner, group: str, q: str, name: str, text: Any, needle: str) -> None:
    r.add(group, q, name, norm(needle) not in norm(text), f"needle={needle!r}, text={str(text)[:500]!r}")


def run_plan_checks(r: CheckRunner) -> None:
    from app.query_planner import plan_query

    group = "plan"

    cases = [
        {
            "question": "stock for keyboard",
            "module": "inventory",
            "intent": "stock_by_item_name",
            "sql_contains": ["LIKE '%KEYBOARD%'"],
            "sql_not_contains": ["KEYBOARD W MOUSE"],
        },
        {
            "question": "pending MRS for keyboard",
            "module": "inventory_mrs",
            "intent": "pending_mrs_by_item_name",
            "sql_contains": ["LIKE '%KEYBOARD%'"],
            "sql_not_contains": ["KEYBOARD W MOUSE"],
        },
        {
            "question": "barcode chromo label last purchase",
            "module": "purchase",
            "intent": "last_purchase_date_by_item_name",
            "sql_contains": ["BARCODE CHROMO LABLES 40 X 25MM"],
        },
        {
            "question": "last supplier for mouse",
            "module": "purchase",
            "intent": "purchase_last_supplier_by_material",
            "sql_contains": ["LIKE '%MOUSE%'"],
            "sql_not_contains": ["MOUSE USB", "TOUCH PAD MOUSE"],
            "top_type": "material",
            "top_location_not_contains": ["PARTYMASTER", "ADDRESS"],
        },
        {
            "question": "mouse last purchased supplier name",
            "module": "purchase",
            "intent": "purchase_last_supplier_by_material",
            "sql_contains": ["LIKE '%MOUSE%'"],
            "sql_not_contains": ["MOUSE USB", "TOUCH PAD MOUSE"],
            "top_type": "material",
            "top_location_not_contains": ["PARTYMASTER", "ADDRESS"],
        },
        {
            "question": "MRS for unit B-Unit",
            "module": "inventory_mrs",
            "intent": "mrs_by_unit",
            "sql_contains": ["LIKE '%B-UNIT%'"],
            "sql_not_contains": ["B UNIT DRAWING"],
        },
        {
            "question": "cash bank entries for party ODUT001",
            "module": "admin_cashbank",
            "intent": "cashbank_voucher_details",
        },
        {
            "question": "camera details",
            "module": "admin_camera",
            "top_location_not_contains": ["PARTYMASTER"],
            "top_type_not": "supplier_or_party",
        },
        {
            "question": "show camera ip details",
            "module": "admin_camera",
            "top_location_contains": ["ADMIN.CAMERAIP"],
        },
        {
            "question": "latest GRN for supplier 800967 in 2025",
            "module": "purchase",
            "intent_not": "purchase_last_supply_by_supplier",
        },
        {
            "question": "show goods receipt for supplier 800967",
            "module": "purchase",
            "intent_not": "purchase_last_supply_by_supplier",
        },
        {
            "question": "last purchase from THE GALAXY",
            "module": "purchase",
            "intent": "purchase_last_supply_by_supplier",
            "sql_contains": ["THE GALAXY"],
        },
    ]

    for case in cases:
        q = case["question"]
        plan = plan_query(q)
        template = plan_template(plan)
        sql = plan_sql(plan)
        intent = plan_intent(plan)
        top = top_result(plan.get("resolved_values"))

        check_eq(r, group, q, "success", plan.get("success"), True)

        if "module" in case:
            check_eq(r, group, q, "module", plan.get("module"), case["module"])

        if "intent" in case:
            check_eq(r, group, q, "intent", intent, case["intent"])

        if "intent_not" in case:
            r.add(group, q, "intent_not", intent != case["intent_not"], f"actual={intent!r}")

        if "top_type" in case:
            check_eq(r, group, q, "top_type", top.get("entity_type"), case["top_type"])

        if "top_type_not" in case:
            r.add(
                group,
                q,
                "top_type_not",
                top.get("entity_type") != case["top_type_not"],
                f"actual={top.get('entity_type')!r}",
            )

        for needle in case.get("top_location_contains", []):
            check_contains(r, group, q, "top_location_contains", top.get("location"), needle)

        for needle in case.get("top_location_not_contains", []):
            check_not_contains(r, group, q, "top_location_not_contains", top.get("location"), needle)

        for needle in case.get("sql_contains", []):
            check_contains(r, group, q, "sql_contains", sql, needle)

        for needle in case.get("sql_not_contains", []):
            check_not_contains(r, group, q, "sql_not_contains", sql, needle)

        r.add(group, q, "safe_select_sql", is_safe_select(sql), sql[:500])


def run_ask_dbfree_checks(r: CheckRunner) -> None:
    import app.query_engine as qe

    group = "ask_dbfree"

    def fake_run_safe_select(sql: str) -> dict[str, Any]:
        return {
            "columns": ["MOCK"],
            "rows": [],
            "row_count": 0,
        }

    qe.run_safe_select = fake_run_safe_select

    cases = [
        {
            "question": "last supplier for mouse",
            "source": "purchase_analytics_router",
            "intent": "purchase_last_supplier_by_material",
            "sql_contains": ["LIKE '%MOUSE%'"],
            "sql_not_contains": ["MOUSE USB", "TOUCH PAD MOUSE"],
        },
        {
            "question": "mouse last purchased supplier name",
            "source": "purchase_analytics_router",
            "intent": "purchase_last_supplier_by_material",
            "sql_contains": ["LIKE '%MOUSE%'"],
            "sql_not_contains": ["MOUSE USB", "TOUCH PAD MOUSE"],
        },
        {
            "question": "stock for keyboard",
            "source": "layman_router",
            "intent": "stock_by_item_name",
            "sql_contains": ["LIKE '%KEYBOARD%'"],
            "sql_not_contains": ["KEYBOARD W MOUSE"],
        },
        {
            "question": "pending MRS for keyboard",
            "source": "layman_router",
            "intent": "pending_mrs_by_item_name",
            "sql_contains": ["LIKE '%KEYBOARD%'"],
            "sql_not_contains": ["KEYBOARD W MOUSE"],
        },
        {
            "question": "barcode chromo label last purchase",
            "source": "layman_router",
            "intent": "last_purchase_date_by_item_name",
            "sql_contains": ["BARCODE CHROMO LABLES 40 X 25MM"],
        },
        {
            "question": "latest GRN for supplier 800967 in 2025",
            "source": "layman_router",
            "intent": "grn_by_supplier_code",
            "sql_contains": ["INVENTORY.GRN", "G.SUP_CODE", "20250101", "20251231"],
            "sql_not_contains": ["PURCHASE_LAST_SUPPLY_BY_SUPPLIER"],
        },
        {
            "question": "show goods receipt for supplier 800967",
            "source": "layman_router",
            "intent": "grn_by_supplier_code",
            "sql_contains": ["INVENTORY.GRN", "G.SUP_CODE"],
        },
    ]

    for case in cases:
        q = case["question"]
        res = qe.answer_question(q)
        sql = res.get("sql") or ""

        check_eq(r, group, q, "success", res.get("success"), True)
        check_eq(r, group, q, "source", res.get("source"), case["source"])
        check_eq(r, group, q, "intent", res.get("intent"), case["intent"])

        for needle in case.get("sql_contains", []):
            check_contains(r, group, q, "sql_contains", sql, needle)

        for needle in case.get("sql_not_contains", []):
            check_not_contains(r, group, q, "sql_not_contains", sql, needle)

        r.add(group, q, "safe_select_sql", is_safe_select(sql), sql[:500])


def http_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 180) -> dict[str, Any]:
    data = None
    headers = {}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_api_checks(r: CheckRunner, api_url: str) -> None:
    group = "api_oracle"
    api_url = api_url.rstrip("/")

    health = http_json("GET", f"{api_url}/health/db")
    r.add(group, "/health/db", "oracle_reachable", health.get("oracle_reachable") is True, json.dumps(health))

    cases = [
        {
            "question": "last supplier for mouse",
            "source": "purchase_analytics_router",
            "intent": "purchase_last_supplier_by_material",
            "min_rows": 1,
            "sql_contains": ["LIKE '%MOUSE%'"],
        },
        {
            "question": "mouse last purchased supplier name",
            "source": "purchase_analytics_router",
            "intent": "purchase_last_supplier_by_material",
            "min_rows": 1,
            "sql_contains": ["LIKE '%MOUSE%'"],
        },
        {
            "question": "stock for keyboard",
            "source": "layman_router",
            "intent": "stock_by_item_name",
            "allow_zero": True,
            "sql_contains": ["LIKE '%KEYBOARD%'"],
            "sql_not_contains": ["KEYBOARD W MOUSE"],
        },
        {
            "question": "pending MRS for keyboard",
            "source": "layman_router",
            "intent": "pending_mrs_by_item_name",
            "min_rows": 1,
            "sql_contains": ["LIKE '%KEYBOARD%'"],
            "sql_not_contains": ["KEYBOARD W MOUSE"],
        },
        {
            "question": "barcode chromo label last purchase",
            "source": "layman_router",
            "intent": "last_purchase_date_by_item_name",
            "min_rows": 1,
            "sql_contains": ["BARCODE CHROMO LABLES 40 X 25MM"],
        },
        {
            "question": "latest GRN for supplier 800967 in 2025",
            "source": "layman_router",
            "intent": "grn_by_supplier_code",
            "allow_zero": True,
            "sql_contains": ["INVENTORY.GRN", "20250101", "20251231"],
        },
        {
            "question": "show goods receipt for supplier 800967",
            "source": "layman_router",
            "intent": "grn_by_supplier_code",
            "min_rows": 1,
            "sql_contains": ["INVENTORY.GRN"],
        },
        {
            "question": "camera details",
            "source": "qwen_schema_fallback",
            "min_rows": 1,
            "sql_contains": ["ADMIN.CAMERAIP"],
        },
        {
            "question": "show camera ip details",
            "source": "qwen_schema_fallback",
            "min_rows": 1,
            "sql_contains": ["ADMIN.CAMERAIP"],
        },
    ]

    for case in cases:
        q = case["question"]
        outer = http_json("POST", f"{api_url}/ask", {"question": q})
        res = outer.get("data") or outer
        sql = res.get("sql") or ""
        row_count = res.get("row_count")

        check_eq(r, group, q, "success", res.get("success"), True)

        if "source" in case:
            check_eq(r, group, q, "source", res.get("source"), case["source"])

        if "intent" in case:
            check_eq(r, group, q, "intent", res.get("intent"), case["intent"])

        if case.get("min_rows") is not None:
            r.add(
                group,
                q,
                "min_rows",
                isinstance(row_count, int) and row_count >= case["min_rows"],
                f"row_count={row_count!r}, expected_min={case['min_rows']}",
            )

        if case.get("allow_zero"):
            r.add(
                group,
                q,
                "row_count_present",
                isinstance(row_count, int) and row_count >= 0,
                f"row_count={row_count!r}",
            )

        for needle in case.get("sql_contains", []):
            check_contains(r, group, q, "sql_contains", sql, needle)

        for needle in case.get("sql_not_contains", []):
            check_not_contains(r, group, q, "sql_not_contains", sql, needle)

        r.add(group, q, "safe_select_sql", is_safe_select(sql), sql[:500])


def main() -> int:
    parser = argparse.ArgumentParser(description="AJSMGPT planner/query regression checks.")
    parser.add_argument(
        "--mode",
        choices=["dbfree", "api", "both"],
        default="dbfree",
        help="dbfree = planner + monkeypatched /ask checks. api = live API Oracle SELECT checks.",
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    runner = CheckRunner()

    if args.mode in {"dbfree", "both"}:
        run_plan_checks(runner)
        run_ask_dbfree_checks(runner)

    if args.mode in {"api", "both"}:
        run_api_checks(runner, args.api_url)

    runner.print_summary()
    report_path = runner.write_report(args.mode)

    print()
    print("REPORT:", report_path)

    return 1 if runner.fail_count() else 0


if __name__ == "__main__":
    raise SystemExit(main())
