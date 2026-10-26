"""
backend/services/assessment_service.py
----------------------------------------
Module 3 — Per-product inventory assessment orchestrator.

Coordinates:
  1. Data retrieval (inventory_service)
  2. Safety stock calculation (safety_stock_service)
  3. Reorder point calculation (reorder_point_service)
  4. Replenishment quantity calculation (replenishment_service)
  5. Routing decision (deterministic rules)
  6. Persistence (inventory_service.upsert_assessment)

Routing decision logic (priority order, highest first):
  1. insufficient_data  — missing stock, missing lead time, or missing product record
  2. procurement_required — inventory_position ≤ computed ROP
                            OR inventory_position ≤ product.reorder_level
  3. excess_stock_review — inventory_position > maximum_stock_level × EXCESS_RATIO
                           (and NOT procurement_required)
  4. normal             — none of the above

Conflict rule: procurement_required always overrides excess_stock_review.
Both signals are recorded in decision_reasons.

Excess stock note:
  With the corrected stock calculation (latest_batch.current_quantity), all
  3,046 products currently sit at or below reorder_level. The excess_stock_review
  path handles future scenarios where new batches raise inventory above the
  threshold. The routing logic is correct and data-driven; the current state
  of the dataset simply does not produce excess-stock cases.

XGBoost note:
  Stockout-risk prediction is NOT implemented. Reliable binary stockout labels
  cannot be constructed from this dataset (daily_demand missing dates are
  ambiguous: zero-sales vs stockout). XGBoost is deferred pending valid labels.

No downstream agent logic is invoked here.
"""

import logging
import os
from datetime import date
from typing import Optional

from backend.services.inventory_service import (
    get_product_with_supplier,
    get_latest_batch_stock,
    get_demand_statistics,
    get_latest_forecast_run,
    get_product_forecast_demand,
    upsert_assessment,
    get_all_product_ids,
    apply_schema,
)
from backend.services.safety_stock_service import calculate_safety_stock
from backend.services.reorder_point_service import (
    calculate_reorder_point,
    FORECAST_HORIZON,
)
from backend.services.replenishment_service import calculate_replenishment_qty

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
EXCESS_STOCK_RATIO = float(os.getenv("M3_EXCESS_STOCK_RATIO", "1.5"))
POLICY_VERSION = "v1.0"


# ── Routing decision ──────────────────────────────────────────────────────────

def _make_routing_decision(
    inventory_position: Optional[int],
    reorder_point: Optional[float],
    reorder_level_from_product: Optional[int],
    maximum_stock_level: Optional[int],
    lead_time_days: Optional[int],
    has_stock_data: bool,
) -> tuple:
    """
    Apply routing rules and return (routing_decision, reasons_list).

    Priority (highest first):
      1. insufficient_data
      2. procurement_required
      3. excess_stock_review
      4. normal
    """
    reasons = []

    # 1. Insufficient data
    if not has_stock_data:
        reasons.append("no_inventory_batch_data")
        return "insufficient_data", reasons

    if lead_time_days is None:
        reasons.append("no_supplier_lead_time")
        return "insufficient_data", reasons

    if inventory_position is None:
        reasons.append("inventory_position_unavailable")
        return "insufficient_data", reasons

    # Collect evidence for all signals (record even if not the final decision)
    is_procurement = False
    is_excess = False

    # Procurement signal: below computed ROP
    if reorder_point is not None and inventory_position <= reorder_point:
        is_procurement = True
        reasons.append(
            f"inventory_position({inventory_position}) <= computed_rop({reorder_point:.1f})"
        )

    # Procurement signal: below static product reorder_level
    if reorder_level_from_product is not None and inventory_position <= reorder_level_from_product:
        is_procurement = True
        reasons.append(
            f"inventory_position({inventory_position}) <= product_reorder_level({reorder_level_from_product})"
        )

    # Excess stock signal
    if maximum_stock_level and inventory_position > maximum_stock_level * EXCESS_STOCK_RATIO:
        is_excess = True
        threshold = maximum_stock_level * EXCESS_STOCK_RATIO
        reasons.append(
            f"inventory_position({inventory_position}) > excess_threshold({threshold:.1f} = "
            f"{maximum_stock_level} × {EXCESS_STOCK_RATIO})"
        )

    # Apply priority: procurement beats excess
    if is_procurement:
        if is_excess:
            reasons.append("NOTE: excess_stock signal present but overridden by procurement_required")
        return "procurement_required", reasons

    if is_excess:
        return "excess_stock_review", reasons

    reasons.append("inventory_position within normal range")
    return "normal", reasons


# ── Single product assessment ─────────────────────────────────────────────────

def assess_product(conn, product_id: str, forecast_run_meta: Optional[dict]) -> dict:
    """
    Run a full inventory assessment for one product.

    Returns a dict suitable for upsert_assessment() and API responses.
    Does NOT commit the DB transaction — caller controls that via upsert_assessment.
    """
    result: dict = {
        "product_id": product_id,
        "forecast_run_id": None,
        "model_version": None,
        "policy_version": POLICY_VERSION,
        # Will be filled below
    }
    notes_parts = []

    # ── 1. Product master + supplier ────────────────────────────────────────
    product = get_product_with_supplier(conn, product_id)
    if product is None:
        logger.warning(f"Product {product_id} not found in product table.")
        result.update({
            "available_stock": 0,
            "inventory_position": 0,
            "stock_calculation_method": "unavailable",
            "inventory_position_note": "Product not found in product table.",
            "forecast_demand_7d": None,
            "avg_daily_demand": None,
            "demand_source": None,
            "demand_std_daily": None,
            "history_days_used": None,
            "nonzero_demand_days": None,
            "lead_time_days": None,
            "safety_stock": None,
            "safety_stock_z": None,
            "safety_stock_status": "insufficient_data",
            "reorder_point": None,
            "reorder_level_from_product": None,
            "rop_status": "insufficient_data",
            "suggested_replenishment_qty": None,
            "minimum_order_qty": None,
            "maximum_stock_level": None,
            "replenishment_status": "insufficient_data",
            "routing_decision": "insufficient_data",
            "decision_reasons": "product_not_found",
            "calculation_notes": "Product record not found. No assessment possible.",
        })
        return result

    lead_time_days = product.get("lead_time_days")
    reorder_level_from_product = product.get("reorder_level")
    maximum_stock_level = product.get("maximum_stock_level")
    minimum_order_qty = product.get("minimum_order_qty")

    # ── 2. Forecast run metadata ─────────────────────────────────────────────
    if forecast_run_meta:
        result["forecast_run_id"] = str(forecast_run_meta["forecast_run_id"])
        result["model_version"] = forecast_run_meta.get("model_version")
        forecast_origin: date = forecast_run_meta["forecast_origin"]
    else:
        forecast_origin = date.today()
        notes_parts.append("No completed forecast run found; demand statistics use today as window boundary.")

    # ── 3. Stock data ────────────────────────────────────────────────────────
    stock = get_latest_batch_stock(conn, product_id)
    has_stock_data = stock is not None

    if has_stock_data:
        available_stock = stock["available_stock"]
        inventory_position = stock["inventory_position"]
        result["available_stock"] = available_stock
        result["inventory_position"] = inventory_position
        result["stock_calculation_method"] = stock["stock_calculation_method"]
        result["inventory_position_note"] = stock["inventory_position_note"]
    else:
        result["available_stock"] = 0
        result["inventory_position"] = 0
        result["stock_calculation_method"] = "unavailable"
        result["inventory_position_note"] = "No inventory_batch rows found for this product."
        notes_parts.append("No inventory batch data found.")

    # ── 4. Demand statistics (90-day gap-filled window) ──────────────────────
    demand_stats = get_demand_statistics(conn, product_id, forecast_origin)

    avg_daily_demand = demand_stats["avg_daily_demand"]
    demand_std_daily = demand_stats["demand_std_daily"]
    demand_source = demand_stats["demand_source"]
    history_days_used = demand_stats["history_days_used"]
    nonzero_demand_days = demand_stats["nonzero_demand_days"]

    # Fallback: if historical avg is 0 or insufficient, use product.expected_daily_demand
    if (avg_daily_demand is None or avg_daily_demand == 0.0) and product.get("expected_daily_demand"):
        avg_daily_demand = float(product["expected_daily_demand"])
        demand_source = "product_field"
        notes_parts.append(
            "avg_daily_demand fell back to product.expected_daily_demand "
            "(historical window had zero demand or was unavailable)."
        )

    result["avg_daily_demand"] = avg_daily_demand
    result["demand_std_daily"] = demand_std_daily
    result["demand_source"] = demand_source
    result["history_days_used"] = history_days_used
    result["nonzero_demand_days"] = nonzero_demand_days

    # ── 5. Forecast demand (7-day total) ─────────────────────────────────────
    forecast_demand_7d = None
    avg_daily_forecast = None

    if forecast_run_meta:
        forecast_data = get_product_forecast_demand(
            conn, product_id, result["forecast_run_id"]
        )
        if not forecast_data["missing_forecast"]:
            forecast_demand_7d = forecast_data["total_7d"]
            avg_daily_forecast = forecast_data["avg_daily_forecast"]
        else:
            notes_parts.append(
                f"Forecast has only {forecast_data['n_days']} of expected 7 days. "
                "Using historical demand for ROP."
            )

    result["forecast_demand_7d"] = forecast_demand_7d

    # ── 6. Demand rate for ROP ───────────────────────────────────────────────
    # Prefer forecast average for short lead times; historical for longer
    lead_time_exceeds_horizon = (
        lead_time_days is not None and lead_time_days > FORECAST_HORIZON
    )

    if lead_time_exceeds_horizon:
        rop_avg_daily = avg_daily_demand
        rop_demand_source = demand_source
        notes_parts.append(
            f"Lead time ({lead_time_days}d) exceeds the 7-day forecast horizon. "
            "Historical average daily demand used for ROP (documented limitation)."
        )
    elif avg_daily_forecast is not None:
        rop_avg_daily = avg_daily_forecast
        rop_demand_source = "forecast_avg"
    else:
        rop_avg_daily = avg_daily_demand
        rop_demand_source = demand_source

    # ── 7. Safety stock ──────────────────────────────────────────────────────
    ss_result = calculate_safety_stock(
        demand_std_daily=demand_std_daily or 0.0,
        lead_time_days=lead_time_days or 0,
        nonzero_demand_days=nonzero_demand_days or 0,
    )
    safety_stock = ss_result["safety_stock"]

    result["safety_stock"] = safety_stock
    result["safety_stock_z"] = ss_result["safety_stock_z"]
    result["safety_stock_status"] = ss_result["safety_stock_status"]

    # ── 8. Reorder point ─────────────────────────────────────────────────────
    rop_result = calculate_reorder_point(
        avg_daily_demand=rop_avg_daily,
        lead_time_days=lead_time_days,
        safety_stock=safety_stock,
        demand_source=rop_demand_source,
        reorder_level_from_product=reorder_level_from_product,
        lead_time_exceeds_horizon=lead_time_exceeds_horizon,
    )
    reorder_point = rop_result["reorder_point"]

    result["reorder_point"] = reorder_point
    result["reorder_level_from_product"] = reorder_level_from_product
    result["lead_time_days"] = lead_time_days   # must be stored for upsert SQL
    result["rop_status"] = rop_result["rop_status"]

    # ── 9. Replenishment quantity ─────────────────────────────────────────────
    rep_result = calculate_replenishment_qty(
        inventory_position=inventory_position if has_stock_data else None,
        maximum_stock_level=maximum_stock_level,
        minimum_order_qty=minimum_order_qty,
    )

    result["suggested_replenishment_qty"] = rep_result["suggested_replenishment_qty"]
    result["minimum_order_qty"] = minimum_order_qty
    result["maximum_stock_level"] = maximum_stock_level
    result["replenishment_status"] = rep_result["replenishment_status"]

    # ── 10. Routing decision ──────────────────────────────────────────────────
    routing_decision, decision_reasons = _make_routing_decision(
        inventory_position=inventory_position if has_stock_data else None,
        reorder_point=reorder_point,
        reorder_level_from_product=reorder_level_from_product,
        maximum_stock_level=maximum_stock_level,
        lead_time_days=lead_time_days,
        has_stock_data=has_stock_data,
    )

    result["routing_decision"] = routing_decision
    result["decision_reasons"] = "; ".join(decision_reasons)

    # ── 11. Calculation notes ─────────────────────────────────────────────────
    notes_parts.append(ss_result["notes"])
    notes_parts.append(rop_result["notes"])
    notes_parts.append(rep_result["notes"])
    notes_parts.append(
        "XGBoost stockout prediction not implemented: "
        "reliable stockout labels cannot be constructed from this dataset "
        "(daily_demand missing dates are ambiguous: zero-sales vs stockout). "
        "Pending valid training data."
    )
    result["calculation_notes"] = " | ".join(notes_parts)

    return result


# ── Batch assessment ──────────────────────────────────────────────────────────

def run_assessment(
    conn,
    product_id: Optional[str] = None,
    create_schema_if_missing: bool = True,
) -> dict:
    """
    Run inventory assessment for all products or a single product.
    Persists results via upsert_assessment (idempotent).

    Parameters
    ----------
    conn : psycopg2 connection
    product_id : str | None
        If provided, assess only this product.
        If None, assess all products.
    create_schema_if_missing : bool
        If True, apply schema_m3.sql before assessing.

    Returns
    -------
    dict with:
        assessed    : int   — number of products assessed
        errors      : int   — number of per-product failures
        error_list  : list  — list of {product_id, error} for failures
        forecast_run_id : str | None
        status      : str   — 'completed' | 'partial' | 'failed'
    """
    if create_schema_if_missing:
        try:
            apply_schema(conn)
        except Exception as e:
            logger.error(f"Schema application failed: {e}", exc_info=True)
            return {
                "assessed": 0,
                "errors": 1,
                "error_list": [{"product_id": None, "error": f"Schema error: {e}"}],
                "forecast_run_id": None,
                "status": "failed",
            }

    # Fetch forecast run once for all products
    forecast_run_meta = get_latest_forecast_run(conn)
    if forecast_run_meta is None:
        logger.warning("No completed forecast run found. Assessments will use fallback demand values.")

    # Product list
    if product_id:
        product_ids = [product_id.upper()]
    else:
        product_ids = get_all_product_ids(conn)

    assessed = 0
    errors = 0
    error_list = []

    for pid in product_ids:
        try:
            row = assess_product(conn, pid, forecast_run_meta)
            upsert_assessment(conn, row)
            assessed += 1
        except Exception as e:
            errors += 1
            error_list.append({"product_id": pid, "error": str(e)})
            logger.error(f"Assessment failed for product {pid}: {e}", exc_info=True)
            # One failure does not abort the whole batch

    run_id = str(forecast_run_meta["forecast_run_id"]) if forecast_run_meta else None
    status = "completed" if errors == 0 else ("partial" if assessed > 0 else "failed")

    logger.info(
        f"Assessment complete: assessed={assessed}, errors={errors}, "
        f"forecast_run_id={run_id}"
    )
    return {
        "assessed": assessed,
        "errors": errors,
        "error_list": error_list,
        "forecast_run_id": run_id,
        "status": status,
    }
