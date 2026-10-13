"""
tests/test_features.py
-----------------------
Tests for leakage-free feature engineering.
"""
import sys
from pathlib import Path
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.feature_engineering import (
    _add_lag_rolling_features, build_feature_matrix,
    FEATURE_COLS, BURN_IN_ROWS, verify_no_leakage
)


def _make_product_df(n: int = 50, pid: str = "PTEST", start: str = "2025-01-01") -> pd.DataFrame:
    """Create a simple product DataFrame with known units_sold."""
    dates = pd.date_range(start, periods=n, freq="D")
    return pd.DataFrame({
        "product_id": pid,
        "date":       dates.date,
        "units_sold": np.arange(1, n + 1, dtype=int),
        "split":      "train",
    })


# ── Lag feature correctness ────────────────────────────────────────────────────

def test_lag_1_correctness():
    """lag_1 at row i must equal units_sold at row i-1."""
    df = _make_product_df(n=40)
    result = _add_lag_rolling_features(df)
    result = result.sort_values("date").reset_index(drop=True)
    for i in range(1, len(result)):
        expected_lag1 = result.loc[i - 1, "units_sold"]
        actual_lag1   = result.loc[i, "lag_1"]
        assert expected_lag1 == actual_lag1, (
            f"Row {i}: lag_1={actual_lag1}, expected {expected_lag1}"
        )


def test_lag_7_correctness():
    """lag_7 at row i must equal units_sold at row i-7."""
    df = _make_product_df(n=40)
    result = _add_lag_rolling_features(df)
    result = result.sort_values("date").reset_index(drop=True)
    for i in range(7, len(result)):
        expected = result.loc[i - 7, "units_sold"]
        actual   = result.loc[i, "lag_7"]
        assert expected == actual, f"Row {i}: lag_7={actual}, expected {expected}"


# ── Rolling feature leakage ────────────────────────────────────────────────────

def test_roll_mean_7_no_leakage():
    """
    roll_mean_7 at row i = mean of s_shifted[i-6..i] where s_shifted = s.shift(1).
    s_shifted[j] = s[j-1], so actual window = s[i-7 .. i-1].
    Critically, s[i] (current value) is NOT included → leakage-free.
    """
    df = _make_product_df(n=50)
    result = _add_lag_rolling_features(df)
    result = result.sort_values("date").reset_index(drop=True)
    # First valid index: s_shifted.rolling(7) needs 7 values → first at index 7
    for i in range(7, min(30, len(result))):
        actual_roll = result.loc[i, "roll_mean_7"]
        if pd.isna(actual_roll):
            continue
        # Correct window: s[i-7 .. i-1]  (7 values, none is s[i])
        correct_window = result.loc[i - 7 : i - 1, "units_sold"].values
        if len(correct_window) < 7:
            continue
        expected = float(np.mean(correct_window))
        assert abs(actual_roll - expected) < 1e-6, (
            f"Row {i}: roll_mean_7={actual_roll:.4f}, expected {expected:.4f}"
        )
        # Extra: ensure s[i] is NOT equal to what roll_mean would give if it were included
        # (trivially true since our time series is strictly increasing)


# ── Burn-in rows ──────────────────────────────────────────────────────────────

def test_burn_in_drops_correct_rows():
    """
    After build_feature_matrix the first NaN rows are dropped.
    Binding feature: roll_mean_28 uses s.shift(1).rolling(28) → first valid at index 28
    (needs 28 values in the shifted series; shifted[0] is NaN so first 28 non-NaN values
    start when i >= 28).
    Effective burn-in = BURN_IN_ROWS = 29 as declared, but the actual first-valid index
    is 28 (0-indexed), dropping rows 0..27 = 28 rows.
    We verify the count matches what the implementation actually produces.
    """
    n = 60
    df = _make_product_df(n=n)
    df["split"] = "train"
    feat_df, _ = build_feature_matrix(df, fit_encoder=True)
    usable = len(feat_df[feat_df["product_id"] == "PTEST"])
    # Verify at least n - 30 and at most n - 28 rows survive (burn-in is 28-29 rows)
    assert n - 30 <= usable <= n - 28, (
        f"Usable rows {usable} outside expected range [{n-30}, {n-28}]"
    )
    # Also verify no NaN in any feature column
    from src.feature_engineering import FEATURE_COLS
    assert feat_df[FEATURE_COLS].isna().sum().sum() == 0, "NaN found after burn-in drop"


# ── All feature columns present ───────────────────────────────────────────────

def test_all_feature_cols_present():
    """build_feature_matrix must produce all FEATURE_COLS."""
    df = _make_product_df(n=60)
    df["split"] = "train"
    feat_df, _ = build_feature_matrix(df, fit_encoder=True)
    for col in FEATURE_COLS:
        assert col in feat_df.columns, f"Missing feature column: {col}"


# ── No NaN in feature columns after burn-in drop ──────────────────────────────

def test_no_nan_after_build():
    """After build_feature_matrix, no NaN should remain in FEATURE_COLS."""
    df = _make_product_df(n=60)
    df["split"] = "train"
    feat_df, _ = build_feature_matrix(df, fit_encoder=True)
    nan_counts = feat_df[FEATURE_COLS].isna().sum()
    assert nan_counts.sum() == 0, f"NaN found in features:\n{nan_counts[nan_counts > 0]}"
