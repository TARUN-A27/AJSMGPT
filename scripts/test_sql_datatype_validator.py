from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import app.sql_datatype_validator as dv
from app.oracle_client import run_safe_select

PO_SQL = "SELECT * FROM INVENTORY.PURCHASEORDER PO WHERE PO.SUP_CODE = :sup_code"
QTY_SQL = "SELECT * FROM INVENTORY.PURCHASEORDER PO WHERE PO.QTY = :qty"
DATE_SQL = (
    "SELECT * FROM INVENTORY.PURCHASEORDER PO "
    "WHERE PO.ORDERDATE BETWEEN :date_from AND :date_to"
)
UNKNOWN_SQL = "SELECT * FROM INVENTORY.PURCHASEORDER PO WHERE PO.APP_STATUS = :status"
MRS_NUMBER_SQL = "SELECT * FROM INVENTORY.MRS_TEMP M WHERE M.MRSNO = :mrs_number"
MRS_DUE_DATE_SQL = "SELECT * FROM INVENTORY.MRS_TEMP M WHERE M.DUEDATE BETWEEN :date_from AND :date_to"
MRS_REJECTION_REASON_SQL = "SELECT M.REASONFORREJECTIONSTORES FROM INVENTORY.MRS_TEMP M WHERE M.MRSNO = :mrs_number"


class OfflineDatatypeValidationTests(unittest.TestCase):
    def test_mrs_number_numeric_bind_accepted(self) -> None:
        dv.validate_sql_datatypes(MRS_NUMBER_SQL, {"mrs_number": 890330})

    def test_mrs_number_text_bind_rejected(self) -> None:
        with self.assertRaises(ValueError):
            dv.validate_sql_datatypes(MRS_NUMBER_SQL, {"mrs_number": "890330-A"})

    def test_mrs_due_date_bind_accepted(self) -> None:
        # Business dates are VARCHAR2(8) 'YYYYMMDD' in the company database
        # (catalog datatype_category DATE_TEXT): only 8-digit strings bind.
        dv.validate_sql_datatypes(MRS_DUE_DATE_SQL, {"date_from": "20260101", "date_to": "20270101"})

    def test_mrs_rejection_reason_is_text_not_a_status_filter(self) -> None:
        # REASONFORREJECTIONSTORES is a plain text display column; it is only
        # ever selected here, never compared/filtered, so no status semantics
        # are being asserted or invented.
        dv.validate_sql_datatypes(MRS_REJECTION_REASON_SQL, {"mrs_number": 890330})

    def test_valid_text_bind(self) -> None:
        dv.validate_sql_datatypes(PO_SQL, {"sup_code": "SUP001"})

    def test_valid_numeric_bind(self) -> None:
        dv.validate_sql_datatypes(QTY_SQL, {"qty": 10})

    def test_valid_date_bind(self) -> None:
        dv.validate_sql_datatypes(DATE_SQL, {"date_from": "20260101", "date_to": "20270101"})

    def test_date_text_rejects_python_date_bind(self) -> None:
        # A Python date would be sent as an Oracle DATE and compared against
        # 'YYYYMMDD' text under NLS DD-MON-RR: ORA-01861 or a wrong answer.
        with self.assertRaisesRegex(ValueError, "DATE_TEXT"):
            dv.validate_sql_datatypes(DATE_SQL, {"date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)})
        with self.assertRaisesRegex(ValueError, "DATE_TEXT"):
            dv.validate_sql_datatypes(DATE_SQL, {"date_from": "2026-01-01", "date_to": "2026-12-31"})

    def test_fully_qualified_date_column_still_rejects_a_python_date_bind(self) -> None:
        # The bind scan used to consume INVENTORY.PURCHASEORDER as schema.table
        # and never see .ORDERDATE, so the guard silently did not run on the
        # fully qualified spelling the SQL validator explicitly supports.
        sql = (
            "SELECT INVENTORY.PURCHASEORDER.NET FROM INVENTORY.PURCHASEORDER "
            "WHERE INVENTORY.PURCHASEORDER.ORDERDATE >= :date_from "
            "AND INVENTORY.PURCHASEORDER.ORDERDATE < :date_to"
        )
        with self.assertRaisesRegex(ValueError, "DATE_TEXT"):
            dv.validate_sql_datatypes(sql, {"date_from": date(2026, 1, 1), "date_to": date(2026, 12, 31)})
        dv.validate_sql_datatypes(sql, {"date_from": "20260101", "date_to": "20261231"})

    def test_like_on_a_date_text_column_is_rejected(self) -> None:
        # A YYYYMMDD column is text, so LIKE '2026%' is legal SQL -- and an
        # unbounded date filter the half-open bind rule never sees.
        with self.assertRaisesRegex(ValueError, "LIKE"):
            dv.validate_sql_datatypes(
                "SELECT po.NET FROM INVENTORY.PURCHASEORDER po WHERE po.ORDERDATE LIKE '2026%'"
            )

    def test_invalid_text_to_numeric_bind(self) -> None:
        with self.assertRaises(ValueError):
            dv.validate_sql_datatypes(QTY_SQL, {"qty": "not-a-number"})

    def test_invalid_numeric_to_date_bind(self) -> None:
        with self.assertRaises(ValueError):
            dv.validate_sql_datatypes(
                DATE_SQL, {"date_from": 20260101, "date_to": 20261231}
            )

    def test_missing_datatype_metadata_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            dv.validate_sql_datatypes(UNKNOWN_SQL, {"status": "X"})

    def test_relative_date_v1_query_still_validates(self) -> None:
        sql = (
            "SELECT PO.SUP_CODE, SUM(PO.NET) FROM INVENTORY.PURCHASEORDER PO "
            "WHERE PO.ORDERDATE BETWEEN :date_from AND :date_to "
            "GROUP BY PO.SUP_CODE"
        )
        dv.validate_sql_datatypes(sql, {"date_from": "20260601", "date_to": "20260901"})

    def test_no_live_oracle_metadata_lookup(self) -> None:
        self.assertFalse(hasattr(dv, "oracledb"))
        self.assertFalse(hasattr(dv, "_get_connection"))

        source = Path(dv.__file__).read_text()
        forbidden = ["ALL_TAB_COLUMNS", "oracledb", "_get_connection", "get_connection"]
        lowered = source.lower()
        for term in forbidden:
            self.assertNotIn(
                term.lower(), lowered, f"forbidden live-metadata term {term!r} found in {dv.__file__}"
            )

    def test_real_loader_gives_no_implicit_text_fallback(self) -> None:
        # Exercises the real _load_offline_column_categories() against a
        # temporary catalog fixture; no mocking of the loader itself.
        fixture = {
            "concepts": [
                {
                    "name": "unclassified_dimension",
                    "columns": [
                        {
                            "table": "SOME.TABLE",
                            "column": "SOME_COLUMN",
                            "roles": ["dimension"],
                        }
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            fixture_path = Path(tmp_dir) / "catalog.json"
            fixture_path.write_text(json.dumps(fixture))

            dv._load_offline_column_categories.cache_clear()
            with patch.object(dv, "CATALOG_PATH", fixture_path):
                try:
                    categories = dv._load_offline_column_categories()
                    self.assertNotEqual(
                        categories.get(("SOME", "TABLE", "SOME_COLUMN")), dv.TEXT
                    )
                    self.assertIsNone(categories.get(("SOME", "TABLE", "SOME_COLUMN")))
                finally:
                    dv._load_offline_column_categories.cache_clear()

    def test_role_only_column_without_explicit_category_fails_closed(self) -> None:
        # DEPT_NAME carries only grouping/entity_filter roles; it is only
        # validated because the catalog now declares it explicitly as TEXT.
        # Strip that declaration to prove the loader no longer defaults to TEXT.
        with patch.object(dv, "_load_offline_column_categories") as mock_load:
            mock_load.return_value = {}
            sql = "SELECT * FROM INVENTORY.DEPT D WHERE D.DEPT_NAME = :dept_name"
            with self.assertRaises(ValueError):
                dv.validate_sql_datatypes(sql, {"dept_name": "STORES"})

    def test_explicitly_declared_text_column_valid_bind(self) -> None:
        sql = "SELECT * FROM INVENTORY.DEPT D WHERE D.DEPT_NAME = :dept_name"
        dv.validate_sql_datatypes(sql, {"dept_name": "STORES"})

    def test_previously_misclassified_numeric_column_now_rejects_text(self) -> None:
        # STATUS and DEPT_CODE are NUMBER in Oracle (data/inventory_schema_metadata.json)
        # but only carried non-explanatory roles; they were silently defaulted
        # to TEXT before. They must now be explicit NUMERIC and reject text.
        sql = "SELECT * FROM INVENTORY.ISSUE I WHERE I.STATUS = :status"
        with self.assertRaises(ValueError):
            dv.validate_sql_datatypes(sql, {"status": "PENDING"})
        dv.validate_sql_datatypes(sql, {"status": 1})

    def test_invalid_datatype_causes_zero_runner_calls(self) -> None:
        with patch("app.oracle_client.get_connection") as mock_get_connection:
            with self.assertRaises(ValueError):
                run_safe_select(QTY_SQL, binds={"qty": "not-a-number"})
            mock_get_connection.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
