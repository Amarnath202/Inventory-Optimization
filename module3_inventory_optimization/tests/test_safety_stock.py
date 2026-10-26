"""
module3_inventory_optimization/tests/test_safety_stock.py
-----------------------------------------------------------
Unit tests for safety_stock_service.calculate_safety_stock().

All tests are pure — no database required.
Formula: SS = z × σ_d × √(lead_time_days), rounded up.
"""

import math
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.services.safety_stock_service import calculate_safety_stock


# ── Correct calculation ────────────────────────────────────────────────────────

def test_correct_calculation_basic():
    """SS = ceil(z × σ × √L) with known inputs."""
    z = 1.645
    std = 2.0
    lead = 4
    result = calculate_safety_stock(
        demand_std_daily=std,
        lead_time_days=lead,
        nonzero_demand_days=30,
        z=z,
    )
    expected_raw = z * std * math.sqrt(lead)  # 1.645 × 2 × 2 = 6.58
    expected = math.ceil(expected_raw)
    assert result["safety_stock"] == expected
    assert result["safety_stock_status"] == "calculated"
    assert result["safety_stock_z"] == z


def test_correct_calculation_lead_time_1():
    """Lead time of 1 day — √(1) = 1, so SS = ceil(z × σ)."""
    z = 1.645
    std = 3.0
    result = calculate_safety_stock(
        demand_std_daily=std,
        lead_time_days=1,
        nonzero_demand_days=20,
        z=z,
    )
    expected = math.ceil(z * std)  # ceil(4.935) = 5
    assert result["safety_stock"] == expected
    assert result["safety_stock_status"] == "calculated"


def test_correct_calculation_lead_time_7():
    """Standard 7-day lead time."""
    z = 1.645
    std = 1.6781
    result = calculate_safety_stock(
        demand_std_daily=std,
        lead_time_days=7,
        nonzero_demand_days=63,
        z=z,
    )
    expected_raw = z * std * math.sqrt(7)
    expected = math.ceil(expected_raw)
    assert result["safety_stock"] == expected
    assert result["safety_stock_status"] == "calculated"


def test_result_is_integer():
    """safety_stock must be a whole number (rounded up)."""
    result = calculate_safety_stock(
        demand_std_daily=1.5,
        lead_time_days=3,
        nonzero_demand_days=15,
    )
    assert result["safety_stock"] == int(result["safety_stock"])
    assert result["safety_stock"] >= 0


def test_formula_inputs_recorded():
    """formula_inputs dict must include all calculation inputs."""
    result = calculate_safety_stock(
        demand_std_daily=2.5,
        lead_time_days=5,
        nonzero_demand_days=20,
    )
    fi = result["formula_inputs"]
    assert "demand_std_daily" in fi
    assert "lead_time_days" in fi
    assert "nonzero_demand_days" in fi
    assert "z" in fi


# ── Service level configuration ────────────────────────────────────────────────

def test_custom_z_factor():
    """A custom z-factor should be used in the calculation."""
    z_90 = 1.282  # 90% service level
    result = calculate_safety_stock(
        demand_std_daily=2.0,
        lead_time_days=4,
        nonzero_demand_days=30,
        z=z_90,
    )
    expected = math.ceil(z_90 * 2.0 * math.sqrt(4))
    assert result["safety_stock"] == expected
    assert result["safety_stock_z"] == z_90


def test_z_99_gives_higher_ss_than_z_90():
    """Higher service level must produce higher (or equal) safety stock."""
    kwargs = dict(demand_std_daily=2.0, lead_time_days=4, nonzero_demand_days=30)
    ss_90 = calculate_safety_stock(**kwargs, z=1.282)["safety_stock"]
    ss_99 = calculate_safety_stock(**kwargs, z=2.326)["safety_stock"]
    assert ss_99 >= ss_90


# ── Zero variability ───────────────────────────────────────────────────────────

def test_zero_std_returns_zero_ss():
    """Zero σ → SS = 0, status = 'zero_variability'."""
    result = calculate_safety_stock(
        demand_std_daily=0.0,
        lead_time_days=4,
        nonzero_demand_days=30,
    )
    assert result["safety_stock"] == 0.0
    assert result["safety_stock_status"] == "zero_variability"


def test_negative_std_treated_as_zero():
    """Negative σ (invalid input) → SS = 0, status = 'zero_variability'."""
    result = calculate_safety_stock(
        demand_std_daily=-1.0,
        lead_time_days=4,
        nonzero_demand_days=30,
    )
    assert result["safety_stock"] == 0.0
    assert result["safety_stock_status"] == "zero_variability"


# ── Insufficient history ───────────────────────────────────────────────────────

def test_insufficient_nonzero_days():
    """Fewer than 7 nonzero demand days → SS = 0, status = 'insufficient_nonzero_history'."""
    result = calculate_safety_stock(
        demand_std_daily=2.0,
        lead_time_days=4,
        nonzero_demand_days=3,  # below MIN_NONZERO_DAYS=7
    )
    assert result["safety_stock"] == 0.0
    assert result["safety_stock_status"] == "insufficient_nonzero_history"


def test_zero_nonzero_days():
    """Zero nonzero demand days → SS = 0, status = 'insufficient_nonzero_history'."""
    result = calculate_safety_stock(
        demand_std_daily=1.5,
        lead_time_days=3,
        nonzero_demand_days=0,
    )
    assert result["safety_stock"] == 0.0
    assert result["safety_stock_status"] == "insufficient_nonzero_history"


def test_exactly_min_nonzero_days():
    """Exactly 7 nonzero days with positive std → calculated (boundary)."""
    result = calculate_safety_stock(
        demand_std_daily=1.0,
        lead_time_days=3,
        nonzero_demand_days=7,
    )
    assert result["safety_stock_status"] == "calculated"
    assert result["safety_stock"] > 0


# ── Missing lead time ──────────────────────────────────────────────────────────

def test_zero_lead_time_returns_zero():
    """Zero lead time → SS = 0 (guard case)."""
    result = calculate_safety_stock(
        demand_std_daily=2.0,
        lead_time_days=0,
        nonzero_demand_days=30,
    )
    assert result["safety_stock"] == 0.0


def test_none_lead_time_returns_zero():
    """None lead time → SS = 0 (guard case)."""
    result = calculate_safety_stock(
        demand_std_daily=2.0,
        lead_time_days=None,
        nonzero_demand_days=30,
    )
    assert result["safety_stock"] == 0.0


# ── Notes field ────────────────────────────────────────────────────────────────

def test_notes_present_and_non_empty():
    """Notes field must always be present and non-empty."""
    for kwargs in [
        dict(demand_std_daily=2.0, lead_time_days=3, nonzero_demand_days=20),
        dict(demand_std_daily=0.0, lead_time_days=3, nonzero_demand_days=20),
        dict(demand_std_daily=2.0, lead_time_days=3, nonzero_demand_days=2),
    ]:
        result = calculate_safety_stock(**kwargs)
        assert "notes" in result
        assert len(result["notes"]) > 0
