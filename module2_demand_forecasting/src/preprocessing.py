"""
src/preprocessing.py
--------------------
Module 2 — Preprocessing and Calendar Gap-Filling.

Gap-fill policy (Issue 2 / Phase 1 revised report):
  - Fill missing calendar dates BETWEEN each product's first and last observed sale.
  - Do NOT extend beyond a product's last_sale date.
  - Missing dates within the active window → units_sold = 0.

Evaluation window exclusion (Case C):
  - If a product's last_sale < evaluation window start, it is excluded from that
    window's metrics (no evaluation rows, not zero rows).
  - It still receives production forecasts.
"""

import sys
import logging
from pathlib import Path
from datetime import date, timedelta
from typing import Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    MIN_HISTORY_DAYS, TRAIN_END_DATE, VAL_END_DATE, TEST_END_DATE, REPORTS_DIR
)

logger = logging.getLogger(__name__)

TRAIN_END = date.fromisoformat(TRAIN_END_DATE)
VAL_END   = date.fromisoformat(VAL_END_DATE)
TEST_END  = date.fromisoformat(TEST_END_DATE)
VAL_START = TRAIN_END + timedelta(days=1)   # 2026-06-01
TEST_START = VAL_END  + timedelta(days=1)   # 2026-07-16


# ── Gap-filling ───────────────────────────────────────────────────────────────

def gap_fill_product(group: pd.DataFrame) -> pd.DataFrame:
    """
    Given a single product's observed sale rows (sorted by date),
    return a calendar-complete daily series from first_sale to last_sale.
    Missing days → units_sold = 0.
    Does NOT extend beyond last_sale.
    """
    pid   = group["product_id"].iloc[0]   # save before reindex drops it
    first = group["date"].min()
    last  = group["date"].max()
    full_range = pd.date_range(first, last, freq="D")

    # reindex on the date column only, keep units_sold
    units = group.set_index("date")["units_sold"].reindex(full_range).fillna(0).astype(int)
    result = pd.DataFrame({"date": full_range, "units_sold": units.values, "product_id": pid})
    result["date"] = result["date"].dt.date
    return result


def build_gap_filled_series(demand_df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply gap-fill to all products.
    Returns a calendar-complete DataFrame sorted by (product_id, date).
    Columns: product_id (str), date (datetime.date), units_sold (int)
    """
    logger.info("Applying calendar gap-fill (first_sale -> last_sale per product)...")
    demand_df = demand_df.copy()
    demand_df["date"] = pd.to_datetime(demand_df["date"])

    pieces = []
    for pid, group in demand_df.groupby("product_id"):
        first = group["date"].min()
        last  = group["date"].max()
        full_range = pd.date_range(first, last, freq="D")
        units = (
            group.set_index("date")["units_sold"]
            .reindex(full_range)
            .fillna(0)
            .astype(int)
        )
        piece = pd.DataFrame({
            "product_id": pid,
            "date":       full_range.date,
            "units_sold": units.values,
        })
        pieces.append(piece)

    filled = pd.concat(pieces, ignore_index=True)
    filled = filled.sort_values(["product_id", "date"]).reset_index(drop=True)

    before = len(demand_df)
    after  = len(filled)
    logger.info(
        f"Gap-fill complete: {before:,} observed rows -> {after:,} calendar rows "
        f"({after - before:,} zero-demand rows added)"
    )
    return filled



# ── Eligibility and split assignment ─────────────────────────────────────────

def assign_splits(filled_df: pd.DataFrame) -> pd.DataFrame:
    """
    Assign each row to a split: 'train', 'val', 'test', or 'forecast'.
    Evaluation Case C: products with last_sale < split_start are excluded
    from that split's evaluation (rows still exist for feature continuity,
    but split label = 'excluded_from_<split>').
    """
    df = filled_df.copy()
    # Compute per-product last_sale from the filled series
    last_sales = df.groupby("product_id")["date"].max().rename("last_sale")
    df = df.join(last_sales, on="product_id")

    # Primary split by date
    def _split(row):
        d = row["date"]
        if d <= TRAIN_END:
            return "train"
        elif d <= VAL_END:
            # Case C: last_sale < VAL_START → exclude from val eval
            if row["last_sale"] < VAL_START:
                return "excluded_from_val"
            return "val"
        elif d <= TEST_END:
            # Case C: last_sale < TEST_START → exclude from test eval
            if row["last_sale"] < TEST_START:
                return "excluded_from_test"
            return "test"
        else:
            return "forecast"

    df["split"] = df.apply(_split, axis=1)
    df = df.drop(columns=["last_sale"])
    return df


def compute_split_stats(df_with_splits: pd.DataFrame) -> dict:
    """Compute and log usable row counts per split."""
    counts = df_with_splits["split"].value_counts()
    stats = {
        "train_rows": int(counts.get("train", 0)),
        "val_rows": int(counts.get("val", 0)),
        "test_rows": int(counts.get("test", 0)),
        "excluded_from_val": int(counts.get("excluded_from_val", 0)),
        "excluded_from_test": int(counts.get("excluded_from_test", 0)),
    }
    # Products per split
    for split_name in ["train", "val", "test"]:
        n = df_with_splits[df_with_splits["split"] == split_name]["product_id"].nunique()
        stats[f"{split_name}_products"] = n

    logger.info(
        f"Split stats — train: {stats['train_rows']:,} rows ({stats['train_products']} products) | "
        f"val: {stats['val_rows']:,} rows ({stats['val_products']} products) | "
        f"test: {stats['test_rows']:,} rows ({stats['test_products']} products)"
    )
    return stats


# ── Main preprocessing pipeline ───────────────────────────────────────────────

def preprocess(demand_df: pd.DataFrame, verbose: bool = True) -> Tuple[pd.DataFrame, dict]:
    """
    Full preprocessing pipeline:
      1. Gap-fill (first_sale -> last_sale per product).
      2. Assign chronological splits with Case C exclusion.
      3. Compute and return split stats.

    Returns: (filled_df_with_splits, stats_dict)
    """
    filled = build_gap_filled_series(demand_df)
    df     = assign_splits(filled)
    stats  = compute_split_stats(df)

    if verbose:
        print(f"\n{'='*60}")
        print("PREPROCESSING — SUMMARY")
        print(f"{'='*60}")
        print(f"  Calendar rows total       : {len(df):,}")
        print(f"  Train rows                : {stats['train_rows']:,} ({stats['train_products']} products)")
        print(f"  Val rows                  : {stats['val_rows']:,} ({stats['val_products']} products)")
        print(f"  Test rows                 : {stats['test_rows']:,} ({stats['test_products']} products)")
        print(f"  Excluded from val eval    : {stats['excluded_from_val']:,} rows")
        print(f"  Excluded from test eval   : {stats['excluded_from_test']:,} rows")
        print(f"{'='*60}\n")

    return df, stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from data_loader import load_all
    demand_df, _, _, _, _ = load_all(verbose=False)
    df, stats = preprocess(demand_df)
    print(df.head(20))
