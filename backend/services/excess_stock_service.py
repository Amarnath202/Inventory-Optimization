"""
backend/services/excess_stock_service.py
-----------------------------------------
Module 5 — Excess Stock Management: core analytical service.

Implements:
  A. ABC Classification  — population-level cumulative revenue ranking
  B. XYZ Classification  — CV of 90-day gap-filled daily demand
  C. Excess-stock detection — overstock / slow_moving / dead_stock rules
  D. Recommendation logic  — based on ABC/XYZ matrix + excess category
  E. Persistence helpers   — apply schema, upsert assessment, queries

Design constraints (enforced here):
  - Does NOT modify stock quantities, write-off records, or place orders.
  - Does NOT overwrite Module 3 inventory_assessment rows.
  - All stock figures are the static 2026-08-01 snapshot (latest_batch).
  - Forecasts have origin 2026-08-31 (historical). Staleness is documented
    in every result as a data_warning.
  - ABC classification requires the full eligible product population, sorted
    by cumulative revenue DESC, to apply the proper threshold boundaries.
  - Zero-revenue and zero-demand edge cases are handled explicitly without
    division by zero or silent misclassification.

ABC methodology (population-level cumulative ranking):
  - Window: 12 months ending 2026-08-31 (cutoff = 2025-08-31).
  - Metric: SUM(quantity_sold × selling_price) per product.
  - Sort: revenue DESC.
  - Cumulative share thresholds:
      A: cumulative_share ≤ 0.70  (top 70% of total revenue)
      B: cumulative_share ≤ 0.90  (next 20%)
      C: remainder
  - Products with zero revenue: classified as C.
  - Products with no sale_item rows: classified as C with warning.

XYZ methodology (90-day demand variability):
  - Window: 90 calendar days ending 2026-08-30 (window_end exclusive = 2026-08-31).
  - Gap-fill: missing dates within [effective_start, 2026-08-30] receive 0.
  - Metric: CV = sample_std / mean (using n-1 denominator).
  - Thresholds:
      X: CV ≤ 0.30  (stable)
      Y: 0.30 < CV ≤ 0.60  (moderate)
      Z: CV > 0.60  (highly variable / intermittent)
  - Zero-mean products: classified as Z with warning 'zero_mean'.
  - No history in window: classified as Z with warning 'no_history'.
  - Insufficient nonzero days (< 7): classified as Z with warning 'insufficient_nonzero'.

Excess stock detection rules (applied independently, multiple can trigger):
  1. overstock:   inventory_position > maximum_stock_level × EXCESS_RATIO (1.5)
                  Consistent with Module 3 routing threshold.
  2. slow_moving: nonzero_demand_days_90d < MIN_NONZERO_DAYS (7)
                  AND inventory_position > 0
  3. dead_stock:  nonzero_demand_days_90d == 0 AND inventory_position > 0
  Category precedence: dead_stock > overstock > slow_moving > normal

EXCESS_RATIO and MIN_NONZERO_DAYS intentionally match Module 3 env-var defaults
so both modules use consistent policy. Override via M3_EXCESS_STOCK_RATIO and
M3_MIN_HISTORY_DAYS environment variables.
"""

import logging
import math
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Policy constants ──────────────────────────────────────────────────────────
ABC_A_THRESHOLD = 0.70   # cumulative revenue share ≤ 70% → class A
ABC_B_THRESHOLD = 0.90   # cumulative revenue share ≤ 90% → class B

XYZ_X_CV = 0.30          # CV ≤ 0.30 → X (stable)
XYZ_Y_CV = 0.60          # 0.30 < CV ≤ 0.60 → Y (moderate variability)

EXCESS_STOCK_RATIO = float(os.getenv("M3_EXCESS_STOCK_RATIO", "1.5"))
MIN_NONZERO_DAYS = int(os.getenv("M3_MIN_HISTORY_DAYS", "7"))

# Dataset boundaries (static historical snapshot)
DATA_ORIGIN = date(2026, 8, 31)           # forecast origin / assessment reference
ABC_WINDOW_DAYS = 365                      # 12-month revenue window
XYZ_WINDOW_DAYS = 90                       # demand-variability window

POLICY_VERSION = "v1.0"

class AssessmentLinkageError(ValueError):
    """Raised when an excess-stock assessment cannot be linked safely."""


# ── Recommendation map: (abc_class, xyz_class) → action ──────────────────────
RECOMMENDATION_MAP: dict = {
    ("A", "X"): "monitor_and_review_replenishment",
    ("A", "Y"): "investigate_demand_shift",
    ("A", "Z"): "investigate_demand_shift",
    ("B", "X"): "consider_promotion_or_discount",
    ("B", "Y"): "consider_promotion_or_discount",
    ("B", "Z"): "suspend_replenishment_and_review",
    ("C", "X"): "consider_promotion_or_discount",
    ("C", "Y"): "suspend_replenishment_and_review",
    ("C", "Z"): "review_for_discontinuation",
}

# For dead stock, override with stronger action
DEAD_STOCK_RECOMMENDATION = "review_for_discontinuation"
SLOW_MOVING_FALLBACK = "consider_promotion_or_discount"


# ─────────────────────────────────────────────────────────────────────────────
# A. ABC Classification (population-level)
# ─────────────────────────────────────────────────────────────────────────────

def compute_population_abc(conn) -> list:
    """
    Compute ABC classification for ALL products with sale data in the 12-month
    window, using proper cumulative revenue ranking.

    Returns a list of dicts, one per product, sorted by revenue DESC:
        product_id, revenue_12m, total_revenue_12m, revenue_share,
        cumulative_revenue_share, abc_rank, abc_class, abc_eligible
    """
    cutoff = DATA_ORIGIN - timedelta(days=ABC_WINDOW_DAYS)
    cur = conn.cursor()
    try:
        # Revenue per product in the 12-month window
        cur.execute(
            """
            SELECT
                si.product_id,
                COALESCE(SUM(si.quantity_sold * si.selling_price), 0) AS revenue_12m
            FROM sale_item si
            JOIN sale sa ON si.bill_no = sa.bill_no
            WHERE sa.sale_date >= %s AND sa.sale_date < %s
            GROUP BY si.product_id
            """,
            (cutoff, DATA_ORIGIN),
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    if not rows:
        logger.warning("[ABC] No sale_item rows found in 12-month window.")
        return []

    # Build revenue list
    revenues = [(r["product_id"], float(r["revenue_12m"])) for r in rows]

    # Sort by revenue DESC
    revenues.sort(key=lambda x: x[1], reverse=True)

    total_rev = sum(r for _, r in revenues)
    if total_rev <= 0:
        # All zero revenue — all C
        return [
            {
                "product_id": pid,
                "revenue_12m": rev,
                "total_revenue_12m": 0.0,
                "revenue_share": 0.0,
                "cumulative_revenue_share": 0.0,
                "abc_rank": i + 1,
                "abc_class": "C",
                "abc_eligible": True,
            }
            for i, (pid, rev) in enumerate(revenues)
        ]

    results = []
    cumulative = 0.0
    for rank, (pid, rev) in enumerate(revenues, start=1):
        share = rev / total_rev
        cumulative += share
        if cumulative <= ABC_A_THRESHOLD:
            abc_class = "A"
        elif cumulative <= ABC_B_THRESHOLD:
            abc_class = "B"
        else:
            abc_class = "C"
        results.append({
            "product_id": pid,
            "revenue_12m": round(rev, 4),
            "total_revenue_12m": round(total_rev, 4),
            "revenue_share": round(share, 6),
            "cumulative_revenue_share": round(cumulative, 6),
            "abc_rank": rank,
            "abc_class": abc_class,
            "abc_eligible": True,
        })
    return results


def get_product_abc(conn, product_id: str, abc_population: Optional[list] = None) -> dict:
    """
    Return the ABC classification for a single product.

    If abc_population is provided (pre-computed list from compute_population_abc),
    looks up this product in it — avoids re-scanning all products.
    If not provided, performs a full population computation (expensive for single call).

    Products absent from sale_item (no revenue, no sales) are classified as C
    with abc_eligible=True and a warning.
    """
    if abc_population is None:
        abc_population = compute_population_abc(conn)

    lookup = {r["product_id"]: r for r in abc_population}

    if product_id in lookup:
        return lookup[product_id]

    # Not in the sale data → zero revenue → class C
    # Still need total revenue for the share calculation
    total_rev = sum(r["total_revenue_12m"] for r in abc_population[:1]) if abc_population else 0.0
    return {
        "product_id": product_id,
        "revenue_12m": 0.0,
        "total_revenue_12m": total_rev,
        "revenue_share": 0.0,
        "cumulative_revenue_share": 1.0,
        "abc_rank": len(abc_population) + 1,
        "abc_class": "C",
        "abc_eligible": True,
        "abc_warning": "Product has no sale_item rows in the 12-month window. Revenue = 0; classified as C.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# B. XYZ Classification (per-product)
# ─────────────────────────────────────────────────────────────────────────────

def compute_product_xyz(conn, product_id: str) -> dict:
    """
    Compute XYZ class for a single product from 90-day gap-filled daily demand.

    Window: [DATA_ORIGIN - 90 days, DATA_ORIGIN - 1 day] (inclusive).
    Gap-fill: zeros for missing dates within [effective_start, window_end].
    Effective start = max(window_start, product_first_sale_date).

    Returns dict with:
        xyz_class, demand_cv, avg_daily_demand_90d, std_daily_demand_90d,
        nonzero_demand_days_90d, history_days_90d,
        xyz_eligible, xyz_warning
    """
    window_end = DATA_ORIGIN - timedelta(days=1)          # 2026-08-30
    window_start = DATA_ORIGIN - timedelta(days=XYZ_WINDOW_DAYS)   # 2026-06-02

    cur = conn.cursor()
    try:
        # Product's first ever sale date
        cur.execute(
            """
            SELECT MIN(date) AS first_date
            FROM daily_demand
            WHERE product_id = %s
            """,
            (product_id,),
        )
        row = cur.fetchone()
        first_date = row["first_date"] if row else None

        if first_date is None:
            return {
                "xyz_class": "Z",
                "demand_cv": None,
                "avg_daily_demand_90d": None,
                "std_daily_demand_90d": None,
                "nonzero_demand_days_90d": 0,
                "history_days_90d": 0,
                "xyz_eligible": False,
                "xyz_warning": "no_history: no daily_demand rows found for this product",
            }

        effective_start = max(window_start, first_date)

        # Fetch demand rows in window
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
    finally:
        cur.close()

    # Build gap-filled series
    demand_lookup = {r["date"]: int(r["units_sold"]) for r in rows}
    n_calendar = (window_end - effective_start).days + 1
    series = [
        demand_lookup.get(effective_start + timedelta(days=i), 0)
        for i in range(n_calendar)
    ]
    n_nonzero = sum(1 for x in series if x > 0)
    n_days = len(series)

    if n_days == 0:
        return {
            "xyz_class": "Z",
            "demand_cv": None,
            "avg_daily_demand_90d": 0.0,
            "std_daily_demand_90d": 0.0,
            "nonzero_demand_days_90d": 0,
            "history_days_90d": 0,
            "xyz_eligible": False,
            "xyz_warning": "no_history: effective window is empty",
        }

    avg = sum(series) / n_days
    std = math.sqrt(sum((x - avg) ** 2 for x in series) / max(n_days - 1, 1)) if n_days > 1 else 0.0

    # Handle edge cases
    if avg == 0.0:
        return {
            "xyz_class": "Z",
            "demand_cv": None,
            "avg_daily_demand_90d": 0.0,
            "std_daily_demand_90d": round(std, 4),
            "nonzero_demand_days_90d": n_nonzero,
            "history_days_90d": n_days,
            "xyz_eligible": False,
            "xyz_warning": "zero_mean: all demand observations in the 90-day window are zero",
        }

    cv = std / avg

    # Insufficient nonzero days → unreliable CV estimate
    if n_nonzero < MIN_NONZERO_DAYS:
        return {
            "xyz_class": "Z",
            "demand_cv": round(cv, 6),
            "avg_daily_demand_90d": round(avg, 4),
            "std_daily_demand_90d": round(std, 4),
            "nonzero_demand_days_90d": n_nonzero,
            "history_days_90d": n_days,
            "xyz_eligible": False,
            "xyz_warning": (
                f"insufficient_nonzero: only {n_nonzero} nonzero demand days in 90-day window "
                f"(minimum required: {MIN_NONZERO_DAYS}). CV computed but classification may be unreliable."
            ),
        }

    if cv <= XYZ_X_CV:
        xyz_class = "X"
    elif cv <= XYZ_Y_CV:
        xyz_class = "Y"
    else:
        xyz_class = "Z"

    return {
        "xyz_class": xyz_class,
        "demand_cv": round(cv, 6),
        "avg_daily_demand_90d": round(avg, 4),
        "std_daily_demand_90d": round(std, 4),
        "nonzero_demand_days_90d": n_nonzero,
        "history_days_90d": n_days,
        "xyz_eligible": True,
        "xyz_warning": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# C. Excess Stock Detection
# ─────────────────────────────────────────────────────────────────────────────

def detect_excess_stock(
    inventory_position: Optional[int],
    maximum_stock_level: Optional[int],
    nonzero_demand_days_90d: int,
) -> dict:
    """
    Apply excess-stock detection rules.

    Rules (all evaluated independently; multiple can trigger):
      1. dead_stock:   nonzero_demand_days == 0 AND inventory_position > 0
      2. overstock:    inventory_position > maximum_stock_level × EXCESS_RATIO
      3. slow_moving:  nonzero_demand_days < MIN_NONZERO_DAYS AND inventory_position > 0

    Category precedence for the single category field:
      dead_stock > overstock > slow_moving > normal

    Returns dict with:
        excess_category, is_overstock, is_slow_moving, is_dead_stock,
        excess_qty_estimate, excess_trigger_reasons
    """
    triggers = []
    is_dead_stock = False
    is_overstock = False
    is_slow_moving = False
    excess_qty = 0

    pos = inventory_position if inventory_position is not None else 0
    max_s = maximum_stock_level if maximum_stock_level is not None else 0

    # Rule 1: dead stock
    if nonzero_demand_days_90d == 0 and pos > 0:
        is_dead_stock = True
        triggers.append(
            f"dead_stock: zero demand days in 90-day window AND inventory_position={pos} > 0"
        )

    # Rule 2: overstock
    if max_s > 0 and pos > max_s * EXCESS_STOCK_RATIO:
        is_overstock = True
        threshold = max_s * EXCESS_STOCK_RATIO
        excess_qty = pos - max_s
        triggers.append(
            f"overstock: inventory_position({pos}) > maximum_stock_level({max_s}) × "
            f"{EXCESS_STOCK_RATIO} = {threshold:.1f}"
        )

    # Rule 3: slow moving (not dead stock, but low demand)
    if 0 < nonzero_demand_days_90d < MIN_NONZERO_DAYS and pos > 0:
        is_slow_moving = True
        triggers.append(
            f"slow_moving: nonzero_demand_days({nonzero_demand_days_90d}) < "
            f"MIN_NONZERO_DAYS({MIN_NONZERO_DAYS}) AND inventory_position={pos} > 0"
        )

    # Precedence for single category label
    if is_dead_stock:
        category = "dead_stock"
    elif is_overstock:
        category = "overstock"
    elif is_slow_moving:
        category = "slow_moving"
    else:
        category = "normal"

    if not triggers:
        triggers.append("inventory_position within normal operational range")

    return {
        "excess_category": category,
        "is_overstock": is_overstock,
        "is_slow_moving": is_slow_moving,
        "is_dead_stock": is_dead_stock,
        "excess_qty_estimate": excess_qty,
        "excess_trigger_reasons": "; ".join(triggers),
    }


# ─────────────────────────────────────────────────────────────────────────────
# D. Recommendation logic
# ─────────────────────────────────────────────────────────────────────────────

def determine_recommendation(
    abc_class: Optional[str],
    xyz_class: Optional[str],
    excess_category: str,
    abc_eligible: bool,
    xyz_eligible: bool,
) -> tuple:
    """
    Determine recommended action and a confidence label.

    Returns (recommended_action, confidence).
    """
    if excess_category == "normal":
        return "no_action_required", "high"

    # Dead stock: override with strongest action regardless of ABC/XYZ
    if excess_category == "dead_stock":
        return DEAD_STOCK_RECOMMENDATION, "high" if abc_eligible and xyz_eligible else "low"

    # If either classification is missing, fall back to slow_moving action
    if abc_class is None or xyz_class is None or not abc_eligible or not xyz_eligible:
        action = SLOW_MOVING_FALLBACK
        confidence = "low"
        return action, confidence

    action = RECOMMENDATION_MAP.get((abc_class, xyz_class), "manual_review")

    # Confidence: high if both classifications are reliable
    if abc_eligible and xyz_eligible:
        confidence = "high"
    elif abc_eligible or xyz_eligible:
        confidence = "medium"
    else:
        confidence = "low"

    return action, confidence


# ─────────────────────────────────────────────────────────────────────────────
# E. Full single-product assessment
# ─────────────────────────────────────────────────────────────────────────────

def assess_excess_stock_product(
    conn,
    product_id: str,
    assessment: Optional[dict] = None,
    abc_population: Optional[list] = None,
) -> dict:
    """
    Run the full Module 5 assessment for a single product.

    Parameters
    ----------
    conn : psycopg2 connection
    product_id : str
    assessment : dict | None
        Module 3 inventory_assessment dict. If provided, stock fields are
        reused. If None, stock is fetched independently.
    abc_population : list | None
        Pre-computed list from compute_population_abc(). If None, the full
        population is recomputed for this call (expensive).

    Returns
    -------
    dict suitable for upsert_excess_assessment() and the API response.
    """
    data_warnings = []

    # ── Stock inputs ──────────────────────────────────────────────────────────
    if assessment is None:
        assessment = get_latest_linked_inventory_assessment(conn, product_id)
    else:
        assessment_product = assessment.get("product_id")
        if assessment_product != product_id:
            raise AssessmentLinkageError(
                f"Inventory assessment product '{assessment_product}' does not "
                f"match requested product '{product_id}'."
            )
        if assessment.get("assessment_id") is None:
            raise AssessmentLinkageError(
                f"Inventory assessment for product '{product_id}' has no persisted assessment_id."
            )

    inventory_position = assessment.get("inventory_position")
    maximum_stock_level = assessment.get("maximum_stock_level")
    inventory_assessment_id = assessment.get("assessment_id")
    forecast_run_id = assessment.get("forecast_run_id")
    available_stock = assessment.get("available_stock")

    data_warnings.append(
        "STOCK: Available stock is a static 2026-08-01 snapshot (latest_batch). "
        "Physical stock may differ. Do not treat as a live inventory feed."
    )
    data_warnings.append(
        "FORECAST: Demand data ends 2026-08-30. Assessments are historical, "
        "not live operational data."
    )

    # ── ABC classification ────────────────────────────────────────────────────
    abc_result = get_product_abc(conn, product_id, abc_population)

    # ── XYZ classification ────────────────────────────────────────────────────
    xyz_result = compute_product_xyz(conn, product_id)

    # ── Use the more complete nonzero_demand_days source ──────────────────────
    # XYZ computation's 90-day window is the authoritative source for this metric.
    nonzero_90d = xyz_result["nonzero_demand_days_90d"]

    # ── Excess stock detection ────────────────────────────────────────────────
    excess = detect_excess_stock(
        inventory_position=inventory_position,
        maximum_stock_level=maximum_stock_level,
        nonzero_demand_days_90d=nonzero_90d,
    )

    # ── Recommendation ────────────────────────────────────────────────────────
    recommended_action, confidence = determine_recommendation(
        abc_class=abc_result.get("abc_class"),
        xyz_class=xyz_result.get("xyz_class"),
        excess_category=excess["excess_category"],
        abc_eligible=abc_result.get("abc_eligible", False),
        xyz_eligible=xyz_result.get("xyz_eligible", False),
    )

    # ── Compose recommendation notes ─────────────────────────────────────────
    abc_class = abc_result.get("abc_class")
    xyz_class = xyz_result.get("xyz_class")
    rev_share = abc_result.get("revenue_share", 0)
    cv = xyz_result.get("demand_cv")

    notes_parts = [
        f"ABC={abc_class} (revenue_share={rev_share:.4%}, rank={abc_result.get('abc_rank')}); ",
        f"XYZ={xyz_class} (CV={cv}); " if cv is not None else f"XYZ={xyz_class} (CV=N/A); ",
        f"Excess category={excess['excess_category']}; ",
        f"Recommended action: {recommended_action}. ",
        f"Confidence: {confidence}. ",
        "Requires manager approval before any stock action. ",
        "Module 5 does not modify stock quantities or place orders.",
    ]
    if abc_result.get("abc_warning"):
        notes_parts.append(f" ABC warning: {abc_result['abc_warning']}")
    if xyz_result.get("xyz_warning"):
        notes_parts.append(f" XYZ warning: {xyz_result['xyz_warning']}")
    if not abc_result.get("abc_eligible"):
        notes_parts.append(" ABC classification is based on C (insufficient sale data).")
    if not xyz_result.get("xyz_eligible"):
        notes_parts.append(" XYZ classification is tentative (insufficient demand history).")

    recommendation_notes = "".join(notes_parts)

    # Compile data warnings
    if xyz_result.get("xyz_warning"):
        data_warnings.append(f"XYZ: {xyz_result['xyz_warning']}")
    if abc_result.get("abc_warning"):
        data_warnings.append(f"ABC: {abc_result['abc_warning']}")

    return {
        "product_id": product_id,
        "inventory_assessment_id": inventory_assessment_id,
        "forecast_run_id": forecast_run_id,
        "available_stock": available_stock,
        "inventory_position": inventory_position,
        "maximum_stock_level": maximum_stock_level,
        "excess_qty_estimate": excess["excess_qty_estimate"],
        # ABC
        "abc_class": abc_class,
        "revenue_12m": abc_result.get("revenue_12m"),
        "total_revenue_12m": abc_result.get("total_revenue_12m"),
        "revenue_share": abc_result.get("revenue_share"),
        "cumulative_revenue_share": abc_result.get("cumulative_revenue_share"),
        "abc_rank": abc_result.get("abc_rank"),
        "abc_eligible": abc_result.get("abc_eligible", False),
        # XYZ
        "xyz_class": xyz_class,
        "demand_cv": xyz_result.get("demand_cv"),
        "avg_daily_demand_90d": xyz_result.get("avg_daily_demand_90d"),
        "std_daily_demand_90d": xyz_result.get("std_daily_demand_90d"),
        "nonzero_demand_days_90d": nonzero_90d,
        "history_days_90d": xyz_result.get("history_days_90d"),
        "xyz_eligible": xyz_result.get("xyz_eligible", False),
        "xyz_warning": xyz_result.get("xyz_warning"),
        # Excess detection
        "excess_category": excess["excess_category"],
        "is_overstock": excess["is_overstock"],
        "is_slow_moving": excess["is_slow_moving"],
        "is_dead_stock": excess["is_dead_stock"],
        "excess_trigger_reasons": excess["excess_trigger_reasons"],
        # Recommendation
        "recommended_action": recommended_action,
        "recommendation_notes": recommendation_notes,
        "requires_manager_approval": True,
        "data_warnings": " | ".join(data_warnings),
        "confidence": confidence,
        "policy_version": POLICY_VERSION,
    }


def get_latest_linked_inventory_assessment(
    conn,
    product_id: str,
    assessment_id: Optional[int] = None,
) -> dict:
    """
    Resolve and validate a persisted Module 3 assessment and its forecast run.

    The latest assessment is selected by assessed_at, with assessment_id as a
    deterministic tie-breaker. If an ID is supplied, that exact product/
    assessment pair is validated instead.
    """
    cur = conn.cursor()
    try:
        where = "WHERE ia.product_id = %s"
        params: tuple = (product_id,)
        if assessment_id is not None:
            where += " AND ia.assessment_id = %s"
            params = (product_id, assessment_id)

        order = (
            "ORDER BY ia.assessed_at DESC, ia.assessment_id DESC LIMIT 1"
            if assessment_id is None
            else ""
        )
        cur.execute(
            f"""
            SELECT ia.*,
                   fr.model_version AS linked_model_version,
                   fr.status AS linked_forecast_status,
                   EXISTS (
                       SELECT 1
                       FROM demand_forecast df
                       WHERE df.forecast_run_id = ia.forecast_run_id
                         AND df.product_id = ia.product_id
                   ) AS has_product_forecast,
                   EXISTS (
                       SELECT 1
                       FROM demand_forecast df
                       WHERE df.forecast_run_id = ia.forecast_run_id
                         AND df.product_id = ia.product_id
                         AND df.model_version <> fr.model_version
                   ) AS has_mismatched_forecast_model
            FROM inventory_assessment ia
            LEFT JOIN forecast_run fr
              ON fr.forecast_run_id = ia.forecast_run_id
            {where}
            {order}
            """,
            params,
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if row is None:
        if assessment_id is None:
            raise AssessmentLinkageError(
                f"No persisted Module 3 inventory assessment exists for product '{product_id}'."
            )
        raise AssessmentLinkageError(
            f"Inventory assessment {assessment_id} does not belong to product '{product_id}'."
        )

    resolved = dict(row)
    if resolved["forecast_run_id"] is None:
        raise AssessmentLinkageError(
            f"Inventory assessment {resolved['assessment_id']} for product "
            f"'{product_id}' has no associated forecast run."
        )
    if resolved.get("linked_forecast_status") != "completed":
        raise AssessmentLinkageError(
            f"Forecast run '{resolved['forecast_run_id']}' linked to inventory "
            f"assessment {resolved['assessment_id']} is missing or not completed."
        )
    if resolved.get("model_version") != resolved.get("linked_model_version"):
        raise AssessmentLinkageError(
            f"Forecast model version does not match inventory assessment "
            f"{resolved['assessment_id']} for product '{product_id}'."
        )
    if not resolved.get("has_product_forecast"):
        raise AssessmentLinkageError(
            f"Forecast run '{resolved['forecast_run_id']}' has no forecast rows "
            f"for product '{product_id}'."
        )
    if resolved.get("has_mismatched_forecast_model"):
        raise AssessmentLinkageError(
            f"Forecast rows for product '{product_id}' do not consistently match "
            f"forecast run '{resolved['forecast_run_id']}'."
        )
    return resolved


# ─────────────────────────────────────────────────────────────────────────────
# F. Schema and persistence
# ─────────────────────────────────────────────────────────────────────────────

def apply_excess_schema(conn) -> None:
    """Apply schema_m5.sql — creates excess_stock_assessment table. Safe to rerun."""
    sql_path = (
        Path(__file__).parent.parent.parent
        / "module3_inventory_optimization"
        / "sql"
        / "schema_m5.sql"
    )
    sql = sql_path.read_text(encoding="utf-8")
    cur = conn.cursor()
    try:
        cur.execute(sql)
        conn.commit()
        logger.info("Module 5 schema applied/verified (excess_stock_assessment).")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def upsert_excess_assessment(conn, row: dict) -> int:
    """
    Insert a new excess_stock_assessment row.
    Each call inserts a new row (history preserved).
    Returns the new assessment_id.
    """
    product_id = row.get("product_id")
    inventory_assessment_id = row.get("inventory_assessment_id")
    forecast_run_id = row.get("forecast_run_id")
    if not product_id:
        raise AssessmentLinkageError("Excess-stock assessment has no product_id.")
    if inventory_assessment_id is None or forecast_run_id is None:
        raise AssessmentLinkageError(
            f"Excess-stock assessment for product '{product_id}' requires both "
            "a persisted inventory_assessment_id and forecast_run_id."
        )

    cur = conn.cursor()
    try:
        linked_assessment = get_latest_linked_inventory_assessment(
            conn, product_id, assessment_id=inventory_assessment_id
        )
        if str(linked_assessment["forecast_run_id"]) != str(forecast_run_id):
            raise AssessmentLinkageError(
                f"Forecast run '{forecast_run_id}' does not belong to inventory "
                f"assessment {inventory_assessment_id} for product '{product_id}'."
            )
        latest_assessment = get_latest_linked_inventory_assessment(conn, product_id)
        if latest_assessment["assessment_id"] != linked_assessment["assessment_id"]:
            raise AssessmentLinkageError(
                f"Inventory assessment {inventory_assessment_id} is no longer the "
                f"latest assessment for product '{product_id}'."
            )
        cur.execute(
            """
            INSERT INTO excess_stock_assessment (
                product_id, assessed_at, inventory_assessment_id, forecast_run_id,
                available_stock, inventory_position, maximum_stock_level, excess_qty_estimate,
                abc_class, revenue_12m, total_revenue_12m, revenue_share,
                cumulative_revenue_share, abc_rank, abc_eligible,
                xyz_class, demand_cv, avg_daily_demand_90d, std_daily_demand_90d,
                nonzero_demand_days_90d, history_days_90d, xyz_eligible, xyz_warning,
                excess_category, is_overstock, is_slow_moving, is_dead_stock,
                excess_trigger_reasons,
                recommended_action, recommendation_notes, requires_manager_approval,
                data_warnings, confidence, policy_version
            ) VALUES (
                %(product_id)s, NOW(), %(inventory_assessment_id)s, %(forecast_run_id)s,
                %(available_stock)s, %(inventory_position)s, %(maximum_stock_level)s,
                %(excess_qty_estimate)s,
                %(abc_class)s, %(revenue_12m)s, %(total_revenue_12m)s, %(revenue_share)s,
                %(cumulative_revenue_share)s, %(abc_rank)s, %(abc_eligible)s,
                %(xyz_class)s, %(demand_cv)s, %(avg_daily_demand_90d)s,
                %(std_daily_demand_90d)s, %(nonzero_demand_days_90d)s, %(history_days_90d)s,
                %(xyz_eligible)s, %(xyz_warning)s,
                %(excess_category)s, %(is_overstock)s, %(is_slow_moving)s, %(is_dead_stock)s,
                %(excess_trigger_reasons)s,
                %(recommended_action)s, %(recommendation_notes)s,
                %(requires_manager_approval)s, %(data_warnings)s, %(confidence)s,
                %(policy_version)s
            )
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


def get_excess_assessment_by_product(conn, product_id: str) -> Optional[dict]:
    """Return the most recent excess_stock_assessment for a product."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT * FROM excess_stock_assessment
            WHERE product_id = %s
            ORDER BY assessed_at DESC, assessment_id DESC
            LIMIT 1
            """,
            (product_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()


def get_excess_assessments_paginated(
    conn,
    excess_category: Optional[str] = None,
    abc_class: Optional[str] = None,
    xyz_class: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    Return paginated excess_stock_assessment results (latest per product).
    Optional filters: excess_category, abc_class, xyz_class.
    """
    cur = conn.cursor()
    try:
        conditions = []
        params: list = []
        if excess_category:
            conditions.append("esa.excess_category = %s")
            params.append(excess_category)
        if abc_class:
            conditions.append("esa.abc_class = %s")
            params.append(abc_class)
        if xyz_class:
            conditions.append("esa.xyz_class = %s")
            params.append(xyz_class)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        count_sql = f"""
            SELECT COUNT(*) AS cnt
            FROM (
                SELECT DISTINCT ON (product_id)
                    product_id, excess_category, abc_class, xyz_class
                FROM excess_stock_assessment
                ORDER BY product_id, assessed_at DESC, assessment_id DESC
            ) esa
            {where}
        """
        cur.execute(count_sql, params)
        total = int(cur.fetchone()["cnt"])

        data_sql = f"""
            SELECT esa.*, p.product_name, p.category, p.brand
            FROM (
                SELECT DISTINCT ON (product_id) *
                FROM excess_stock_assessment
                ORDER BY product_id, assessed_at DESC, assessment_id DESC
            ) esa
            JOIN product p ON esa.product_id = p.product_id
            {where}
            ORDER BY
                CASE esa.excess_category
                    WHEN 'dead_stock' THEN 0
                    WHEN 'overstock' THEN 1
                    WHEN 'slow_moving' THEN 2
                    WHEN 'normal' THEN 3
                    ELSE 4
                END,
                esa.abc_rank ASC NULLS LAST,
                esa.product_id ASC
            LIMIT %s OFFSET %s
        """
        cur.execute(data_sql, params + [limit, offset])
        items = [dict(r) for r in cur.fetchall()]
        return {"total": total, "items": items, "limit": limit, "offset": offset}
    finally:
        cur.close()


def get_excess_assessment_summary(conn) -> dict:
    """Return summary counts of latest excess_stock_assessment per product."""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            WITH latest AS (
                SELECT DISTINCT ON (product_id)
                    product_id, excess_category, abc_class, xyz_class, assessed_at
                FROM excess_stock_assessment
                ORDER BY product_id, assessed_at DESC, assessment_id DESC
            )
            SELECT
                COUNT(*)                                                        AS total_assessed,
                COUNT(*) FILTER (WHERE excess_category = 'overstock')           AS overstock,
                COUNT(*) FILTER (WHERE excess_category = 'slow_moving')         AS slow_moving,
                COUNT(*) FILTER (WHERE excess_category = 'dead_stock')          AS dead_stock,
                COUNT(*) FILTER (WHERE excess_category = 'normal')              AS normal,
                COUNT(*) FILTER (WHERE abc_class = 'A')                         AS abc_a,
                COUNT(*) FILTER (WHERE abc_class = 'B')                         AS abc_b,
                COUNT(*) FILTER (WHERE abc_class = 'C')                         AS abc_c,
                COUNT(*) FILTER (WHERE xyz_class = 'X')                         AS xyz_x,
                COUNT(*) FILTER (WHERE xyz_class = 'Y')                         AS xyz_y,
                COUNT(*) FILTER (WHERE xyz_class = 'Z')                         AS xyz_z,
                MAX(assessed_at)                                                 AS last_assessed_at
            FROM latest
            """
        )
        row = cur.fetchone()
        return dict(row) if row else {}
    finally:
        cur.close()
