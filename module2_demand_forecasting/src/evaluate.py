"""
src/evaluate.py
---------------
Module 2 — Evaluation Metrics and Baseline.

Metrics:
  MAE   = mean(|actual - pred|)
  RMSE  = sqrt(mean((actual - pred)^2))
  WAPE  = SUM(|actual - pred|) / SUM(actual)   — undefined when SUM(actual)==0
  sMAPE = mean(2*|actual-pred| / (|actual|+|pred|+eps))

Baseline: 7-calendar-day moving average (MA7).
  - Uses 7 consecutive prior calendar days from the gap-filled series.
  - Includes zero-demand days.
  - Calculated per-product per-date.

Evaluation protocols:
  1. One-step-ahead: predict T using actual features up to T-1.
  2. Recursive 7-day horizon: from each origin, predict 7 days forward.

Case C exclusion: products with last_sale < split_start are excluded from
that split's evaluation (split == 'excluded_from_test' etc.).
"""

import sys
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import REPORTS_DIR

logger = logging.getLogger(__name__)

SMAPE_EPS = 1e-8


# ── Individual metric functions ───────────────────────────────────────────────

def mae(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.mean(np.abs(actual - pred)))


def rmse(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((actual - pred) ** 2)))


def wape(actual: np.ndarray, pred: np.ndarray) -> Optional[float]:
    """
    Aggregate WAPE. Returns None if SUM(actual) == 0.
    Formula: SUM(|actual - pred|) / SUM(actual)
    """
    total_actual = float(actual.sum())
    if total_actual == 0.0:
        return None
    return float(np.abs(actual - pred).sum() / total_actual)


def smape(actual: np.ndarray, pred: np.ndarray) -> float:
    """
    sMAPE with epsilon to prevent division by zero.
    Formula: mean(2 * |actual - pred| / (|actual| + |pred| + eps))
    """
    denom = np.abs(actual) + np.abs(pred) + SMAPE_EPS
    return float(np.mean(2.0 * np.abs(actual - pred) / denom))


def compute_metrics(actual: np.ndarray, pred: np.ndarray, label: str = "") -> dict:
    """Compute all four metrics for an array pair."""
    w = wape(actual, pred)
    result = {
        "label":  label,
        "n":      len(actual),
        "mae":    round(mae(actual, pred), 4),
        "rmse":   round(rmse(actual, pred), 4),
        "wape":   round(w, 4) if w is not None else None,
        "smape":  round(smape(actual, pred), 4),
    }
    return result


# ── MA7 Baseline ──────────────────────────────────────────────────────────────

def compute_ma7_baseline(
    filled_df: pd.DataFrame,
    eval_split: str,
) -> pd.DataFrame:
    """
    Compute MA7 baseline predictions for the evaluation split.

    MA7 for date T = mean of units_sold on the 7 calendar days prior to T
    (T-7, T-6, ..., T-1) from the gap-filled series (including zero-demand days).

    Args:
        filled_df: full gap-filled DataFrame (all splits, all products).
                   Must include 'split', 'product_id', 'date', 'units_sold'.
        eval_split: 'val' or 'test'.

    Returns:
        DataFrame with columns: product_id, date, actual, ma7_pred
        Only rows with split == eval_split and sufficient history (>= 7 prior days).
    """
    logger.info(f"Computing MA7 baseline for split='{eval_split}'...")
    results = []

    for pid, group in filled_df.groupby("product_id"):
        group = group.sort_values("date").reset_index(drop=True)

        # Only evaluate where split == eval_split
        eval_rows = group[group["split"] == eval_split]
        if len(eval_rows) == 0:
            continue

        # Build a date-indexed lookup for quick prior-day access
        date_to_idx = {row["date"]: i for i, row in group.iterrows()}

        for _, row in eval_rows.iterrows():
            target_date = row["date"]
            target_idx  = date_to_idx.get(target_date)
            if target_idx is None or target_idx < 7:
                continue

            # 7 consecutive calendar-day prior values from the gap-filled series
            prior_vals = group.loc[target_idx - 7 : target_idx - 1, "units_sold"].values
            if len(prior_vals) < 7:
                continue  # insufficient history; skip

            results.append({
                "product_id": pid,
                "date":       target_date,
                "actual":     int(row["units_sold"]),
                "ma7_pred":   float(np.mean(prior_vals)),
            })

    result_df = pd.DataFrame(results)
    logger.info(
        f"MA7 baseline computed: {len(result_df):,} rows for {result_df['product_id'].nunique()} products"
    )
    return result_df


# ── Per-product evaluation ────────────────────────────────────────────────────

def evaluate_per_product(
    eval_df: pd.DataFrame,
    pred_col: str = "lgbm_pred",
    label: str = "LightGBM"
) -> pd.DataFrame:
    """
    Compute per-product MAE, RMSE, WAPE, sMAPE.
    WAPE is None when total actual = 0 (zero-demand product in eval window).
    eval_df must have columns: product_id, actual, <pred_col>
    """
    rows = []
    for pid, group in eval_df.groupby("product_id"):
        actual = group["actual"].values.astype(float)
        pred   = group[pred_col].values.astype(float)
        w = wape(actual, pred)
        rows.append({
            "product_id": pid,
            "n":          len(actual),
            "total_actual": float(actual.sum()),
            "mae":        round(mae(actual, pred), 4),
            "rmse":       round(rmse(actual, pred), 4),
            "wape":       round(w, 4) if w is not None else None,
            "smape":      round(smape(actual, pred), 4),
            "model":      label,
        })
    return pd.DataFrame(rows)


# ── Full evaluation report ────────────────────────────────────────────────────

def run_evaluation(
    feat_df: pd.DataFrame,
    lgbm_model,
    filled_df: pd.DataFrame,
    eval_split: str = "test",
) -> dict:
    """
    Evaluate LightGBM (one-step-ahead) and MA7 baseline on the same eval split.
    Returns a dict with overall and per-product metrics.
    """
    from feature_engineering import FEATURE_COLS

    # ── LightGBM one-step-ahead ───────────────────────────────────────────────
    eval_feat = feat_df[feat_df["split"] == eval_split].copy()
    if len(eval_feat) == 0:
        logger.warning(f"No rows for split='{eval_split}' in feature matrix.")
        return {}

    X_eval = eval_feat[FEATURE_COLS]
    y_eval = eval_feat["units_sold"].values.astype(float)
    lgbm_preds = np.maximum(0, lgbm_model.predict(X_eval))

    eval_feat = eval_feat.copy()
    eval_feat["actual"]     = y_eval
    eval_feat["lgbm_pred"]  = lgbm_preds

    lgbm_overall = compute_metrics(y_eval, lgbm_preds, label=f"LightGBM_one_step_{eval_split}")
    lgbm_per_prod = evaluate_per_product(eval_feat, pred_col="lgbm_pred", label="LightGBM")

    # ── MA7 baseline ──────────────────────────────────────────────────────────
    ma7_df = compute_ma7_baseline(filled_df, eval_split=eval_split)

    if len(ma7_df) > 0:
        ma7_overall = compute_metrics(
            ma7_df["actual"].values.astype(float),
            ma7_df["ma7_pred"].values.astype(float),
            label=f"MA7_baseline_{eval_split}"
        )
        ma7_per_prod = evaluate_per_product(
            ma7_df.rename(columns={"ma7_pred": "lgbm_pred"}),
            pred_col="lgbm_pred",
            label="MA7"
        )
    else:
        ma7_overall  = {}
        ma7_per_prod = pd.DataFrame()

    # ── Comparison ────────────────────────────────────────────────────────────
    if ma7_overall:
        diff_mae  = round(lgbm_overall["mae"]  - ma7_overall["mae"],  4)
        diff_rmse = round(lgbm_overall["rmse"] - ma7_overall["rmse"], 4)
        lgbm_wape = lgbm_overall["wape"]
        ma7_wape  = ma7_overall["wape"]
        diff_wape = round(lgbm_wape - ma7_wape, 4) if (lgbm_wape and ma7_wape) else None
    else:
        diff_mae = diff_rmse = diff_wape = None

    # Products where baseline wins on MAE
    if len(lgbm_per_prod) > 0 and len(ma7_per_prod) > 0:
        merged = lgbm_per_prod[["product_id","mae"]].merge(
            ma7_per_prod[["product_id","mae"]], on="product_id", suffixes=("_lgbm","_ma7")
        )
        baseline_wins = merged[merged["mae_ma7"] < merged["mae_lgbm"]]
    else:
        baseline_wins = pd.DataFrame()

    result = {
        "eval_split":         eval_split,
        "protocol":           "one_step_ahead",
        "lgbm_overall":       lgbm_overall,
        "ma7_overall":        ma7_overall,
        "diff_mae":           diff_mae,
        "diff_rmse":          diff_rmse,
        "diff_wape":          diff_wape,
        "lgbm_per_product":   lgbm_per_prod,
        "ma7_per_product":    ma7_per_prod,
        "baseline_wins_count": len(baseline_wins),
        "baseline_wins":      baseline_wins,
        "n_eval_rows":        len(eval_feat),
        "n_eval_products":    eval_feat["product_id"].nunique(),
    }

    _log_eval_summary(result)
    return result


def _log_eval_summary(result: dict):
    lg = result["lgbm_overall"]
    ma = result["ma7_overall"]
    logger.info(f"\n{'='*60}")
    logger.info(f"EVALUATION — {result['eval_split'].upper()} ({result['protocol']})")
    logger.info(f"  Rows: {result['n_eval_rows']:,} | Products: {result['n_eval_products']}")
    logger.info(f"  {'Metric':<10} {'LightGBM':>12} {'MA7 Baseline':>14} {'Diff (LGB-MA7)':>16}")
    logger.info(f"  {'MAE':<10} {lg.get('mae','N/A'):>12} {ma.get('mae','N/A'):>14} {result['diff_mae'] or 'N/A':>16}")
    logger.info(f"  {'RMSE':<10} {lg.get('rmse','N/A'):>12} {ma.get('rmse','N/A'):>14} {result['diff_rmse'] or 'N/A':>16}")
    w_lg = lg.get('wape'); w_ma = ma.get('wape')
    logger.info(f"  {'WAPE':<10} {w_lg if w_lg else 'N/A':>12} {w_ma if w_ma else 'N/A':>14} {result['diff_wape'] or 'N/A':>16}")
    logger.info(f"  {'sMAPE':<10} {lg.get('smape','N/A'):>12} {ma.get('smape','N/A'):>14}")
    logger.info(f"  Baseline wins on MAE: {result['baseline_wins_count']} products")
    logger.info(f"{'='*60}")


def save_metrics(result: dict, split: str = "test"):
    """Save evaluation results to CSV and markdown report."""
    # Overall CSV
    rows = []
    for label, d in [("LightGBM", result["lgbm_overall"]), ("MA7_Baseline", result["ma7_overall"])]:
        if d:
            rows.append({"model": label, **{k: v for k, v in d.items() if k != "label"}})
    if rows:
        pd.DataFrame(rows).to_csv(REPORTS_DIR / f"forecasting_metrics_{split}.csv", index=False)
        logger.info(f"Metrics saved to reports/forecasting_metrics_{split}.csv")

    # Per-product CSV
    if "lgbm_per_product" in result and len(result["lgbm_per_product"]) > 0:
        result["lgbm_per_product"].to_csv(
            REPORTS_DIR / f"per_product_metrics_{split}.csv", index=False
        )
