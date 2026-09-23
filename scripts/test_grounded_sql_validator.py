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
WHERE po.ORDERDATE >= :date_start
AND po.ORDERDATE < :date_end
GROUP BY pm.PARTYNAME
ORDER BY total_purchase_value DESC
""".strip()

FULLY_QUALIFIED_PURCHASE_RANKING_SQL = """
SELECT SCM.PARTYMASTER.PARTYNAME AS supplier,
SUM(INVENTORY.PURCHASEORDER.NET) AS value
FROM INVENTORY.PURCHASEORDER
JOIN SCM.PARTYMASTER
ON INVENTORY.PURCHASEORDER.SUP_CODE = SCM.PARTYMASTER.PARTYCODE
WHERE INVENTORY.PURCHASEORDER.ORDERDATE >= :date_start
AND INVENTORY.PURCHASEORDER.ORDERDATE < :date_end
GROUP BY SCM.PARTYMASTER.PARTYNAME
ORDER BY value DESC
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

    # -- date ranges: two half-open binds, values computed by nlp_execution.date_bounds --
    # The company Oracle stores business dates as VARCHAR2(8) 'YYYYMMDD'
    # (docs/ORACLE_SCHEMA_STUDY_2026-09-22.md §5); SYSDATE arithmetic against
    # them raises ORA-01861, so the model must never write it.

    def test_relative_date_requires_half_open_bind_bounds(self) -> None:
        for period in ("last six months", "last 1 month", "last month", "last 30 days"):
            plan = self.plan.model_copy(update={
                "date_range": DateRange(kind=DateRangeKind.RELATIVE, original_text=period)
            })
            grounding = ground_query_plan(plan)
            validate_grounded_sql(PURCHASE_RANKING_SQL, plan, grounding)
        for broken in (
            "po.ORDERDATE >= :date_start",                                  # upper bound missing
            "po.ORDERDATE >= :date_start\nAND po.ORDERDATE <= :date_end",   # inclusive upper bound
            "po.ORDERDATE >= :date_start\nAND po.ORDERDATE < :date_start",  # same bind twice
        ):
            sql = PURCHASE_RANKING_SQL.replace("po.ORDERDATE >= :date_start\nAND po.ORDERDATE < :date_end", broken)
            with self.assertRaisesRegex(GroundedSqlSemanticError, "date_column >= :date_start AND date_column < :date_end"):
                validate_grounded_sql(sql, self.plan, self.grounding)

    def test_date_filter_rejects_sysdate_math(self) -> None:
        for expression in (
            "po.ORDERDATE >= ADD_MONTHS(TRUNC(SYSDATE), -6)\nAND po.ORDERDATE < TRUNC(SYSDATE) + 1",
            "po.ORDERDATE >= TRUNC(SYSDATE) - 30\nAND po.ORDERDATE < TRUNC(SYSDATE) + 1",
            "po.ORDERDATE >= SYSDATE - 180\nAND po.ORDERDATE < :date_end",
        ):
            sql = PURCHASE_RANKING_SQL.replace("po.ORDERDATE >= :date_start\nAND po.ORDERDATE < :date_end", expression)
            with self.assertRaises(GroundedSqlValidationError) as ctx:
                validate_grounded_sql(sql, self.plan, self.grounding)
            self.assertRegex(str(ctx.exception), "SYSDATE|Unsupported SQL function")

    def test_absolute_date_requires_half_open_bind_bounds(self) -> None:
        plan = absolute_purchase_ranking_plan()
        grounding = ground_query_plan(plan)
        validate_grounded_sql(FULLY_QUALIFIED_PURCHASE_RANKING_SQL, plan, grounding)
        between = FULLY_QUALIFIED_PURCHASE_RANKING_SQL.replace(
            "INVENTORY.PURCHASEORDER.ORDERDATE >= :date_start\nAND INVENTORY.PURCHASEORDER.ORDERDATE < :date_end",
            "INVENTORY.PURCHASEORDER.ORDERDATE BETWEEN :date_start AND :date_end",
        )
        with self.assertRaisesRegex(GroundedSqlSemanticError, "never BETWEEN"):
            validate_grounded_sql(between, plan, grounding)

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
WHERE po.ORDERDATE >= :date_start
AND po.ORDERDATE < :date_end
GROUP BY pm.PARTYNAME
ORDER BY value DESC"""
        validate_grounded_sql(sql, plan, grounding)

    def test_unique_unqualified_columns_pass(self) -> None:
        plan = absolute_purchase_ranking_plan()
        grounding = ground_query_plan(plan)
        sql = """SELECT PARTYNAME AS supplier, SUM(NET) AS value
FROM INVENTORY.PURCHASEORDER
JOIN SCM.PARTYMASTER ON SUP_CODE = PARTYCODE
WHERE ORDERDATE >= :date_start
AND ORDERDATE < :date_end
GROUP BY PARTYNAME
ORDER BY value DESC"""
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
WHERE po.ORDERDATE >= :date_start
AND po.ORDERDATE < :date_end
GROUP BY po.SUP_CODE
ORDER BY value DESC"""
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
WHERE issue.ISSUEDATE >= :date_start
AND issue.ISSUEDATE < :date_end
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

    def test_rejects_aggregate_hidden_inside_an_allowed_function(self) -> None:
        # ROUND(SUM(...)) aggregates exactly as SUM(...) does. Detecting only
        # an outermost aggregate switched the whole ORA-00937 rule off for the
        # one wrapper a model is most likely to write (review, 2026-09-22).
        plan = purchase_ranking_plan()
        grounding = ground_query_plan(plan)
        rounded = PURCHASE_RANKING_SQL.replace(
            "SELECT pm.PARTYNAME AS supplier, SUM(po.NET) AS total_purchase_value",
            "SELECT pm.PARTYNAME AS supplier, po.ORDERDATE, ROUND(SUM(po.NET), 2) AS total_purchase_value",
        )
        with self.assertRaisesRegex(GroundedSqlSemanticError, "GROUP BY"):
            validate_grounded_sql(rounded, plan, grounding)
        # ... and the same wrapper with a matching GROUP BY stays valid.
        validate_grounded_sql(
            PURCHASE_RANKING_SQL.replace("SUM(po.NET) AS total_purchase_value",
                                         "ROUND(SUM(po.NET), 2) AS total_purchase_value"),
            plan, grounding,
        )

    def test_rejects_group_by_column_the_select_list_does_not_return(self) -> None:
        # Valid Oracle, silently wrong answer: GROUP BY supplier, ORDERDATE
        # returns one row per supplier per DAY, which the report then presents
        # as the top suppliers (review, 2026-09-22).
        plan = purchase_ranking_plan()
        with self.assertRaisesRegex(GroundedSqlSemanticError, "grain"):
            validate_grounded_sql(
                PURCHASE_RANKING_SQL.replace("GROUP BY pm.PARTYNAME", "GROUP BY pm.PARTYNAME, po.ORDERDATE"),
                plan, ground_query_plan(plan),
            )

    def test_rejects_aggregate_beside_ungrouped_column(self) -> None:
        # fix.md #7: the first real end-to-end pass produced
        # `SELECT ORDERDATE, SUM(QTY) ... ORDER BY ORDERDATE` with no GROUP BY --
        # Oracle raises ORA-00937. The check is on the SQL text, independent of
        # the plan, so any plan/prompt mistake still fails closed here.
        plan = QueryPlan(
            original_question="purchase quantity by supplier in the last six months",
            domain="purchase",
            operation="aggregate",
            business_subject=BusinessSubject(concept="purchase"),
            measures=[Measure(concept="purchase quantity", aggregation=Aggregation.SUM)],
            dimensions=[Dimension(concept="supplier", grouping=True)],
            date_range=DateRange(kind=DateRangeKind.RELATIVE, original_text="last six months"),
            requested_output=RequestedOutput(fields=["supplier", "purchase quantity"]),
            confidence=0.9,
        )
        grounding = ground_query_plan(plan)
        grouped = """SELECT pm.PARTYNAME AS supplier, po.ORDERDATE, SUM(po.QTY) AS purchase_quantity
FROM INVENTORY.PURCHASEORDER po
JOIN SCM.PARTYMASTER pm ON po.SUP_CODE = pm.PARTYCODE
WHERE po.ORDERDATE >= :date_start
AND po.ORDERDATE < :date_end
GROUP BY pm.PARTYNAME, po.ORDERDATE"""
        validate_grounded_sql(grouped, plan, grounding)
        with self.assertRaisesRegex(GroundedSqlSemanticError, "non-aggregated column in GROUP BY: po.ORDERDATE"):
            validate_grounded_sql(grouped.replace("GROUP BY pm.PARTYNAME, po.ORDERDATE", "GROUP BY pm.PARTYNAME"), plan, grounding)

    def test_rejects_missing_date_filter(self) -> None:
        self.assert_rejected(PURCHASE_RANKING_SQL.replace(
            "WHERE po.ORDERDATE >= :date_start\nAND po.ORDERDATE < :date_end", ""
        ), GroundedSqlSemanticError)

    def test_rejects_model_supplied_limit_syntax(self) -> None:
        # The company Oracle is 11.2 (no FETCH FIRST) and ROWNUM only limits
        # correctly around an ordered subquery. The model must not write any
        # limit; nlp_execution.add_execution_probe_limit wraps the validated
        # SQL with ROWNUM afterwards.
        for suffix in ("\nFETCH FIRST 10 ROWS ONLY", "\nOFFSET 5 ROWS", "\nAND ROWNUM <= 10"):
            with self.assertRaisesRegex(GroundedSqlSemanticError, "Do not write a row limit"):
                validate_grounded_sql(PURCHASE_RANKING_SQL + suffix, self.plan, self.grounding)

    def test_rejects_sort_direction_attached_to_wrong_field(self) -> None:
        sql = PURCHASE_RANKING_SQL.replace(
            "ORDER BY total_purchase_value DESC",
            "ORDER BY total_purchase_value ASC, pm.PARTYNAME DESC",
        )
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, self.plan, self.grounding)

    def test_rejects_nested_select_limit_as_outer_limit(self) -> None:
        sql = "WITH unused_rows AS (SELECT po.NET FROM INVENTORY.PURCHASEORDER po) " + PURCHASE_RANKING_SQL
        with self.assertRaises(UnsafeGroundedSqlError):
            validate_grounded_sql(sql, self.plan, self.grounding)

    def test_rejects_limit_with_oracle_specific_error(self) -> None:
        sql = PURCHASE_RANKING_SQL + "\nLIMIT 10"
        with self.assertRaisesRegex(
            OracleDialectGroundedSqlError,
            r"Oracle SQL does not support LIMIT; do not write a row limit",
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
        sql = PURCHASE_RANKING_SQL + "\nFETCH FIRST :limit ROWS ONLY"
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
        sql = PURCHASE_RANKING_SQL + "\nLIMIT :limit"
        with self.assertRaisesRegex(
            OracleDialectGroundedSqlError, "Oracle SQL does not support LIMIT",
        ):
            validate_grounded_sql(sql, self.plan, self.grounding)

    def test_to_date_rejection_names_the_bind_fix(self) -> None:
        plan = absolute_purchase_ranking_plan()
        grounding = ground_query_plan(plan)
        sql = FULLY_QUALIFIED_PURCHASE_RANKING_SQL.replace(
            ">= :date_start",
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

    def test_appended_or_of_an_already_required_column_is_rejected(self):
        # Independent-review finding: this predates value-pinning entirely --
        # duplicating one already-required column into "OR (<that column> =
        # <its own required value>)" outside the tracked AND-chain used to be
        # invisible to every check, silently widening "approved" to just
        # "APPROVALSTATUS = 1" regardless of READYFORAPPROVAL.
        plan = self._plan("mrs approved")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT M.MRSNO FROM INVENTORY.MRS_TEMP M WHERE M.MRSNO = :mrs_number "
            "AND M.APPROVALSTATUS = 1 AND M.READYFORAPPROVAL = 1 OR (M.APPROVALSTATUS = 1)"
        )
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)


class PoOrderPendingLadderTests(unittest.TestCase):
    """Value-pinned compound conditions for the SO/IA/JMD approval ladder
    (docs/ORACLE_SCHEMA_STUDY_2026-09-22.md §6.1, fix.md #4)."""

    def _plan(self, concept: str) -> QueryPlan:
        return QueryPlan(
            original_question="po pending ladder test",
            domain="purchase",
            operation="detail",
            business_subject=BusinessSubject(concept="purchase"),
            entities=[EntityReference(
                concept=concept, original_value="1", selected_value="1",
                confidence=0.9, status=EntityStatus.RESOLVED,
            )],
            confidence=0.9,
        )

    def test_pending_at_so_requires_all_three_flags_pinned_to_zero(self):
        plan = self._plan("pending at so")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT PO.SOORDERAPPROVAL FROM INVENTORY.PURCHASEORDER PO WHERE "
            "PO.SOORDERAPPROVAL = 0 AND PO.IAORDERAPPROVAL = 0 AND PO.JMDORDERAPPROVAL = 0"
        )
        validate_grounded_sql(sql, plan, grounding)

    def test_pending_at_so_with_a_wrong_pinned_value_is_rejected(self):
        plan = self._plan("pending at so")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT PO.SOORDERAPPROVAL FROM INVENTORY.PURCHASEORDER PO WHERE "
            "PO.SOORDERAPPROVAL = 1 AND PO.IAORDERAPPROVAL = 0 AND PO.JMDORDERAPPROVAL = 0"
        )
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    def test_pending_at_so_missing_a_flag_is_rejected(self):
        plan = self._plan("pending at so")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT PO.SOORDERAPPROVAL FROM INVENTORY.PURCHASEORDER PO WHERE "
            "PO.SOORDERAPPROVAL = 0 AND PO.IAORDERAPPROVAL = 0"
        )
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    def test_pending_at_ia_needs_only_so_and_ia_never_jmd(self):
        # docs/ORACLE_SCHEMA_STUDY_2026-09-22.md §6.1 never restates JMD for
        # this row -- the catalog concept has only two pinned columns, so
        # SQL that never mentions JMD at all is correct, not incomplete.
        plan = self._plan("pending at ia")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT PO.SOORDERAPPROVAL FROM INVENTORY.PURCHASEORDER PO WHERE "
            "PO.SOORDERAPPROVAL = 1 AND PO.IAORDERAPPROVAL = 0"
        )
        validate_grounded_sql(sql, plan, grounding)

    def test_pending_at_jmd_requires_all_three_with_jmd_zero(self):
        plan = self._plan("pending at jmd")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT PO.SOORDERAPPROVAL FROM INVENTORY.PURCHASEORDER PO WHERE "
            "PO.SOORDERAPPROVAL = 1 AND PO.IAORDERAPPROVAL = 1 AND PO.JMDORDERAPPROVAL = 0"
        )
        validate_grounded_sql(sql, plan, grounding)

    def test_po_approved_requires_all_three_pinned_to_one(self):
        plan = self._plan("po approved")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT PO.SOORDERAPPROVAL FROM INVENTORY.PURCHASEORDER PO WHERE "
            "PO.SOORDERAPPROVAL = 1 AND PO.IAORDERAPPROVAL = 1 AND PO.JMDORDERAPPROVAL = 1"
        )
        validate_grounded_sql(sql, plan, grounding)

    def test_po_pending_any_stage_is_an_or_of_zero_pins(self):
        plan = self._plan("po pending")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT PO.SOORDERAPPROVAL FROM INVENTORY.PURCHASEORDER PO WHERE "
            "PO.SOORDERAPPROVAL = 0 OR PO.IAORDERAPPROVAL = 0 OR PO.JMDORDERAPPROVAL = 0"
        )
        validate_grounded_sql(sql, plan, grounding)

    def test_po_pending_any_stage_using_and_instead_of_or_is_rejected(self):
        plan = self._plan("po pending")
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT PO.SOORDERAPPROVAL FROM INVENTORY.PURCHASEORDER PO WHERE "
            "PO.SOORDERAPPROVAL = 0 AND PO.IAORDERAPPROVAL = 0 AND PO.JMDORDERAPPROVAL = 0"
        )
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)


class MrsPendingAntiJoinTests(unittest.TestCase):
    """The flag-AND, value-or-null, and anti-join pieces of mrs_pending
    (Tarun's verified production query, 2026-09-23; fix.md #4)."""

    def _plan(self) -> QueryPlan:
        return QueryPlan(
            original_question="mrs pending test",
            domain="mrs",
            operation="detail",
            business_subject=BusinessSubject(concept="mrs"),
            entities=[EntityReference(
                concept="mrs pending", original_value="1", selected_value="1",
                confidence=0.9, status=EntityStatus.RESOLVED,
            )],
            confidence=0.9,
        )

    _VALID_SQL = (
        "SELECT M.MRSNO FROM INVENTORY.MRS_TEMP M "
        "LEFT JOIN INVENTORY.MRS MR ON M.MRSNO = MR.MRSNO AND M.SLNO = MR.SLNO WHERE "
        "M.REJECTIONSTATUS = 0 AND M.STORESREJECTIONSTATUS = 0 AND M.ITEMDELETE = 0 "
        "AND M.ISDELETE = 0 AND M.MRSFLAG = 1 AND (M.MILLCODE = 0 OR M.MILLCODE IS NULL) "
        "AND NVL(MR.ORDERNO, 0) = 0"
    )

    def test_verified_shape_is_valid(self):
        plan = self._plan()
        grounding = ground_query_plan(plan)
        validate_grounded_sql(self._VALID_SQL, plan, grounding)

    def test_inner_join_instead_of_left_join_is_rejected(self):
        # An INNER JOIN would silently drop every row with no matching MRS
        # order, making NVL(...)=0 always false -- the opposite of pending.
        plan = self._plan()
        grounding = ground_query_plan(plan)
        sql = self._VALID_SQL.replace("LEFT JOIN", "JOIN")
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    def test_partial_composite_join_key_is_rejected(self):
        # MRSNO alone does not uniquely match a MRS_TEMP line; dropping SLNO
        # from the join would silently change which rows count as matched.
        plan = self._plan()
        grounding = ground_query_plan(plan)
        sql = self._VALID_SQL.replace(" AND M.SLNO = MR.SLNO", "")
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    def test_is_null_instead_of_nvl_shape_is_rejected(self):
        # IS NULL on ORDERNO would be logically equivalent here, but only the
        # exact verified query shape is accepted -- not an inferred rewrite.
        plan = self._plan()
        grounding = ground_query_plan(plan)
        sql = self._VALID_SQL.replace("NVL(MR.ORDERNO, 0) = 0", "MR.ORDERNO IS NULL")
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    def test_missing_value_or_null_clause_is_rejected(self):
        plan = self._plan()
        grounding = ground_query_plan(plan)
        sql = self._VALID_SQL.replace("AND (M.MILLCODE = 0 OR M.MILLCODE IS NULL) ", "")
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    def test_millcode_or_null_accepts_either_operand_order(self):
        plan = self._plan()
        grounding = ground_query_plan(plan)
        sql = self._VALID_SQL.replace(
            "(M.MILLCODE = 0 OR M.MILLCODE IS NULL)", "(M.MILLCODE IS NULL OR M.MILLCODE = 0)"
        )
        validate_grounded_sql(sql, plan, grounding)

    def test_wrong_pinned_flag_value_is_rejected(self):
        plan = self._plan()
        grounding = ground_query_plan(plan)
        sql = self._VALID_SQL.replace("M.MRSFLAG = 1", "M.MRSFLAG = 0")
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    def test_wrong_value_or_null_value_is_rejected(self):
        plan = self._plan()
        grounding = ground_query_plan(plan)
        sql = self._VALID_SQL.replace(
            "(M.MILLCODE = 0 OR M.MILLCODE IS NULL)", "(M.MILLCODE = 5 OR M.MILLCODE IS NULL)"
        )
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    def test_appended_or_clause_cannot_widen_the_predicate(self):
        # Independent-review finding: appending "OR (<already-true thing>)"
        # after the last tracked fragment used to be invisible to every
        # check -- Oracle precedence then reads the whole WHERE as "the real
        # condition OR that other thing", matching far more rows than
        # mrs_pending should.
        plan = self._plan()
        grounding = ground_query_plan(plan)
        sql = self._VALID_SQL + " OR (M.REJECTIONSTATUS = 0)"
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)

    def test_left_join_alias_confusion_is_rejected(self):
        # Independent-review finding: a second, wrongly-shaped join to the
        # same physical table (here a plain JOIN on a partial key, alias X)
        # must not be able to supply the alias the NVL check reads from,
        # even when a separate, correctly-shaped LEFT JOIN (alias MR) also
        # exists and would satisfy the composite-key check on its own.
        plan = self._plan()
        grounding = ground_query_plan(plan)
        sql = (
            "SELECT M.MRSNO FROM INVENTORY.MRS_TEMP M "
            "JOIN INVENTORY.MRS X ON M.MRSNO = X.MRSNO "
            "LEFT JOIN INVENTORY.MRS MR ON M.MRSNO = MR.MRSNO AND M.SLNO = MR.SLNO WHERE "
            "M.REJECTIONSTATUS = 0 AND M.STORESREJECTIONSTATUS = 0 AND M.ITEMDELETE = 0 "
            "AND M.ISDELETE = 0 AND M.MRSFLAG = 1 AND (M.MILLCODE = 0 OR M.MILLCODE IS NULL) "
            "AND NVL(X.ORDERNO, 0) = 0"
        )
        with self.assertRaises(GroundedSqlSemanticError):
            validate_grounded_sql(sql, plan, grounding)


if __name__ == "__main__":
    unittest.main(verbosity=2)
