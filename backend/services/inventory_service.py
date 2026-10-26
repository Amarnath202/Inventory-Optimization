"""
backend/services/inventory_service.py
--------------------------------------
Module 3 — Data access layer for inventory optimization.

Retrieves product inventory, demand statistics, forecast data, and supplier
lead times from existing Module 1 / Module 2 tables.

Stock calculation:
  available_stock = current_quantity from the LATEST inventory_batch row per
  product (ordered by received_date DESC, batch_id DESC).
  The sum of current_quantity across all historical batches is NOT used —
  it represents a cumulative dataset artifact, not physical on-hand stock.

No purchase orders or backorders are tracked in this schema
(reserved_quantity is universally 0). Inventory position equals available stock.
"""

import logging
import os
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Configuration (environment variables with defaults) ───────────────────────
DEMAND_HISTORY_DAYS = int(os.getenv("M3_DEMAND_HISTORY_DAYS", "90"))
MIN_NONZERO_DAYS = int(os.getenv("M3_MIN_HISTORY_DAYS", "7"))


# ── Product & supplier data ───────────────────────────────────────────────────

def get_product_with_supplier(conn, product_id: str) -> Optional[dict]:
    """
    Return product master fields plus supplier lead time.
    Returns None if the product does not exist.

    Uses product_supplier join to retrieve the single associated supplier.
    (Current dataset has exactly one supplier per product.)
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                p.product_id,
                p.product_name,
                p.brand,
                p.category,
                p.reorder_level,
                p.maximum_stock_level,
                p.minimum_order_qty,
                p.expected_daily_demand,
                p.purchase_frequency,
                s.supplier_id,
                s.supplier_name,
                s.lead_time_days
            FROM product p
            LEFT JOIN product_supplier ps ON p.product_id = ps.product_id
            LEFT JOIN supplier s ON ps.supplier_id = s.supplier_id
            WHERE p.product_id = %s
            """,
            (product_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()


def get_all_product_ids(conn) -> list:
    """Return all product_ids from the product table, ordered."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT product_id FROM product ORDER BY product_id")
        return [r["product_id"] for r in cur.fetchall()]
    finally:
        cur.close()


# ── Stock data ────────────────────────────────────────────────────────────────

def get_latest_batch_stock(conn, product_id: str) -> Optional[dict]:
    """
    Return the current_quantity from the most recent inventory_batch row
    for the given product.

    Rationale: inventory_batch stores one row per monthly replenishment batch.
    current_quantity in each row = unsold remainder from that specific batch.
    The latest batch represents the current replenishment period's remaining
    stock. Summing all batches is incorrect — it accumulates all historical
    remainders, not a single physical stock level.

    Returns a dict with:
        available_stock       : int (current_quantity from latest batch)
        latest_batch_date     : date (received_date of latest batch)
        latest_batch_id       : str
        stock_calculation_method : 'latest_batch'
        inventory_position_note  : str documenting the simplification
    Returns None if no inventory_batch rows exist for the product.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT DISTINCT ON (product_id)
                product_id,
                batch_id,
                received_date,
                current_quantity
            FROM inventory_batch
            WHERE product_id = %s
            ORDER BY product_id, received_date DESC, batch_id DESC
            """,
            (product_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        available_stock = int(row["current_quantity"])
        return {
            "available_stock": available_stock,
            "inventory_position": available_stock,  # no POs/backorders tracked
            "latest_batch_date": row["received_date"],
            "latest_batch_id": row["batch_id"],
            "stock_calculation_method": "latest_batch",
            "inventory_position_note": (
                f"Simplified: equals current_quantity from latest inventory_batch "
                f"(batch_id={row['batch_id']}, received_date={row['received_date']}). "
                "No purchase orders or backorders are tracked in this schema. "
                "Sum of all historical batch remainders is intentionally excluded "
                "(cumulative dataset artifact, not physical stock)."
            ),
        }
    finally:
        cur.close()


# ── Demand history ────────────────────────────────────────────────────────────

def get_demand_statistics(
    conn,
    product_id: str,
    window_end_exclusive: date,
    history_days: int = DEMAND_HISTORY_DAYS,
) -> dict:
    """
    Compute demand statistics from the daily_demand table for the given product
    over a rolling window of `history_days` calendar days ending the day before
    `window_end_exclusive` (i.e. [window_start, window_end_exclusive - 1]).

    Gap-fill methodology:
      daily_demand has no calendar gap-filling. Missing dates within a product's
      observed date range represent genuine zero-sale days. This function gap-fills
      zeros for missing calendar dates within
      [max(window_start, product_first_sale), window_end_exclusive - 1].
      Dates before the product's first ever sale are NOT included (pre-launch).

      Zero-sale days are demand variability, not stockout evidence.
      The XGBoost stockout model is deferred pending reliable stockout labels.

    Returns dict with:
        avg_daily_demand     : float — mean of gap-filled series
        demand_std_daily     : float — sample std dev of gap-filled series
        history_days_used    : int   — total calendar days in the gap-filled window
        nonzero_demand_days  : int   — actual rows in daily_demand for this window
        demand_source        : 'historical_avg'
        window_start         : date
        window_end           : date (= window_end_exclusive - 1)
        insufficient_history : bool — True if nonzero_demand_days < MIN_NONZERO_DAYS
    """
    import math

    window_start = window_end_exclusive - timedelta(days=history_days)
    window_end = window_end_exclusive - timedelta(days=1)

    cur = conn.cursor()
    try:
        # Product's first and last sale date (for gap-fill boundary)
        cur.execute(
            """
            SELECT MIN(date) AS first_date, MAX(date) AS last_date
            FROM daily_demand
            WHERE product_id = %s
            """,
            (product_id,),
        )
        date_row = cur.fetchone()
        if date_row is None or date_row["first_date"] is None:
            return {
                "avg_daily_demand": None,
                "demand_std_daily": None,
                "history_days_used": 0,
                "nonzero_demand_days": 0,
                "demand_source": "historical_avg",
                "window_start": window_start,
                "window_end": window_end,
                "insufficient_history": True,
            }

        product_first_date = date_row["first_date"]

        # Effective window start: do not include pre-launch dates
        effective_start = max(window_start, product_first_date)

        # Fetch actual demand rows in window
        cur.execute(
            """
            SELECT date, units_sold
            FROM daily_demand
            WHERE product_id = %s
              AND date >= %s
              AND date <= %s
            ORDER BY date
            """,
            (product_id, effective_start, window_end),
        )
        rows = cur.fetchall()

        # Build gap-filled calendar series
        demand_lookup = {r["date"]: int(r["units_sold"]) for r in rows}
        n_calendar_days = (window_end - effective_start).days + 1
        gap_filled = [
            demand_lookup.get(effective_start + timedelta(days=i), 0)
            for i in range(n_calendar_days)
        ]

        n_nonzero = sum(1 for x in gap_filled if x > 0)
        total_days = len(gap_filled)

        if total_days == 0:
            avg = 0.0
            std = 0.0
        else:
            avg = sum(gap_filled) / total_days
            if total_days > 1:
                variance = sum((x - avg) ** 2 for x in gap_filled) / (total_days - 1)
                std = math.sqrt(variance)
            else:
                std = 0.0

        return {
            "avg_daily_demand": round(avg, 6),
            "demand_std_daily": round(std, 6),
            "history_days_used": total_days,
            "nonzero_demand_days": n_nonzero,
            "demand_source": "historical_avg",
            "window_start": effective_start,
            "window_end": window_end,
            "insufficient_history": n_nonzero < MIN_NONZERO_DAYS,
        }
    finally:
        cur.close()


# ── Forecast data ─────────────────────────────────────────────────────────────

def get_latest_forecast_run(conn) -> Optional[dict]:
    """
    Return metadata for the latest completed forecast run.
    Returns None if no completed run exists.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT forecast_run_id, model_version, forecast_origin,
                   target_date_from, target_date_to, run_completed_at
            FROM forecast_run
            WHERE status = 'completed'
            ORDER BY run_completed_at DESC
            LIMIT 1
            """,
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()


def get_product_forecast_demand(conn, product_id: str, forecast_run_id: str) -> dict:
    """
    Return total 7-day forecast demand and per-day rows for a product
    from the specified forecast run.

    Returns dict with:
        total_7d         : float — sum of forecasted_units across all 7 days
        daily_rows       : list of {forecast_date, forecasted_units}
        n_days           : int   — number of forecast days found (expect 7)
        avg_daily_forecast: float — total_7d / n_days (or None if n_days == 0)
        missing_forecast  : bool  — True if n_days < 7
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT forecast_date, forecasted_units
            FROM demand_forecast
            WHERE forecast_run_id = %s AND product_id = %s
            ORDER BY forecast_date
            """,
            (forecast_run_id, product_id),
        )
        rows = cur.fetchall()
        n_days = len(rows)
        daily = [
            {"forecast_date": r["forecast_date"],
             "forecasted_units": float(r["forecasted_units"])}
            for r in rows
        ]
        total_7d = sum(r["forecasted_units"] for r in daily)
        avg_daily = total_7d / n_days if n_days > 0 else None
        return {
            "total_7d": round(total_7d, 4),
            "daily_rows": daily,
            "n_days": n_days,
            "avg_daily_forecast": round(avg_daily, 6) if avg_daily is not None else None,
            "missing_forecast": n_days < 7,
        }
    finally:
        cur.close()


# ── Assessment persistence ────────────────────────────────────────────────────

def upsert_assessment(conn, row: dict) -> int:
    """
    Insert or update a single inventory_assessment row.
    Uses ON CONFLICT (product_id, forecast_run_id) DO UPDATE to preserve
    history across different forecast runs while preventing duplicates within
    the same run.

    Returns the assessment_id of the inserted/updated row.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO inventory_assessment (
                product_id, assessed_at, forecast_run_id, model_version,
                available_stock, inventory_position,
                stock_calculation_method, inventory_position_note,
                forecast_demand_7d, avg_daily_demand, demand_source,
                demand_std_daily, history_days_used, nonzero_demand_days,
                lead_time_days,
                safety_stock, safety_stock_z, safety_stock_status,
                reorder_point, reorder_level_from_product, rop_status,
                suggested_replenishment_qty, minimum_order_qty,
                maximum_stock_level, replenishment_status,
                routing_decision, decision_reasons, calculation_notes,
                policy_version
            ) VALUES (
                %(product_id)s, NOW(), %(forecast_run_id)s, %(model_version)s,
                %(available_stock)s, %(inventory_position)s,
                %(stock_calculation_method)s, %(inventory_position_note)s,
                %(forecast_demand_7d)s, %(avg_daily_demand)s, %(demand_source)s,
                %(demand_std_daily)s, %(history_days_used)s, %(nonzero_demand_days)s,
                %(lead_time_days)s,
                %(safety_stock)s, %(safety_stock_z)s, %(safety_stock_status)s,
                %(reorder_point)s, %(reorder_level_from_product)s, %(rop_status)s,
                %(suggested_replenishment_qty)s, %(minimum_order_qty)s,
                %(maximum_stock_level)s, %(replenishment_status)s,
                %(routing_decision)s, %(decision_reasons)s, %(calculation_notes)s,
                %(policy_version)s
            )
            ON CONFLICT (product_id, forecast_run_id)
            DO UPDATE SET
                assessed_at                 = NOW(),
                model_version               = EXCLUDED.model_version,
                available_stock             = EXCLUDED.available_stock,
                inventory_position          = EXCLUDED.inventory_position,
                stock_calculation_method    = EXCLUDED.stock_calculation_method,
                inventory_position_note     = EXCLUDED.inventory_position_note,
                forecast_demand_7d          = EXCLUDED.forecast_demand_7d,
                avg_daily_demand            = EXCLUDED.avg_daily_demand,
                demand_source               = EXCLUDED.demand_source,
                demand_std_daily            = EXCLUDED.demand_std_daily,
                history_days_used           = EXCLUDED.history_days_used,
                nonzero_demand_days         = EXCLUDED.nonzero_demand_days,
                lead_time_days              = EXCLUDED.lead_time_days,
                safety_stock                = EXCLUDED.safety_stock,
                safety_stock_z              = EXCLUDED.safety_stock_z,
                safety_stock_status         = EXCLUDED.safety_stock_status,
                reorder_point               = EXCLUDED.reorder_point,
                reorder_level_from_product  = EXCLUDED.reorder_level_from_product,
                rop_status                  = EXCLUDED.rop_status,
                suggested_replenishment_qty = EXCLUDED.suggested_replenishment_qty,
                minimum_order_qty           = EXCLUDED.minimum_order_qty,
                maximum_stock_level         = EXCLUDED.maximum_stock_level,
                replenishment_status        = EXCLUDED.replenishment_status,
                routing_decision            = EXCLUDED.routing_decision,
                decision_reasons            = EXCLUDED.decision_reasons,
                calculation_notes           = EXCLUDED.calculation_notes,
                policy_version              = EXCLUDED.policy_version
            RETURNING assessment_id
            """,
            row,
        )
        result = cur.fetchone()
        conn.commit()
        return int(result["assessment_id"])
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def get_assessment_by_product(conn, product_id: str) -> Optional[dict]:
    """
    Return the most recent assessment for a product (by assessed_at DESC).
    Returns None if no assessment exists.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT * FROM inventory_assessment
            WHERE product_id = %s
            ORDER BY assessed_at DESC
            LIMIT 1
            """,
            (product_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()


def get_assessments_paginated(
    conn,
    routing_decision: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    Return paginated inventory assessments (latest per product).
    Optionally filter by routing_decision.
    """
    cur = conn.cursor()
    try:
        # Latest assessment per product via DISTINCT ON
        where = ""
        params: list = []
        if routing_decision:
            where = "WHERE ia.routing_decision = %s"
            params.append(routing_decision)

        count_sql = f"""
            SELECT COUNT(*) AS cnt
            FROM (
                SELECT DISTINCT ON (product_id) product_id, routing_decision
                FROM inventory_assessment
                ORDER BY product_id, assessed_at DESC
            ) ia
            {where}
        """
        cur.execute(count_sql, params)
        total = int(cur.fetchone()["cnt"])

        data_sql = f"""
            SELECT ia.*,
                   p.product_name, p.category, p.brand
            FROM (
                SELECT DISTINCT ON (product_id) *
                FROM inventory_assessment
                ORDER BY product_id, assessed_at DESC
            ) ia
            JOIN product p ON ia.product_id = p.product_id
            {where}
            ORDER BY ia.routing_decision, ia.product_id
            LIMIT %s OFFSET %s
        """
        cur.execute(data_sql, params + [limit, offset])
        items = [dict(r) for r in cur.fetchall()]
        return {"total": total, "items": items, "limit": limit, "offset": offset}
    finally:
        cur.close()


def get_assessment_overview(conn) -> dict:
    """
    Return summary counts of the latest assessment per product,
    broken down by routing_decision.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            WITH latest AS (
                SELECT DISTINCT ON (product_id)
                    product_id, routing_decision, assessed_at, forecast_run_id
                FROM inventory_assessment
                ORDER BY product_id, assessed_at DESC
            )
            SELECT
                COUNT(*)                                                  AS total_assessed,
                COUNT(*) FILTER (WHERE routing_decision = 'procurement_required')  AS procurement_required,
                COUNT(*) FILTER (WHERE routing_decision = 'excess_stock_review')   AS excess_stock_review,
                COUNT(*) FILTER (WHERE routing_decision = 'normal')               AS normal,
                COUNT(*) FILTER (WHERE routing_decision = 'insufficient_data')     AS insufficient_data,
                MAX(assessed_at)                                          AS last_assessed_at,
                (SELECT forecast_run_id FROM latest ORDER BY assessed_at DESC LIMIT 1) AS latest_run_id
            FROM latest
            """
        )
        row = cur.fetchone()
        return dict(row) if row else {}
    finally:
        cur.close()


def apply_schema(conn) -> None:
    """Apply schema_m3.sql to create the inventory_assessment table. Safe to rerun."""
    import sys
    from pathlib import Path

    sql_path = (
        Path(__file__).parent.parent.parent
        / "module3_inventory_optimization"
        / "sql"
        / "schema_m3.sql"
    )
    sql = sql_path.read_text(encoding="utf-8")
    cur = conn.cursor()
    try:
        cur.execute(sql)
        conn.commit()
        logger.info("Module 3 schema applied/verified (inventory_assessment).")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
