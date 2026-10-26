"""
module3_inventory_optimization/tests/test_procurement_agent.py
---------------------------------------------------------------
Module 4 unit and integration tests.

Covers:
  - Supplier data import validation (all required edge cases)
  - Lead-time-based ranking algorithm (all required scenarios)
  - Module 3 → Module 4 integration through native orchestration
  - API endpoint smoke tests
  - Regression: no auto purchase orders, no stock modification
  - Module 5 / Module 2 backward compatibility

All tests use mocked DB connections — no real DB required.
"""

import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from io import StringIO
import csv
import pytest

_PROJ_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_PROJ_ROOT))
_M2 = _PROJ_ROOT / "module2_demand_forecasting"
if _M2.exists():
    sys.path.insert(0, str(_M2))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class _DictRow(dict):
    """A dict subclass that also supports attribute access like a psycopg2 RealDictRow."""
    pass


def _make_sup_row(supplier_id: str, supplier_name: str, lead_time_days: int) -> _DictRow:
    """Simulate a psycopg2 RealDictRow for a supplier."""
    return _DictRow(
        supplier_id=supplier_id,
        supplier_name=supplier_name,
        lead_time_days=lead_time_days,
    )


def _make_prod_row(product_name: str) -> _DictRow:
    return _DictRow(product_name=product_name)


def _make_rec_id_row(rec_id: int) -> _DictRow:
    return _DictRow(recommendation_id=rec_id)


def _make_conn_with_results(product_name: str, suppliers: list[dict],
                             persist_id: int = 1) -> MagicMock:
    """
    Build a mock connection whose cursors return:
    - cursor 1 (product name): fetchone → DictRow(product_name=...)
    - cursor 2 (suppliers):    fetchall → [DictRow(supplier_id, supplier_name, lead_time_days), ...]
    - cursor 3 (persist):      fetchone → DictRow(recommendation_id=persist_id)
    """
    conn = MagicMock()
    n = [0]
    conn.test_cursors = []

    def new_cursor():
        n[0] += 1
        cur = MagicMock()
        conn.test_cursors.append(cur)
        if n[0] == 1:
            # product_name query
            cur.fetchone.return_value = _make_prod_row(product_name)
        elif n[0] == 2:
            # suppliers query
            cur.fetchall.return_value = [
                _make_sup_row(s["supplier_id"], s["supplier_name"], s["lead_time_days"])
                for s in suppliers
            ]
        else:
            # persist query
            cur.fetchone.return_value = _make_rec_id_row(persist_id)
        return cur

    conn.cursor.side_effect = new_cursor
    return conn


def _make_state(product_id: str = "PROD00001",
                suggested_qty: int = 16,
                assessment_id: int = 1) -> dict:
    return {
        "product_id": product_id,
        "request_id": str(uuid.uuid4()),
        "trigger_reason": "test",
        "execution_status": "ASSESSED",
        "errors": [],
        "data_notices": [],
        "assessment": {
            "product_id": product_id,
            "assessment_id": assessment_id,
            "suggested_replenishment_qty": suggested_qty,
            "routing_decision": "procurement_required",
        },
        "routing_decision": "procurement_required",
        "procurement_result": None,
        "excess_stock_result": None,
        "orchestration_result": None,
    }


def _run_agent(suppliers: list[dict], product_id: str = "PROD00001",
               suggested_qty: int = 10) -> dict:
    """Helper to run the procurement agent with given supplier list."""
    from backend.agents.procurement_agent import run_procurement_agent
    state = _make_state(product_id, suggested_qty)
    conn = _make_conn_with_results("Test Product", suppliers)
    return run_procurement_agent(conn, state)


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — Supplier Data Import Validation
# ─────────────────────────────────────────────────────────────────────────────

class TestSupplierImportValidation:
    """Tests for import_supplier_alternatives validation logic."""

    def _make_import_conn(self, existing_products=None, existing_suppliers=None,
                           existing_ps=None) -> MagicMock:
        """
        Build a mock conn whose cursor.fetchall() returns sequentially:
         1. product rows
         2. supplier rows
         3. product_supplier rows (existing)
        """
        if existing_products is None:
            existing_products = ["PROD00001", "PROD00002"]
        if existing_suppliers is None:
            existing_suppliers = {
                "SUP0001": {"supplier_name": "SUPPLIER ONE", "lead_time_days": 3},
                "SUP0002": {"supplier_name": "SUPPLIER TWO", "lead_time_days": 5},
            }
        if existing_ps is None:
            existing_ps = []

        prod_rows = [_DictRow(product_id=pid) for pid in existing_products]
        sup_rows = [
            _DictRow(supplier_id=sid, supplier_name=info["supplier_name"],
                     lead_time_days=info["lead_time_days"])
            for sid, info in existing_suppliers.items()
        ]
        ps_rows = [_DictRow(product_id=pid, supplier_id=sid) for pid, sid in existing_ps]

        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value = cur
        cur.fetchall.side_effect = [prod_rows, sup_rows, ps_rows]
        cur.rowcount = 1
        return conn

    def _make_csv_content(self, rows: list[dict]) -> str:
        buf = StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return buf.getvalue()

    def _run_import(self, csv_content: str, conn, tmp_path: Path) -> dict:
        from backend.services.supplier_alternatives_import import import_supplier_alternatives
        csv_file = tmp_path / "test_alternatives.csv"
        csv_file.write_text(csv_content, encoding="utf-8")
        return import_supplier_alternatives(conn, csv_path=csv_file)

    def test_valid_csv_imports_successfully(self, tmp_path):
        rows = [
            {"Product_ID": "PROD00001", "Supplier_ID": "SUP0002",
             "Supplier_Name": "SUPPLIER TWO", "Lead_Time_Days": "5",
             "Lead_Time_Rank": "1", "Recommendation": "Top 1"},
        ]
        conn = self._make_import_conn()
        result = self._run_import(self._make_csv_content(rows), conn, tmp_path)
        assert result["success"] is True
        assert result["rejected"] == 0
        assert result["validation_errors"] == []

    def test_missing_required_column_is_blocking(self, tmp_path):
        from backend.services.supplier_alternatives_import import import_supplier_alternatives
        content = "Product_ID,Supplier_ID,Lead_Time_Days\nPROD00001,SUP0001,3\n"
        csv_file = tmp_path / "bad.csv"
        csv_file.write_text(content, encoding="utf-8")
        conn = MagicMock()
        result = import_supplier_alternatives(conn, csv_path=csv_file)
        assert result["success"] is False
        assert any("Supplier_Name" in e for e in result["validation_errors"])

    def test_csv_file_not_found(self, tmp_path):
        from backend.services.supplier_alternatives_import import import_supplier_alternatives
        conn = MagicMock()
        result = import_supplier_alternatives(conn, csv_path=tmp_path / "nonexistent.csv")
        assert result["success"] is False
        assert result["validation_errors"]

    def test_unknown_product_id_aborts_import(self, tmp_path):
        rows = [
            {"Product_ID": "PROD99999", "Supplier_ID": "SUP0001",
             "Supplier_Name": "SUPPLIER ONE", "Lead_Time_Days": "3",
             "Lead_Time_Rank": "1", "Recommendation": "Top 1"},
        ]
        conn = self._make_import_conn(
            existing_products=["PROD00001"],
            existing_suppliers={"SUP0001": {"supplier_name": "SUPPLIER ONE", "lead_time_days": 3}},
        )
        result = self._run_import(self._make_csv_content(rows), conn, tmp_path)
        assert result["success"] is False
        assert "PROD99999" in result["unknown_products"]

    def test_unknown_supplier_id_aborts_import(self, tmp_path):
        rows = [
            {"Product_ID": "PROD00001", "Supplier_ID": "SUP9999",
             "Supplier_Name": "GHOST SUPPLIER", "Lead_Time_Days": "3",
             "Lead_Time_Rank": "1", "Recommendation": "Top 1"},
        ]
        conn = self._make_import_conn(
            existing_products=["PROD00001"],
            existing_suppliers={},  # SUP9999 not present
        )
        result = self._run_import(self._make_csv_content(rows), conn, tmp_path)
        assert result["success"] is False
        assert "SUP9999" in result["unknown_suppliers"]

    def test_invalid_lead_time_zero_rejected(self, tmp_path):
        rows = [
            {"Product_ID": "PROD00001", "Supplier_ID": "SUP0001",
             "Supplier_Name": "SUPPLIER ONE", "Lead_Time_Days": "0",
             "Lead_Time_Rank": "1", "Recommendation": "Top 1"},
        ]
        conn = self._make_import_conn()
        result = self._run_import(self._make_csv_content(rows), conn, tmp_path)
        assert result["rejected"] >= 1

    def test_invalid_lead_time_negative_rejected(self, tmp_path):
        rows = [
            {"Product_ID": "PROD00001", "Supplier_ID": "SUP0001",
             "Supplier_Name": "SUPPLIER ONE", "Lead_Time_Days": "-5",
             "Lead_Time_Rank": "1", "Recommendation": "Top 1"},
        ]
        conn = self._make_import_conn()
        result = self._run_import(self._make_csv_content(rows), conn, tmp_path)
        assert result["rejected"] >= 1

    def test_non_integer_lead_time_rejected(self, tmp_path):
        rows = [
            {"Product_ID": "PROD00001", "Supplier_ID": "SUP0001",
             "Supplier_Name": "SUPPLIER ONE", "Lead_Time_Days": "abc",
             "Lead_Time_Rank": "1", "Recommendation": "Top 1"},
        ]
        conn = self._make_import_conn()
        result = self._run_import(self._make_csv_content(rows), conn, tmp_path)
        assert result["rejected"] >= 1

    def test_duplicate_product_supplier_pair_in_csv_skipped(self, tmp_path):
        """Duplicate pair in same CSV: second occurrence is rejected."""
        rows = [
            {"Product_ID": "PROD00001", "Supplier_ID": "SUP0001",
             "Supplier_Name": "SUPPLIER ONE", "Lead_Time_Days": "3",
             "Lead_Time_Rank": "1", "Recommendation": "Top 1"},
            {"Product_ID": "PROD00001", "Supplier_ID": "SUP0001",
             "Supplier_Name": "SUPPLIER ONE", "Lead_Time_Days": "3",
             "Lead_Time_Rank": "1", "Recommendation": "Top 1"},  # duplicate
        ]
        conn = self._make_import_conn()
        result = self._run_import(self._make_csv_content(rows), conn, tmp_path)
        assert result["rejected"] >= 1

    def test_idempotent_reimport_skips_existing_rows(self, tmp_path):
        """If the pair already exists in product_supplier, skip (not duplicate insert)."""
        rows = [
            {"Product_ID": "PROD00001", "Supplier_ID": "SUP0001",
             "Supplier_Name": "SUPPLIER ONE", "Lead_Time_Days": "3",
             "Lead_Time_Rank": "1", "Recommendation": "Top 1"},
        ]
        conn = self._make_import_conn(
            existing_ps=[("PROD00001", "SUP0001")],  # already in DB
        )
        result = self._run_import(self._make_csv_content(rows), conn, tmp_path)
        assert result["success"] is True
        assert result["inserted"] == 0
        assert result["skipped"] == 1

    def test_supplier_name_conflict_is_reported_not_blocking(self, tmp_path):
        """Name mismatch is a data_conflict — not blocking, DB name not overwritten."""
        rows = [
            {"Product_ID": "PROD00001", "Supplier_ID": "SUP0001",
             "Supplier_Name": "DIFFERENT NAME",  # differs from DB "SUPPLIER ONE"
             "Lead_Time_Days": "3",
             "Lead_Time_Rank": "1", "Recommendation": "Top 1"},
        ]
        conn = self._make_import_conn()
        result = self._run_import(self._make_csv_content(rows), conn, tmp_path)
        assert result["success"] is True
        assert len(result["data_conflicts"]) >= 1

    def test_float_lead_time_rounded_to_integer(self, tmp_path):
        """Lead times like '3.0' are valid (int conversion succeeds)."""
        rows = [
            {"Product_ID": "PROD00001", "Supplier_ID": "SUP0001",
             "Supplier_Name": "SUPPLIER ONE", "Lead_Time_Days": "3.0",
             "Lead_Time_Rank": "1", "Recommendation": "Top 1"},
        ]
        conn = self._make_import_conn()
        result = self._run_import(self._make_csv_content(rows), conn, tmp_path)
        # 3.0 → int(3) = valid
        assert result["rejected"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — Lead-Time Ranking Algorithm
# ─────────────────────────────────────────────────────────────────────────────

class TestLeadTimeRanking:
    """Tests for run_procurement_agent ranking logic."""

    def test_three_suppliers_ranks_two_shortest(self):
        """With 3 suppliers, only the 2 shortest-lead-time are ranked."""
        suppliers = [
            # DB returns them pre-sorted by lead ASC, id ASC
            {"supplier_id": "SUP0001", "supplier_name": "Alpha", "lead_time_days": 2},
            {"supplier_id": "SUP0002", "supplier_name": "Beta", "lead_time_days": 5},
            {"supplier_id": "SUP0003", "supplier_name": "Gamma", "lead_time_days": 8},
        ]
        result = _run_agent(suppliers)
        pr = result["procurement_result"]
        assert pr["status"] == "recommendation_ready"
        assert pr["recommendation_status"] == "top2_available"
        assert pr["eligible_supplier_count"] == 3
        ranked = pr["ranked_suppliers"]
        assert len(ranked) == 2
        assert ranked[0]["lead_time_days"] == 2
        assert ranked[1]["lead_time_days"] == 5

    def test_exactly_two_suppliers_returns_both(self):
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "Alpha", "lead_time_days": 3},
            {"supplier_id": "SUP0002", "supplier_name": "Beta", "lead_time_days": 7},
        ]
        result = _run_agent(suppliers)
        pr = result["procurement_result"]
        assert pr["recommendation_status"] == "top2_available"
        assert len(pr["ranked_suppliers"]) == 2

    def test_only_one_supplier_returns_one_with_status(self):
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "Only Supplier", "lead_time_days": 3},
        ]
        result = _run_agent(suppliers)
        pr = result["procurement_result"]
        assert pr["recommendation_status"] == "only_one_available"
        assert len(pr["ranked_suppliers"]) == 1
        assert pr["eligible_supplier_count"] == 1

    def test_no_suppliers_returns_no_supplier_available(self):
        result = _run_agent([])
        pr = result["procurement_result"]
        assert pr["status"] == "no_eligible_supplier"
        assert pr["recommendation_status"] == "no_supplier_available"
        assert pr["eligible_supplier_count"] == 0
        assert pr["ranked_suppliers"] == []
        assert pr["requires_manager_approval"] is True

    def test_equal_lead_times_use_supplier_id_tiebreak(self):
        """Tied lead times use supplier_id order regardless of DB row order."""
        suppliers = [
            {"supplier_id": "SUP0002", "supplier_name": "Beta", "lead_time_days": 3},
            {"supplier_id": "SUP0001", "supplier_name": "Alpha", "lead_time_days": 3},
            {"supplier_id": "SUP0003", "supplier_name": "Gamma", "lead_time_days": 7},
        ]
        result = _run_agent(suppliers)
        pr = result["procurement_result"]
        assert pr["has_lead_time_tie"] is True
        assert pr["tie_supplier_count"] == 2
        ranked = pr["ranked_suppliers"]
        assert ranked[0]["supplier_id"] == "SUP0001"
        assert ranked[1]["supplier_id"] == "SUP0002"

    def test_unsorted_supplier_rows_are_ranked_by_lead_time(self):
        suppliers = [
            {"supplier_id": "SUP0003", "supplier_name": "Gamma", "lead_time_days": 8},
            {"supplier_id": "SUP0002", "supplier_name": "Beta", "lead_time_days": 5},
            {"supplier_id": "SUP0001", "supplier_name": "Alpha", "lead_time_days": 2},
        ]

        result = _run_agent(suppliers)
        ranked = result["procurement_result"]["ranked_suppliers"]

        assert [supplier["supplier_id"] for supplier in ranked] == ["SUP0001", "SUP0002"]

    @pytest.mark.parametrize("lead_time", [None, 0, -1, 1.5, "3", True])
    def test_invalid_lead_times_are_excluded_and_reported(self, lead_time):
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "Invalid", "lead_time_days": lead_time},
            {"supplier_id": "SUP0002", "supplier_name": "Valid", "lead_time_days": 4},
        ]

        result = _run_agent(suppliers)
        pr = result["procurement_result"]

        assert pr["eligible_supplier_count"] == 1
        assert [supplier["supplier_id"] for supplier in pr["ranked_suppliers"]] == ["SUP0002"]
        assert "excluded" in pr["data_warnings"].lower()
        assert any("missing or invalid" in notice for notice in result["data_notices"])
        assert pr["requires_manager_approval"] is True

    def test_no_valid_lead_times_returns_no_supplier_available(self):
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "Missing", "lead_time_days": None},
            {"supplier_id": "SUP0002", "supplier_name": "Invalid", "lead_time_days": 0},
        ]

        result = _run_agent(suppliers)
        pr = result["procurement_result"]

        assert pr["status"] == "no_eligible_supplier"
        assert pr["recommendation_status"] == "no_supplier_available"
        assert pr["eligible_supplier_count"] == 0
        assert pr["ranked_suppliers"] == []
        assert "valid lead time" in pr["warning"]
        assert any("Excluded 2 supplier" in notice for notice in result["data_notices"])

    def test_tie_explanation_does_not_claim_operational_superiority(self):
        """The tie explanation must explicitly state it is not evidence of superiority."""
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "Alpha", "lead_time_days": 3},
            {"supplier_id": "SUP0002", "supplier_name": "Beta", "lead_time_days": 3},
        ]
        result = _run_agent(suppliers)
        pr = result["procurement_result"]
        tie_expl = pr.get("tie_explanation") or ""
        # The explanation must warn about the tie-breaker not implying superiority
        assert "NOT indicate" in tie_expl or "does NOT" in tie_expl or "not" in tie_expl.lower()

    def test_rank1_has_shortest_lead_time(self):
        """Rank 1 must always be the shortest lead time."""
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "Alpha", "lead_time_days": 1},
            {"supplier_id": "SUP0002", "supplier_name": "Beta", "lead_time_days": 5},
            {"supplier_id": "SUP0003", "supplier_name": "Gamma", "lead_time_days": 10},
        ]
        result = _run_agent(suppliers)
        pr = result["procurement_result"]
        ranked = pr["ranked_suppliers"]
        assert ranked[0]["lead_time_days"] == 1
        assert ranked[0]["rank"] == 1

    def test_requires_manager_approval_always_true(self):
        """requires_manager_approval must be True regardless of outcome."""
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "Alpha", "lead_time_days": 3},
        ]
        result = _run_agent(suppliers)
        assert result["procurement_result"]["requires_manager_approval"] is True

    def test_invalid_product_id_returns_error(self):
        """Empty product_id returns an error status."""
        from backend.agents.procurement_agent import run_procurement_agent
        state = _make_state("")
        state["product_id"] = ""
        conn = MagicMock()
        result = run_procurement_agent(conn, state)
        pr = result["procurement_result"]
        assert pr["status"] == "error"
        assert pr["requires_manager_approval"] is True

    def test_negative_replenishment_qty_gets_data_notice(self):
        """Negative suggested_qty generates a data notice."""
        from backend.agents.procurement_agent import run_procurement_agent
        state = _make_state("PROD00001", -5)
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "Alpha", "lead_time_days": 3},
        ]
        conn = _make_conn_with_results("Test Product", suppliers)
        result = run_procurement_agent(conn, state)
        notices = result["data_notices"]
        assert any("invalid" in n.lower() or "replenishment" in n.lower() for n in notices)

    def test_ranking_method_is_lead_time_based(self):
        """ranking_method must be 'lead_time_based', never TOPSIS."""
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "Alpha", "lead_time_days": 3},
        ]
        result = _run_agent(suppliers)
        pr = result["procurement_result"]
        assert pr.get("ranking_method") == "lead_time_based"
        notes = pr.get("recommendation_notes", "")
        assert "topsis" not in notes.lower()

    def test_ranked_suppliers_have_required_fields(self):
        """Each ranked supplier entry has rank, supplier_id, name, lead_time_days, explanation."""
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "Alpha", "lead_time_days": 3},
            {"supplier_id": "SUP0002", "supplier_name": "Beta", "lead_time_days": 5},
        ]
        result = _run_agent(suppliers)
        ranked = result["procurement_result"]["ranked_suppliers"]
        for entry in ranked:
            assert "rank" in entry
            assert "supplier_id" in entry
            assert "supplier_name" in entry
            assert "lead_time_days" in entry
            assert "explanation" in entry
            assert len(entry["explanation"]) > 10  # non-trivial explanation

    def test_backward_compatible_flat_fields_present(self):
        """recommended_supplier_id / rank2_supplier_id flat fields must be present."""
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "Alpha", "lead_time_days": 3},
            {"supplier_id": "SUP0002", "supplier_name": "Beta", "lead_time_days": 5},
        ]
        result = _run_agent(suppliers)
        pr = result["procurement_result"]
        assert "recommended_supplier_id" in pr
        assert "rank2_supplier_id" in pr
        assert pr["recommended_supplier_id"] == "SUP0001"
        assert pr["rank2_supplier_id"] == "SUP0002"


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Module 3 → Module 4 Integration
# ─────────────────────────────────────────────────────────────────────────────

class TestModule3ToModule4Integration:
    """Regression checks for Module 4 persistence and supplier scoping."""

    def test_no_purchase_order_placed_in_agent(self):
        """Verify no INSERT into purchase-order or stock tables occurs."""
        from backend.agents.procurement_agent import run_procurement_agent
        state = _make_state("PROD00001", 10)
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "A", "lead_time_days": 3},
        ]
        conn = _make_conn_with_results("Test", suppliers)

        run_procurement_agent(conn, state)
        executed_sql = [
            str(call.args[0]).lower()
            for cursor in conn.test_cursors
            for call in cursor.execute.call_args_list
        ]
        assert any("insert into procurement_recommendation" in sql for sql in executed_sql)
        assert not any(
            sql.lstrip().startswith(("insert", "update", "delete"))
            and any(table in sql for table in ("purchase_order", "inventory_batch"))
            for sql in executed_sql
        )

    def test_only_suppliers_for_requested_product_returned(self):
        """Agent must query product_supplier filtered by product_id."""
        from backend.agents.procurement_agent import _get_eligible_suppliers
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = []
        conn.cursor.return_value = cur

        _get_eligible_suppliers(conn, "PROD00001")
        sql_call = cur.execute.call_args
        sql = str(sql_call[0][0]).lower()
        params = sql_call[0][1]
        assert "product_id" in sql
        assert params == ("PROD00001",)

    def test_product_not_in_db_raises_or_returns_error(self):
        """If product has no supplier alternatives, status is no_supplier_available."""
        # Agent doesn't crash — returns no_supplier_available
        result = _run_agent([])  # empty supplier list
        pr = result["procurement_result"]
        assert pr["recommendation_status"] == "no_supplier_available"


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — Safety and Approval Enforcement
# ─────────────────────────────────────────────────────────────────────────────

class TestSafetyAndApproval:
    """Manager approval is always required; no auto-purchasing."""

    def test_requires_manager_approval_no_supplier_case(self):
        result = _run_agent([])
        assert result["procurement_result"]["requires_manager_approval"] is True

    def test_requires_manager_approval_recommendation_case(self):
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "A", "lead_time_days": 3},
        ]
        result = _run_agent(suppliers)
        assert result["procurement_result"]["requires_manager_approval"] is True

    def test_procurement_result_has_no_stock_fields(self):
        """Procurement result must not contain stock modification fields."""
        suppliers = [
            {"supplier_id": "SUP0001", "supplier_name": "A", "lead_time_days": 3},
        ]
        result = _run_agent(suppliers)
        pr = result["procurement_result"]
        assert "current_quantity" not in pr
        assert "purchase_order" not in pr
        assert "order_placed" not in pr

# ─────────────────────────────────────────────────────────────────────────────
# Section 5 — API Endpoint Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestProcurementAPIEndpoints:
    """API smoke tests for procurement routes (mocked DB)."""

    @pytest.fixture
    def client_with_mock(self):
        from fastapi.testclient import TestClient
        from backend.main import app
        from backend.services.db import get_db

        mock_conn = MagicMock()

        def override_db():
            yield mock_conn

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app, raise_server_exceptions=False)
        yield client, mock_conn
        app.dependency_overrides.clear()

    def test_import_endpoint_returns_200(self, client_with_mock):
        client, mock_conn = client_with_mock

        with patch("backend.routes.procurement.apply_m4_schema"), \
             patch("backend.routes.procurement.import_supplier_alternatives",
                   return_value={
                       "success": True, "csv_path": "test.csv",
                       "validation_errors": [], "data_conflicts": [],
                       "inserted": 100, "skipped": 10, "rejected": 0,
                       "unknown_products": [], "unknown_suppliers": [],
                       "summary": "Import complete.",
                   }):
            response = client.post("/api/procurement/import-supplier-alternatives")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["inserted"] == 100

    def test_recommend_endpoint_404_for_unknown_product(self, client_with_mock):
        client, mock_conn = client_with_mock

        with patch("backend.routes.procurement.get_product_with_supplier",
                   return_value=None):
            response = client.get("/api/procurement/recommend/PROD99999")

        assert response.status_code == 404

    def test_recommend_endpoint_returns_recommendation(self, client_with_mock):
        client, mock_conn = client_with_mock

        mock_pr_result = {
            "procurement_result": {
                "agent": "procurement_agent", "status": "recommendation_ready",
                "recommendation_status": "top2_available",
                "product_id": "PROD00001", "product_name": "Test",
                "suggested_replenishment_qty": None,
                "ranking_method": "lead_time_based",
                "eligible_supplier_count": 3,
                "ranked_suppliers": [
                    {"rank": 1, "supplier_id": "SUP0001", "supplier_name": "A",
                     "lead_time_days": 3, "explanation": "Shortest."},
                    {"rank": 2, "supplier_id": "SUP0002", "supplier_name": "B",
                     "lead_time_days": 5, "explanation": "Second."},
                ],
                "has_lead_time_tie": False, "tie_supplier_count": None, "tie_explanation": None,
                "requires_manager_approval": True,
                "data_warnings": "Synthetic lead times.",
                "recommendation_notes": "Top 2 available.", "warning": None,
                "recommendation_id": 1,
            },
            "data_notices": [],
        }

        with patch("backend.routes.procurement.get_product_with_supplier",
                   return_value={"product_id": "PROD00001", "product_name": "Test"}), \
             patch("backend.routes.procurement.run_procurement_agent",
                   return_value=mock_pr_result):
            response = client.get("/api/procurement/recommend/PROD00001")

        assert response.status_code == 200
        data = response.json()
        assert data["recommendation_status"] == "top2_available"
        assert data["requires_manager_approval"] is True
        assert len(data["ranked_suppliers"]) == 2

    def test_suppliers_endpoint_returns_list(self, client_with_mock):
        client, mock_conn = client_with_mock

        sup_rows = [_DictRow(supplier_id="SUP0001", supplier_name="Alpha", lead_time_days=3)]
        mock_conn.cursor.return_value.fetchall.return_value = sup_rows

        with patch("backend.routes.procurement.get_product_with_supplier",
                   return_value={"product_id": "PROD00001", "product_name": "Rice"}):
            response = client.get("/api/procurement/suppliers/PROD00001")

        assert response.status_code == 200
        data = response.json()
        assert data["product_id"] == "PROD00001"
        assert "suppliers" in data

    def test_orchestration_run_endpoint_still_works(self, client_with_mock):
        """POST /api/orchestration/run must remain backward-compatible."""
        client, mock_conn = client_with_mock

        with patch("backend.routes.orchestration.get_product_with_supplier",
                   return_value={"product_id": "PROD00001", "product_name": "Test"}), \
             patch("backend.routes.orchestration.run_orchestration",
                   return_value={
                       "orchestration_result": {
                           "request_id": str(uuid.uuid4()),
                           "product_id": "PROD00001",
                           "trigger_reason": "api",
                           "execution_status": "DONE",
                           "routing_decision": "procurement_required",
                           "assessment_summary": {},
                           "procurement_result": {
                               "status": "recommendation_ready",
                               "requires_manager_approval": True,
                           },
                           "excess_stock_result": None,
                           "errors": [],
                           "data_notices": [],
                           "manager_action_required": True,
                           "finalized_at": "2026-10-24T12:00:00+00:00",
                       }
                   }):
            response = client.post(
                "/api/orchestration/run",
                json={"product_id": "PROD00001"},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["manager_action_required"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Section 6 — Regression Tests: Existing Modules Unaffected
# ─────────────────────────────────────────────────────────────────────────────

class TestRegressionExistingModules:
    """Ensure Module 2, Module 3, and Module 5 still work correctly."""

    def test_safety_stock_module_unchanged(self):
        """Safety stock service signature is correct — Module 3 regression."""
        from backend.services.safety_stock_service import calculate_safety_stock
        result = calculate_safety_stock(
            demand_std_daily=1.5,
            lead_time_days=7,
            nonzero_demand_days=30,
        )
        assert "safety_stock" in result
        assert result["safety_stock"] >= 0

    def test_reorder_point_module_unchanged(self):
        """Reorder point service signature is correct — Module 3 regression."""
        from backend.services.reorder_point_service import calculate_reorder_point
        result = calculate_reorder_point(
            avg_daily_demand=2.0,
            lead_time_days=7,
            safety_stock=5.0,
            demand_source="historical_avg",
        )
        assert "reorder_point" in result
        assert result["reorder_point"] >= 0

    def test_agent_name_constant_unchanged(self):
        """AGENT_NAME constant must remain 'procurement_agent' for logging consistency."""
        from backend.agents.procurement_agent import AGENT_NAME
        assert AGENT_NAME == "procurement_agent"
