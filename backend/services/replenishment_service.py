"""
backend/services/replenishment_service.py
------------------------------------------
Module 3 — Replenishment quantity calculation service.

Formula (order-up-to policy):
    target              = product.maximum_stock_level
    raw_suggested       = max(0, target - inventory_position)
    suggested           = max(raw_suggested, minimum_order_qty) if raw_suggested > 0
                          else 0

The target is product.maximum_stock_level, which is fully populated for all 3,046
products in this dataset (no nulls). This field represents the replenishment ceiling
defined in the product master.

Minimum order quantity (MOQ): applied only when a positive replenishment is needed.
If raw_suggested is already >= MOQ, no adjustment is made. If raw_suggested > 0 but
< MOQ, it is raised to MOQ. If raw_suggested = 0 (already at or above target), MOQ
is not applied.

This module does NOT implement supplier ranking, TOPSIS, or procurement decisions.
It calculates the suggested quantity only. Module 4 (Procurement Agent) handles
supplier selection and purchase order creation.

This module is purely computational — no database access.
"""

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def calculate_replenishment_qty(
    inventory_position: Optional[int],
    maximum_stock_level: Optional[int],
    minimum_order_qty: Optional[int],
) -> dict:
    """
    Calculate suggested replenishment quantity.

    Parameters
    ----------
    inventory_position : int | None
        Current inventory position (= available stock in this schema).
    maximum_stock_level : int | None
        Target (order-up-to) stock level from product master.
    minimum_order_qty : int | None
        Minimum order quantity from product master or product_supplier.

    Returns
    -------
    dict with keys:
        suggested_replenishment_qty : int | None
        replenishment_status        : str
        formula_inputs              : dict
        notes                       : str
    """
    formula_inputs = {
        "inventory_position": inventory_position,
        "maximum_stock_level": maximum_stock_level,
        "minimum_order_qty": minimum_order_qty,
    }

    # Guard: missing inputs
    if inventory_position is None:
        return {
            "suggested_replenishment_qty": None,
            "replenishment_status": "insufficient_data",
            "formula_inputs": formula_inputs,
            "notes": "Replenishment quantity unavailable: inventory_position is missing.",
        }

    if maximum_stock_level is None or maximum_stock_level <= 0:
        return {
            "suggested_replenishment_qty": None,
            "replenishment_status": "insufficient_data",
            "formula_inputs": formula_inputs,
            "notes": "Replenishment quantity unavailable: maximum_stock_level is missing or zero.",
        }

    # Core calculation
    raw_suggested = max(0, maximum_stock_level - inventory_position)

    if raw_suggested == 0:
        return {
            "suggested_replenishment_qty": 0,
            "replenishment_status": "not_required",
            "formula_inputs": formula_inputs,
            "notes": (
                f"No replenishment required: inventory_position ({inventory_position}) "
                f">= maximum_stock_level ({maximum_stock_level})."
            ),
        }

    # Apply MOQ if set and positive
    moq = minimum_order_qty if (minimum_order_qty and minimum_order_qty > 0) else 1
    suggested = max(raw_suggested, moq)

    moq_note = ""
    if moq > 1 and suggested != raw_suggested:
        moq_note = (
            f" Raw quantity ({raw_suggested}) was below minimum_order_qty ({moq}); "
            f"raised to {suggested}."
        )

    return {
        "suggested_replenishment_qty": suggested,
        "replenishment_status": "calculated",
        "formula_inputs": formula_inputs,
        "notes": (
            f"Suggested = max(0, target({maximum_stock_level}) - "
            f"inventory_position({inventory_position})) = {raw_suggested}, "
            f"then MOQ-adjusted to {suggested}.{moq_note} "
            "Supplier selection is handled by Module 4 (Procurement Agent)."
        ),
    }
