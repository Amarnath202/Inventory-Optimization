"""
src/train.py
------------
Module 2 — LightGBM Training and Model Persistence.

Global model trained across all 3,046 eligible products.
Chronological split: Train (Jan 2025–May 2026) / Val (Jun–Jul 15 2026) / Test (Jul 16–Aug 30 2026).
model_version is fixed at training time (e.g. lgbm_20260831) and does NOT change
between forecast runs that use the same model artifact.
"""

import sys
import json
import logging
from pathlib import Path
from datetime import date

import numpy as np
import pandas as pd
import lightgbm as lgb
import joblib

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    MODELS_DIR, REPORTS_DIR,
    TRAIN_END_DATE, VAL_END_DATE, TEST_END_DATE
)
from feature_engineering import FEATURE_COLS, TARGET_COL, BURN_IN_ROWS

logger = logging.getLogger(__name__)

# ── LightGBM hyperparameters ──────────────────────────────────────────────────
LGBM_PARAMS = {
    "objective":        "regression_l1",   # MAE-optimal, robust to sparse/intermittent demand
    "n_estimators":     500,
    "learning_rate":    0.05,
    "num_leaves":       31,
    "max_depth":        -1,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "min_child_samples": 20,
    "random_state":     42,
    "n_jobs":           -1,
    "verbose":          -1,
}


def generate_model_version(latest_data_date: date = None) -> str:
    """
    Returns the version token used in model_metadata.json and latest_model_version.txt.
    Format: YYYYMMDD (date of the latest ingested data row).
    save_model() prepends 'lgbm_' when naming the .joblib file.
    """
    d = latest_data_date or date.today()
    return d.strftime('%Y%m%d')


def train_model(feat_df: pd.DataFrame, latest_data_date: date = None) -> tuple:
    """
    Train a global LightGBM model on the training split.
    Uses validation split for early stopping if available.

    Returns: (model, model_version, train_metadata)
    """
    logger.info("Starting LightGBM training...")
    logger.info(f"  Parameters: {LGBM_PARAMS}")

    train_mask = feat_df["split"] == "train"
    val_mask   = feat_df["split"] == "val"

    X_train = feat_df.loc[train_mask, FEATURE_COLS]
    y_train = feat_df.loc[train_mask, TARGET_COL].values.astype(float)
    X_val   = feat_df.loc[val_mask,   FEATURE_COLS]
    y_val   = feat_df.loc[val_mask,   TARGET_COL].values.astype(float)

    n_train = len(X_train)
    n_val   = len(X_val)
    logger.info(f"  Train rows: {n_train:,} | Val rows: {n_val:,}")
    logger.info(f"  Train date range: up to {TRAIN_END_DATE}")
    logger.info(f"  Val date range:   {TRAIN_END_DATE} -> {VAL_END_DATE}")

    model = lgb.LGBMRegressor(**LGBM_PARAMS)

    callbacks = [lgb.early_stopping(50, verbose=False), lgb.log_evaluation(period=100)]
    model.fit(
        X_train, y_train,
        eval_set=[(X_val, y_val)],
        callbacks=callbacks,
    )

    best_iter = model.best_iteration_ if hasattr(model, 'best_iteration_') else LGBM_PARAMS["n_estimators"]
    logger.info(f"  Best iteration: {best_iter}")

    # Val MAE
    val_preds = np.maximum(0, model.predict(X_val))
    val_mae   = float(np.mean(np.abs(y_val - val_preds)))
    logger.info(f"  Val MAE (one-step-ahead): {val_mae:.4f}")

    model_version = generate_model_version(latest_data_date)

    metadata = {
        "model_version":   model_version,
        "train_end":       TRAIN_END_DATE,
        "val_end":         VAL_END_DATE,
        "test_end":        TEST_END_DATE,
        "n_train_rows":    int(n_train),
        "n_val_rows":      int(n_val),
        "n_train_products": int(feat_df.loc[train_mask, "product_id"].nunique()),
        "feature_cols":    FEATURE_COLS,
        "target_col":      TARGET_COL,
        "burn_in_rows":    BURN_IN_ROWS,
        "lgbm_params":     LGBM_PARAMS,
        "best_iteration":  int(best_iter),
        "val_mae":         round(val_mae, 4),
        "trained_on":      str(date.today()),
        "latest_data_date": str(latest_data_date or date.today()),
    }

    return model, model_version, metadata


def save_model(model, encoder, metadata: dict, model_version: str):
    """Persist model, encoder, and metadata to MODELS_DIR."""
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    model_path    = MODELS_DIR / f"lgbm_{model_version}.joblib"
    encoder_path  = MODELS_DIR / "product_encoder.joblib"
    meta_path     = MODELS_DIR / "model_metadata.json"
    latest_path   = MODELS_DIR / "latest_model_version.txt"

    joblib.dump(model,   model_path)
    joblib.dump(encoder, encoder_path)

    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)

    with open(latest_path, "w") as f:
        f.write(model_version)

    logger.info(f"Model saved: {model_path}")
    logger.info(f"Encoder saved: {encoder_path}")
    logger.info(f"Metadata saved: {meta_path}")
    logger.info(f"Latest version: {model_version}")

    return model_path


def load_model(model_version: str = None):
    """Load the latest (or specified) model and encoder from MODELS_DIR."""
    if model_version is None:
        latest_path = MODELS_DIR / "latest_model_version.txt"
        if not latest_path.exists():
            raise FileNotFoundError("No trained model found. Run src/train.py first.")
        model_version = latest_path.read_text().strip()

    model_path = MODELS_DIR / f"lgbm_{model_version}.joblib"
    if not model_path.exists():
        raise FileNotFoundError(f"Model artifact not found: {model_path}")

    model   = joblib.load(model_path)
    encoder = joblib.load(MODELS_DIR / "product_encoder.joblib")

    with open(MODELS_DIR / "model_metadata.json") as f:
        metadata = json.load(f)

    logger.info(f"Model loaded: {model_path} (version={model_version})")
    return model, encoder, metadata, model_version


def get_current_model_version() -> str:
    """Return the current model version string from disk."""
    latest_path = MODELS_DIR / "latest_model_version.txt"
    if not latest_path.exists():
        raise FileNotFoundError("No trained model found.")
    return latest_path.read_text().strip()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(str(Path(__file__).parent.parent / "logs" / "train.log")),
        ]
    )
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from data_loader import load_all
    from preprocessing import preprocess
    from feature_engineering import build_feature_matrix, save_encoder, verify_no_leakage
    from evaluate import run_evaluation, save_metrics

    # ── Load & preprocess ────────────────────────────────────────────────────
    demand_df, product_df, inv_df, latest_date, eligibility = load_all()
    filled_df, stats = preprocess(demand_df)

    # ── Feature engineering ──────────────────────────────────────────────────
    feat_df, encoder = build_feature_matrix(filled_df)
    save_encoder(encoder)

    # Verify leakage on a sample product
    verify_no_leakage(feat_df)

    # ── Report actual usable row counts ──────────────────────────────────────
    for split in ["train", "val", "test"]:
        n = (feat_df["split"] == split).sum()
        prods = feat_df[feat_df["split"] == split]["product_id"].nunique()
        print(f"  Usable {split} rows: {n:,} ({prods} products)")

    # ── Train ────────────────────────────────────────────────────────────────
    model, model_version, metadata = train_model(feat_df, latest_data_date=latest_date)
    save_model(model, encoder, metadata, model_version)

    # ── Evaluate on val and test ──────────────────────────────────────────────
    val_result  = run_evaluation(feat_df, model, filled_df, eval_split="val")
    test_result = run_evaluation(feat_df, model, filled_df, eval_split="test")
    save_metrics(val_result,  split="val")
    save_metrics(test_result, split="test")

    print(f"\nTraining complete. Model version: {model_version}")
    print(f"Val  MAE={val_result['lgbm_overall']['mae']} | RMSE={val_result['lgbm_overall']['rmse']}")
    print(f"Test MAE={test_result['lgbm_overall']['mae']} | RMSE={test_result['lgbm_overall']['rmse']}")
