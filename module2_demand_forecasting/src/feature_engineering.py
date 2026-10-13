"""
src/feature_engineering.py
--------------------------
Module 2 — Leakage-Safe Feature Engineering (vectorised).

Feature set:
  Lags         : lag_1, lag_2, lag_3, lag_7, lag_14, lag_21, lag_28
  Rolling mean : roll_mean_7, roll_mean_14, roll_mean_28
  Rolling std  : roll_std_7, roll_std_14
  Calendar     : day_of_week, day_of_month, week_of_year, month, is_weekend
  Product      : product_code (LabelEncoded integer)

All rolling features use .shift(1) BEFORE the rolling window (per group),
so the feature at row T uses s[T-1], s[T-2], …, s[T-w] — never s[T].

Burn-in: rolling_mean_28 shift(1) needs 28 values in the shifted series.
The shifted series loses s[0] to NaN, so the first non-NaN position is index 28
(0-indexed), dropping indices 0..27 = 28 burn-in rows per product.

Performance: fully vectorised with groupby().shift() and groupby().transform().
No per-product Python loop; handles 1.8 M rows in < 60 s.
"""

import sys
import logging
from pathlib import Path
from typing import Tuple, List

import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder
import joblib

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import MODELS_DIR

logger = logging.getLogger(__name__)

FEATURE_COLS: List[str] = [
    "lag_1", "lag_2", "lag_3", "lag_7", "lag_14", "lag_21", "lag_28",
    "roll_mean_7", "roll_mean_14", "roll_mean_28",
    "roll_std_7", "roll_std_14",
    "day_of_week", "day_of_month", "week_of_year", "month", "is_weekend",
    "product_code",
]
TARGET_COL   = "units_sold"
BURN_IN_ROWS = 28   # rows dropped per product (roll_mean_28 with shift(1) → first valid at idx 28)


# ── Kept for unit-test compatibility (single-group entry point) ───────────────

def _add_lag_rolling_features(group: pd.DataFrame) -> pd.DataFrame:
    """
    Add lag and rolling features for a single product group.
    Used by unit tests and verify_no_leakage.
    For bulk processing, build_feature_matrix() uses the vectorised path.
    """
    g = group.copy().sort_values("date").reset_index(drop=True)
    s = g["units_sold"]

    for k in [1, 2, 3, 7, 14, 21, 28]:
        g[f"lag_{k}"] = s.shift(k)

    s_shifted = s.shift(1)
    for w in [7, 14, 28]:
        g[f"roll_mean_{w}"] = s_shifted.rolling(window=w, min_periods=w).mean()
    for w in [7, 14]:
        g[f"roll_std_{w}"]  = s_shifted.rolling(window=w, min_periods=w).std()

    return g


def add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add calendar features from the date column (vectorised, no copy needed)."""
    dates = pd.to_datetime(df["date"])
    df = df.copy()
    df["day_of_week"]  = dates.dt.dayofweek
    df["day_of_month"] = dates.dt.day
    df["week_of_year"] = dates.dt.isocalendar().week.astype(int)
    df["month"]        = dates.dt.month
    df["is_weekend"]   = (dates.dt.dayofweek >= 5).astype(int)
    return df


def encode_products(
    df: pd.DataFrame,
    encoder: LabelEncoder = None,
    fit: bool = True,
) -> Tuple[pd.DataFrame, LabelEncoder]:
    """
    LabelEncode product_id -> product_code (int).
    Uses a fast mapping dict instead of per-row apply().
    Unseen labels → -1.
    """
    df = df.copy()
    if fit:
        encoder = LabelEncoder()
        encoder.fit(df["product_id"].unique())
    label_map = {label: idx for idx, label in enumerate(encoder.classes_)}
    df["product_code"] = df["product_id"].map(label_map).fillna(-1).astype(int)
    return df, encoder


def build_feature_matrix(
    df: pd.DataFrame,
    encoder: LabelEncoder = None,
    fit_encoder: bool = True,
) -> Tuple[pd.DataFrame, LabelEncoder]:
    """
    Full feature engineering pipeline — fully vectorised (no per-product loop).

    Vectorisation strategy:
      - Sort by (product_id, date) once.
      - groupby('product_id')['units_sold'].shift(k) for all 7 lag features.
      - groupby('product_id')['units_sold'].transform(
            lambda s: s.shift(1).rolling(w, min_periods=w).mean()
        ) for rolling features.  The lambda is applied per group, so rolling
        never crosses a product boundary and shift(1) is within-group only.
      - Calendar features: pure vectorised dt accessor.
      - Encoding: dict.map — O(n), no per-row Python.
      - Drop NaN rows (burn-in) once at the end.

    Returns: (feature_df, encoder)
    """
    logger.info("Building feature matrix (vectorised)...")
    t0 = pd.Timestamp.now()

    # Ensure correct order so per-group shift/rolling is sequential in time
    df = df.sort_values(["product_id", "date"]).reset_index(drop=True)
    grp = df.groupby("product_id", sort=False)["units_sold"]

    # ── Lag features ──────────────────────────────────────────────────────────
    logger.info("  Lag features...")
    for k in [1, 2, 3, 7, 14, 21, 28]:
        df[f"lag_{k}"] = grp.shift(k)

    # ── Rolling features (leakage-safe: shift(1) before rolling, per group) ──
    logger.info("  Rolling mean features...")
    for w in [7, 14, 28]:
        df[f"roll_mean_{w}"] = grp.transform(
            lambda s: s.shift(1).rolling(w, min_periods=w).mean()  # noqa: B023
        )
    logger.info("  Rolling std features...")
    for w in [7, 14]:
        df[f"roll_std_{w}"] = grp.transform(
            lambda s: s.shift(1).rolling(w, min_periods=w).std()   # noqa: B023
        )

    # ── Calendar features ─────────────────────────────────────────────────────
    logger.info("  Calendar features...")
    df = add_calendar_features(df)

    # ── Product encoding ──────────────────────────────────────────────────────
    df, encoder = encode_products(df, encoder=encoder, fit=fit_encoder)

    # ── Drop burn-in rows (NaN in any FEATURE_COLS column) ───────────────────
    before = len(df)
    df = df.dropna(subset=FEATURE_COLS).reset_index(drop=True)
    after = len(df)
    elapsed = (pd.Timestamp.now() - t0).total_seconds()
    logger.info(
        f"Feature matrix: {before:,} rows -> {after:,} usable rows "
        f"({before - after:,} burn-in rows dropped) in {elapsed:.1f}s"
    )

    return df, encoder


def get_X_y(df: pd.DataFrame, split: str) -> Tuple[pd.DataFrame, pd.Series]:
    """Extract feature matrix X and target y for a given split."""
    subset = df[df["split"] == split]
    return subset[FEATURE_COLS], subset[TARGET_COL]


def save_encoder(encoder: LabelEncoder, path: Path = None) -> Path:
    path = path or (MODELS_DIR / "product_encoder.joblib")
    joblib.dump(encoder, path)
    logger.info(f"Product encoder saved to {path}")
    return path


def load_encoder(path: Path = None) -> LabelEncoder:
    path = path or (MODELS_DIR / "product_encoder.joblib")
    enc = joblib.load(path)
    logger.info(f"Product encoder loaded from {path}")
    return enc


def verify_no_leakage(df: pd.DataFrame, sample_product: str = None) -> bool:
    """
    Leakage check: for each feature row at date T, verify:
    - lag_1 == units_sold at T-1 (not T).
    - roll_mean_7 == mean of s[T-7 .. T-1]  (7 values, no s[T]).
    """
    pid = sample_product or df["product_id"].iloc[0]
    sub = df[df["product_id"] == pid].sort_values("date").reset_index(drop=True)

    for i in range(1, min(50, len(sub))):
        actual_prev = sub.loc[i - 1, "units_sold"]
        lag1_this   = sub.loc[i, "lag_1"]
        assert lag1_this == actual_prev, (
            f"Leakage in lag_1 for {pid} at {sub.loc[i,'date']}: "
            f"lag_1={lag1_this}, expected {actual_prev}"
        )

    for i in range(8, min(40, len(sub))):
        actual_roll = sub.loc[i, "roll_mean_7"]
        if pd.isna(actual_roll):
            continue
        # Correct window: s[i-7 .. i-1]
        window_vals = sub.loc[i - 7 : i - 1, "units_sold"].values
        if len(window_vals) < 7:
            continue
        expected_mean = float(np.mean(window_vals))
        assert abs(float(actual_roll) - expected_mean) < 1e-6, (
            f"Leakage in roll_mean_7 for {pid} at row {i}: "
            f"expected {expected_mean:.4f}, got {float(actual_roll):.4f}"
        )

    logger.info(f"Leakage verification passed for product {pid}.")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.path.insert(0, str(Path(__file__).parent))
    from data_loader import load_all
    from preprocessing import preprocess

    demand_df, _, _, _, _ = load_all(verbose=False)
    df, _ = preprocess(demand_df, verbose=False)
    feat_df, encoder = build_feature_matrix(df)

    print(f"\nFeature matrix shape: {feat_df.shape}")
    for sp in ["train", "val", "test"]:
        n = (feat_df["split"] == sp).sum()
        p = feat_df[feat_df["split"] == sp]["product_id"].nunique()
        print(f"  {sp}: {n:,} rows ({p} products)")

    verify_no_leakage(feat_df)
    print("Leakage verification: PASSED")
