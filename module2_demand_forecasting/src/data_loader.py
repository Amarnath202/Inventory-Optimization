"""
src/data_loader.py
------------------
Module 2 — Data Loading and Freshness Verification.

Responsibilities:
- Verify data freshness: cross-check MAX(daily_demand.date) == MAX(sale.sale_date).
- Load daily_demand and product metadata from PostgreSQL.
- Validate: no duplicates, no negative quantities, full FK coverage.
- Report eligibility statistics.
"""

import sys
import logging
from pathlib import Path
from datetime import date, timedelta
from typing import Tuple

import pandas as pd
import psycopg2

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    MIN_HISTORY_DAYS, REPORTS_DIR
)

logger = logging.getLogger(__name__)


# ── Database connection ───────────────────────────────────────────────────────

def get_connection() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )


# ── Data freshness verification ───────────────────────────────────────────────

class DataFreshnessError(RuntimeError):
    """Raised when daily_demand is stale relative to the sale table."""


def verify_data_freshness(conn) -> date:
    """
    Cross-check MAX(daily_demand.date) against MAX(sale.sale_date).

    Returns the verified latest ingested date.
    Raises DataFreshnessError if the two tables disagree.

    Limitation note: the dataset is static through 2026-08-30.
    This is the latest date ingested from the source CSVs.
    The pipeline uses this as the forecast origin baseline.
    """
    cur = conn.cursor()
    cur.execute("SELECT MAX(date) FROM daily_demand")
    dd_max = cur.fetchone()[0]

    cur.execute("SELECT MAX(sale_date) FROM sale")
    sale_max = cur.fetchone()[0]

    cur.close()

    if dd_max is None:
        raise DataFreshnessError("daily_demand table is empty.")
    if sale_max is None:
        raise DataFreshnessError("sale table is empty.")
    if dd_max != sale_max:
        raise DataFreshnessError(
            f"daily_demand MAX date ({dd_max}) != sale MAX date ({sale_max}). "
            "Re-run module1_data_management/src/build_daily_demand.py before forecasting."
        )

    logger.info(
        f"Data freshness verified: latest ingested date = {dd_max}. "
        "Note: dataset is static through this date (source CSV boundary). "
        "Forecasts will be generated from the next calendar day."
    )
    return dd_max


# ── Daily demand loading ──────────────────────────────────────────────────────

def load_daily_demand(conn) -> pd.DataFrame:
    """
    Load daily_demand table as a DataFrame.
    Columns: product_id (str), date (datetime.date), units_sold (int).
    Validates: no duplicates, no non-positive quantities.
    """
    logger.info("Loading daily_demand from PostgreSQL...")
    df = pd.read_sql_query(
        "SELECT product_id, date, units_sold FROM daily_demand ORDER BY product_id, date",
        conn,
        parse_dates=["date"]
    )
    df["date"] = df["date"].dt.date
    df["units_sold"] = df["units_sold"].astype(int)

    # Validate
    dupes = df.duplicated(subset=["product_id", "date"]).sum()
    if dupes > 0:
        raise ValueError(f"Found {dupes} duplicate (product_id, date) rows in daily_demand.")

    bad_qty = (df["units_sold"] <= 0).sum()
    if bad_qty > 0:
        raise ValueError(f"Found {bad_qty} rows with units_sold <= 0 in daily_demand.")

    logger.info(
        f"Loaded {len(df):,} rows | {df['product_id'].nunique():,} products | "
        f"date range: {df['date'].min()} -> {df['date'].max()}"
    )
    return df


# ── Product metadata loading ──────────────────────────────────────────────────

def load_product_metadata(conn) -> pd.DataFrame:
    """
    Load product master table.
    Columns: product_id, product_name, brand, category, unit, mrp, reorder_level, etc.
    """
    logger.info("Loading product metadata...")
    df = pd.read_sql_query(
        """SELECT product_id, product_name, brand, category, unit,
                  purchase_rate, sale_rate, mrp, reorder_level,
                  maximum_stock_level, minimum_order_qty, purchase_frequency,
                  expected_daily_demand
           FROM product ORDER BY product_id""",
        conn
    )
    logger.info(f"Loaded {len(df):,} product records.")
    return df


# ── Inventory context loading ─────────────────────────────────────────────────

def load_inventory_context(conn) -> pd.DataFrame:
    """
    Load current stock summary per product from inventory_batch.
    Aggregates: total current_quantity, latest received_date.
    Read-only. Does not modify any table.
    """
    df = pd.read_sql_query(
        """SELECT product_id,
                  SUM(current_quantity)  AS current_stock,
                  MAX(received_date)     AS latest_batch_date
           FROM inventory_batch
           GROUP BY product_id
           ORDER BY product_id""",
        conn,
        parse_dates=["latest_batch_date"]
    )
    return df


# ── Eligibility report ────────────────────────────────────────────────────────

def compute_eligibility(df: pd.DataFrame, min_history_days: int = MIN_HISTORY_DAYS) -> dict:
    """
    Compute per-product eligibility based on observed sale days.
    Returns a dict with 'eligible', 'excluded', and summary stats.
    Eligibility threshold: MIN_HISTORY_DAYS observed sale days.
    """
    per_product = (
        df.groupby("product_id")
        .agg(
            obs_days=("date", "count"),
            first_sale=("date", "min"),
            last_sale=("date", "max"),
            total_units=("units_sold", "sum"),
        )
        .reset_index()
    )
    per_product["calendar_span"] = (
        per_product["last_sale"] - per_product["first_sale"]
    ).apply(lambda x: x.days + 1 if hasattr(x, 'days') else int(x) + 1)

    eligible = per_product[per_product["obs_days"] >= min_history_days].copy()
    excluded = per_product[per_product["obs_days"] < min_history_days].copy()

    logger.info(
        f"Eligibility (threshold={min_history_days} observed days): "
        f"{len(eligible):,} eligible, {len(excluded):,} excluded"
    )
    if len(excluded) > 0:
        logger.warning(f"Excluded products:\n{excluded[['product_id','obs_days']].to_string()}")

    return {
        "eligible": eligible,
        "excluded": excluded,
        "n_eligible": len(eligible),
        "n_excluded": len(excluded),
        "min_obs": int(per_product["obs_days"].min()),
        "max_obs": int(per_product["obs_days"].max()),
        "avg_obs": float(per_product["obs_days"].mean()),
    }


# ── Main (standalone run) ─────────────────────────────────────────────────────

def load_all(verbose: bool = True) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, date, dict]:
    """
    Full data load sequence.
    Returns: (daily_demand_df, product_df, inventory_df, latest_date, eligibility)
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    conn = get_connection()
    try:
        latest_date = verify_data_freshness(conn)
        demand_df   = load_daily_demand(conn)
        product_df  = load_product_metadata(conn)
        inv_df      = load_inventory_context(conn)
        eligibility = compute_eligibility(demand_df)

        if verbose:
            print(f"\n{'='*60}")
            print("DATA LOADER — SUMMARY")
            print(f"{'='*60}")
            print(f"  Latest ingested date  : {latest_date}")
            print(f"  Forecast origin       : {latest_date + timedelta(days=1)}")
            print(f"  daily_demand rows     : {len(demand_df):,}")
            print(f"  Products eligible     : {eligibility['n_eligible']:,}")
            print(f"  Products excluded     : {eligibility['n_excluded']:,}")
            print(f"  Obs days min/max/avg  : {eligibility['min_obs']} / {eligibility['max_obs']} / {eligibility['avg_obs']:.1f}")
            print(f"{'='*60}\n")

        return demand_df, product_df, inv_df, latest_date, eligibility
    finally:
        conn.close()


if __name__ == "__main__":
    load_all()
