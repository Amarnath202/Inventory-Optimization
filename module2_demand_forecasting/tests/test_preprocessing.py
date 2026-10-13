"""
tests/test_preprocessing.py
----------------------------
Unit tests for gap-fill and split-assignment logic.
"""
import sys
from pathlib import Path
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.preprocessing import (
    build_gap_filled_series, assign_splits, compute_split_stats
)


def _make_demand(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["product_id", "date", "units_sold"])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


# ── Gap-fill tests ────────────────────────────────────────────────────────────

def test_gap_fill_no_gaps():
    """Products with no missing days should not gain rows."""
    df = _make_demand([
        ("P001", "2026-01-01", 5),
        ("P001", "2026-01-02", 3),
        ("P001", "2026-01-03", 7),
    ])
    filled = build_gap_filled_series(df)
    assert len(filled[filled["product_id"] == "P001"]) == 3


def test_gap_fill_adds_zero_rows():
    """A 3-day gap should yield 2 zero-demand rows."""
    df = _make_demand([
        ("P001", "2026-01-01", 5),
        ("P001", "2026-01-05", 3),   # gap: 2nd, 3rd, 4th Jan are missing
    ])
    filled = build_gap_filled_series(df)
    p = filled[filled["product_id"] == "P001"].sort_values("date")
    assert len(p) == 5                              # Jan 1–5
    assert p[p["date"] == date(2026, 1, 2)]["units_sold"].values[0] == 0
    assert p[p["date"] == date(2026, 1, 4)]["units_sold"].values[0] == 0


def test_gap_fill_no_extension_beyond_last_sale():
    """Gap-fill must NOT add rows after last_sale."""
    df = _make_demand([
        ("P001", "2026-01-01", 5),
        ("P001", "2026-01-03", 2),
    ])
    filled = build_gap_filled_series(df)
    p = filled[filled["product_id"] == "P001"]
    assert p["date"].max() == date(2026, 1, 3)


def test_gap_fill_no_extension_before_first_sale():
    """Gap-fill must NOT add rows before first_sale."""
    df = _make_demand([
        ("P001", "2026-03-10", 5),
        ("P001", "2026-03-15", 2),
    ])
    filled = build_gap_filled_series(df)
    p = filled[filled["product_id"] == "P001"]
    assert p["date"].min() == date(2026, 3, 10)


def test_gap_fill_multiple_products():
    """Each product is gap-filled independently."""
    df = _make_demand([
        ("P001", "2026-01-01", 5),
        ("P001", "2026-01-05", 3),
        ("P002", "2026-02-01", 2),
        ("P002", "2026-02-02", 4),
    ])
    filled = build_gap_filled_series(df)
    assert len(filled[filled["product_id"] == "P001"]) == 5  # 5-day span
    assert len(filled[filled["product_id"] == "P002"]) == 2  # no gaps


def test_gap_fill_preserves_units():
    """Original units_sold values must be preserved."""
    df = _make_demand([
        ("P001", "2026-01-01", 10),
        ("P001", "2026-01-03", 20),
    ])
    filled = build_gap_filled_series(df)
    p = filled[filled["product_id"] == "P001"].sort_values("date")
    assert p.iloc[0]["units_sold"] == 10
    assert p.iloc[2]["units_sold"] == 20


# ── Case C exclusion tests ────────────────────────────────────────────────────

def test_case_c_excluded_from_test():
    """Product whose last_sale < test_start is excluded from test evaluation."""
    # Create a product that last sold on 2026-07-01 (before TEST_START = 2026-07-16)
    dates = pd.date_range("2025-01-01", "2026-07-01", freq="D")
    rows = [("P001", d.date(), 1) for d in dates]
    df = _make_demand(rows)
    filled = build_gap_filled_series(df)
    df_splits = assign_splits(filled)
    # Test rows for this product should be excluded
    test_rows = df_splits[
        (df_splits["product_id"] == "P001") &
        (df_splits["split"] == "test")
    ]
    excluded_rows = df_splits[
        (df_splits["product_id"] == "P001") &
        (df_splits["split"] == "excluded_from_test")
    ]
    assert len(test_rows) == 0, "Case C product should not have 'test' split rows"
    # Actually for this product, last_sale = 2026-07-01 which is before TEST_START 2026-07-16
    # so any dates in test range would be post-last-sale => no rows at all in test range


def test_active_product_in_test():
    """Product with last_sale after test_start should have 'test' split rows."""
    dates = pd.date_range("2025-01-01", "2026-08-30", freq="14D")
    rows = [("PACTIVE", d.date(), 1) for d in dates]
    df = _make_demand(rows)
    filled = build_gap_filled_series(df)
    df_splits = assign_splits(filled)
    test_rows = df_splits[
        (df_splits["product_id"] == "PACTIVE") &
        (df_splits["split"] == "test")
    ]
    assert len(test_rows) > 0
