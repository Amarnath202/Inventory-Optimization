"""
module3_inventory_optimization/tests/conftest.py
--------------------------------------------------
Shared pytest fixtures for Module 3 unit and API tests.

Design principles:
  - No production database is read or modified.
  - DB-dependent tests use mocked connections / service functions.
  - Pure calculation tests (safety stock, ROP, replenishment, routing)
    require no fixtures — they operate on plain Python inputs.
  - The FastAPI TestClient fixture provides an isolated app instance
    with the Module 3 router registered.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure FinalProject root is importable
_PROJ_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_PROJ_ROOT))
_M2_ROOT = _PROJ_ROOT / "module2_demand_forecasting"
if _M2_ROOT.exists():
    sys.path.insert(0, str(_M2_ROOT))


# ── FastAPI test client ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def test_client():
    """
    TestClient for the full FastAPI application.
    Database dependency (get_db) is overridden with a mock connection
    so no real DB is needed.
    """
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.services.db import get_db

    mock_conn = MagicMock()

    def override_get_db():
        yield mock_conn

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=False)
    yield client, mock_conn
    app.dependency_overrides.clear()


# ── Sample product data ────────────────────────────────────────────────────────

@pytest.fixture
def sample_product():
    """A representative product record matching Module 1 schema."""
    return {
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


@pytest.fixture
def sample_stock():
    """Stock result for a product with latest-batch stock = 6."""
    return {
        "available_stock": 6,
        "inventory_position": 6,
        "latest_batch_date": "2026-08-01",
        "latest_batch_id": "BAT00057875",
        "stock_calculation_method": "latest_batch",
        "inventory_position_note": (
            "Simplified: equals current_quantity from latest inventory_batch "
            "(batch_id=BAT00057875, received_date=2026-08-01). "
            "No purchase orders or backorders tracked."
        ),
    }


@pytest.fixture
def sample_demand_stats():
    """Demand stats for a product with moderate history."""
    return {
        "avg_daily_demand": 1.7556,
        "demand_std_daily": 1.6781,
        "history_days_used": 90,
        "nonzero_demand_days": 63,
        "demand_source": "historical_avg",
        "window_start": "2026-06-02",
        "window_end": "2026-08-30",
        "insufficient_history": False,
    }
