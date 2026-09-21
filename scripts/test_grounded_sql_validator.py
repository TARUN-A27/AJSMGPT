from __future__ import annotations

import unittest

from app.grounded_sql_validator import (
    GroundedSqlGroundingError,
    GroundedSqlSemanticError,
    GroundedSqlValidationError,
    OracleDialectGroundedSqlError,
    UnsafeGroundedSqlError,
    validate_grounded_sql,
)
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
    QueryPlan,
    QueryFilter,
    RequestedOutput,
    SortDirection,
    SortInstruction,
)
from app.schema_grounding import ground_query_plan


PURCHASE_RANKING_SQL = """
SELECT pm.PARTYNAME AS supplier, SUM(po.NET) AS total_purchase_value
FROM INVENTORY.PURCHASEORDER po
JOIN SCM.PARTYMASTER pm ON po.SUP_CODE = pm.PARTYCODE
WHERE po.ORDERDATE >= ADD_MONTHS(TRUNC(SYSDATE), -6)
AND po.ORDERDATE < TRUNC(SYSDATE) + 1
GROUP BY pm.PARTYNAME
ORDER BY total_purchase_value DESC
FETCH FIRST 10 ROWS ONLY
""".strip()

FULLY_QUALIFIED_PURCHASE_RANKING_SQL = """
SELECT SCM.PARTYMASTER.PARTYNAME AS supplier,
SUM(INVENTORY.PURCHASEORDER.NET) AS value
FROM INVENTORY.PURCHASEORDER
JOIN SCM.PARTYMASTER
ON INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE
WHERE INVENTORY.PURCHASEORDER.ORDERDATE BETWEEN :start_date AND :end_date
GROUP BY SCM.PARTYMASTER.PARTYNAME
ORDER BY value DESC
FETCH FIRST 10 ROWS ONLY
""".strip()


def purchase_ranking_plan() -> QueryPlan:
    return QueryPlan(
        original_question="top 10 suppliers by purchase value in the last six months",
        domain="purchase",
        operation="ranking",
        business_subject=BusinessSubject(concept="purchase"),
        measures=[Measure(concept="purchase value", aggregation=Aggregation.SUM, alias="total_purchase_value")],
        dimensions=[Dimension(concept="supplier", grouping=True)],
        date_range=DateRange(kind=DateRangeKind.RELATIVE, original_text="last six months"),
        sorting=[SortInstruction(field_concept="purchase value", direction=SortDirection.DESC, priority=0)],
        limit=10,
        requested_output=RequestedOutput(fields=["supplier", "purchase value"]),
        confidence=0.95,
    )


def absolute_purchase_ranking_plan() -> QueryPlan:
    return purchase_ranking_plan().model_copy(update={
        "date_range": DateRange(
            kind=DateRangeKind.ABSOLUTE,
            start="2025-01-01",
            end="2025-12-31",
            original_text="in 2025",
        )
    })


class GroundedSqlValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = purchase_ranking_plan()
        self.grounding = ground_query_plan(self.plan)
        self.assertTrue(self.grounding.is_grounded, self.grounding.model_dump())

    def test_valid_purchase_ranking_sql(self) -> None:
        validate_grounded_sql(PURCHASE_RANKING_SQL, self.plan, self.grounding)

    def test_last_six_months_accepts_calendar_month_boundary(self) -> None:
        validate_grounded_sql(PURCHASE_RANKING_SQL, self.plan, self.grounding)

    def test_last_six_months_rejects_single_month_between(self) -> None:
        sql = PURCHASE_RANKING_SQL.replace(
            "po.ORDERDATE >= ADD_MONTHS(TRUNC(SYSDATE), -6)\nAND po.ORDERDATE < TRUNC(SYSDATE) + 1",
            "po.ORDERDATE BETWEEN ADD_MONTHS(TRUNC(SYSDATE), -6) AND ADD_MONTHS(TRUNC(SYSDATE), -5)",
        )
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, self.plan, self.grounding)

    def test_last_one_month_accepts_calendar_month_boundary(self) -> None:
        plan = self.plan.model_copy(update={
            "date_range": DateRange(kind=DateRangeKind.RELATIVE, original_text="last 1 month")
        })
        grounding = ground_query_plan(plan)
        sql = PURCHASE_RANKING_SQL.replace("-6", "-1")
        validate_grounded_sql(sql, plan, grounding)

    def test_last_month_rejects_wrong_month_count(self) -> None:
        plan = self.plan.model_copy(update={
            "date_range": DateRange(kind=DateRangeKind.RELATIVE, original_text="last month")
        })
        grounding = ground_query_plan(plan)
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(PURCHASE_RANKING_SQL, plan, grounding)

    def test_last_thirty_days_remains_day_based(self) -> None:
        plan = self.plan.model_copy(update={
            "date_range": DateRange(kind=DateRangeKind.RELATIVE, original_text="last 30 days")
        })
        grounding = ground_query_plan(plan)
        sql = PURCHASE_RANKING_SQL.replace(
            "ADD_MONTHS(TRUNC(SYSDATE), -6)", "TRUNC(SYSDATE) - 30"
        )
        validate_grounded_sql(sql, plan, grounding)

    def test_last_thirty_days_accepts_redundant_generic_date_filter(self) -> None:
        plan = self.plan.model_copy(update={
            "date_range": DateRange(kind=DateRangeKind.RELATIVE, original_text="last 30 days"),
            "filters": [QueryFilter(
                concept="date",
                operator=FilterOperator.GREATER_THAN,
                value="30 days ago",
                value_type="date",
            )],
        })
        grounding = ground_query_plan(plan)
        sql = PURCHASE_RANKING_SQL.replace(
            "ADD_MONTHS(TRUNC(SYSDATE), -6)", "TRUNC(SYSDATE) - 30"
        )
        validate_grounded_sql(sql, plan, grounding)

    def test_last_thirty_days_rejects_month_logic(self) -> None:
        plan = self.plan.model_copy(update={
            "date_range": DateRange(kind=DateRangeKind.RELATIVE, original_text="last 30 days")
        })
        grounding = ground_query_plan(plan)
        with self.assertRaisesRegex(GroundedSqlSemanticError, "TRUNC\\(SYSDATE\\) - 30"):
            validate_grounded_sql(PURCHASE_RANKING_SQL, plan, grounding)

    def test_month_range_rejects_fractional_day_approximation(self) -> None:
        sql = PURCHASE_RANKING_SQL.replace(
            "ADD_MONTHS(TRUNC(SYSDATE), -6)", "TRUNC(SYSDATE - 182.5)"
        )
        with self.assertRaisesRegex(GroundedSqlSemanticError, "ADD_MONTHS\(TRUNC\(SYSDATE\), -6\)"):
            validate_grounded_sql(sql, self.plan, self.grounding)

    def test_month_range_rejects_integer_day_approximation(self) -> None:
        sql = PURCHASE_RANKING_SQL.replace(
            "ADD_MONTHS(TRUNC(SYSDATE), -6)", "SYSDATE - 180"
        )
        with self.assertRaisesRegex(GroundedSqlSemanticError, "day approximations"):
            validate_grounded_sql(sql, self.plan, self.grounding)

    def test_corrected_fully_qualified_supplier_ranking_sql_passes(self) -> None:
        plan = absolute_purchase_ranking_plan()
        grounding = ground_query_plan(plan)
        validate_grounded_sql(FULLY_QUALIFIED_PURCHASE_RANKING_SQL, plan, grounding)

    def test_alias_based_equivalent_passes(self) -> None:
        plan = absolute_purchase_ranking_plan()
        grounding = ground_query_plan(plan)
        sql = """SELECT pm.PARTYNAME AS supplier, SUM(po.NET) AS value
FROM INVENTORY.PURCHASEORDER po
JOIN SCM.PARTYMASTER pm ON po.SUP_CODE = pm.PARTYCODE
WHERE po.ORDERDATE BETWEEN :start_date AND :end_date
GROUP BY pm.PARTYNAME
ORDER BY value DESC
FETCH FIRST 10 ROWS ONLY"""
        validate_grounded_sql(sql, plan, grounding)

    def test_unique_unqualified_columns_pass(self) -> None:
        plan = absolute_purchase_ranking_plan()
        grounding = ground_query_plan(plan)
        sql = """SELECT PARTYNAME AS supplier, SUM(NET) AS value
FROM INVENTORY.PURCHASEORDER
JOIN SCM.PARTYMASTER ON SUP_CODE = PARTYCODE
WHERE ORDERDATE BETWEEN :start_date AND :end_date
GROUP BY PARTYNAME
ORDER BY value DESC
FETCH FIRST 10 ROWS ONLY"""
        validate_grounded_sql(sql, plan, grounding)

    def test_valid_supplier_code_does_not_require_master_display(self) -> None:
        plan = QueryPlan(
            original_question="purchase value by supplier code",
            domain="purchase",
            operation="aggregate",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase value", aggregation=Aggregation.SUM)],
            dimensions=[Dimension(concept="supplier code", grouping=True)],
            requested_output=RequestedOutput(fields=["supplier code", "purchase value"]),
            confidence=0.9,
        )
        grounding = ground_query_plan(plan)
        sql = """SELECT po.SUP_CODE, SUM(po.NET) AS purchase_value
FROM INVENTORY.PURCHASEORDER po
GROUP BY po.SUP_CODE"""
        validate_grounded_sql(sql, plan, grounding)
        self.assertEqual([item.full_table_name for item in grounding.selected_tables], ["INVENTORY.PURCHASEORDER"])

    def test_supplier_code_cannot_replace_requested_supplier_display(self) -> None:
        sql = """SELECT po.SUP_CODE AS supplier, SUM(po.NET) AS value
FROM INVENTORY.PURCHASEORDER po
WHERE po.ORDERDATE >= ADD_MONTHS(TRUNC(SYSDATE), -6)
GROUP BY po.SUP_CODE
ORDER BY value DESC
FETCH FIRST 10 ROWS ONLY"""
        with self.assertRaisesRegex(GroundedSqlSemanticError, "PARTYNAME"):
            validate_grounded_sql(sql, self.plan, self.grounding)

    def test_valid_material_purchase_preview_uses_bind(self) -> None:
        plan = QueryPlan(
            original_question="purchases for material bearing",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            entities=[EntityReference(
                concept="material", original_value="bearing", confidence=0.8, status=EntityStatus.UNRESOLVED
            )],
            requested_output=RequestedOutput(fields=["material"]),
            confidence=0.85,
        )
        grounding = ground_query_plan(plan)
        sql = """SELECT items.ITEM_NAME
FROM INVENTORY.PURCHASEORDER po
JOIN INVENTORY.INVITEMS items ON po.ITEM_CODE = items.ITEM_CODE
WHERE items.ITEM_NAME = :material_name"""
        validate_grounded_sql(sql, plan, grounding)

    def test_valid_material_preview_when_bind_name_collides_with_column_name(self) -> None:
        """Regression for Phase 9C: a bind named after its own column, e.g.
        `ITEM_NAME = :ITEM_NAME`, must not be double-counted as two column
        occurrences. Same fixture as test_valid_material_purchase_preview_uses_bind,
        only the bind name differs."""
        plan = QueryPlan(
            original_question="purchases for material bearing",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            entities=[EntityReference(
                concept="material", original_value="bearing", confidence=0.8, status=EntityStatus.UNRESOLVED
            )],
            requested_output=RequestedOutput(fields=["material"]),
            confidence=0.85,
        )
        grounding = ground_query_plan(plan)
        sql = """SELECT items.ITEM_NAME
FROM INVENTORY.PURCHASEORDER po
JOIN INVENTORY.INVITEMS items ON po.ITEM_CODE = items.ITEM_CODE
WHERE items.ITEM_NAME = :ITEM_NAME"""
        validate_grounded_sql(sql, plan, grounding)

    def test_valid_mrs_preview(self) -> None:
        plan = QueryPlan(
            original_question="mrs quantity by department",
            domain="mrs",
            operation="aggregate",
            business_subject=BusinessSubject(concept="mrs"),
            measures=[Measure(concept="mrs quantity", aggregation=Aggregation.SUM)],
            dimensions=[Dimension(concept="department", grouping=True)],
            confidence=0.9,
        )
        grounding = ground_query_plan(plan)
        sql = """SELECT m.DEPT_CODE, SUM(m.QTY) AS mrs_quantity
FROM INVENTORY.MRS_TEMP m
GROUP BY m.DEPT_CODE"""
        validate_grounded_sql(sql, plan, grounding)

    def test_valid_consumption_issue_preview(self) -> None:
        plan = QueryPlan(
            original_question="consumption quantity by material last month",
            domain="consumption",
            operation="aggregate",
            business_subject=BusinessSubject(concept="consumption"),
            measures=[Measure(concept="consumption quantity", aggregation=Aggregation.SUM)],
            dimensions=[Dimension(concept="material", grouping=True)],
            date_range=DateRange(kind=DateRangeKind.RELATIVE, original_text="last month"),
            confidence=0.9,
        )
        grounding = ground_query_plan(plan)
        sql = """SELECT items.ITEM_NAME, SUM(issue.QTY) AS issued_quantity
FROM INVENTORY.ISSUE issue
JOIN INVENTORY.INVITEMS items ON issue.CODE = items.ITEM_CODE
WHERE issue.ISSUEDATE >= ADD_MONTHS(TRUNC(SYSDATE), -1)
AND issue.ISSUEDATE < TRUNC(SYSDATE) + 1
GROUP BY items.ITEM_NAME"""
        validate_grounded_sql(sql, plan, grounding)

    def assert_rejected(self, sql: str, error_type: type[Exception]) -> None:
        with self.assertRaises(error_type):
            validate_grounded_sql(sql, self.plan, self.grounding)

    def test_rejects_select_star(self) -> None:
        self.assert_rejected(PURCHASE_RANKING_SQL.replace(
            "pm.PARTYNAME AS supplier, SUM(po.NET) AS total_purchase_value", "*"
        ), UnsafeGroundedSqlError)

    def test_rejects_ungrounded_table(self) -> None:
        self.assert_rejected(PURCHASE_RANKING_SQL.replace(
            "INVENTORY.PURCHASEORDER po", "INVENTORY.SECRET_TABLE po"
        ), GroundedSqlGroundingError)

    def test_rejects_ungrounded_column(self) -> None:
        self.assert_rejected(PURCHASE_RANKING_SQL.replace("po.NET", "po.STATUS"), GroundedSqlGroundingError)

    def test_rejects_unknown_schema_table_column(self) -> None:
        plan = absolute_purchase_ranking_plan()
        grounding = ground_query_plan(plan)
        with self.assertRaises(GroundedSqlGroundingError):
            validate_grounded_sql(
                FULLY_QUALIFIED_PURCHASE_RANKING_SQL.replace(
                    "SCM.PARTYMASTER.PARTYNAME", "OTHER.TABLE.COLUMN", 1
                ),
                plan,
                grounding,
            )

    def test_rejects_unknown_fully_qualified_column(self) -> None:
        plan = absolute_purchase_ranking_plan()
        grounding = ground_query_plan(plan)
        with self.assertRaises(GroundedSqlGroundingError):
            validate_grounded_sql(
                FULLY_QUALIFIED_PURCHASE_RANKING_SQL.replace(
                    "SCM.PARTYMASTER.PARTYNAME", "SCM.PARTYMASTER.UNKNOWN_COLUMN", 1
                ),
                plan,
                grounding,
            )

    def test_rejects_unknown_alias(self) -> None:
        self.assert_rejected(PURCHASE_RANKING_SQL.replace("po.NET", "ghost.NET"), GroundedSqlGroundingError)

    def test_rejects_invented_join(self) -> None:
        self.assert_rejected(PURCHASE_RANKING_SQL.replace(
            "po.SUP_CODE = pm.PARTYCODE", "po.NET = pm.PARTYCODE"
        ), GroundedSqlGroundingError)

    def test_rejects_dml_ddl_and_multiple_statements(self) -> None:
        for sql in (
            "DELETE FROM INVENTORY.PURCHASEORDER",
            "CREATE TABLE X (Y NUMBER)",
            PURCHASE_RANKING_SQL + "; SELECT po.NET FROM INVENTORY.PURCHASEORDER po",
        ):
            with self.subTest(sql=sql[:20]):
                self.assert_rejected(sql, UnsafeGroundedSqlError)

    def test_rejects_missing_grouping(self) -> None:
        self.assert_rejected(PURCHASE_RANKING_SQL.replace(
            "GROUP BY pm.PARTYNAME", ""
        ), GroundedSqlSemanticError)

    def test_rejects_missing_date_filter(self) -> None:
        self.assert_rejected(PURCHASE_RANKING_SQL.replace(
            "WHERE po.ORDERDATE >= ADD_MONTHS(TRUNC(SYSDATE), -6)", ""
        ), GroundedSqlSemanticError)

    def test_rejects_missing_limit(self) -> None:
        self.assert_rejected(PURCHASE_RANKING_SQL.replace(
            "FETCH FIRST 10 ROWS ONLY", ""
        ), GroundedSqlSemanticError)

    def test_rejects_sort_direction_attached_to_wrong_field(self) -> None:
        sql = PURCHASE_RANKING_SQL.replace(
            "ORDER BY total_purchase_value DESC",
            "ORDER BY total_purchase_value ASC, pm.PARTYNAME DESC",
        )
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, self.plan, self.grounding)

    def test_rejects_nested_select_limit_as_outer_limit(self) -> None:
        sql = "WITH unused_rows AS (SELECT po.NET FROM INVENTORY.PURCHASEORDER po FETCH FIRST 10 ROWS ONLY) " + PURCHASE_RANKING_SQL.replace(
            "FETCH FIRST 10 ROWS ONLY", ""
        )
        with self.assertRaises(UnsafeGroundedSqlError):
            validate_grounded_sql(sql, self.plan, self.grounding)

    def test_rejects_limit_with_oracle_specific_error(self) -> None:
        sql = PURCHASE_RANKING_SQL.replace("FETCH FIRST 10 ROWS ONLY", "LIMIT 10")
        with self.assertRaisesRegex(
            OracleDialectGroundedSqlError,
            r"Oracle SQL does not support LIMIT; use FETCH FIRST <N> ROWS ONLY",
        ):
            validate_grounded_sql(sql, self.plan, self.grounding)

    def test_rejects_embedded_entity_literal(self) -> None:
        plan = QueryPlan(
            original_question="purchases for supplier ABC",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            entities=[EntityReference(
                concept="supplier", original_value="ABC", confidence=0.9, status=EntityStatus.RESOLVED,
                selected_value="ABC",
            )],
            confidence=0.9,
        )
        grounding = ground_query_plan(plan)
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(
                "SELECT po.SUP_CODE FROM INVENTORY.PURCHASEORDER po WHERE po.SUP_CODE = 'ABC'",
                plan,
                grounding,
            )

    def test_rejects_transformed_entity_literal_alongside_bind(self) -> None:
        plan = QueryPlan(
            original_question="purchases for supplier ABC",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            entities=[EntityReference(
                concept="supplier", selected_value="ABC", confidence=0.9,
                status=EntityStatus.RESOLVED,
            )],
            confidence=0.9,
        )
        grounding = ground_query_plan(plan)
        sql = """SELECT po.SUP_CODE
FROM INVENTORY.PURCHASEORDER po
WHERE po.SUP_CODE = :supplier OR po.SUP_CODE LIKE 'ABC%'"""
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    # -- Phase 9b fixes ---------------------------------------------------

    def test_bind_named_limit_does_not_trigger_the_limit_dialect_error(self) -> None:
        # A bind literally named `:limit` must not be mistaken for the
        # unsupported MySQL/Postgres LIMIT clause; it should still be
        # rejected, but for the real reason (no literal row count present).
        sql = PURCHASE_RANKING_SQL.replace("FETCH FIRST 10 ROWS ONLY", "FETCH FIRST :limit ROWS ONLY")
        try:
            validate_grounded_sql(sql, self.plan, self.grounding)
            self.fail("Expected a rejection for the missing literal row limit.")
        except OracleDialectGroundedSqlError:
            self.fail("A `:limit` bind name must not trigger the LIMIT dialect check.")
        except GroundedSqlSemanticError as exc:
            self.assertIn("row limit", str(exc).lower())

    def test_real_limit_clause_is_still_rejected_next_to_a_limit_named_bind(self) -> None:
        # Guards against a regression that disables the LIMIT check entirely
        # instead of just excluding bind-name matches.
        sql = PURCHASE_RANKING_SQL.replace("FETCH FIRST 10 ROWS ONLY", "LIMIT :limit")
        with self.assertRaisesRegex(
            OracleDialectGroundedSqlError, "Oracle SQL does not support LIMIT",
        ):
            validate_grounded_sql(sql, self.plan, self.grounding)

    def test_to_date_rejection_names_the_bind_fix(self) -> None:
        plan = absolute_purchase_ranking_plan()
        grounding = ground_query_plan(plan)
        sql = FULLY_QUALIFIED_PURCHASE_RANKING_SQL.replace(
            "BETWEEN :start_date AND :end_date",
            ">= TO_DATE('2025-01-01', 'YYYY-MM-DD')",
        )
        with self.assertRaisesRegex(UnsafeGroundedSqlError, "named bind parameters"):
            validate_grounded_sql(sql, plan, grounding)

    def test_rejects_unrequested_filters_on_grounded_measure(self) -> None:
        plan = QueryPlan(
            original_question="total purchase value",
            domain="purchase",
            operation="aggregate",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase value", aggregation=Aggregation.SUM)],
            confidence=0.9,
        )
        grounding = ground_query_plan(plan)
        for predicate in ("po.NET > 0", "po.NET = '0'", "po.NET IS NOT NULL"):
            with self.subTest(predicate=predicate), self.assertRaises(GroundedSqlSemanticError):
                validate_grounded_sql(
                    "SELECT SUM(po.NET) AS total_value FROM INVENTORY.PURCHASEORDER po WHERE " + predicate,
                    plan,
                    grounding,
                )

    def test_rejects_filter_operator_mismatch_and_extra_predicate(self) -> None:
        plan = QueryPlan(
            original_question="purchase for supplier containing abc",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            filters=[QueryFilter(
                concept="supplier",
                operator=FilterOperator.CONTAINS,
                value="abc",
                value_type="string",
            )],
            confidence=0.9,
        )
        grounding = ground_query_plan(plan)
        for predicate in (
            "po.SUP_CODE = :supplier",
            "po.SUP_CODE LIKE :supplier OR po.SUP_CODE IS NULL",
        ):
            with self.subTest(predicate=predicate), self.assertRaises(GroundedSqlSemanticError):
                validate_grounded_sql(
                    "SELECT po.SUP_CODE FROM INVENTORY.PURCHASEORDER po WHERE " + predicate,
                    plan,
                    grounding,
                )


class MrsCompoundConditionTests(unittest.TestCase):
    def _plan(self, concept: str) -> QueryPlan:
        return QueryPlan(
            original_question="mrs compound condition test",
            domain="mrs",
            operation="detail",
            business_subject=BusinessSubject(concept="mrs"),
            entities=[
                EntityReference(
                    concept="mrs number", original_value="890330", selected_value="890330",
                    confidence=0.9, status=EntityStatus.RESOLVED,
                ),
                EntityReference(
                    concept=concept, original_value="1", selected_value="1",
                    confidence=0.9, status=EntityStatus.RESOLVED,
                ),
            ],
            confidence=0.9,
        )

    def test_rejected_with_both_columns_and_or_is_valid(self):
        plan = self._plan("mrs rejected")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT M.MRSNO FROM INVENTORY.MRS_TEMP M "
            "WHERE M.MRSNO = :mrs_number AND (M.REJECTIONSTATUS = 1 OR M.STORESREJECTIONSTATUS = 1)"
        )
        validate_grounded_sql(sql, plan, grounding)

    def test_approved_with_both_columns_and_and_is_valid(self):
        plan = self._plan("mrs approved")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT M.MRSNO FROM INVENTORY.MRS_TEMP M "
            "WHERE M.MRSNO = :mrs_number AND M.APPROVALSTATUS = 1 AND M.READYFORAPPROVAL = 1"
        )
        validate_grounded_sql(sql, plan, grounding)

    def test_alias_and_whitespace_variations_are_tolerated(self):
        plan = self._plan("mrs rejected")
        grounding = ground_query_plan(plan)
        sql = """SELECT m.MRSNO
FROM INVENTORY.MRS_TEMP m
WHERE m.MRSNO = :mrs_number AND (   m.REJECTIONSTATUS  =  1   OR   m.STORESREJECTIONSTATUS  =  1   )"""
        validate_grounded_sql(sql, plan, grounding)

    def test_rejected_with_only_one_column_is_rejected(self):
        plan = self._plan("mrs rejected")
        grounding = ground_query_plan(plan)
        sql = "SELECT M.MRSNO FROM INVENTORY.MRS_TEMP M WHERE M.MRSNO = :mrs_number AND M.REJECTIONSTATUS = 1"
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    def test_rejected_using_and_instead_of_or_is_rejected(self):
        plan = self._plan("mrs rejected")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT M.MRSNO FROM INVENTORY.MRS_TEMP M WHERE M.MRSNO = :mrs_number "
            "AND M.REJECTIONSTATUS = 1 AND M.STORESREJECTIONSTATUS = 1"
        )
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    def test_approved_using_or_instead_of_and_is_rejected(self):
        plan = self._plan("mrs approved")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT M.MRSNO FROM INVENTORY.MRS_TEMP M WHERE M.MRSNO = :mrs_number "
            "AND M.APPROVALSTATUS = 1 OR M.READYFORAPPROVAL = 1"
        )
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    def test_approved_with_only_one_column_is_rejected(self):
        plan = self._plan("mrs approved")
        grounding = ground_query_plan(plan)
        sql = "SELECT M.MRSNO FROM INVENTORY.MRS_TEMP M WHERE M.MRSNO = :mrs_number AND M.APPROVALSTATUS = 1"
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    def test_ungrounded_substitute_column_is_rejected(self):
        plan = self._plan("mrs approved")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT M.MRSNO FROM INVENTORY.MRS_TEMP M WHERE M.MRSNO = :mrs_number "
            "AND M.APPROVALSTATUS = 1 AND M.STATUS = 1"
        )
        with self.assertRaises(GroundedSqlValidationError):
            validate_grounded_sql(sql, plan, grounding)

    def test_compound_condition_omitted_is_rejected(self):
        plan = self._plan("mrs rejected")
        grounding = ground_query_plan(plan)
        sql = "SELECT M.MRSNO FROM INVENTORY.MRS_TEMP M WHERE M.MRSNO = :mrs_number"
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)


if __name__ == "__main__":
    unittest.main(verbosity=2)
