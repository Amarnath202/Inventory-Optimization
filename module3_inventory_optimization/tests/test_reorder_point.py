"""
module3_inventory_optimization/tests/test_reorder_point.py
------------------------------------------------------------
Unit tests for reorder_point_service.calculate_reorder_point().

All tests are pure — no database required.
Formula: ROP = avg_daily_demand × lead_time_days + safety_stock, rounded up.
"""

import math
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.services.reorder_point_service import (
    calculate_reorder_point,
    FORECAST_HORIZON,
)


# ── Correct calculation ────────────────────────────────────────────────────────

def test_correct_basic_rop():
    """ROP = ceil(avg_daily × lead_time + safety_stock)."""
    result = calculate_reorder_point(
        avg_daily_demand=2.0,
        lead_time_days=3,
        safety_stock=5.0,
        demand_source="historical_avg",
    )
    expected = math.ceil(2.0 * 3 + 5.0)  # ceil(11.0) = 11
    assert result["reorder_point"] == expected
    assert result["rop_status"] == "calculated"


def test_rop_is_integer():
    """ROP is always rounded up to a whole number."""
    result = calculate_reorder_point(
        avg_daily_demand=1.75,
        lead_time_days=4,
        safety_stock=2.3,
        demand_source="forecast_avg",
    )
    assert result["reorder_point"] == int(result["reorder_point"])


def test_rop_with_forecast_avg_source():
    """Forecast-derived avg demand is accepted when lead_time ≤ 7."""
    result = calculate_reorder_point(
        avg_daily_demand=3.5,
        lead_time_days=7,
        safety_stock=4.0,
        demand_source="forecast_avg",
        lead_time_exceeds_horizon=False,
    )
    expected = math.ceil(3.5 * 7 + 4.0)  # ceil(28.5) = 29
    assert result["reorder_point"] == expected
    assert result["rop_status"] == "calculated"


def test_reorder_level_from_product_stored():
    """Static product.reorder_level is stored in result for comparison."""
    result = calculate_reorder_point(
        avg_daily_demand=2.0,
        lead_time_days=3,
        safety_stock=5.0,
        demand_source="historical_avg",
        reorder_level_from_product=12,
    )
    assert result["reorder_level_from_product"] == 12


def test_formula_inputs_recorded():
    """formula_inputs must capture all calculation inputs."""
    result = calculate_reorder_point(
        avg_daily_demand=2.0,
        lead_time_days=3,
        safety_stock=5.0,
        demand_source="historical_avg",
    )
    fi = result["formula_inputs"]
    assert "avg_daily_demand" in fi
    assert "lead_time_days" in fi
    assert "safety_stock" in fi
    assert "demand_source" in fi


# ── Lead time exceeds forecast horizon ────────────────────────────────────────

def test_lead_time_exceeds_horizon_flagged():
    """When lead_time > 7, status = 'lead_time_exceeds_horizon'."""
    result = calculate_reorder_point(
        avg_daily_demand=2.0,
        lead_time_days=10,  # exceeds FORECAST_HORIZON=7
        safety_stock=3.0,
        demand_source="historical_avg",
        lead_time_exceeds_horizon=True,
    )
    assert result["rop_status"] == "lead_time_exceeds_horizon"
    assert "10" in result["notes"] or "lead" in result["notes"].lower()


def test_lead_time_within_horizon_not_flagged():
    """When lead_time ≤ 7, lead_time_exceeds_horizon status is NOT set."""
    result = calculate_reorder_point(
        avg_daily_demand=2.0,
        lead_time_days=7,
        safety_stock=3.0,
        demand_source="forecast_avg",
        lead_time_exceeds_horizon=False,
    )
    assert result["rop_status"] == "calculated"


# ── Zero demand ────────────────────────────────────────────────────────────────

def test_zero_demand_rop_equals_safety_stock():
    """ROP with zero demand = ceil(safety_stock)."""
    result = calculate_reorder_point(
        avg_daily_demand=0.0,
        lead_time_days=5,
        safety_stock=3.5,
        demand_source="historical_avg",
    )
    expected = math.ceil(3.5)  # = 4
    assert result["reorder_point"] == expected
    assert result["rop_status"] == "calculated"


def test_zero_demand_zero_safety_stock():
    """ROP = 0 when both demand and safety stock are zero."""
    result = calculate_reorder_point(
        avg_daily_demand=0.0,
        lead_time_days=5,
        safety_stock=0.0,
        demand_source="historical_avg",
    )
    assert result["reorder_point"] == 0
    assert result["rop_status"] == "calculated"


# ── Missing / invalid inputs ───────────────────────────────────────────────────

def test_none_avg_demand_returns_insufficient_data():
    """Missing avg_daily_demand → rop_status = 'insufficient_data'."""
    result = calculate_reorder_point(
        avg_daily_demand=None,
        lead_time_days=3,
        safety_stock=5.0,
        demand_source="historical_avg",
    )
    assert result["rop_status"] == "insufficient_data"
    assert result["reorder_point"] is None


def test_none_lead_time_returns_insufficient_data():
    """Missing lead_time_days → rop_status = 'insufficient_data'."""
    result = calculate_reorder_point(
        avg_daily_demand=2.0,
        lead_time_days=None,
        safety_stock=5.0,
        demand_source="historical_avg",
    )
    assert result["rop_status"] == "insufficient_data"
    assert result["reorder_point"] is None


def test_zero_lead_time_returns_insufficient_data():
    """Zero lead_time_days → rop_status = 'insufficient_data'."""
    result = calculate_reorder_point(
        avg_daily_demand=2.0,
        lead_time_days=0,
        safety_stock=5.0,
        demand_source="historical_avg",
    )
    assert result["rop_status"] == "insufficient_data"
    assert result["reorder_point"] is None


# ── Notes field ────────────────────────────────────────────────────────────────

def test_notes_present_in_all_paths():
    """Notes must always be non-empty strings."""
    cases = [
        dict(avg_daily_demand=2.0, lead_time_days=3, safety_stock=5.0, demand_source="forecast_avg"),
        dict(avg_daily_demand=None, lead_time_days=3, safety_stock=5.0, demand_source="historical_avg"),
        dict(avg_daily_demand=2.0, lead_time_days=None, safety_stock=5.0, demand_source="historical_avg"),
        dict(avg_daily_demand=0.0, lead_time_days=5, safety_stock=2.0, demand_source="product_field"),
    ]
    for case in cases:
        result = calculate_reorder_point(**case)
        assert "notes" in result
        assert isinstance(result["notes"], str)
        assert len(result["notes"]) > 0
