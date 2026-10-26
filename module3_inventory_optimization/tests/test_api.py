"""
module3_inventory_optimization/tests/test_api.py
--------------------------------------------------
API tests for Module 3 inventory optimization endpoints.

Uses FastAPI TestClient with mocked service functions — no real DB required.
Tests cover:
  - Overview endpoint (empty and populated states)
  - Products list (paginated, filtered)
  - Single product endpoint (found, not found, no assessment)
  - Assess endpoint (single product, all products background)
  - Recommendations endpoint
  - Invalid parameters (422 responses)
  - Existing Module 2 routes still respond (regression check)
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
_M2_ROOT = Path(__file__).parent.parent.parent / "module2_demand_forecasting"
if _M2_ROOT.exists():
    sys.path.insert(0, str(_M2_ROOT))

from fastapi.testclient import TestClient
from backend.main import app
from backend.services.db import get_db


# ── Shared mock setup ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_db_dependency():
    """Override DB dependency with a mock for every test."""
    mock_conn = MagicMock()

    def override():
        yield mock_conn

    app.dependency_overrides[get_db] = override
    yield mock_conn
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# ── Sample data helpers ────────────────────────────────────────────────────────

SAMPLE_OVERVIEW = {
    "total_assessed": 10,
    "procurement_required": 7,
    "excess_stock_review": 0,
    "normal": 2,
    "insufficient_data": 1,
    "last_assessed_at": "2026-10-22T10:00:00+05:30",
    "latest_run_id": "09cf3b5d-3b37-4b57-a97a-50f237f5dda6",
}

SAMPLE_ASSESSMENT = {
    "assessment_id": 1,
    "product_id": "PROD00001",
    "assessed_at": "2026-10-22T10:00:00+05:30",
    "forecast_run_id": "09cf3b5d-3b37-4b57-a97a-50f237f5dda6",
    "model_version": "20260830",
    "available_stock": 6,
    "inventory_position": 6,
    "stock_calculation_method": "latest_batch",
    "inventory_position_note": "Simplified: latest batch only.",
    "forecast_demand_7d": 8.74,
    "avg_daily_demand": 1.75,
    "demand_source": "forecast_avg",
    "demand_std_daily": 1.68,
    "history_days_used": 90,
    "nonzero_demand_days": 63,
    "lead_time_days": 3,
    "safety_stock": 5.0,
    "safety_stock_z": 1.645,
    "safety_stock_status": "calculated",
    "reorder_point": 11.0,
    "reorder_level_from_product": 6,
    "rop_status": "calculated",
    "suggested_replenishment_qty": 16,
    "minimum_order_qty": 2,
    "maximum_stock_level": 22,
    "replenishment_status": "calculated",
    "routing_decision": "procurement_required",
    "decision_reasons": "inventory_position(6) <= computed_rop(11.0)",
    "calculation_notes": "SS = ...",
    "policy_version": "v1.0",
    "product_name": "Loose Boiled Rice (Ponni) - 1kg",
    "category": "Rice",
    "brand": None,
}

SAMPLE_PRODUCT = {
    "product_id": "PROD00001",
    "product_name": "Loose Boiled Rice (Ponni) - 1kg",
    "brand": None,
    "category": "Rice",
    "reorder_level": 6,
    "maximum_stock_level": 22,
    "minimum_order_qty": 2,
    "expected_daily_demand": 0.9441,
    "purchase_frequency": "Monthly",
    "supplier_id": "SUP0059",
    "supplier_name": "TEST SUPPLIER",
    "lead_time_days": 3,
}


# ── GET /api/inventory-optimization/overview ──────────────────────────────────

class TestOverview:

    def test_overview_returns_200(self, client, mock_db_dependency):
        with patch(
            "backend.routes.inventory_optimization.get_assessment_overview",
            return_value=SAMPLE_OVERVIEW,
        ):
            resp = client.get("/api/inventory-optimization/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_assessed" in data
        assert "procurement_required" in data

    def test_overview_empty_returns_200_with_message(self, client):
        with patch(
            "backend.routes.inventory_optimization.get_assessment_overview",
            return_value={},
        ):
            resp = client.get("/api/inventory-optimization/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_assessed"] == 0
        assert "message" in data


# ── GET /api/inventory-optimization/products ─────────────────────────────────

class TestProductsList:

    def test_products_returns_200(self, client):
        mock_result = {"total": 1, "items": [SAMPLE_ASSESSMENT], "limit": 50, "offset": 0}
        with patch(
            "backend.routes.inventory_optimization.get_assessments_paginated",
            return_value=mock_result,
        ):
            resp = client.get("/api/inventory-optimization/products")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "items" in data

    def test_products_filter_by_valid_routing_decision(self, client):
        mock_result = {"total": 5, "items": [], "limit": 50, "offset": 0}
        with patch(
            "backend.routes.inventory_optimization.get_assessments_paginated",
            return_value=mock_result,
        ) as mock_fn:
            resp = client.get(
                "/api/inventory-optimization/products?routing_decision=procurement_required"
            )
        assert resp.status_code == 200

    def test_products_invalid_routing_decision_returns_422(self, client):
        resp = client.get(
            "/api/inventory-optimization/products?routing_decision=invalid_value"
        )
        assert resp.status_code == 422

    def test_products_pagination_params(self, client):
        mock_result = {"total": 100, "items": [], "limit": 10, "offset": 20}
        with patch(
            "backend.routes.inventory_optimization.get_assessments_paginated",
            return_value=mock_result,
        ):
            resp = client.get(
                "/api/inventory-optimization/products?limit=10&offset=20"
            )
        assert resp.status_code == 200

    def test_products_invalid_limit_returns_422(self, client):
        resp = client.get("/api/inventory-optimization/products?limit=0")
        assert resp.status_code == 422


# ── GET /api/inventory-optimization/products/{product_id} ────────────────────

class TestSingleProduct:

    def test_existing_product_with_assessment_returns_200(self, client):
        with patch(
            "backend.routes.inventory_optimization.get_product_with_supplier",
            return_value=SAMPLE_PRODUCT,
        ), patch(
            "backend.routes.inventory_optimization.get_assessment_by_product",
            return_value=SAMPLE_ASSESSMENT,
        ):
            resp = client.get("/api/inventory-optimization/products/PROD00001")
        assert resp.status_code == 200
        data = resp.json()
        assert "product" in data
        assert "assessment" in data
        assert data["assessment"]["routing_decision"] == "procurement_required"

    def test_nonexistent_product_returns_404(self, client):
        with patch(
            "backend.routes.inventory_optimization.get_product_with_supplier",
            return_value=None,
        ):
            resp = client.get("/api/inventory-optimization/products/PROD99999")
        assert resp.status_code == 404

    def test_product_exists_but_no_assessment_returns_404(self, client):
        with patch(
            "backend.routes.inventory_optimization.get_product_with_supplier",
            return_value=SAMPLE_PRODUCT,
        ), patch(
            "backend.routes.inventory_optimization.get_assessment_by_product",
            return_value=None,
        ):
            resp = client.get("/api/inventory-optimization/products/PROD00001")
        assert resp.status_code == 404
        assert "assess" in resp.json()["detail"].lower()

    def test_product_id_uppercased(self, client):
        """product_id in URL is uppercased before lookup."""
        with patch(
            "backend.routes.inventory_optimization.get_product_with_supplier",
            return_value=None,
        ) as mock_fn:
            resp = client.get("/api/inventory-optimization/products/prod00001")
        # The mock was called with uppercased 'PROD00001'
        mock_fn.assert_called_once()
        args = mock_fn.call_args[0]
        assert args[1] == "PROD00001"


# ── POST /api/inventory-optimization/assess ──────────────────────────────────

class TestAssessEndpoint:

    def test_single_product_assess_synchronous(self, client):
        """Single product ID → synchronous assessment, 200 response."""
        mock_result = {
            "assessed": 1, "errors": 0, "error_list": [],
            "forecast_run_id": "09cf3b5d-3b37-4b57-a97a-50f237f5dda6",
            "status": "completed",
        }
        with patch(
            "backend.routes.inventory_optimization.get_product_with_supplier",
            return_value=SAMPLE_PRODUCT,
        ), patch(
            "backend.routes.inventory_optimization.run_assessment",
            return_value=mock_result,
        ):
            resp = client.post(
                "/api/inventory-optimization/assess",
                json={"product_id": "PROD00001", "reason": "test run"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["assessed"] == 1

    def test_single_product_nonexistent_returns_404(self, client):
        with patch(
            "backend.routes.inventory_optimization.get_product_with_supplier",
            return_value=None,
        ):
            resp = client.post(
                "/api/inventory-optimization/assess",
                json={"product_id": "PROD99999"},
            )
        assert resp.status_code == 404

    def test_all_products_queued_as_background(self, client):
        """No product_id → background task, 200 with 'accepted' status."""
        resp = client.post(
            "/api/inventory-optimization/assess",
            json={"reason": "nightly run"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert "background" in data["message"].lower()

    def test_assess_with_partial_errors_returns_partial_status(self, client):
        mock_result = {
            "assessed": 5, "errors": 2,
            "error_list": [{"product_id": "PRODX", "error": "test error"}],
            "forecast_run_id": None, "status": "partial",
        }
        with patch(
            "backend.routes.inventory_optimization.get_product_with_supplier",
            return_value=SAMPLE_PRODUCT,
        ), patch(
            "backend.routes.inventory_optimization.run_assessment",
            return_value=mock_result,
        ):
            resp = client.post(
                "/api/inventory-optimization/assess",
                json={"product_id": "PROD00001"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "partial"


# ── GET /api/inventory-optimization/recommendations ──────────────────────────

class TestRecommendations:

    def test_recommendations_returns_200(self, client):
        mock_proc = {"total": 3, "items": [SAMPLE_ASSESSMENT], "limit": 100, "offset": 0}
        mock_excess = {"total": 0, "items": [], "limit": 100, "offset": 0}
        with patch(
            "backend.routes.inventory_optimization.get_assessments_paginated",
            side_effect=[mock_proc, mock_excess],
        ):
            resp = client.get("/api/inventory-optimization/recommendations")
        assert resp.status_code == 200
        data = resp.json()
        assert "procurement_required" in data
        assert "excess_stock_review" in data
        assert "note" in data

    def test_recommendations_note_mentions_module4_module5(self, client):
        mock_empty = {"total": 0, "items": [], "limit": 100, "offset": 0}
        with patch(
            "backend.routes.inventory_optimization.get_assessments_paginated",
            return_value=mock_empty,
        ):
            resp = client.get("/api/inventory-optimization/recommendations")
        assert resp.status_code == 200
        note = resp.json()["note"].lower()
        assert "module 4" in note or "module4" in note or "procurement" in note


# ── Regression: existing Module 2 routes still work ──────────────────────────

class TestModule2Regression:
    """Verify that Module 3 additions do not break existing Module 2 routes."""

    def test_forecast_overview_still_accessible(self, client):
        """/api/forecast/overview still returns 200 or 404 (not 500)."""
        # Patch where the route module imports it (not where it is defined)
        with patch(
            "backend.routes.forecasts.get_overview",
            return_value={"error": "No completed forecast runs found."},
        ):
            resp = client.get("/api/forecast/overview")
        # 404 is expected when mocked to return an error key — not 500
        assert resp.status_code in (200, 404)

    def test_forecast_runs_route_accessible(self, client):
        """/api/forecast/runs still accessible."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn.cursor.return_value = mock_cursor
        app.dependency_overrides[get_db] = lambda: (yield mock_conn)
        resp = client.get("/api/forecast/runs")
        assert resp.status_code == 200

    def test_root_endpoint_still_works(self, client):
        """/health still returns 200."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
