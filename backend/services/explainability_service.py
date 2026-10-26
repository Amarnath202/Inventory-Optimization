"""
backend/services/explainability_service.py
------------------------------------------
Module 6 — Explainability Service.

Provides three categories of explanation:

1. LightGBM TreeSHAP explanations (forecast_shap)
   - Loads the exact trained LightGBM artifact (lgbm_20260830.joblib).
   - Uses the exact 18-feature inference pipeline from Module 2.
   - Computes TreeSHAP values via shap.TreeExplainer on the actual model.
   - Returns feature contributions for an actual stored forecast row.
   - Model objective: regression_l1 (raw output space = units_sold, MAE-optimal).
   - SHAP baseline = expected_value from the explainer (E[f(x)] over training data).
   - Signed contributions: positive = pushes prediction above baseline,
     negative = pushes prediction below baseline.
   - Does NOT imply causality or future demand direction.

2. Module 3 inventory rule explanations (inventory_rule)
   - Formula-based safety stock, ROP, replenishment, and routing.
   - Uses stored inventory_assessment values — no recomputation.

3. Module 4 procurement rule explanations (procurement_rule)
   - Lead-time-based supplier ranking explanation.
   - Uses stored procurement_recommendation values.

4. Module 5 excess-stock statistical explanations (excess_stock_rule)
   - ABC/XYZ/excess-stock classification explanation.
   - Uses stored excess_stock_assessment values.

Design constraints:
  - Model is loaded once and cached at the module level (singleton pattern).
  - SHAP explainer is also cached after first construction.
  - No model is retrained; no business data is modified.
  - Fabricated SHAP values are never returned — errors surface as exceptions.
  - All explanations reference the stored source record and its actual values.
"""

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Module 2 paths ────────────────────────────────────────────────────────────
_PROJ_ROOT = Path(__file__).parent.parent.parent
_M2_ROOT = _PROJ_ROOT / "module2_demand_forecasting"
_MODELS_DIR = _M2_ROOT / "models"

# Feature column order (must match training — from feature_engineering.py)
FEATURE_COLS = [
    "lag_1", "lag_2", "lag_3", "lag_7", "lag_14", "lag_21", "lag_28",
    "roll_mean_7", "roll_mean_14", "roll_mean_28",
    "roll_std_7", "roll_std_14",
    "day_of_week", "day_of_month", "week_of_year", "month", "is_weekend",
    "product_code",
]
N_FEATURES = len(FEATURE_COLS)  # 18

# Model constants
MODEL_NAME = "LightGBM_demand_forecasting"
PREDICTION_UNITS = "units_sold"

# ── Singleton model + explainer cache ────────────────────────────────────────
_model = None
_encoder = None
_metadata = None
_model_version = None
_shap_explainer = None
_shap_available = None  # None = not yet checked, True/False after check


def _check_shap_available() -> bool:
    """Check once if SHAP is importable. Result is cached."""
    global _shap_available
    if _shap_available is not None:
        return _shap_available
    try:
        import shap  # noqa: F401
        _shap_available = True
    except ImportError:
        _shap_available = False
        logger.warning("[ExplainabilityService] SHAP not installed — TreeSHAP unavailable.")
    return _shap_available


def load_model_artifacts():
    """
    Load LightGBM model, encoder, and metadata (idempotent singleton).

    Returns (model, encoder, metadata, model_version) or raises on failure.
    Does NOT retrain the model.
    """
    global _model, _encoder, _metadata, _model_version

    if _model is not None:
        return _model, _encoder, _metadata, _model_version

    latest_path = _MODELS_DIR / "latest_model_version.txt"
    if not latest_path.exists():
        raise FileNotFoundError(
            f"Model version file not found at {latest_path}. "
            "The LightGBM model artifact may not have been trained yet."
        )

    version = latest_path.read_text().strip()
    model_path = _MODELS_DIR / f"lgbm_{version}.joblib"
    encoder_path = _MODELS_DIR / "product_encoder.joblib"
    meta_path = _MODELS_DIR / "model_metadata.json"

    for p in [model_path, encoder_path, meta_path]:
        if not p.exists():
            raise FileNotFoundError(f"Required model artifact not found: {p}")

    import joblib

    model = joblib.load(model_path)
    encoder = joblib.load(encoder_path)
    with open(meta_path) as f:
        metadata = json.load(f)

    # Validate feature columns match training expectations
    meta_features = metadata.get("feature_cols", [])
    if meta_features != FEATURE_COLS:
        raise ValueError(
            f"Feature column mismatch between model_metadata.json and service constants.\n"
            f"Metadata: {meta_features}\nService:  {FEATURE_COLS}"
        )

    _model = model
    _encoder = encoder
    _metadata = metadata
    _model_version = version
    logger.info(
        f"[ExplainabilityService] Model loaded: version={version}, "
        f"features={N_FEATURES}, objective={metadata.get('lgbm_params', {}).get('objective')}"
    )
    return _model, _encoder, _metadata, _model_version


def get_shap_explainer():
    """
    Build or retrieve the cached TreeSHAP explainer for the loaded model.

    Uses shap.TreeExplainer which is compatible with LightGBM's tree structure
    and computes exact TreeSHAP values (not approximations).

    Returns the explainer, or raises if SHAP is unavailable or model not loaded.
    """
    global _shap_explainer

    if _shap_explainer is not None:
        return _shap_explainer

    if not _check_shap_available():
        raise ImportError(
            "SHAP library is not installed. Install with: pip install shap. "
            "TreeSHAP explanations are unavailable until SHAP is installed."
        )

    model, _, _, _ = load_model_artifacts()

    import shap
    explainer = shap.TreeExplainer(model)
    _shap_explainer = explainer
    _ev = explainer.expected_value
    _ev_float = float(np.asarray(_ev).flat[0]) if hasattr(_ev, '__len__') else float(
        np.asarray(_ev).item() if hasattr(np.asarray(_ev), 'item') else _ev
    )
    logger.info(
        f"[ExplainabilityService] TreeExplainer built. "
        f"Expected value (baseline): {_ev_float:.6f}"
    )
    return _shap_explainer


# ── SHAP explanation for a single feature row ─────────────────────────────────

def compute_shap_for_row(feature_values: dict) -> dict:
    """
    Compute TreeSHAP values for a single feature row.

    Parameters
    ----------
    feature_values : dict
        Mapping of {feature_name: value} in any order.
        Must contain all 18 FEATURE_COLS keys.

    Returns
    -------
    dict with:
        shap_values          : list[float] (one per feature, ordered by FEATURE_COLS)
        baseline_value       : float (explainer.expected_value)
        prediction_value     : float (model prediction for this row, non-negative floored)
        feature_contributions: list[dict] with rank, name, value, shap, direction
        shap_sum_check       : float (baseline + sum(shap) — should match prediction)
    """
    # Validate all required features present BEFORE building explainer or loading model
    missing = [f for f in FEATURE_COLS if f not in feature_values]
    if missing:
        raise ValueError(f"Missing required feature columns: {missing}")

    model, _, _, _ = load_model_artifacts()
    explainer = get_shap_explainer()

    # Build single-row DataFrame in correct column order
    X = pd.DataFrame([{f: feature_values[f] for f in FEATURE_COLS}])[FEATURE_COLS]

    # Get model prediction (non-negative, matching forecast.py behaviour)
    raw_pred = float(model.predict(X)[0])
    prediction = max(0.0, raw_pred)

    # Compute exact TreeSHAP values
    shap_values = explainer.shap_values(X)

    # shap_values shape: (1, 18) for regression
    if hasattr(shap_values, "__len__") and len(shap_values) == 1:
        # Some SHAP versions return list of arrays
        sv = np.array(shap_values[0]).flatten() if isinstance(shap_values, list) else np.array(shap_values).flatten()
    else:
        sv = np.array(shap_values).flatten()

    if len(sv) != N_FEATURES:
        raise ValueError(
            f"SHAP returned {len(sv)} values, expected {N_FEATURES}. "
            f"Explainer/model feature mismatch."
        )

    # Convert expected_value safely — some SHAP versions return a 0-d or 1-d numpy array
    ev = explainer.expected_value
    if hasattr(ev, '__len__'):
        # 1-d array (multi-class or multi-output): take first element
        baseline = float(np.asarray(ev).flat[0])
    else:
        baseline = float(np.asarray(ev).item() if hasattr(np.asarray(ev), 'item') else ev)

    # Build ranked contributions (by absolute magnitude)
    contributions = []
    for i, feat_name in enumerate(FEATURE_COLS):
        contributions.append({
            "feature_name": feat_name,
            "feature_value": float(feature_values[feat_name]),
            "shap_contribution": round(float(sv[i]), 6),
            "direction": "positive" if sv[i] > 0 else ("negative" if sv[i] < 0 else "neutral"),
        })

    # Sort by absolute SHAP value descending (most influential first)
    contributions.sort(key=lambda c: abs(c["shap_contribution"]), reverse=True)
    for rank_i, c in enumerate(contributions, start=1):
        c["rank"] = rank_i

    shap_sum_check = round(baseline + float(np.sum(sv)), 6)

    return {
        "shap_values": [round(float(v), 6) for v in sv],
        "baseline_value": round(baseline, 6),
        "prediction_value": round(prediction, 6),
        "feature_contributions": contributions,
        "shap_sum_check": shap_sum_check,
    }


# ── Model info ────────────────────────────────────────────────────────────────

def get_model_info() -> dict:
    """Return model availability info without loading the model."""
    available = True
    error_msg = None
    version = None
    metadata = {}

    try:
        _, _, metadata, version = load_model_artifacts()
    except Exception as e:
        available = False
        error_msg = str(e)

    shap_ok = _check_shap_available()

    return {
        "model_name": MODEL_NAME,
        "model_version": version,
        "model_available": available,
        "shap_available": shap_ok,
        "tree_shap_explanation_available": available and shap_ok,
        "feature_count": N_FEATURES,
        "feature_cols": FEATURE_COLS,
        "objective": metadata.get("lgbm_params", {}).get("objective") if metadata else None,
        "val_mae": metadata.get("val_mae") if metadata else None,
        "trained_on": metadata.get("trained_on") if metadata else None,
        "latest_data_date": metadata.get("latest_data_date") if metadata else None,
        "error": error_msg,
    }


# ── Forecast SHAP explanation (uses stored DB forecast row) ───────────────────

def explain_forecast_from_db(conn, product_id: str, forecast_date: str,
                              run_id: Optional[str] = None) -> dict:
    """
    Explain a stored demand forecast using TreeSHAP.

    Reconstructs the exact feature row used for the forecast prediction by
    querying the actual historical demand data from daily_demand table.
    Does NOT re-run the forecast — uses the stored forecasted_units value.

    Parameters
    ----------
    conn : psycopg2 connection
    product_id : str
    forecast_date : str  (YYYY-MM-DD)
    run_id : str | None  (uses latest completed run if None)

    Returns explanation dict or raises on error.
    """
    from datetime import date, timedelta

    data_warnings = []

    # ── Resolve forecast run ─────────────────────────────────────────────────
    cur = conn.cursor()
    try:
        if run_id is None:
            cur.execute(
                """SELECT forecast_run_id, model_version, forecast_origin,
                          target_date_from, target_date_to, run_completed_at
                   FROM forecast_run
                   WHERE status = 'completed'
                   ORDER BY run_completed_at DESC LIMIT 1"""
            )
        else:
            cur.execute(
                """SELECT forecast_run_id, model_version, forecast_origin,
                          target_date_from, target_date_to, run_completed_at
                   FROM forecast_run
                   WHERE forecast_run_id = %s AND status = 'completed'""",
                (run_id,)
            )
        run_row = cur.fetchone()
    finally:
        cur.close()

    if run_row is None:
        raise ValueError(
            f"No completed forecast run found"
            + (f" with id={run_id}" if run_id else "")
            + ". Cannot explain an unexecuted forecast."
        )

    actual_run_id = str(run_row["forecast_run_id"])
    stored_model_version = run_row["model_version"]
    run_completed_at = run_row["run_completed_at"]
    forecast_origin = run_row["forecast_origin"]

    # Staleness check
    now = datetime.now(timezone.utc)
    if hasattr(run_completed_at, "tzinfo") and run_completed_at.tzinfo:
        age_days = (now - run_completed_at).days
    else:
        age_days = None

    if age_days is not None and age_days > 7:
        data_warnings.append(
            f"STALE_FORECAST: This forecast was completed {age_days} days ago "
            f"(run_completed_at={run_completed_at}). "
            "The prediction and feature values reflect historical data, not current conditions."
        )
    data_warnings.append(
        "STATIC_DATASET: Data ends 2026-08-30. Forecasts have origin 2026-08-31. "
        "These are historical model outputs, not live predictions."
    )

    # ── Fetch stored forecasted_units ─────────────────────────────────────────
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT forecasted_units, forecast_date
               FROM demand_forecast
               WHERE forecast_run_id = %s AND product_id = %s AND forecast_date = %s""",
            (actual_run_id, product_id, forecast_date)
        )
        fc_row = cur.fetchone()
    finally:
        cur.close()

    if fc_row is None:
        raise ValueError(
            f"No forecast record found for product='{product_id}', "
            f"date='{forecast_date}', run='{actual_run_id}'."
        )

    stored_prediction = float(fc_row["forecasted_units"])
    actual_fc_date = fc_row["forecast_date"]

    # ── Reconstruct feature row from historical demand ────────────────────────
    # The forecast uses lag/rolling values from up to 28 days before forecast_date.
    # We query the 60-day window before forecast_date to reconstruct these.
    target_date = pd.Timestamp(forecast_date).date()
    lookback_start = target_date - timedelta(days=60)

    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT date, units_sold FROM daily_demand
               WHERE product_id = %s AND date >= %s AND date < %s
               ORDER BY date""",
            (product_id, lookback_start, target_date)
        )
        hist_rows = cur.fetchall()
    finally:
        cur.close()

    if not hist_rows:
        raise ValueError(
            f"No historical demand data found for product='{product_id}' "
            f"in the 60-day lookback window before {forecast_date}. "
            "Cannot reconstruct feature values for this product/date."
        )

    # Build demand series dict: date -> units_sold
    hist_dict = {r["date"]: float(r["units_sold"]) for r in hist_rows}

    # Reconstruct features exactly as forecast.py _build_feature_row does
    model, encoder, metadata, model_version = load_model_artifacts()

    # Product encoding
    known_products = set(encoder.classes_)
    prod_code = int(encoder.transform([product_id])[0]) if product_id in known_products else -1
    if prod_code == -1:
        data_warnings.append(
            f"UNKNOWN_PRODUCT: product_id='{product_id}' was not in the training encoder. "
            "product_code=-1 is used (unseen product behaviour)."
        )

    # Lag features
    lags = {}
    for k in [1, 2, 3, 7, 14, 21, 28]:
        lag_date = target_date - timedelta(days=k)
        lags[f"lag_{k}"] = float(hist_dict.get(lag_date, 0.0))

    # Rolling features (matching forecast.py: mean/std of days 1..w before target_date)
    rolls = {}
    for w in [7, 14, 28]:
        vals = [hist_dict.get(target_date - timedelta(days=i), 0.0) for i in range(1, w + 1)]
        rolls[f"roll_mean_{w}"] = float(np.mean(vals))
    for w in [7, 14]:
        vals = [hist_dict.get(target_date - timedelta(days=i), 0.0) for i in range(1, w + 1)]
        rolls[f"roll_std_{w}"] = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0

    # Calendar features
    d = pd.Timestamp(target_date)
    cal = {
        "day_of_week":  int(d.dayofweek),
        "day_of_month": int(d.day),
        "week_of_year": int(d.isocalendar()[1]),
        "month":        int(d.month),
        "is_weekend":   int(d.dayofweek >= 5),
    }

    feature_values = {**lags, **rolls, **cal, "product_code": prod_code}

    # Verify all features present
    missing = [f for f in FEATURE_COLS if f not in feature_values]
    if missing:
        raise ValueError(f"Internal error: reconstructed feature row missing columns: {missing}")

    # ── Compute SHAP ──────────────────────────────────────────────────────────
    shap_result = compute_shap_for_row(feature_values)

    # Cross-check: SHAP prediction vs stored prediction (should be close)
    shap_pred = shap_result["prediction_value"]
    discrepancy = abs(shap_pred - stored_prediction)
    if discrepancy > 0.1:
        data_warnings.append(
            f"FEATURE_RECONSTRUCTION_NOTE: SHAP-reconstructed prediction ({shap_pred:.4f}) "
            f"differs from stored forecasted_units ({stored_prediction:.4f}) by {discrepancy:.4f}. "
            "This may occur because the recursive forecast used predicted values from prior "
            "forecast days as lag inputs, while this explanation reconstructs from stored "
            "historical actuals only. The SHAP contributions reflect the reconstructed feature "
            "values, not the exact recursive-forecast inputs."
        )

    # ── Interpretation narrative ──────────────────────────────────────────────
    baseline = shap_result["baseline_value"]
    top_contribs = shap_result["feature_contributions"][:5]
    pos_top = [c for c in top_contribs if c["direction"] == "positive"]
    neg_top = [c for c in top_contribs if c["direction"] == "negative"]

    interp_parts = [
        f"The LightGBM model predicts {shap_pred:.2f} units_sold for {forecast_date}. "
        f"The baseline (average model output across training data) is {baseline:.2f} units. "
    ]
    if pos_top:
        names = ", ".join(c["feature_name"] for c in pos_top[:3])
        interp_parts.append(
            f"Features pushing the prediction above baseline: {names}. "
        )
    if neg_top:
        names = ", ".join(c["feature_name"] for c in neg_top[:3])
        interp_parts.append(
            f"Features pushing the prediction below baseline: {names}. "
        )
    interp_parts.append(
        "SHAP contributions indicate relative importance for this specific prediction "
        "only — they do not imply causality or predict future demand direction."
    )

    return {
        "explanation_type": "forecast_shap",
        "explanation_method": "tree_shap",
        "product_id": product_id,
        "forecast_date": str(actual_fc_date),
        "forecast_run_id": actual_run_id,
        "model_name": MODEL_NAME,
        "model_version": model_version,
        "stored_model_version": stored_model_version,
        "objective": metadata.get("lgbm_params", {}).get("objective"),
        "prediction_value": shap_result["prediction_value"],
        "stored_prediction_value": stored_prediction,
        "prediction_units": PREDICTION_UNITS,
        "baseline_value": shap_result["baseline_value"],
        "shap_sum_check": shap_result["shap_sum_check"],
        "feature_count": N_FEATURES,
        "feature_contributions": shap_result["feature_contributions"],
        "feature_values_used": feature_values,
        "interpretation": " ".join(interp_parts),
        "data_warnings": data_warnings,
        "run_completed_at": str(run_completed_at),
    }


# ── Module 3 — Inventory rule explanation ─────────────────────────────────────

def explain_inventory_assessment(conn, assessment_id: int) -> dict:
    """
    Produce a rule-based explanation for a stored Module 3 inventory assessment.

    Uses actual stored values from inventory_assessment — no recomputation.
    All formulas are documented with their actual inputs and outputs.

    Returns explanation dict or raises if record not found.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM inventory_assessment WHERE assessment_id = %s",
            (assessment_id,)
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if row is None:
        raise ValueError(f"No inventory_assessment record found with assessment_id={assessment_id}.")

    a = dict(row)
    data_warnings = [
        "STOCK: Available stock is a static snapshot from the latest inventory_batch "
        "(received_date ≈ 2026-08-01). Physical stock may differ.",
        "FORECAST: Demand data ends 2026-08-30. This assessment is historical, "
        "not a live operational decision.",
    ]

    # ── Safety stock explanation ──────────────────────────────────────────────
    ss = a.get("safety_stock")
    ss_z = a.get("safety_stock_z")
    ss_status = a.get("safety_stock_status")
    demand_std = a.get("demand_std_daily")
    lead_time = a.get("lead_time_days")

    ss_explanation = {}
    if ss_status == "calculated" and all(v is not None for v in [ss_z, demand_std, lead_time]):
        raw = float(ss_z) * float(demand_std) * math.sqrt(float(lead_time))
        ss_explanation = {
            "formula": "SS = z × σ_d × √(lead_time_days)",
            "inputs": {
                "z_factor": float(ss_z),
                "demand_std_daily": float(demand_std),
                "lead_time_days": int(lead_time),
            },
            "raw_result": round(raw, 4),
            "rounded_up": int(math.ceil(raw)),
            "stored_value": float(ss) if ss is not None else None,
            "status": ss_status,
            "interpretation": (
                f"Safety stock = {ss_z} × {demand_std:.4f} × √{lead_time} "
                f"= {raw:.4f} → rounded up to {int(math.ceil(raw))}. "
                f"Provides a buffer for {float(ss_z):.3f}-sigma demand variability "
                f"over the {lead_time}-day lead time. "
                "Assumes constant lead time and normally distributed demand. "
                "No lead-time variability data is available in this schema."
            ),
        }
    else:
        ss_explanation = {
            "formula": "SS = z × σ_d × √(lead_time_days)",
            "status": ss_status,
            "stored_value": float(ss) if ss is not None else None,
            "interpretation": (
                f"Safety stock set to 0 (status={ss_status}). "
                "Demand variability estimate is unreliable or inputs are missing."
            ),
        }

    # ── Reorder point explanation ─────────────────────────────────────────────
    rop = a.get("reorder_point")
    avg_demand = a.get("avg_daily_demand")
    rop_status = a.get("rop_status")
    demand_source = a.get("demand_source")
    reorder_level_product = a.get("reorder_level_from_product")

    rop_explanation = {}
    if rop is not None and avg_demand is not None and lead_time is not None:
        raw_rop = float(avg_demand) * float(lead_time) + float(ss or 0)
        rop_explanation = {
            "formula": "ROP = avg_daily_demand × lead_time_days + safety_stock",
            "inputs": {
                "avg_daily_demand": float(avg_demand),
                "lead_time_days": int(lead_time),
                "safety_stock": float(ss or 0),
                "demand_source": demand_source,
            },
            "raw_result": round(raw_rop, 4),
            "stored_value": float(rop),
            "static_reorder_level": int(reorder_level_product) if reorder_level_product else None,
            "status": rop_status,
            "interpretation": (
                f"Reorder point = {avg_demand:.4f} × {lead_time} + {float(ss or 0):.4f} "
                f"= {raw_rop:.4f} → {float(rop):.0f}. "
                f"Demand rate source: {demand_source}. "
                f"Static product reorder_level: {reorder_level_product}. "
                "The procurement decision uses the lower of these two thresholds."
            ),
        }
    else:
        rop_explanation = {
            "formula": "ROP = avg_daily_demand × lead_time_days + safety_stock",
            "status": rop_status or "insufficient_data",
            "stored_value": float(rop) if rop is not None else None,
            "interpretation": "Reorder point could not be calculated (missing inputs).",
        }

    # ── Replenishment quantity explanation ────────────────────────────────────
    inv_pos = a.get("inventory_position")
    max_stock = a.get("maximum_stock_level")
    min_oq = a.get("minimum_order_qty")
    rep_qty = a.get("suggested_replenishment_qty")
    rep_status = a.get("replenishment_status")

    rep_explanation = {}
    if inv_pos is not None and max_stock is not None:
        raw_rep = max(0, int(max_stock) - int(inv_pos))
        effective_moq = int(min_oq) if (min_oq and int(min_oq) > 0) else 1
        adjusted = max(raw_rep, effective_moq) if raw_rep > 0 else 0
        rep_explanation = {
            "formula": "replenishment = max(0, maximum_stock_level − inventory_position), MOQ-adjusted",
            "inputs": {
                "inventory_position": int(inv_pos),
                "maximum_stock_level": int(max_stock),
                "minimum_order_qty": int(min_oq) if min_oq else None,
            },
            "raw_quantity": raw_rep,
            "moq_adjusted_quantity": adjusted,
            "stored_value": int(rep_qty) if rep_qty is not None else None,
            "status": rep_status,
            "interpretation": (
                f"Replenishment = max(0, {max_stock} − {inv_pos}) = {raw_rep}, "
                f"then MOQ-adjusted to {adjusted}. "
                "This is a suggested quantity only. "
                "Supplier selection and purchase order creation are handled by Module 4 "
                "and require manager approval before any procurement action."
            ),
        }
    else:
        rep_explanation = {
            "formula": "replenishment = max(0, maximum_stock_level − inventory_position)",
            "status": rep_status or "insufficient_data",
            "stored_value": int(rep_qty) if rep_qty is not None else None,
            "interpretation": "Replenishment quantity unavailable (missing stock or max_stock inputs).",
        }

    # ── Routing decision explanation ──────────────────────────────────────────
    routing = a.get("routing_decision")
    decision_reasons = a.get("decision_reasons", "")
    inv_pos_val = int(inv_pos) if inv_pos is not None else None
    rop_val = float(rop) if rop is not None else None
    rop_level = int(reorder_level_product) if reorder_level_product else None
    max_stock_val = int(max_stock) if max_stock else None

    routing_explanation = {
        "routing_decision": routing,
        "decision_reasons": decision_reasons,
        "routing_rules": {
            "rule_1_insufficient_data": "Triggered if no stock data, no lead time, or no inventory position.",
            "rule_2_procurement_required": (
                f"Triggered if inventory_position ≤ computed_ROP ({rop_val}) "
                f"OR inventory_position ≤ product_reorder_level ({rop_level}). "
                f"Actual inventory_position = {inv_pos_val}. "
                "Procurement overrides excess_stock when both signals present."
            ),
            "rule_3_excess_stock_review": (
                f"Triggered if inventory_position > maximum_stock_level × 1.5 "
                f"(threshold = {float(max_stock_val) * 1.5:.1f} if max_stock={max_stock_val}). "
                f"Actual inventory_position = {inv_pos_val}."
            ),
            "rule_4_normal": "Triggered when none of the above conditions are met.",
        },
        "decision_applied": routing,
        "interpretation": (
            f"Routing decision: '{routing}'. "
            f"Reasons: {decision_reasons}. "
            "This is a deterministic rule-based decision, not a machine learning prediction. "
            "No automatic procurement or stock changes are triggered — "
            "manager approval is required for any action."
        ),
    }

    return {
        "explanation_type": "inventory_rule",
        "explanation_method": "rule_based",
        "assessment_id": int(a["assessment_id"]),
        "product_id": a["product_id"],
        "assessed_at": str(a.get("assessed_at", "")),
        "model_version": a.get("model_version"),
        "policy_version": a.get("policy_version"),
        "stock_inputs": {
            "available_stock": int(a.get("available_stock", 0)),
            "inventory_position": inv_pos_val,
            "stock_calculation_method": a.get("stock_calculation_method"),
            "lead_time_days": int(lead_time) if lead_time else None,
            "nonzero_demand_days": a.get("nonzero_demand_days"),
            "history_days_used": a.get("history_days_used"),
            "avg_daily_demand": float(avg_demand) if avg_demand else None,
            "demand_std_daily": float(demand_std) if demand_std else None,
            "demand_source": demand_source,
            "forecast_demand_7d": float(a["forecast_demand_7d"]) if a.get("forecast_demand_7d") else None,
        },
        "safety_stock_explanation": ss_explanation,
        "reorder_point_explanation": rop_explanation,
        "replenishment_explanation": rep_explanation,
        "routing_explanation": routing_explanation,
        "calculation_notes": a.get("calculation_notes"),
        "data_warnings": data_warnings,
        "requires_manager_approval": True,
    }


# ── Module 4 — Procurement rule explanation ───────────────────────────────────

def explain_procurement_recommendation(conn, recommendation_id: int) -> dict:
    """
    Produce a rule-based explanation for a stored Module 4 procurement recommendation.

    Uses actual stored values from procurement_recommendation.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM procurement_recommendation WHERE recommendation_id = %s",
            (recommendation_id,)
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if row is None:
        raise ValueError(
            f"No procurement_recommendation record found with recommendation_id={recommendation_id}."
        )

    r = dict(row)
    data_warnings = [
        "SYNTHETIC_LEAD_TIMES: Supplier lead times are modelled synthetic values from the "
        "supplier master table. Verify with actual supplier contracts before procurement.",
        "MANAGER_APPROVAL_REQUIRED: No purchase orders are placed automatically.",
    ]

    # Build ranked supplier list from stored flat fields
    ranked_suppliers = []
    if r.get("rank1_supplier_id"):
        ranked_suppliers.append({
            "rank": 1,
            "supplier_id": r["rank1_supplier_id"],
            "supplier_name": r["rank1_supplier_name"],
            "lead_time_days": r["rank1_lead_time_days"],
            "explanation": r.get("rank1_explanation", ""),
        })
    if r.get("rank2_supplier_id"):
        ranked_suppliers.append({
            "rank": 2,
            "supplier_id": r["rank2_supplier_id"],
            "supplier_name": r["rank2_supplier_name"],
            "lead_time_days": r["rank2_lead_time_days"],
            "explanation": r.get("rank2_explanation", ""),
        })

    # Tie handling details
    has_tie = r.get("has_lead_time_tie", False)
    tie_explanation = r.get("tie_explanation")

    interpretation = (
        f"Ranking method: lead_time_based. "
        f"Eligible suppliers considered: {r.get('eligible_supplier_count', 0)}. "
        "Suppliers are ranked in ascending order of lead_time_days from the supplier master. "
        "When two or more suppliers share the shortest lead time, supplier_id is used as a "
        "deterministic tie-breaker — this does not indicate operational superiority. "
        f"{'A tie was detected: ' + tie_explanation if has_tie and tie_explanation else 'No tie detected.'} "
        "This is a deterministic algorithm, not a machine learning model — SHAP is not applicable. "
        "Manager approval is required before any procurement action."
    )

    return {
        "explanation_type": "procurement_rule",
        "explanation_method": "rule_based",
        "recommendation_id": int(r["recommendation_id"]),
        "product_id": r["product_id"],
        "recommended_at": str(r.get("recommended_at", "")),
        "ranking_method": r.get("ranking_method"),
        "policy_version": r.get("policy_version"),
        "eligible_supplier_count": r.get("eligible_supplier_count", 0),
        "recommendation_status": r.get("recommendation_status"),
        "suggested_replenishment_qty": r.get("suggested_replenishment_qty"),
        "ranked_suppliers": ranked_suppliers,
        "has_lead_time_tie": has_tie,
        "tie_supplier_count": r.get("tie_supplier_count"),
        "tie_explanation": tie_explanation,
        "ranking_rule_description": (
            "1. Retrieve all suppliers linked to this product via product_supplier table. "
            "2. Sort by ascending lead_time_days from the supplier master. "
            "3. Break ties by ascending supplier_id (deterministic, not merit-based). "
            "4. Return top 2 ranked suppliers."
        ),
        "interpretation": interpretation,
        "data_warnings": data_warnings,
        "requires_manager_approval": True,
    }


# ── Module 5 — Excess-stock statistical explanation ───────────────────────────

def explain_excess_stock_assessment(conn, assessment_id: int) -> dict:
    """
    Produce a statistical/rule-based explanation for a stored Module 5
    excess_stock_assessment record.

    Uses actual stored values — no recomputation.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT * FROM excess_stock_assessment WHERE assessment_id = %s",
            (assessment_id,)
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if row is None:
        raise ValueError(
            f"No excess_stock_assessment record found with assessment_id={assessment_id}."
        )

    e = dict(row)
    data_warnings = [
        "STOCK: Available stock is a static snapshot from the latest inventory_batch "
        "(received_date ≈ 2026-08-01). Physical stock may differ.",
        "FORECAST: Data ends 2026-08-30. This assessment is historical.",
        "MANAGER_APPROVAL_REQUIRED: Module 5 does not modify stock quantities or place orders.",
    ]
    if e.get("xyz_warning"):
        data_warnings.append(f"XYZ_WARNING: {e['xyz_warning']}")
    if e.get("data_warnings"):
        data_warnings.append(e["data_warnings"])

    abc_class = e.get("abc_class")
    xyz_class = e.get("xyz_class")
    excess_category = e.get("excess_category")
    inv_pos = e.get("inventory_position")
    max_stock = e.get("maximum_stock_level")
    cv = e.get("demand_cv")
    avg_demand = e.get("avg_daily_demand_90d")
    nonzero_days = e.get("nonzero_demand_days_90d")
    rev_12m = e.get("revenue_12m")
    rev_share = e.get("revenue_share")
    cumulative_share = e.get("cumulative_revenue_share")
    abc_rank = e.get("abc_rank")

    # ── ABC classification explanation ────────────────────────────────────────
    abc_explanation = {
        "classification": abc_class,
        "method": "statistical",
        "window": "12 months ending 2026-08-31",
        "metric": "cumulative revenue share (SUM(quantity_sold × selling_price))",
        "thresholds": {
            "A": "cumulative_revenue_share ≤ 0.70 (top 70% of total revenue)",
            "B": "cumulative_revenue_share ≤ 0.90 (next 20%)",
            "C": "remainder (bottom 10%)",
        },
        "actual_values": {
            "revenue_12m": float(rev_12m) if rev_12m else 0.0,
            "revenue_share": float(rev_share) if rev_share else 0.0,
            "cumulative_revenue_share": float(cumulative_share) if cumulative_share else 0.0,
            "abc_rank": int(abc_rank) if abc_rank else None,
            "abc_eligible": e.get("abc_eligible", False),
        },
        "classification_reason": (
            f"Product has cumulative_revenue_share={float(cumulative_share or 0):.4%} "
            f"(rank={abc_rank} by 12-month revenue), "
            f"classifying it as ABC class '{abc_class}'. "
            f"Individual revenue_share={float(rev_share or 0):.4%} of total population revenue."
        ),
    }

    # ── XYZ classification explanation ────────────────────────────────────────
    xyz_explanation = {
        "classification": xyz_class,
        "method": "statistical",
        "window": "90 calendar days ending 2026-08-30",
        "metric": "coefficient of variation (CV) = std / mean of gap-filled daily demand",
        "thresholds": {
            "X": "CV ≤ 0.30 (stable demand)",
            "Y": "0.30 < CV ≤ 0.60 (moderate variability)",
            "Z": "CV > 0.60 (highly variable / intermittent)",
        },
        "actual_values": {
            "demand_cv": float(cv) if cv is not None else None,
            "avg_daily_demand_90d": float(avg_demand) if avg_demand is not None else None,
            "std_daily_demand_90d": float(e.get("std_daily_demand_90d") or 0),
            "nonzero_demand_days_90d": int(nonzero_days) if nonzero_days is not None else 0,
            "history_days_90d": e.get("history_days_90d"),
            "xyz_eligible": e.get("xyz_eligible", False),
        },
        "classification_reason": (
            f"CV={float(cv):.4f} classifies product as XYZ class '{xyz_class}'. "
            if cv is not None
            else f"CV unavailable (reason: {e.get('xyz_warning', 'unknown')}). "
                 f"Defaulting to class '{xyz_class}'."
        ),
    }

    # ── Excess-stock detection explanation ────────────────────────────────────
    excess_threshold = float(max_stock) * 1.5 if max_stock else None
    triggered_rules = e.get("excess_trigger_reasons", "")

    excess_explanation = {
        "excess_category": excess_category,
        "detection_rules": {
            "rule_1_dead_stock": (
                "nonzero_demand_days_90d == 0 AND inventory_position > 0 → dead_stock"
            ),
            "rule_2_overstock": (
                f"inventory_position > maximum_stock_level × 1.5 "
                f"(threshold = {excess_threshold:.1f} if max_stock={max_stock}) → overstock"
            ),
            "rule_3_slow_moving": (
                "0 < nonzero_demand_days_90d < 7 AND inventory_position > 0 → slow_moving"
            ),
            "precedence": "dead_stock > overstock > slow_moving > normal",
        },
        "actual_values": {
            "inventory_position": int(inv_pos) if inv_pos is not None else None,
            "maximum_stock_level": int(max_stock) if max_stock is not None else None,
            "overstock_threshold_1_5x": round(excess_threshold, 1) if excess_threshold else None,
            "nonzero_demand_days_90d": int(nonzero_days) if nonzero_days is not None else 0,
        },
        "triggered_rules": triggered_rules,
        "is_overstock": e.get("is_overstock", False),
        "is_slow_moving": e.get("is_slow_moving", False),
        "is_dead_stock": e.get("is_dead_stock", False),
        "excess_qty_estimate": e.get("excess_qty_estimate", 0),
        "classification_reason": triggered_rules,
    }

    # ── Recommendation explanation ────────────────────────────────────────────
    rec_action = e.get("recommended_action")
    confidence = e.get("confidence")

    recommendation_explanation = {
        "recommended_action": rec_action,
        "confidence": confidence,
        "matrix_lookup": {
            "abc_class": abc_class,
            "xyz_class": xyz_class,
            "excess_category": excess_category,
        },
        "recommendation_matrix": {
            "(A, X)": "monitor_and_review_replenishment",
            "(A, Y)": "investigate_demand_shift",
            "(A, Z)": "investigate_demand_shift",
            "(B, X)": "consider_promotion_or_discount",
            "(B, Y)": "consider_promotion_or_discount",
            "(B, Z)": "suspend_replenishment_and_review",
            "(C, X)": "consider_promotion_or_discount",
            "(C, Y)": "suspend_replenishment_and_review",
            "(C, Z)": "review_for_discontinuation",
            "dead_stock (override)": "review_for_discontinuation",
            "normal": "no_action_required",
        },
        "interpretation": (
            f"For ABC='{abc_class}', XYZ='{xyz_class}', excess='{excess_category}': "
            f"recommended_action='{rec_action}' (confidence={confidence}). "
            "This recommendation requires manager approval before any stock action. "
            "Module 5 does not modify stock quantities or place orders."
        ),
    }

    return {
        "explanation_type": "excess_stock_rule",
        "explanation_method": "statistical",
        "assessment_id": int(e["assessment_id"]),
        "product_id": e["product_id"],
        "assessed_at": str(e.get("assessed_at", "")),
        "policy_version": e.get("policy_version"),
        "abc_classification_explanation": abc_explanation,
        "xyz_classification_explanation": xyz_explanation,
        "excess_stock_detection_explanation": excess_explanation,
        "recommendation_explanation": recommendation_explanation,
        "recommendation_notes": e.get("recommendation_notes"),
        "data_warnings": data_warnings,
        "requires_manager_approval": True,
    }


# ── Persistence ───────────────────────────────────────────────────────────────

def apply_explainability_schema(conn) -> None:
    """Apply schema_m6.sql — creates explanation_record table. Safe to rerun."""
    sql_path = (
        Path(__file__).parent.parent.parent
        / "module3_inventory_optimization"
        / "sql"
        / "schema_m6.sql"
    )
    if not sql_path.exists():
        raise FileNotFoundError(f"Module 6 schema SQL not found at {sql_path}")
    sql = sql_path.read_text(encoding="utf-8")
    cur = conn.cursor()
    try:
        cur.execute(sql)
        conn.commit()
        logger.info("Module 6 schema applied/verified (explanation_record).")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def persist_explanation(conn, explanation: dict, explanation_type: str,
                         explanation_method: str, product_id: Optional[str],
                         source_record_id: Optional[str]) -> Optional[int]:
    """
    Persist an explanation record to explanation_record table.
    Returns explanation_id or None if persistence fails (non-fatal).
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO explanation_record (
                explanation_type, explanation_method, product_id, source_record_id,
                model_name, model_version,
                prediction_value, baseline_value, prediction_units,
                explanation_body, data_warnings
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING explanation_id
            """,
            (
                explanation_type,
                explanation_method,
                product_id,
                source_record_id,
                explanation.get("model_name"),
                explanation.get("model_version"),
                explanation.get("prediction_value"),
                explanation.get("baseline_value"),
                explanation.get("prediction_units"),
                json.dumps(explanation, default=str),
                "; ".join(explanation.get("data_warnings", [])) if isinstance(
                    explanation.get("data_warnings"), list
                ) else explanation.get("data_warnings"),
            )
        )
        row = cur.fetchone()
        conn.commit()
        return int(row["explanation_id"]) if row else None
    except Exception as ex:
        conn.rollback()
        logger.warning(f"[ExplainabilityService] Failed to persist explanation: {ex}")
        return None
    finally:
        cur.close()
