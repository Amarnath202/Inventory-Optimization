"""
backend/agents/excess_stock_agent.py
--------------------------------------
Module 5 — Excess Stock Management Agent.

Receives an inventory assessment result via OrchestrationState and:
  1. Computes proper population-level ABC classification.
  2. Computes XYZ classification via CV of 90-day gap-filled daily demand.
  3. Detects excess stock categories: overstock / slow_moving / dead_stock.
  4. Produces a structured recommendation with confidence and data-quality warnings.
  5. Returns an ExcessStockResult matching the orchestration state contract.

This node replaces the prior prototype which used a per-product revenue
threshold approximation for ABC. The full implementation performs population-
level cumulative revenue ranking using ALL products with 12-month sale data.

Explicit exclusions (enforced by design):
  - Does NOT modify stock quantities, write-off records, or place orders.
  - Does NOT overwrite Module 3's routing_decision or assessment fields.
  - Orchestration / Module 4 / Module 6 logic are not invoked here.
  - Forecast and stock data are historical (origin 2026-08-31).

Data limitation note:
  The current dataset has NO products routed to excess_stock_review in the
  static August 2026 snapshot (all 3,046 products sit at or below reorder_level).
  This agent is invoked only when Module 3 routes routing_decision =
  'excess_stock_review'. The implementation is correct and complete; the current
  data simply does not produce that routing path with the present inventory snapshot.
"""

import logging
from typing import Optional

from backend.services.excess_stock_service import (
    assess_excess_stock_product,
    apply_excess_schema,
    upsert_excess_assessment,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "excess_stock_agent"


def run_excess_stock_agent(conn, state: dict) -> dict:
    """
    Execute the excess-stock agent for the product in state.

    Parameters
    ----------
    conn : psycopg2 connection (injected by graph runner)
    state : OrchestrationState dict

    Returns
    -------
    Partial state update dict containing:
        excess_stock_result : ExcessStockResult
        data_notices        : list (appended, not replaced)
    """
    product_id = state.get("product_id", "UNKNOWN")
    assessment = state.get("assessment") or {}
    data_notices = list(state.get("data_notices", []))

    logger.info(f"[ExcessStockAgent] Starting assessment for product={product_id}")

    try:
        # Ensure Module 5 schema exists (idempotent)
        try:
            apply_excess_schema(conn)
        except Exception as schema_err:
            logger.warning(
                f"[ExcessStockAgent] Schema application warning: {schema_err}. "
                "Continuing without schema creation (table may already exist)."
            )

        # Run full assessment (reuse Module 3 assessment context)
        result_row = assess_excess_stock_product(
            conn=conn,
            product_id=product_id,
            assessment=assessment if assessment else None,
            abc_population=None,  # population computed inside the service
        )

        assessment_id_m5 = upsert_excess_assessment(conn, result_row)
        logger.info(
            f"[ExcessStockAgent] Persisted assessment_id={assessment_id_m5} "
            f"for product={product_id}"
        )

        # Append standing data notice
        data_notices.append(
            "EXCESS STOCK: Available stock is the current_quantity from the most recent "
            "inventory_batch (2026-08-01). This is a static historical snapshot. "
            "All demand data ends 2026-08-30. "
            "Manager approval is required before any write-off, markdown, "
            "return-to-supplier, or discontinuation action."
        )

        # Log key findings
        logger.info(
            f"[ExcessStockAgent] product={product_id} "
            f"ABC={result_row.get('abc_class')} XYZ={result_row.get('xyz_class')} "
            f"excess_category={result_row.get('excess_category')} "
            f"action={result_row.get('recommended_action')}"
        )

        return {
            "excess_stock_result": {
                "agent": AGENT_NAME,
                "status": "review_ready",
                "product_id": product_id,
                "abc_class": result_row.get("abc_class"),
                "xyz_class": result_row.get("xyz_class"),
                "excess_qty_estimate": result_row.get("excess_qty_estimate"),
                "recommended_action": result_row.get("recommended_action"),
                "recommendation_notes": result_row.get("recommendation_notes", ""),
                "requires_manager_approval": True,
                # Extended fields (compatible with ExcessStockResult TypedDict total=False)
                "excess_category": result_row.get("excess_category"),
                "is_overstock": result_row.get("is_overstock"),
                "is_slow_moving": result_row.get("is_slow_moving"),
                "is_dead_stock": result_row.get("is_dead_stock"),
                "confidence": result_row.get("confidence"),
                "abc_eligible": result_row.get("abc_eligible"),
                "xyz_eligible": result_row.get("xyz_eligible"),
                "xyz_warning": result_row.get("xyz_warning"),
                "revenue_share": result_row.get("revenue_share"),
                "demand_cv": result_row.get("demand_cv"),
                "inventory_position": result_row.get("inventory_position"),
                "maximum_stock_level": result_row.get("maximum_stock_level"),
                "data_warnings": result_row.get("data_warnings"),
                "m5_assessment_id": assessment_id_m5,
            },
            "data_notices": data_notices,
        }

    except Exception as e:
        logger.error(
            f"[ExcessStockAgent] Unhandled error for product={product_id}: {e}",
            exc_info=True,
        )
        return {
            "excess_stock_result": {
                "agent": AGENT_NAME,
                "status": "error",
                "product_id": product_id,
                "abc_class": None,
                "xyz_class": None,
                "excess_qty_estimate": None,
                "recommended_action": None,
                "recommendation_notes": f"Agent error: {e}",
                "requires_manager_approval": True,
            },
            "data_notices": data_notices,
        }
