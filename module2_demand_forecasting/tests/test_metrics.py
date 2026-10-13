"""
tests/test_metrics.py
----------------------
Unit tests for evaluation metrics, especially WAPE undefined behaviour.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.evaluate import mae, rmse, wape, smape, compute_metrics


# ── MAE ───────────────────────────────────────────────────────────────────────

def test_mae_zero():
    actual = np.array([1.0, 2.0, 3.0])
    pred   = np.array([1.0, 2.0, 3.0])
    assert mae(actual, pred) == 0.0


def test_mae_positive():
    actual = np.array([1.0, 3.0])
    pred   = np.array([2.0, 2.0])
    assert mae(actual, pred) == 1.0


# ── RMSE ─────────────────────────────────────────────────────────────────────

def test_rmse_zero():
    actual = np.array([5.0, 5.0, 5.0])
    pred   = np.array([5.0, 5.0, 5.0])
    assert rmse(actual, pred) == 0.0


def test_rmse_positive():
    actual = np.array([0.0, 4.0])
    pred   = np.array([0.0, 0.0])
    # errors: [0, 4] -> MSE = 8 -> RMSE = sqrt(8)
    assert abs(rmse(actual, pred) - np.sqrt(8)) < 1e-10


# ── WAPE ─────────────────────────────────────────────────────────────────────

def test_wape_none_when_all_zero_actual():
    """WAPE must return None (not 0.0) when total actual == 0."""
    actual = np.array([0.0, 0.0, 0.0])
    pred   = np.array([1.0, 2.0, 0.0])
    result = wape(actual, pred)
    assert result is None, f"Expected None, got {result}"


def test_wape_correct_value():
    """WAPE = SUM(|actual-pred|) / SUM(actual)."""
    actual = np.array([10.0, 20.0])
    pred   = np.array([8.0,  25.0])
    # |10-8| + |20-25| = 2 + 5 = 7; SUM(actual) = 30; WAPE = 7/30
    expected = 7.0 / 30.0
    assert abs(wape(actual, pred) - expected) < 1e-10


def test_wape_perfect_prediction():
    actual = np.array([5.0, 10.0, 15.0])
    pred   = np.array([5.0, 10.0, 15.0])
    assert wape(actual, pred) == 0.0


def test_wape_some_zero_actuals_non_zero_total():
    """When some rows are zero but total is not, WAPE should still be defined."""
    actual = np.array([0.0, 10.0])
    pred   = np.array([3.0, 10.0])
    # |0-3| + |10-10| = 3; SUM(actual) = 10; WAPE = 0.30
    result = wape(actual, pred)
    assert result is not None
    assert abs(result - 0.30) < 1e-10


# ── sMAPE ────────────────────────────────────────────────────────────────────

def test_smape_no_division_by_zero():
    """sMAPE with epsilon must not raise for all-zero actuals."""
    actual = np.array([0.0, 0.0])
    pred   = np.array([5.0, 0.0])
    result = smape(actual, pred)
    assert result >= 0.0
    assert not np.isnan(result)


def test_smape_perfect_prediction():
    actual = np.array([3.0, 6.0, 9.0])
    pred   = np.array([3.0, 6.0, 9.0])
    result = smape(actual, pred)
    assert result < 1e-6   # near zero


# ── compute_metrics ───────────────────────────────────────────────────────────

def test_compute_metrics_zero_actual():
    """compute_metrics must set wape=None when actual is all zero."""
    actual = np.array([0.0, 0.0])
    pred   = np.array([2.0, 3.0])
    result = compute_metrics(actual, pred, label="test")
    assert result["wape"] is None


def test_compute_metrics_normal():
    actual = np.array([10.0, 20.0])
    pred   = np.array([10.0, 20.0])
    result = compute_metrics(actual, pred, label="perfect")
    assert result["mae"]  == 0.0
    assert result["rmse"] == 0.0
    assert result["wape"] == 0.0
