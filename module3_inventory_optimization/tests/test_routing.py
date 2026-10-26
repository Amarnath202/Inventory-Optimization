"""
module3_inventory_optimization/tests/test_routing.py
------------------------------------------------------
Unit tests for the routing decision logic in assessment_service._make_routing_decision().

All tests are pure — no database required.
Priority order:
  1. insufficient_data  (missing stock, missing lead time)
  2. procurement_required (position ≤ computed ROP OR ≤ product reorder_level)
  3. excess_stock_review (position > max_stock × EXCESS_RATIO, not procurement)
  4. normal
"""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.services.assessment_service import _make_routing_decision


# ── Helper ────────────────────────────────────────────────────────────────────

def _decide(
    inventory_position=10,
    reorder_point=5.0,
    reorder_level_from_product=6,
    maximum_stock_level=22,
    lead_time_days=3,
    has_stock_data=True,
):
    return _make_routing_decision(
        inventory_position=inventory_position,
        reorder_point=reorder_point,
        reorder_level_from_product=reorder_level_from_product,
        maximum_stock_level=maximum_stock_level,
        lead_time_days=lead_time_days,
        has_stock_data=has_stock_data,
    )


# ── Normal ─────────────────────────────────────────────────────────────────────

def test_normal_healthy_inventory():
    """Position well above ROP and below excess threshold → normal."""
    decision, reasons = _decide(
        inventory_position=15,
        reorder_point=5.0,
        reorder_level_from_product=6,
        maximum_stock_level=22,
    )
    assert decision == "normal"


def test_normal_at_maximum_stock():
    """Position == max_stock (not exceeding 1.5×) → normal."""
    decision, reasons = _decide(
        inventory_position=22,
        reorder_point=5.0,
        reorder_level_from_product=6,
        maximum_stock_level=22,
    )
    assert decision == "normal"


# ── Procurement required ───────────────────────────────────────────────────────

def test_procurement_below_computed_rop():
    """Position ≤ computed ROP → procurement_required."""
    decision, reasons = _decide(
        inventory_position=4,
        reorder_point=5.0,
        reorder_level_from_product=3,   # product RL is lower — ROP drives decision
    )
    assert decision == "procurement_required"
    assert any("computed_rop" in r for r in reasons)


def test_procurement_at_computed_rop():
    """Position == computed ROP (boundary, ≤) → procurement_required."""
    decision, reasons = _decide(
        inventory_position=5,
        reorder_point=5.0,
        reorder_level_from_product=3,
    )
    assert decision == "procurement_required"


def test_procurement_below_product_reorder_level():
    """Position ≤ product reorder_level (ROP not triggering) → procurement_required."""
    decision, reasons = _decide(
        inventory_position=5,
        reorder_point=3.0,  # ROP not triggered
        reorder_level_from_product=6,   # product RL triggers
    )
    assert decision == "procurement_required"
    assert any("product_reorder_level" in r for r in reasons)


def test_procurement_at_product_reorder_level():
    """Position == product reorder_level (boundary, ≤) → procurement_required."""
    decision, reasons = _decide(
        inventory_position=6,
        reorder_point=3.0,
        reorder_level_from_product=6,
    )
    assert decision == "procurement_required"


def test_procurement_triggered_by_both_signals():
    """Both ROP and product RL trigger → still procurement_required, both recorded."""
    decision, reasons = _decide(
        inventory_position=3,
        reorder_point=5.0,
        reorder_level_from_product=6,
    )
    assert decision == "procurement_required"
    assert any("computed_rop" in r for r in reasons)
    assert any("product_reorder_level" in r for r in reasons)


# ── Excess stock review ────────────────────────────────────────────────────────

def test_excess_stock_above_1_5x():
    """Position > max_stock × 1.5 and not low stock → excess_stock_review."""
    decision, reasons = _decide(
        inventory_position=34,   # > 22 × 1.5 = 33
        reorder_point=5.0,
        reorder_level_from_product=6,
        maximum_stock_level=22,
    )
    assert decision == "excess_stock_review"
    assert any("excess_threshold" in r for r in reasons)


def test_excess_not_triggered_at_1_5x_boundary():
    """Position == max_stock × 1.5 (not strictly greater) → normal."""
    decision, reasons = _decide(
        inventory_position=33,   # exactly 22 × 1.5, not > threshold
        reorder_point=5.0,
        reorder_level_from_product=6,
        maximum_stock_level=22,
    )
    assert decision == "normal"


# ── Conflict: procurement overrides excess ─────────────────────────────────────

def test_procurement_overrides_excess():
    """
    Position simultaneously below ROP AND above excess threshold
    (theoretically impossible in normal operation but defensively handled).
    procurement_required must take priority.
    """
    decision, reasons = _decide(
        inventory_position=2,    # ≤ ROP → procurement
        reorder_point=5.0,
        reorder_level_from_product=6,
        maximum_stock_level=1,   # 2 > 1 × 1.5 = 1.5 → excess also fires
    )
    assert decision == "procurement_required"
    # Both signals recorded in reasons
    assert any("computed_rop" in r or "product_reorder_level" in r for r in reasons)
    assert any("NOTE" in r for r in reasons)  # conflict note


# ── Insufficient data ──────────────────────────────────────────────────────────

def test_insufficient_data_no_stock():
    """No inventory batch data → insufficient_data."""
    decision, reasons = _decide(has_stock_data=False)
    assert decision == "insufficient_data"
    assert any("no_inventory_batch_data" in r for r in reasons)


def test_insufficient_data_no_lead_time():
    """Missing supplier lead time → insufficient_data."""
    decision, reasons = _decide(lead_time_days=None)
    assert decision == "insufficient_data"
    assert any("no_supplier_lead_time" in r for r in reasons)


def test_insufficient_data_none_inventory_position():
    """None inventory_position with stock data present → insufficient_data."""
    decision, reasons = _make_routing_decision(
        inventory_position=None,
        reorder_point=5.0,
        reorder_level_from_product=6,
        maximum_stock_level=22,
        lead_time_days=3,
        has_stock_data=True,
    )
    assert decision == "insufficient_data"


def test_insufficient_data_takes_priority_over_all():
    """insufficient_data has highest priority — even if ROP/excess would fire."""
    decision, reasons = _decide(
        inventory_position=2,
        reorder_point=5.0,
        has_stock_data=False,     # no stock data
        lead_time_days=None,      # no lead time
    )
    assert decision == "insufficient_data"


# ── Reasons always recorded ────────────────────────────────────────────────────

def test_reasons_always_non_empty_list():
    """decision_reasons list must never be empty."""
    for inv, rop, rl, max_s, lt, stock in [
        (15, 5.0, 6, 22, 3, True),   # normal
        (4, 5.0, 6, 22, 3, True),    # procurement
        (40, 5.0, 6, 22, 3, True),   # excess
        (10, 5.0, 6, 22, None, True), # insufficient
        (10, 5.0, 6, 22, 3, False),  # no stock
    ]:
        _, reasons = _make_routing_decision(
            inventory_position=inv,
            reorder_point=rop,
            reorder_level_from_product=rl,
            maximum_stock_level=max_s,
            lead_time_days=lt,
            has_stock_data=stock,
        )
        assert len(reasons) >= 1
