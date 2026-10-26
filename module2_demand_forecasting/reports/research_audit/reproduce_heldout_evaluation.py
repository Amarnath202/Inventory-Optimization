"""Reproduce one-step-ahead held-out metrics without retraining or database writes."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MODULE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MODULE_DIR))
sys.path.insert(0, str(MODULE_DIR / "src"))

from config.settings import TEST_END_DATE, TRAIN_END_DATE, VAL_END_DATE  # noqa: E402
from data_loader import (  # noqa: E402
    compute_eligibility,
    get_connection,
    load_daily_demand,
    verify_data_freshness,
)
from evaluate import (  # noqa: E402
    SMAPE_EPS,
    compute_ma7_baseline,
    run_evaluation,
)
from feature_engineering import FEATURE_COLS, build_feature_matrix  # noqa: E402
from preprocessing import TEST_START, VAL_START, preprocess  # noqa: E402
from train import load_model  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the specified output CSV if it already exists.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    output_path = args.output.resolve()
    if output_path.exists() and not args.overwrite:
        raise FileExistsError(
            f"Refusing to overwrite existing predictions file: {output_path}. "
            "Choose a new path or pass --overwrite explicitly."
        )

    conn = get_connection()
    try:
        conn.set_session(readonly=True)
        latest_date = verify_data_freshness(conn)
        demand_df = load_daily_demand(conn)
        eligibility = compute_eligibility(demand_df)
    finally:
        conn.close()

    if demand_df.empty:
        raise ValueError("daily_demand contains no observations.")
    missing_source_values = int(
        demand_df[["product_id", "date", "units_sold"]].isna().sum().sum()
    )
    if missing_source_values:
        raise ValueError(
            f"daily_demand contains {missing_source_values} missing source values."
        )
    if not np.isfinite(demand_df["units_sold"].to_numpy(dtype=float)).all():
        raise ValueError("daily_demand contains non-finite actual targets.")

    filled_df, split_stats = preprocess(demand_df, verbose=False)
    model, encoder, metadata, model_version = load_model("20260830")
    if metadata["feature_cols"] != FEATURE_COLS:
        raise ValueError("Model metadata feature order differs from FEATURE_COLS.")
    if list(model.booster_.feature_name()) != FEATURE_COLS:
        raise ValueError("LightGBM artifact feature order differs from FEATURE_COLS.")
    if str(latest_date) != metadata["latest_data_date"]:
        raise ValueError(
            f"Source latest date {latest_date} differs from artifact metadata "
            f"{metadata['latest_data_date']}."
        )

    feature_df, _ = build_feature_matrix(
        filled_df,
        encoder=encoder,
        fit_encoder=False,
    )
    if feature_df[FEATURE_COLS].isna().any().any():
        raise ValueError("Feature matrix contains missing feature values.")
    if not np.isfinite(feature_df[FEATURE_COLS].to_numpy(dtype=float)).all():
        raise ValueError("Feature matrix contains non-finite feature values.")

    summaries = {}
    feature_counts = {}
    for split in ("train", "val", "test"):
        subset = feature_df.loc[feature_df["split"] == split]
        feature_counts[split] = {
            "rows": int(len(subset)),
            "products": int(subset["product_id"].nunique()),
            "date_start": str(subset["date"].min()),
            "date_end": str(subset["date"].max()),
        }

    prediction_frames = []
    for split in ("val", "test"):
        result = run_evaluation(feature_df, model, filled_df, eval_split=split)
        eval_df = feature_df.loc[feature_df["split"] == split].copy()
        if eval_df.empty:
            raise ValueError(f"No usable feature rows in {split} split.")
        if eval_df.duplicated(["product_id", "date"]).any():
            raise ValueError(f"Duplicate product/date keys in {split} features.")

        actual = eval_df["units_sold"].to_numpy(dtype=float)
        raw_lgbm = np.asarray(model.predict(eval_df[FEATURE_COLS]), dtype=float)
        if not np.isfinite(raw_lgbm).all():
            raise ValueError(f"LightGBM returned non-finite predictions in {split}.")
        lgbm_pred = np.maximum(0.0, raw_lgbm)

        ma7_df = compute_ma7_baseline(filled_df, eval_split=split)
        ma7_df = ma7_df.rename(columns={"ma7_pred": "ma7_prediction"})
        aligned = eval_df[["product_id", "date", "units_sold"]].merge(
            ma7_df,
            on=["product_id", "date"],
            how="outer",
            validate="one_to_one",
            indicator=True,
        )
        if len(aligned) != len(eval_df) or not (aligned["_merge"] == "both").all():
            raise ValueError(f"MA7 does not align exactly with LightGBM rows in {split}.")
        if not np.array_equal(actual, aligned["units_sold"].to_numpy(dtype=float)):
            raise ValueError(f"Actual targets differ after MA7 alignment in {split}.")
        ma7_pred = aligned["ma7_prediction"].to_numpy(dtype=float)
        if not np.isfinite(ma7_pred).all():
            raise ValueError(f"MA7 returned non-finite predictions in {split}.")

        product_mean = eval_df.assign(_actual=actual).groupby(
            "product_id"
        )["_actual"].transform("mean").to_numpy(dtype=float)
        metrics = {}
        for label, prediction in (("LightGBM", lgbm_pred), ("MA7", ma7_pred)):
            error = actual - prediction
            total_actual = float(actual.sum())
            metrics[label] = {
                "n": int(len(actual)),
                "products": int(eval_df["product_id"].nunique()),
                "mae": float(np.mean(np.abs(error))),
                "mse": float(np.mean(error**2)),
                "rmse": float(np.sqrt(np.mean(error**2))),
                "wape_ratio": float(np.sum(np.abs(error)) / total_actual),
                "wape_percent": float(100.0 * np.sum(np.abs(error)) / total_actual),
                "smape": float(
                    np.mean(
                        2.0
                        * np.abs(error)
                        / (np.abs(actual) + np.abs(prediction) + SMAPE_EPS)
                    )
                ),
                "r2_pooled": float(
                    1.0
                    - np.sum(error**2)
                    / np.sum((actual - np.mean(actual)) ** 2)
                ),
                "r2_within_product": float(
                    1.0
                    - np.sum(error**2)
                    / np.sum((actual - product_mean) ** 2)
                ),
            }

        zero_actual = actual == 0
        zero_lgbm = lgbm_pred[zero_actual]
        zero_ma7 = ma7_pred[zero_actual]
        zero_actual_values = actual[zero_actual]
        summaries[split] = {
            "date_start": str(eval_df["date"].min()),
            "date_end": str(eval_df["date"].max()),
            "rows": int(len(eval_df)),
            "products": int(eval_df["product_id"].nunique()),
            "missing_actuals": int(eval_df["units_sold"].isna().sum()),
            "duplicate_keys": int(eval_df.duplicated(["product_id", "date"]).sum()),
            "raw_lgbm_negative_predictions_clipped": int((raw_lgbm < 0).sum()),
            "zero_actual_rows": int(zero_actual.sum()),
            "zero_actual_lgbm_exact_zero": int((zero_lgbm == 0).sum()),
            "zero_actual_lgbm_positive_below_smape_epsilon": int(
                ((zero_lgbm > 0) & (zero_lgbm < SMAPE_EPS)).sum()
            ),
            "zero_actual_lgbm_at_or_above_smape_epsilon": int(
                (zero_lgbm >= SMAPE_EPS).sum()
            ),
            "zero_actual_ma7_exact_zero": int((zero_ma7 == 0).sum()),
            "zero_actual_lgbm_smape": float(
                np.mean(
                    2.0
                    * np.abs(zero_actual_values - zero_lgbm)
                    / (np.abs(zero_actual_values) + np.abs(zero_lgbm) + SMAPE_EPS)
                )
            ),
            "zero_actual_ma7_smape": float(
                np.mean(
                    2.0
                    * np.abs(zero_actual_values - zero_ma7)
                    / (np.abs(zero_actual_values) + np.abs(zero_ma7) + SMAPE_EPS)
                )
            ),
            "metrics": metrics,
            "established_evaluator_metrics": {
                "LightGBM": result["lgbm_overall"],
                "MA7": result["ma7_overall"],
            },
        }

        rows = eval_df[["product_id", "date"]].copy()
        rows.insert(0, "split", split)
        rows["actual"] = actual
        rows["lightgbm_prediction"] = lgbm_pred
        rows["ma7_prediction"] = ma7_pred
        prediction_frames.append(rows)

    predictions = pd.concat(prediction_frames, ignore_index=True)
    if predictions.isna().any().any():
        raise ValueError("Prediction export contains missing values.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(output_path, index=False, float_format="%.17g")

    summary = {
        "source": {
            "table": "daily_demand",
            "rows": int(len(demand_df)),
            "products": int(demand_df["product_id"].nunique()),
            "date_start": str(demand_df["date"].min()),
            "date_end": str(demand_df["date"].max()),
            "verified_latest_date": str(latest_date),
            "duplicate_keys": int(
                demand_df.duplicated(["product_id", "date"]).sum()
            ),
            "missing_values": missing_source_values,
            "nonpositive_or_nonfinite_targets": int(
                (
                    ~np.isfinite(demand_df["units_sold"].to_numpy(dtype=float))
                    | (demand_df["units_sold"].to_numpy(dtype=float) <= 0)
                ).sum()
            ),
            "eligible_products": int(eligibility["n_eligible"]),
            "ineligible_products": int(eligibility["n_excluded"]),
            "gap_filled_calendar_rows": int(len(filled_df) - len(demand_df)),
        },
        "splits": {
            "raw_gap_filled_population": split_stats,
            "usable_feature_rows": feature_counts,
            "boundaries": {
                "train_end": TRAIN_END_DATE,
                "validation_start": str(VAL_START),
                "validation_end": VAL_END_DATE,
                "test_start": str(TEST_START),
                "test_end": TEST_END_DATE,
            },
        },
        "evaluation_protocol": "one_step_ahead",
        "production_forecast_horizon_calendar_days": 7,
        "evaluation_unit": "product x calendar date",
        "model": {
            "version": model_version,
            "artifact": f"models/lgbm_{model_version}.joblib",
            "best_iteration": int(model.best_iteration_),
            "feature_cols": FEATURE_COLS,
        },
        "smape_epsilon": SMAPE_EPS,
        "evaluations": summaries,
        "prediction_export": {
            "path": str(output_path),
            "rows": int(len(predictions)),
            "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
            "bytes": output_path.stat().st_size,
        },
    }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
