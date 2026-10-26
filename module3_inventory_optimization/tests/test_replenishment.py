"""
module3_inventory_optimization/tests/test_replenishment.py
------------------------------------------------------------
Unit tests for replenishment_service.calculate_replenishment_qty().

All tests are pure — no database required.
Formula: max(0, max_stock - inventory_position), then MOQ-enforced.
"""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.services.replenishment_service import calculate_replenishment_qty


# ── Position below target → replenishment required ────────────────────────────

def test_basic_replenishment_needed():
    """inventory_position < max_stock → positive quantity."""
    result = calculate_replenishment_qty(
        inventory_position=5,
        maximum_stock_level=22,
        minimum_order_qty=2,
    )
    assert result["suggested_replenishment_qty"] == 17  # 22 - 5
    assert result["replenishment_status"] == "calculated"


def test_replenishment_quantity_is_non_negative():
    """Suggested quantity is always >= 0."""
    for inv, max_s in [(30, 22), (22, 22), (5, 22)]:
        result = calculate_replenishment_qty(
            inventory_position=inv,
            maximum_stock_level=max_s,
            minimum_order_qty=1,
        )
        assert result["suggested_replenishment_qty"] >= 0


# ── Position at or above target → not required ────────────────────────────────

def test_at_target_not_required():
    """inventory_position == max_stock → 0, status = 'not_required'."""
    result = calculate_replenishment_qty(
        inventory_position=22,
        maximum_stock_level=22,
        minimum_order_qty=2,
    )
    assert result["suggested_replenishment_qty"] == 0
    assert result["replenishment_status"] == "not_required"


def test_above_target_not_required():
    """inventory_position > max_stock → 0, status = 'not_required'."""
    result = calculate_replenishment_qty(
        inventory_position=50,
        maximum_stock_level=22,
        minimum_order_qty=2,
    )
    assert result["suggested_replenishment_qty"] == 0
    assert result["replenishment_status"] == "not_required"


# ── Minimum order quantity enforcement ────────────────────────────────────────

def test_moq_raises_small_quantity():
    """raw_suggested < MOQ → raised to MOQ."""
    result = calculate_replenishment_qty(
        inventory_position=20,
        maximum_stock_level=22,
        minimum_order_qty=5,
    )
    # raw_suggested = 2, MOQ = 5 → raised to 5
    assert result["suggested_replenishment_qty"] == 5
    assert result["replenishment_status"] == "calculated"


def test_moq_not_applied_when_raw_exceeds_moq():
    """raw_suggested > MOQ → MOQ not applied; raw value used."""
    result = calculate_replenishment_qty(
        inventory_position=5,
        maximum_stock_level=22,
        minimum_order_qty=3,
    )
    # raw_suggested = 17 > MOQ 3 → stays 17
    assert result["suggested_replenishment_qty"] == 17


def test_moq_not_applied_when_not_required():
    """MOQ is never applied when replenishment is not needed."""
    result = calculate_replenishment_qty(
        inventory_position=30,
        maximum_stock_level=22,
        minimum_order_qty=10,
    )
    assert result["suggested_replenishment_qty"] == 0
    assert result["replenishment_status"] == "not_required"


def test_moq_exactly_equals_raw():
    """raw_suggested == MOQ → no change."""
    result = calculate_replenishment_qty(
        inventory_position=17,
        maximum_stock_level=22,
        minimum_order_qty=5,
    )
    assert result["suggested_replenishment_qty"] == 5


def test_null_moq_treated_as_one():
    """None MOQ defaults to 1 (no constraint)."""
    result = calculate_replenishment_qty(
        inventory_position=5,
        maximum_stock_level=22,
        minimum_order_qty=None,
    )
    assert result["suggested_replenishment_qty"] == 17  # raw_suggested unchanged
    assert result["replenishment_status"] == "calculated"


# ── Missing / invalid inputs ───────────────────────────────────────────────────

def test_none_inventory_position_insufficient_data():
    """None inventory_position → status = 'insufficient_data'."""
    result = calculate_replenishment_qty(
        inventory_position=None,
        maximum_stock_level=22,
        minimum_order_qty=2,
    )
    assert result["replenishment_status"] == "insufficient_data"
    assert result["suggested_replenishment_qty"] is None


def test_none_maximum_stock_level_insufficient_data():
    """None max_stock → status = 'insufficient_data'."""
    result = calculate_replenishment_qty(
        inventory_position=5,
        maximum_stock_level=None,
        minimum_order_qty=2,
    )
    assert result["replenishment_status"] == "insufficient_data"
    assert result["suggested_replenishment_qty"] is None


def test_zero_maximum_stock_level_insufficient_data():
    """Zero max_stock → status = 'insufficient_data'."""
    result = calculate_replenishment_qty(
        inventory_position=5,
        maximum_stock_level=0,
        minimum_order_qty=2,
    )
    assert result["replenishment_status"] == "insufficient_data"


# ── Formula inputs and notes ───────────────────────────────────────────────────

def test_formula_inputs_recorded():
    """formula_inputs always present."""
    result = calculate_replenishment_qty(
        inventory_position=5,
        maximum_stock_level=22,
        minimum_order_qty=2,
    )
    fi = result["formula_inputs"]
    assert "inventory_position" in fi
    assert "maximum_stock_level" in fi
    assert "minimum_order_qty" in fi


def test_notes_present_all_paths():
    """Notes field always present and non-empty."""
    cases = [
        (5, 22, 2),
        (30, 22, 2),
        (None, 22, 2),
        (5, None, 2),
    ]
    for ip, ms, moq in cases:
        result = calculate_replenishment_qty(ip, ms, moq)
        assert "notes" in result
        assert isinstance(result["notes"], str)
        assert len(result["notes"]) > 0
