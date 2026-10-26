"""
backend/services/safety_stock_service.py
-----------------------------------------
Module 3 — Safety stock calculation service.

Formula:
    SS = z × σ_d × √(lead_time_days)

Where:
    z              — service-level factor (default 1.645 → 95% service level)
    σ_d            — daily demand standard deviation (sample, from gap-filled 90-day window)
    lead_time_days — supplier lead time in days

Assumptions:
    1. Demand follows a normal distribution (standard fixed-lead-time formula).
    2. Lead time is constant (no lead-time variability data available in schema).
    3. Daily demand std dev is computed over a gap-filled 90-day calendar window,
       where missing daily_demand dates are treated as zero-sale days (not stockouts).
    4. Result is rounded up to the nearest integer (whole-unit products).

Configurable:
    M3_SERVICE_LEVEL_Z  — environment variable (default '1.645')

This module is purely computational — no database access.
"""

import math
import os
import logging

logger = logging.getLogger(__name__)

# Default z-factor for 95% service level
_DEFAULT_Z = float(os.getenv("M3_SERVICE_LEVEL_Z", "1.645"))

# Minimum number of actual (nonzero) demand days required for a reliable σ estimate
_MIN_NONZERO_DAYS = int(os.getenv("M3_MIN_HISTORY_DAYS", "7"))


def calculate_safety_stock(
    demand_std_daily: float,
    lead_time_days: int,
    nonzero_demand_days: int,
    z: float = _DEFAULT_Z,
) -> dict:
    """
    Calculate safety stock using the standard fixed-lead-time formula.

    Parameters
    ----------
    demand_std_daily : float
        Sample standard deviation of daily demand (gap-filled 90-day window).
        Must be >= 0.
    lead_time_days : int
        Supplier lead time in days. Must be > 0.
    nonzero_demand_days : int
        Number of actual sale days in the demand history window.
        Used to decide whether the σ estimate is reliable.
    z : float
        Service-level z-factor (default 1.645 for 95%).

    Returns
    -------
    dict with keys:
        safety_stock        : float — rounded-up safety stock quantity
        safety_stock_z      : float — z-factor used
        safety_stock_status : str   — 'calculated' | 'zero_variability' |
                                      'insufficient_nonzero_history'
        formula_inputs      : dict  — all inputs for auditability
        notes               : str   — human-readable description
    """
    formula_inputs = {
        "demand_std_daily": demand_std_daily,
        "lead_time_days": lead_time_days,
        "nonzero_demand_days": nonzero_demand_days,
        "z": z,
    }

    # Guard: insufficient history
    if nonzero_demand_days < _MIN_NONZERO_DAYS:
        return {
            "safety_stock": 0.0,
            "safety_stock_z": z,
            "safety_stock_status": "insufficient_nonzero_history",
            "formula_inputs": formula_inputs,
            "notes": (
                f"Safety stock set to 0: only {nonzero_demand_days} nonzero demand "
                f"day(s) in history window (minimum required: {_MIN_NONZERO_DAYS}). "
                "Demand variability cannot be estimated reliably."
            ),
        }

    # Guard: zero variability
    if demand_std_daily is None or demand_std_daily <= 0.0:
        return {
            "safety_stock": 0.0,
            "safety_stock_z": z,
            "safety_stock_status": "zero_variability",
            "formula_inputs": formula_inputs,
            "notes": (
                "Safety stock set to 0: demand standard deviation is zero or "
                "unavailable. Demand appears constant over the history window."
            ),
        }

    # Guard: invalid lead time
    if lead_time_days is None or lead_time_days <= 0:
        return {
            "safety_stock": 0.0,
            "safety_stock_z": z,
            "safety_stock_status": "insufficient_nonzero_history",
            "formula_inputs": formula_inputs,
            "notes": "Safety stock set to 0: lead_time_days is missing or zero.",
        }

    # Standard formula: SS = z × σ_d × √(L)
    raw_ss = z * demand_std_daily * math.sqrt(lead_time_days)
    # Round up to nearest integer (whole-unit products)
    ss = math.ceil(raw_ss)

    return {
        "safety_stock": float(ss),
        "safety_stock_z": z,
        "safety_stock_status": "calculated",
        "formula_inputs": formula_inputs,
        "notes": (
            f"SS = z({z}) × σ_d({demand_std_daily:.4f}) × √(L={lead_time_days}) "
            f"= {raw_ss:.4f} → rounded up to {ss}. "
            "Assumes constant lead time and normally distributed demand. "
            "No lead-time variability data available in this schema."
        ),
    }
