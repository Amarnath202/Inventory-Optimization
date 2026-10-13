"""
src/forecast.py
---------------
Module 2 — Recursive 7-Day Forecast Generation.

For each eligible product:
  1. Build the full historical series up to forecast_origin - 1.
  2. Recursively predict each day in [forecast_origin, forecast_origin+6].
  3. Use prior predictions for lag/rolling features when actual is unavailable.
  4. Apply non-negative floor (max(0, pred)).
  5. Record forecast_run_id, model_version, product_id, forecast_date, forecasted_units.

Forecast origin is derived dynamically from MAX(daily_demand.date) + 1 day.
Idempotency: ON CONFLICT DO NOTHING on (forecast_run_id, product_id, forecast_date).
"""

import sys
import uuid
import logging
from pathlib import Path
from datetime import date, timedelta
from typing import List, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import FORECAST_HORIZON
from feature_engineering import FEATURE_COLS

logger = logging.getLogger(__name__)


# ── Single-product recursive forecast ────────────────────────────────────────

def _build_feature_row(
    hist: pd.Series,         # history series indexed by date (calendar-complete up to origin-1)
    target_date: date,
    encoder,
    product_id: str,
) -> dict:
    """
    Build one feature row for target_date.
    hist is a pd.Series indexed by date, values = units_sold (may include prior predictions).
    """
    from sklearn.preprocessing import LabelEncoder
    known = set(encoder.classes_)
    prod_code = encoder.transform([product_id])[0] if product_id in known else -1

    # Lags
    lags = {}
    for k in [1, 2, 3, 7, 14, 21, 28]:
        lag_date = target_date - timedelta(days=k)
        lags[f"lag_{k}"] = float(hist.get(lag_date, 0))

    # Rolling: use shifted values ending at target_date - 1 backwards (days 1 to w)
    rolls = {}
    for w in [7, 14, 28]:
        vals = [hist.get(target_date - timedelta(days=i), 0) for i in range(1, w + 1)]
        rolls[f"roll_mean_{w}"] = float(np.mean(vals))
    for w in [7, 14]:
        vals = [hist.get(target_date - timedelta(days=i), 0) for i in range(1, w + 1)]
        rolls[f"roll_std_{w}"] = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0

    # Calendar
    d = pd.Timestamp(target_date)
    cal = {
        "day_of_week":  d.dayofweek,
        "day_of_month": d.day,
        "week_of_year": d.isocalendar()[1],
        "month":        d.month,
        "is_weekend":   int(d.dayofweek >= 5),
    }

    return {**lags, **rolls, **cal, "product_code": prod_code}


def forecast_product(
    history_series: pd.Series,   # indexed by date; calendar-complete
    forecast_dates: List[date],
    model,
    encoder,
    product_id: str,
) -> List[dict]:
    """
    Generate recursive forecasts for one product.
    Returns list of dicts with {date, forecasted_units}.
    """
    # Working copy: start with actual history, then append predictions
    working = dict(history_series)

    results = []
    for fd in forecast_dates:
        feat_row = _build_feature_row(working, fd, encoder, product_id)
        X = pd.DataFrame([feat_row])[FEATURE_COLS]
        pred = float(max(0.0, model.predict(X)[0]))
        working[fd] = pred
        results.append({"date": fd, "forecasted_units": round(pred, 4)})

    return results


# ── Build history series per product ─────────────────────────────────────────

def build_history_series(filled_df: pd.DataFrame) -> dict:
    """
    Build per-product history series (date -> units_sold) from the gap-filled DataFrame.
    Includes all splits (train + val + test) up to forecast_origin - 1.
    Returns: dict of {product_id: pd.Series indexed by date}
    """
    series = {}
    for pid, group in filled_df.groupby("product_id"):
        group = group.sort_values("date")
        s = pd.Series(
            group["units_sold"].values,
            index=group["date"].values
        )
        series[pid] = s
    return series


# ── Full forecast generation ──────────────────────────────────────────────────

def generate_forecasts(
    filled_df: pd.DataFrame,
    model,
    encoder,
    forecast_origin: date,
    forecast_run_id: str,
    model_version: str,
    horizon: int = FORECAST_HORIZON,
) -> pd.DataFrame:
    """
    Generate recursive forecasts for all eligible products.
    Returns a DataFrame with columns:
      forecast_run_id, product_id, forecast_date, forecasted_units, model_version
    """
    forecast_dates = [forecast_origin + timedelta(days=i) for i in range(horizon)]
    logger.info(
        f"Generating forecasts: origin={forecast_origin}, "
        f"dates={forecast_dates[0]} -> {forecast_dates[-1]}, "
        f"products={filled_df['product_id'].nunique()}"
    )

    history_series = build_history_series(filled_df)
    all_rows = []

    products = sorted(filled_df["product_id"].unique())
    for i, pid in enumerate(products):
        if i % 500 == 0:
            logger.info(f"  Forecasting product {i+1}/{len(products)}...")

        hist = history_series[pid]
        rows = forecast_product(hist, forecast_dates, model, encoder, pid)
        for r in rows:
            all_rows.append({
                "forecast_run_id":  forecast_run_id,
                "product_id":       pid,
                "forecast_date":    r["date"],
                "forecasted_units": r["forecasted_units"],
                "model_version":    model_version,
            })

    df = pd.DataFrame(all_rows)
    expected = len(products) * horizon
    logger.info(
        f"Forecast generation complete: {len(df):,} rows "
        f"(expected {expected:,} = {len(products)} products × {horizon} days)"
    )
    return df


# ── Database persistence ──────────────────────────────────────────────────────

def create_forecast_run(
    conn,
    model_version: str,
    forecast_origin: date,
    target_from: date,
    target_to: date,
    triggered_by: str = "pipeline",
) -> str:
    """
    Insert a new forecast_run row with status='running'.
    Returns the forecast_run_id (UUID string).
    """
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO forecast_run
               (model_version, run_started_at, status, forecast_origin,
                target_date_from, target_date_to, triggered_by)
           VALUES (%s, NOW(), 'running', %s, %s, %s, %s)
           RETURNING forecast_run_id""",
        (model_version, forecast_origin, target_from, target_to, triggered_by)
    )
    run_id = str(cur.fetchone()[0])
    conn.commit()
    cur.close()
    logger.info(f"Created forecast_run: id={run_id}")
    return run_id


def persist_forecasts(conn, forecast_df: pd.DataFrame) -> int:
    """
    Insert forecast rows using ON CONFLICT DO NOTHING (idempotent per run_id + product + date).
    Returns number of rows inserted.
    """
    cur = conn.cursor()
    rows_inserted = 0

    for _, row in forecast_df.iterrows():
        cur.execute(
            """INSERT INTO demand_forecast
                   (forecast_run_id, product_id, forecast_date, forecasted_units, model_version)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (forecast_run_id, product_id, forecast_date) DO NOTHING""",
            (
                row["forecast_run_id"],
                row["product_id"],
                row["forecast_date"],
                float(row["forecasted_units"]),
                row["model_version"],
            )
        )
        rows_inserted += cur.rowcount

    conn.commit()
    cur.close()
    logger.info(f"Persisted {rows_inserted:,} forecast rows.")
    return rows_inserted


def complete_forecast_run(conn, run_id: str, rows_written: int):
    cur = conn.cursor()
    cur.execute(
        """UPDATE forecast_run
           SET status='completed', run_completed_at=NOW(), rows_written=%s
           WHERE forecast_run_id=%s""",
        (rows_written, run_id)
    )
    conn.commit()
    cur.close()


def fail_forecast_run(conn, run_id: str, error: str):
    cur = conn.cursor()
    cur.execute(
        """UPDATE forecast_run
           SET status='failed', run_completed_at=NOW(), error_detail=%s
           WHERE forecast_run_id=%s""",
        (str(error)[:2000], run_id)
    )
    conn.commit()
    cur.close()


def get_latest_completed_run(conn) -> dict:
    """Return the most recent completed forecast run metadata."""
    cur = conn.cursor()
    cur.execute(
        """SELECT forecast_run_id, model_version, run_started_at, run_completed_at,
                  forecast_origin, target_date_from, target_date_to, rows_written
           FROM forecast_run
           WHERE status = 'completed'
           ORDER BY run_completed_at DESC
           LIMIT 1"""
    )
    row = cur.fetchone()
    cur.close()
    if row is None:
        return {}
    return {
        "forecast_run_id": str(row[0]),
        "model_version":   row[1],
        "run_started_at":  str(row[2]),
        "run_completed_at": str(row[3]),
        "forecast_origin": str(row[4]),
        "target_date_from": str(row[5]),
        "target_date_to":  str(row[6]),
        "rows_written":    row[7],
    }
