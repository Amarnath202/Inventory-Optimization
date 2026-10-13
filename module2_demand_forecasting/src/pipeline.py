"""
src/pipeline.py
---------------
Module 2 — End-to-End Pipeline Coordinator.

Phases:
  --create-schema  : Create demand_forecast and forecast_run tables (DDL only, safe to rerun)
  --train          : Full training pipeline (data → preprocess → features → train → evaluate)
  --forecast       : Generate forecasts using existing model (no retraining)
  (default)        : Run --forecast only

Advisory lock (pg_try_advisory_lock) prevents concurrent forecast runs across processes.
"""

import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import date, timedelta

import psycopg2

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    MODELS_DIR, REPORTS_DIR, LOGS_DIR, ADVISORY_LOCK_KEY
)

logger = logging.getLogger(__name__)


def get_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )


# ── Schema creation ───────────────────────────────────────────────────────────

def create_schema(conn):
    """Execute schema_m2.sql to create forecast tables. Safe to rerun (IF NOT EXISTS)."""
    sql_path = Path(__file__).parent.parent / "sql" / "schema_m2.sql"
    sql = sql_path.read_text(encoding="utf-8")
    cur = conn.cursor()
    cur.execute(sql)
    conn.commit()
    cur.close()
    logger.info("Module 2 schema created/verified (demand_forecast, forecast_run).")


# ── Training pipeline ─────────────────────────────────────────────────────────

def run_training_pipeline():
    """Full training: load → preprocess → features → train → evaluate → save."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from data_loader import load_all
    from preprocessing import preprocess
    from feature_engineering import build_feature_matrix, save_encoder, verify_no_leakage
    from train import train_model, save_model
    from evaluate import run_evaluation, save_metrics

    logger.info("=" * 60)
    logger.info("PIPELINE: TRAINING")
    logger.info("=" * 60)

    # 1. Load
    demand_df, product_df, inv_df, latest_date, eligibility = load_all()

    # 2. Preprocess
    filled_df, prep_stats = preprocess(demand_df)

    # 3. Feature engineering
    feat_df, encoder = build_feature_matrix(filled_df)
    save_encoder(encoder)

    # 4. Verify no leakage
    verify_no_leakage(feat_df)

    # 5. Report verified usable row counts
    row_counts = {}
    for split in ["train", "val", "test"]:
        n = int((feat_df["split"] == split).sum())
        p = int(feat_df[feat_df["split"] == split]["product_id"].nunique())
        row_counts[split] = {"rows": n, "products": p}
        logger.info(f"  Verified {split} rows: {n:,} ({p} products)")

    # 6. Train
    model, model_version, metadata = train_model(feat_df, latest_data_date=latest_date)
    metadata["verified_row_counts"] = row_counts
    save_model(model, encoder, metadata, model_version)

    # 7. Evaluate on val and test
    val_result  = run_evaluation(feat_df, model, filled_df, eval_split="val")
    test_result = run_evaluation(feat_df, model, filled_df, eval_split="test")
    save_metrics(val_result,  split="val")
    save_metrics(test_result, split="test")

    # 8. Save forecasting report
    _save_training_report(metadata, val_result, test_result, prep_stats, eligibility)

    logger.info(f"Training pipeline complete. Model version: {model_version}")
    return model_version


def _save_training_report(metadata, val_result, test_result, prep_stats, eligibility):
    """Write human-readable forecasting_report.md."""
    vl = val_result.get("lgbm_overall", {})
    vm = val_result.get("ma7_overall", {})
    tl = test_result.get("lgbm_overall", {})
    tm = test_result.get("ma7_overall", {})

    report = f"""# Module 2 — Forecasting Report
Generated: {date.today()}
Model version: {metadata['model_version']}

## Data Summary
- Latest ingested date: {metadata['latest_data_date']}
- Products eligible: {eligibility['n_eligible']} / {eligibility['n_eligible'] + eligibility['n_excluded']}
- Calendar rows after gap-fill: (see preprocessing stats)

## Verified Usable Feature Row Counts
| Split | Rows | Products |
|---|---:|---:|
| Train | {metadata['n_train_rows']:,} | {metadata['n_train_products']} |
| Val | {val_result.get('n_eval_rows',0):,} | {val_result.get('n_eval_products',0)} |
| Test | {test_result.get('n_eval_rows',0):,} | {test_result.get('n_eval_products',0)} |

## Evaluation — Validation Split (one-step-ahead)
| Metric | LightGBM | MA7 Baseline |
|---|---|---|
| MAE | {vl.get('mae','N/A')} | {vm.get('mae','N/A')} |
| RMSE | {vl.get('rmse','N/A')} | {vm.get('rmse','N/A')} |
| WAPE | {vl.get('wape','N/A')} | {vm.get('wape','N/A')} |
| sMAPE | {vl.get('smape','N/A')} | {vm.get('smape','N/A')} |

## Evaluation — Test Split (one-step-ahead)
| Metric | LightGBM | MA7 Baseline |
|---|---|---|
| MAE | {tl.get('mae','N/A')} | {tm.get('mae','N/A')} |
| RMSE | {tl.get('rmse','N/A')} | {tm.get('rmse','N/A')} |
| WAPE | {tl.get('wape','N/A')} | {tm.get('wape','N/A')} |
| sMAPE | {tl.get('smape','N/A')} | {tm.get('smape','N/A')} |

Baseline wins on test MAE: {test_result.get('baseline_wins_count', 'N/A')} products

## Known Limitations
1. Dataset is static through {metadata['latest_data_date']}. Forecasts are generated from the next day.
2. ~260 missing calendar days per product on average (zero-filled between first and last sale).
3. 2 products excluded from test evaluation (last sale before test period start).
4. 3 products with undefined per-product WAPE in validation (zero actual demand in that window).
"""
    (REPORTS_DIR / "forecasting_report.md").write_text(report, encoding="utf-8")
    logger.info("Forecasting report saved to reports/forecasting_report.md")


# ── Forecast pipeline ─────────────────────────────────────────────────────────

def run_forecast_pipeline(triggered_by: str = "pipeline"):
    """
    Generate forecasts using the existing trained model.
    Uses PostgreSQL advisory lock to prevent concurrent execution.
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from data_loader import load_all, verify_data_freshness
    from preprocessing import preprocess
    from train import load_model, get_current_model_version
    from forecast import (
        generate_forecasts, create_forecast_run, persist_forecasts,
        complete_forecast_run, fail_forecast_run
    )

    logger.info("=" * 60)
    logger.info(f"PIPELINE: FORECAST (triggered_by={triggered_by})")
    logger.info("=" * 60)

    conn = get_connection()
    run_id = None

    try:
        # ── Advisory lock ─────────────────────────────────────────────────────
        cur = conn.cursor()
        cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        acquired = cur.fetchone()[0]
        cur.close()

        if not acquired:
            logger.warning("Forecast skipped: advisory lock held by another process.")
            conn.close()
            return None

        from config.settings import FORECAST_HORIZON

        # ── Data freshness check ──────────────────────────────────────────────
        latest_date = verify_data_freshness(conn)
        forecast_origin = latest_date + timedelta(days=1)
        target_from     = forecast_origin
        target_to       = forecast_origin + timedelta(days=FORECAST_HORIZON - 1)

        logger.info(f"Forecast origin: {forecast_origin} -> {target_to}")

        # ── Load model ────────────────────────────────────────────────────────
        model, encoder, metadata, model_version = load_model()
        logger.info(f"Using model version: {model_version}")

        # ── Create run record ─────────────────────────────────────────────────
        run_id = create_forecast_run(
            conn, model_version, forecast_origin, target_from, target_to, triggered_by
        )

        # ── Load & preprocess data ────────────────────────────────────────────
        demand_df, _, _, _, _ = load_all(verbose=False)
        filled_df, _ = preprocess(demand_df, verbose=False)

        # ── Generate forecasts ────────────────────────────────────────────────
        forecast_df = generate_forecasts(
            filled_df, model, encoder, forecast_origin,
            run_id, model_version, horizon=FORECAST_HORIZON
        )

        # ── Persist ───────────────────────────────────────────────────────────
        rows_written = persist_forecasts(conn, forecast_df)
        complete_forecast_run(conn, run_id, rows_written)

        logger.info(f"Forecast pipeline complete. run_id={run_id}, rows={rows_written}")
        return run_id

    except Exception as e:
        logger.error(f"Forecast pipeline failed: {e}", exc_info=True)
        if run_id and conn and not conn.closed:
            fail_forecast_run(conn, run_id, str(e))
        raise

    finally:
        if conn and not conn.closed:
            try:
                cur = conn.cursor()
                cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
                conn.commit()
                cur.close()
            except Exception:
                pass
            conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Module 2 Pipeline")
    parser.add_argument("--create-schema", action="store_true", help="Create DB schema")
    parser.add_argument("--train", action="store_true", help="Run training pipeline")
    parser.add_argument("--forecast", action="store_true", help="Run forecast pipeline")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(str(LOGS_DIR / "pipeline.log"), encoding="utf-8"),
        ]
    )

    if args.create_schema:
        conn = get_connection()
        create_schema(conn)
        conn.close()

    if args.train:
        run_training_pipeline()

    if args.forecast or (not args.create_schema and not args.train):
        run_forecast_pipeline()


if __name__ == "__main__":
    main()
