from __future__ import annotations

import re
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.grounded_sql_validator import validate_grounded_sql
from app.nlp_execution import add_execution_probe_limit
from app.query_plan import (
    Aggregation,
    BusinessSubject,
    DateRange,
    DateRangeKind,
    Dimension,
    EntityReference,
    EntityStatus,
    FilterOperator,
    Measure,
    QueryFilter,
    QueryPlan,
    RequestedOutput,
    SortDirection,
    SortInstruction,
)
from app.schema_grounding import ground_query_plan
from app.v1_capabilities import evaluate_capability

"""
Data-driven V1 acceptance matrix, sourced from AutomateQuery/reports/question_bank.json.

Each case is a real, previously-observed user question (question_bank.json's
`question`/`occurrence_count`), paired with a hand-authored QueryPlan that
represents the question's V1 interpretation and a hand-authored grounded SQL
string representing the SQL shape the offline pipeline is expected to accept.

This file does not extract, ground, or generate anything from the LLM stages
(Qwen extraction / Qwen SQL generation are both offline in this test); it
exercises the deterministic, already-existing stages directly:
    QueryPlan (given) -> evaluate_capability -> ground_query_plan -> validate_grounded_sql
"""


@dataclass
class AcceptanceCase:
    question: str
    occurrence_count: int
    category: str
    expected_family: str
    plan: QueryPlan
    expected_tables: set[str]
    sql: str
    expect_group_by: bool = False
    expect_order_by: bool = False
    expect_limit: int | None = None


def _purchase_plan(**overrides) -> QueryPlan:
    base = dict(
        domain="purchase",
        operation="detail",
        business_subject=BusinessSubject(concept="purchase"),
        confidence=0.9,
    )
    base.update(overrides)
    return QueryPlan(**base)


CASES: list[AcceptanceCase] = [
    AcceptanceCase(
        question="Last purchase qty purchase of the item Keyboard",
        occurrence_count=24,
        category="purchase_analytics",
        expected_family="purchase_orders",
        plan=_purchase_plan(
            original_question="Last purchase qty purchase of the item Keyboard",
            operation="ranking",
            measures=[Measure(concept="purchase quantity")],
            dimensions=[Dimension(concept="purchase date", grouping=False)],
            entities=[EntityReference(
                concept="material", original_value="Keyboard", confidence=0.8, status=EntityStatus.UNRESOLVED
            )],
            sorting=[SortInstruction(field_concept="purchase date", direction=SortDirection.DESC, priority=0)],
            limit=1,
        ),
        expected_tables={"INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS"},
        sql="""SELECT po.QTY AS purchase_quantity
FROM INVENTORY.PURCHASEORDER po
JOIN INVENTORY.INVITEMS items ON po.ITEM_CODE = items.ITEM_CODE
WHERE items.ITEM_NAME = :material_name
ORDER BY po.ORDERDATE DESC""",
        expect_order_by=True,
        expect_limit=1,
    ),
    AcceptanceCase(
        question='Last 3 purchase details of "MONITOR"',
        occurrence_count=10,
        category="purchase_analytics",
        expected_family="purchase_orders",
        plan=_purchase_plan(
            original_question='Last 3 purchase details of "MONITOR"',
            operation="ranking",
            measures=[Measure(concept="purchase quantity")],
            dimensions=[Dimension(concept="purchase date", grouping=False)],
            entities=[EntityReference(
                concept="material", original_value="MONITOR", confidence=0.8, status=EntityStatus.UNRESOLVED
            )],
            sorting=[SortInstruction(field_concept="purchase date", direction=SortDirection.DESC, priority=0)],
            limit=3,
        ),
        expected_tables={"INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS"},
        sql="""SELECT po.ORDERDATE, po.QTY AS purchase_quantity
FROM INVENTORY.PURCHASEORDER po
JOIN INVENTORY.INVITEMS items ON po.ITEM_CODE = items.ITEM_CODE
WHERE items.ITEM_NAME = :material_name
ORDER BY po.ORDERDATE DESC""",
        expect_order_by=True,
        expect_limit=3,
    ),
    AcceptanceCase(
        question="how many materials placed in last month?",
        occurrence_count=3,
        category="purchase_analytics",
        expected_family="purchase_orders",
        plan=_purchase_plan(
            original_question="how many materials placed in last month?",
            operation="aggregate",
            measures=[Measure(concept="purchase quantity", aggregation=Aggregation.COUNT)],
            date_range=DateRange(kind=DateRangeKind.RELATIVE, original_text="last month"),
        ),
        expected_tables={"INVENTORY.PURCHASEORDER"},
        sql="""SELECT COUNT(po.QTY) AS material_count
FROM INVENTORY.PURCHASEORDER po
WHERE po.ORDERDATE >= :date_start
AND po.ORDERDATE < :date_end""",
    ),
    AcceptanceCase(
        question="Which supplier is given lowest price?",
        occurrence_count=3,
        category="supplier_purchase",
        expected_family="purchase_orders",
        plan=_purchase_plan(
            original_question="Which supplier is given lowest price?",
            operation="ranking",
            measures=[Measure(concept="purchase value", aggregation=Aggregation.MINIMUM)],
            dimensions=[Dimension(concept="supplier", grouping=True)],
            sorting=[SortInstruction(field_concept="purchase value", direction=SortDirection.ASC, priority=0)],
            limit=1,
        ),
        expected_tables={"INVENTORY.PURCHASEORDER", "SCM.PARTYMASTER"},
        sql="""SELECT pm.PARTYNAME AS supplier, MIN(po.NET) AS purchase_value
FROM INVENTORY.PURCHASEORDER po
JOIN SCM.PARTYMASTER pm ON po.SUP_CODE = pm.PARTYCODE
GROUP BY pm.PARTYNAME
ORDER BY purchase_value ASC""",
        expect_group_by=True,
        expect_order_by=True,
        expect_limit=1,
    ),
    AcceptanceCase(
        question="Which supplier is taken highest orders?",
        occurrence_count=3,
        category="supplier_purchase",
        expected_family="purchase_orders",
        plan=_purchase_plan(
            original_question="Which supplier is taken highest orders?",
            operation="ranking",
            measures=[Measure(concept="purchase quantity", aggregation=Aggregation.COUNT)],
            dimensions=[Dimension(concept="supplier", grouping=True)],
            sorting=[SortInstruction(field_concept="purchase quantity", direction=SortDirection.DESC, priority=0)],
            limit=1,
        ),
        expected_tables={"INVENTORY.PURCHASEORDER", "SCM.PARTYMASTER"},
        sql="""SELECT pm.PARTYNAME AS supplier, COUNT(po.QTY) AS order_count
FROM INVENTORY.PURCHASEORDER po
JOIN SCM.PARTYMASTER pm ON po.SUP_CODE = pm.PARTYCODE
GROUP BY pm.PARTYNAME
ORDER BY order_count DESC""",
        expect_group_by=True,
        expect_order_by=True,
        expect_limit=1,
    ),
    AcceptanceCase(
        question="which material cost is high in last month?",
        occurrence_count=3,
        category="purchase_analytics",
        expected_family="purchase_orders",
        plan=_purchase_plan(
            original_question="which material cost is high in last month?",
            operation="ranking",
            measures=[Measure(concept="purchase value", aggregation=Aggregation.SUM)],
            dimensions=[Dimension(concept="material", grouping=True)],
            date_range=DateRange(kind=DateRangeKind.RELATIVE, original_text="last month"),
            sorting=[SortInstruction(field_concept="purchase value", direction=SortDirection.DESC, priority=0)],
            limit=1,
        ),
        expected_tables={"INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS"},
        sql="""SELECT items.ITEM_NAME AS material, SUM(po.NET) AS purchase_value
FROM INVENTORY.PURCHASEORDER po
JOIN INVENTORY.INVITEMS items ON po.ITEM_CODE = items.ITEM_CODE
WHERE po.ORDERDATE >= :date_start
AND po.ORDERDATE < :date_end
GROUP BY items.ITEM_NAME
ORDER BY purchase_value DESC""",
        expect_group_by=True,
        expect_order_by=True,
        expect_limit=1,
    ),
    AcceptanceCase(
        question="Who is given lowest price in last one year?",
        occurrence_count=3,
        category="purchase_analytics",
        expected_family="purchase_orders",
        plan=_purchase_plan(
            original_question="Who is given lowest price in last one year?",
            operation="ranking",
            measures=[Measure(concept="purchase value", aggregation=Aggregation.MINIMUM)],
            dimensions=[Dimension(concept="supplier", grouping=True)],
            # "last one year" is not a unit the validator's relative-date parser
            # recognizes (only months/days); expressed as an equivalent
            # month-based range so the offline date-shape check applies.
            date_range=DateRange(kind=DateRangeKind.RELATIVE, original_text="last 12 months"),
            sorting=[SortInstruction(field_concept="purchase value", direction=SortDirection.ASC, priority=0)],
            limit=1,
        ),
        expected_tables={"INVENTORY.PURCHASEORDER", "SCM.PARTYMASTER"},
        sql="""SELECT pm.PARTYNAME AS supplier, MIN(po.NET) AS purchase_value
FROM INVENTORY.PURCHASEORDER po
JOIN SCM.PARTYMASTER pm ON po.SUP_CODE = pm.PARTYCODE
WHERE po.ORDERDATE >= :date_start
AND po.ORDERDATE < :date_end
GROUP BY pm.PARTYNAME
ORDER BY purchase_value ASC""",
        expect_group_by=True,
        expect_order_by=True,
        expect_limit=1,
    ),
    AcceptanceCase(
        question='WHO are the suppliers for the item "BARCODE CHROMO LABEL"',
        occurrence_count=12,
        category="supplier_purchase",
        expected_family="purchase_orders",
        plan=_purchase_plan(
            original_question='WHO are the suppliers for the item "BARCODE CHROMO LABEL"',
            operation="detail",
            dimensions=[Dimension(concept="supplier", grouping=True)],
            entities=[EntityReference(
                concept="material", original_value="BARCODE CHROMO LABEL", confidence=0.8, status=EntityStatus.UNRESOLVED
            )],
            requested_output=RequestedOutput(fields=["supplier"]),
        ),
        expected_tables={"INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"},
        sql="""SELECT pm.PARTYNAME AS supplier
FROM INVENTORY.PURCHASEORDER po
JOIN INVENTORY.INVITEMS items ON po.ITEM_CODE = items.ITEM_CODE
JOIN SCM.PARTYMASTER pm ON po.SUP_CODE = pm.PARTYCODE
WHERE items.ITEM_NAME = :material_name
GROUP BY pm.PARTYNAME""",
        expect_group_by=True,
    ),
    AcceptanceCase(
        question="last supply from supplier Prime compu systems",
        occurrence_count=6,
        category="supplier_purchase",
        expected_family="purchase_orders",
        plan=_purchase_plan(
            original_question="last supply from supplier Prime compu systems",
            operation="ranking",
            dimensions=[Dimension(concept="purchase date", grouping=False), Dimension(concept="material", grouping=False)],
            entities=[EntityReference(
                concept="supplier name", original_value="Prime compu systems", confidence=0.8, status=EntityStatus.UNRESOLVED
            )],
            requested_output=RequestedOutput(fields=["material"]),
            sorting=[SortInstruction(field_concept="purchase date", direction=SortDirection.DESC, priority=0)],
            limit=1,
        ),
        # The entity value is a supplier display name, not a code, so the
        # QueryPlan uses the catalog's unambiguous "supplier name" concept
        # (SCM.PARTYMASTER.PARTYNAME only) rather than the code-or-name
        # ambiguous generic "supplier" concept. See the supplier-grounding
        # investigation: this is the correct V1 expectation, not a defect.
        expected_tables={"INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"},
        sql="""SELECT items.ITEM_NAME AS material
FROM INVENTORY.PURCHASEORDER po
JOIN INVENTORY.INVITEMS items ON po.ITEM_CODE = items.ITEM_CODE
JOIN SCM.PARTYMASTER pm ON po.SUP_CODE = pm.PARTYCODE
WHERE pm.PARTYNAME = :supplier_name
ORDER BY po.ORDERDATE DESC""",
        expect_order_by=True,
        expect_limit=1,
    ),
    AcceptanceCase(
        question="mouse last purchased supplier name?",
        occurrence_count=4,
        category="supplier_purchase",
        expected_family="purchase_orders",
        plan=_purchase_plan(
            original_question="mouse last purchased supplier name?",
            operation="ranking",
            dimensions=[Dimension(concept="purchase date", grouping=False), Dimension(concept="supplier name", grouping=False)],
            entities=[EntityReference(
                concept="material", original_value="mouse", confidence=0.8, status=EntityStatus.UNRESOLVED
            )],
            requested_output=RequestedOutput(fields=["supplier name"]),
            sorting=[SortInstruction(field_concept="purchase date", direction=SortDirection.DESC, priority=0)],
            limit=1,
        ),
        expected_tables={"INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS", "SCM.PARTYMASTER"},
        sql="""SELECT pm.PARTYNAME AS supplier_name
FROM INVENTORY.PURCHASEORDER po
JOIN INVENTORY.INVITEMS items ON po.ITEM_CODE = items.ITEM_CODE
JOIN SCM.PARTYMASTER pm ON po.SUP_CODE = pm.PARTYCODE
WHERE items.ITEM_NAME = :material_name
ORDER BY po.ORDERDATE DESC""",
        expect_order_by=True,
        expect_limit=1,
    ),
    AcceptanceCase(
        question="latest purchase order for supplier 800967 in 2026",
        occurrence_count=2,
        category="supplier_purchase",
        expected_family="purchase_orders",
        plan=_purchase_plan(
            original_question="latest purchase order for supplier 800967 in 2026",
            operation="ranking",
            dimensions=[Dimension(concept="purchase date", grouping=False), Dimension(concept="material", grouping=False)],
            entities=[EntityReference(
                concept="supplier identifier", original_value="800967", confidence=0.85, status=EntityStatus.UNRESOLVED
            )],
            date_range=DateRange(kind=DateRangeKind.ABSOLUTE, start="2026-01-01", end="2026-12-31", original_text="in 2026"),
            requested_output=RequestedOutput(fields=["material"]),
            sorting=[SortInstruction(field_concept="purchase date", direction=SortDirection.DESC, priority=0)],
            limit=1,
        ),
        expected_tables={"INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS"},
        sql="""SELECT items.ITEM_NAME AS material
FROM INVENTORY.PURCHASEORDER po
JOIN INVENTORY.INVITEMS items ON po.ITEM_CODE = items.ITEM_CODE
WHERE po.SUP_CODE = :supplier_code
AND po.ORDERDATE >= :date_start
AND po.ORDERDATE < :date_end
ORDER BY po.ORDERDATE DESC""",
        expect_order_by=True,
        expect_limit=1,
    ),
    AcceptanceCase(
        question="MRS for department EDP",
        occurrence_count=2,
        category="mrs",
        expected_family="mrs",
        plan=QueryPlan(
            original_question="MRS for department EDP",
            domain="mrs",
            operation="detail",
            business_subject=BusinessSubject(concept="mrs"),
            entities=[EntityReference(
                concept="department", original_value="EDP", confidence=0.8, status=EntityStatus.UNRESOLVED
            )],
            requested_output=RequestedOutput(fields=["department"]),
            confidence=0.85,
        ),
        expected_tables={"INVENTORY.MRS_TEMP"},
        sql="""SELECT m.DEPT_CODE
FROM INVENTORY.MRS_TEMP m
WHERE m.DEPT_CODE = :department""",
    ),
    AcceptanceCase(
        question="MRS due in 2026",
        occurrence_count=2,
        category="mrs",
        expected_family="mrs",
        plan=QueryPlan(
            original_question="MRS due in 2026",
            domain="mrs",
            operation="detail",
            business_subject=BusinessSubject(concept="mrs"),
            dimensions=[Dimension(concept="mrs date", grouping=False)],
            date_range=DateRange(kind=DateRangeKind.ABSOLUTE, start="2026-01-01", end="2026-12-31", original_text="in 2026"),
            confidence=0.85,
        ),
        expected_tables={"INVENTORY.MRS_TEMP"},
        sql="""SELECT m.MRSDATE
FROM INVENTORY.MRS_TEMP m
WHERE m.MRSDATE >= :date_start
AND m.MRSDATE < :date_end""",
    ),
    AcceptanceCase(
        question="how much cost consumed last month?",
        occurrence_count=3,
        category="purchase_analytics",
        expected_family="consumption",
        plan=QueryPlan(
            original_question="how much cost consumed last month?",
            domain="consumption",
            operation="aggregate",
            business_subject=BusinessSubject(concept="consumption"),
            measures=[Measure(concept="consumption value", aggregation=Aggregation.SUM)],
            date_range=DateRange(kind=DateRangeKind.RELATIVE, original_text="last month"),
            confidence=0.85,
        ),
        expected_tables={"INVENTORY.ISSUE"},
        sql="""SELECT SUM(issue.ISSUEVALUE) AS consumption_value
FROM INVENTORY.ISSUE issue
WHERE issue.ISSUEDATE >= :date_start
AND issue.ISSUEDATE < :date_end""",
    ),
    AcceptanceCase(
        # Real Step 4 result (2026-09-23, qwen3:14b, docs/STEP4_MODEL_COMPARISON.md):
        # verbatim plan+SQL the live pipeline produced and PASS_PIPELINE'd -- pinned
        # here, not hand-idealized, so a prompt/model regression on this exact shape
        # is caught. Also the first acceptance case where the model used a bare
        # generic measure concept ("rate") on the purchase domain -- purchase_rate
        # is that concept's home domain, so this is the case fix.md #10 says must
        # keep working once consumption_rate also claims the same generic alias.
        question="last purchase rate of barcode scanner in 2026",
        occurrence_count=1,
        category="purchase_analytics",
        expected_family="purchase_orders",
        plan=_purchase_plan(
            original_question="last purchase rate of barcode scanner in 2026",
            operation="detail",
            measures=[Measure(concept="rate", aggregation=Aggregation.NONE)],
            dimensions=[Dimension(concept="date", grouping=False)],
            entities=[EntityReference(
                concept="material", original_value="barcode scanner", confidence=0.8, status=EntityStatus.UNRESOLVED
            )],
            filters=[QueryFilter(concept="date", operator=FilterOperator.EQUALS, value="2026", value_type="year")],
            date_range=DateRange(kind=DateRangeKind.ABSOLUTE, start="2026-01-01", end="2026-12-31", original_text="in 2026"),
            sorting=[SortInstruction(field_concept="date", direction=SortDirection.DESC, priority=0)],
            requested_output=RequestedOutput(fields=["date"]),
            confidence=0.7,
        ),
        expected_tables={"INVENTORY.PURCHASEORDER", "INVENTORY.INVITEMS"},
        sql="""SELECT INVENTORY.PURCHASEORDER.ORDERDATE AS date, INVENTORY.PURCHASEORDER.RATE AS rate
FROM INVENTORY.PURCHASEORDER
INNER JOIN INVENTORY.INVITEMS ON INVENTORY.PURCHASEORDER.ITEM_CODE = INVENTORY.INVITEMS.ITEM_CODE
WHERE INVENTORY.INVITEMS.ITEM_NAME = :material
AND INVENTORY.PURCHASEORDER.ORDERDATE >= :date_start
AND INVENTORY.PURCHASEORDER.ORDERDATE < :date_end
ORDER BY INVENTORY.PURCHASEORDER.ORDERDATE DESC""",
        expect_order_by=True,
    ),
    AcceptanceCase(
        # fix.md #10 regression anchor at the acceptance level (schema_grounding's
        # own unit tests already cover this; this pins the same bug shape through
        # the full capability -> grounding -> SQL-validation chain this file
        # exercises). Before the fix, "value" on a consumption-domain plan silently
        # grounded to INVENTORY.PURCHASEORDER.NET -- is_grounded=True, no rejection.
        question="how much value consumed?",
        occurrence_count=1,
        category="purchase_analytics",
        expected_family="consumption",
        plan=QueryPlan(
            original_question="how much value consumed?",
            domain="consumption",
            operation="aggregate",
            business_subject=BusinessSubject(concept="consumption"),
            measures=[Measure(concept="value", aggregation=Aggregation.SUM)],
            confidence=0.85,
        ),
        expected_tables={"INVENTORY.ISSUE"},
        sql="""SELECT SUM(issue.ISSUEVALUE) AS value
FROM INVENTORY.ISSUE issue""",
    ),
    AcceptanceCase(
        question="latest issue for yarn in 2024",
        occurrence_count=2,
        category="inventory_movement",
        expected_family="consumption",
        plan=QueryPlan(
            original_question="latest issue for yarn in 2024",
            domain="consumption",
            operation="ranking",
            business_subject=BusinessSubject(concept="consumption"),
            dimensions=[Dimension(concept="consumption date", grouping=False)],
            entities=[EntityReference(
                concept="material", original_value="yarn", confidence=0.8, status=EntityStatus.UNRESOLVED
            )],
            date_range=DateRange(kind=DateRangeKind.ABSOLUTE, start="2024-01-01", end="2024-12-31", original_text="in 2024"),
            sorting=[SortInstruction(field_concept="consumption date", direction=SortDirection.DESC, priority=0)],
            limit=1,
            confidence=0.85,
        ),
        expected_tables={"INVENTORY.ISSUE", "INVENTORY.INVITEMS"},
        sql="""SELECT issue.ISSUEDATE
FROM INVENTORY.ISSUE issue
JOIN INVENTORY.INVITEMS items ON issue.CODE = items.ITEM_CODE
WHERE items.ITEM_NAME = :material_name
AND issue.ISSUEDATE >= :date_start
AND issue.ISSUEDATE < :date_end
ORDER BY issue.ISSUEDATE DESC""",
        expect_order_by=True,
        expect_limit=1,
    ),
]


@dataclass
class RejectionCase:
    question: str
    occurrence_count: int
    category: str
    needs_review: bool
    plan: QueryPlan
    expected_family: str


REJECTION_CASES: list[RejectionCase] = [
    RejectionCase(
        question="how many qty received in last one year?",
        occurrence_count=3,
        category="inventory_movement",
        needs_review=False,
        plan=QueryPlan(
            original_question="how many qty received in last one year?",
            domain="grn",
            operation="aggregate",
            business_subject=BusinessSubject(concept="goods receipt"),
            confidence=0.7,
        ),
        expected_family="grn",
    ),
    RejectionCase(
        question="how many qty in receipt pending?",
        occurrence_count=3,
        category="inventory_movement",
        needs_review=False,
        plan=QueryPlan(
            original_question="how many qty in receipt pending?",
            domain="grn",
            operation="detail",
            business_subject=BusinessSubject(concept="goods receipt"),
            confidence=0.7,
        ),
        expected_family="grn",
    ),
    RejectionCase(
        question="keyboard stock",
        occurrence_count=2,
        category="review_required",
        needs_review=True,
        plan=QueryPlan(
            original_question="keyboard stock",
            domain="stock",
            operation="detail",
            business_subject=BusinessSubject(concept="stock"),
            confidence=0.7,
        ),
        expected_family="stock",
    ),
]


class V1AcceptanceMatrixTests(unittest.TestCase):
    """Real-question regression matrix: QueryPlan -> capability -> grounding -> SQL shape."""

    def test_supported_questions_accept_end_to_end(self) -> None:
        for case in CASES:
            with self.subTest(question=case.question):
                capability = evaluate_capability(case.plan)
                self.assertTrue(
                    capability.supported, f"{case.question!r} rejected: {capability.reject_reasons}"
                )
                self.assertEqual(capability.family, case.expected_family)

                grounding = ground_query_plan(case.plan)
                self.assertTrue(grounding.is_grounded, grounding.model_dump())

                actual_tables = {item.full_table_name for item in grounding.selected_tables}
                self.assertEqual(actual_tables, case.expected_tables)

                # Deterministic, offline structural validation of the SQL shape
                # (no Ollama/Oracle involved) using the real V1 validator.
                validate_grounded_sql(case.sql, case.plan, grounding)

                if case.expect_group_by:
                    self.assertRegex(case.sql, r"(?i)\bGROUP\s+BY\b")
                if case.expect_order_by:
                    self.assertRegex(case.sql, r"(?i)\bORDER\s+BY\b")
                if case.expect_limit is not None:
                    # The model never writes a limit; the deterministic wrapper
                    # applies the plan's own limit after validation.
                    self.assertEqual(case.plan.limit, case.expect_limit)
                    wrapped, _, _ = add_execution_probe_limit(case.sql, case.plan, 100)
                    self.assertRegex(wrapped, rf"(?i)WHERE\s+ROWNUM\s*<=\s*{case.expect_limit}\b")
                    self.assertNotRegex(case.sql, r"(?i)\bFETCH\b|\bROWNUM\b")
                for entity in case.plan.entities:
                    if entity.original_value:
                        self.assertNotIn(
                            entity.original_value, case.sql,
                            "Entity value must be bound, not interpolated as a literal.",
                        )
                for match in re.finditer(r":([A-Za-z_][A-Za-z0-9_]*)", case.sql):
                    self.assertTrue(match.group(1), "Named binds must be non-empty identifiers.")

    def test_unsupported_families_are_rejected(self) -> None:
        for case in REJECTION_CASES:
            with self.subTest(question=case.question):
                capability = evaluate_capability(case.plan)
                self.assertFalse(capability.supported, f"{case.question!r} was unexpectedly accepted")
                self.assertEqual(capability.family, case.expected_family)
                self.assertTrue(capability.reject_reasons)


if __name__ == "__main__":
    unittest.main(verbosity=2)
