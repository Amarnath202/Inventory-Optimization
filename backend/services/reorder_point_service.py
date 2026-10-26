"""
backend/services/reorder_point_service.py
------------------------------------------
Module 3 — Reorder point calculation service.

Formula:
    ROP = avg_daily_demand × lead_time_days + safety_stock

Demand rate selection:
    If lead_time_days ≤ forecast_horizon (7 days):
        avg_daily_demand = total_7d_forecast / forecast_horizon
        demand_source    = 'forecast_avg'
    If lead_time_days > forecast_horizon:
        avg_daily_demand = historical_avg_daily_demand (90-day gap-filled)
        demand_source    = 'historical_avg'  (with limitation note)
    If historical avg also unavailable:
        avg_daily_demand = product.expected_daily_demand
        demand_source    = 'product_field'

Also stored for comparison: product.reorder_level (the pre-computed static value
from the product master). Both the computed ROP and the static reorder_level are
returned so the API consumer can choose the more conservative threshold.

This module is purely computational — no database access.
"""

import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

FORECAST_HORIZON = 7  # Module 2 fixed forecast horizon in days


def calculate_reorder_point(
    avg_daily_demand: Optional[float],
    lead_time_days: Optional[int],
    safety_stock: float,
    demand_source: str,
    reorder_level_from_product: Optional[int] = None,
    lead_time_exceeds_horizon: bool = False,
) -> dict:
    """
    Calculate the reorder point.

    Parameters
    ----------
    avg_daily_demand : float | None
        Average daily demand rate (units/day). None triggers insufficient_data.
    lead_time_days : int | None
        Supplier lead time in days. None triggers insufficient_data.
    safety_stock : float
        Safety stock quantity (calculated or 0).
    demand_source : str
        One of 'forecast_avg' | 'historical_avg' | 'product_field'.
    reorder_level_from_product : int | None
        Static reorder_level from the product master (for comparison only).
    lead_time_exceeds_horizon : bool
        True if lead_time_days > FORECAST_HORIZON. Documents limitation in notes.

    Returns
    -------
    dict with keys:
        reorder_point              : float | None
        reorder_level_from_product : int | None
        rop_status                 : str
        demand_source_used         : str
        formula_inputs             : dict
        notes                      : str
    """
    formula_inputs = {
        "avg_daily_demand": avg_daily_demand,
        "lead_time_days": lead_time_days,
        "safety_stock": safety_stock,
        "demand_source": demand_source,
        "forecast_horizon": FORECAST_HORIZON,
    }

    # Guard: missing inputs
    if avg_daily_demand is None:
        return {
            "reorder_point": None,
            "reorder_level_from_product": reorder_level_from_product,
            "rop_status": "insufficient_data",
            "demand_source_used": demand_source,
            "formula_inputs": formula_inputs,
            "notes": "ROP could not be calculated: avg_daily_demand is unavailable.",
        }

    if lead_time_days is None or lead_time_days <= 0:
        return {
            "reorder_point": None,
            "reorder_level_from_product": reorder_level_from_product,
            "rop_status": "insufficient_data",
            "demand_source_used": demand_source,
            "formula_inputs": formula_inputs,
            "notes": "ROP could not be calculated: lead_time_days is missing or zero.",
        }

    # Guard: zero demand
    if avg_daily_demand <= 0.0:
        rop = math.ceil(safety_stock)
        return {
            "reorder_point": float(rop),
            "reorder_level_from_product": reorder_level_from_product,
            "rop_status": "calculated",
            "demand_source_used": demand_source,
            "formula_inputs": formula_inputs,
            "notes": (
                f"avg_daily_demand is zero. ROP equals safety_stock only: "
                f"ROP = 0 × {lead_time_days} + {safety_stock:.4f} = {rop}."
            ),
        }

    # Standard formula
    raw_rop = avg_daily_demand * lead_time_days + safety_stock
    rop = math.ceil(raw_rop)

    if lead_time_exceeds_horizon:
        status = "lead_time_exceeds_horizon"
        limitation_note = (
            f"Lead time ({lead_time_days}d) exceeds the 7-day forecast horizon. "
            "Forecast-derived demand rate could not be used for the full lead time. "
            f"Historical average ({demand_source}) used instead. "
            "This ROP is an approximation; treat with caution."
        )
    else:
        status = "calculated"
        limitation_note = ""

    notes = (
        f"ROP = avg_daily({avg_daily_demand:.4f}) × lead_time({lead_time_days}) "
        f"+ safety_stock({safety_stock:.4f}) = {raw_rop:.4f} → rounded up to {rop}. "
        f"Demand source: {demand_source}."
    )
    if limitation_note:
        notes += " " + limitation_note

    return {
        "reorder_point": float(rop),
        "reorder_level_from_product": reorder_level_from_product,
        "rop_status": status,
        "demand_source_used": demand_source,
        "formula_inputs": formula_inputs,
        "notes": notes,
    }
