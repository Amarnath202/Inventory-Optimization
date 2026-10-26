"""
backend/routes/explainability.py
----------------------------------
Module 6 — Explainability API routes.

Prefix: /api/explainability

Endpoints:
  GET  /api/explainability/health
      — Service and model availability status.

  GET  /api/explainability/models
      — Available models and supported explanation methods.

  GET  /api/explainability/forecast/{product_id}
      — Explain the latest stored forecast for a product (most recent run).
        Optional query params: date (YYYY-MM-DD), run_id.

  GET  /api/explainability/inventory/{assessment_id}
      — Rule-based explanation for a stored Module 3 inventory assessment.

  GET  /api/explainability/procurement/{recommendation_id}
      — Rule-based explanation for a stored Module 4 procurement recommendation.

  GET  /api/explainability/excess-stock/{assessment_id}
      — Statistical/rule-based explanation for a stored Module 5 excess-stock assessment.

Safety guarantees:
  - No business data is modified (no purchases, no stock changes, no assessment writes).
  - No model is retrained.
  - No fabricated values are returned — errors surface as HTTP 4xx/5xx.
  - Explanation method is always labelled accurately.
  - Manager approval notices are included in all explanations.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.services.db import get_db
from backend.services import explainability_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/explainability",
    tags=["Explainability (Module 6)"],
)

# ── Health endpoint ────────────────────────────────────────────────────────────

@router.get("/health")
def explainability_health():
    """
    Return the health and availability status of the explainability service.

    Reports:
    - Model artifact availability.
    - SHAP library availability.
    - Whether TreeSHAP explanations are currently supported.
    """
    try:
        info = svc.get_model_info()
        status = "ok" if info["model_available"] and info["shap_available"] else "degraded"
        return {
            "service": "explainability",
            "status": status,
            "model_available": info["model_available"],
            "shap_available": info["shap_available"],
            "tree_shap_explanation_available": info["tree_shap_explanation_available"],
            "model_version": info["model_version"],
            "model_error": info.get("error"),
        }
    except Exception as e:
        logger.error(f"[ExplainabilityRoute] Health check error: {e}", exc_info=True)
        return {
            "service": "explainability",
            "status": "error",
            "error": str(e),
        }


# ── Models endpoint ────────────────────────────────────────────────────────────

@router.get("/models")
def list_models():
    """
    List available models and explanation methods.

    Returns information about the LightGBM forecasting model and
    the rule-based explanation support for Modules 3–5.
    """
    info = svc.get_model_info()

    return {
        "models": [
            {
                "model_name": info["model_name"],
                "model_version": info["model_version"],
                "model_available": info["model_available"],
                "objective": info["objective"],
                "val_mae": info["val_mae"],
                "trained_on": info["trained_on"],
                "latest_data_date": info["latest_data_date"],
                "feature_count": info["feature_count"],
                "feature_cols": info["feature_cols"],
                "explanation_method": "tree_shap",
                "explanation_available": info["tree_shap_explanation_available"],
                "shap_library_available": info["shap_available"],
                "note": (
                    "TreeSHAP is available for demand forecasting predictions only. "
                    "Inventory (M3), procurement (M4), and excess-stock (M5) decisions "
                    "are rule-based or statistical — they are explained without SHAP."
                ),
            }
        ],
        "rule_based_explanations": [
            {
                "module": "Module 3 — Inventory Optimization",
                "method": "rule_based",
                "scope": "Safety stock, reorder point, replenishment quantity, routing decision",
                "endpoint": "/api/explainability/inventory/{assessment_id}",
            },
            {
                "module": "Module 4 — Procurement Agent",
                "method": "rule_based",
                "scope": "Lead-time-based Top-2 supplier ranking",
                "endpoint": "/api/explainability/procurement/{recommendation_id}",
            },
            {
                "module": "Module 5 — Excess Stock Management",
                "method": "statistical",
                "scope": "ABC/XYZ classification and excess-stock detection rules",
                "endpoint": "/api/explainability/excess-stock/{assessment_id}",
            },
        ],
        "shap_not_applicable_reason": (
            "Modules 3, 4, and 5 produce decisions via deterministic formulas and "
            "rule-based logic, not trained machine-learning predictions. "
            "SHAP is only applicable to trained model outputs."
        ),
    }


# ── Forecast SHAP explanation ──────────────────────────────────────────────────

@router.get("/forecast/{product_id}")
def explain_forecast(
    product_id: str,
    date: Optional[str] = Query(
        default=None,
        description="Forecast date to explain (YYYY-MM-DD). "
                    "If omitted, the first forecast date in the latest run is used.",
    ),
    run_id: Optional[str] = Query(
        default=None,
        description="Specific forecast_run_id to use. "
                    "If omitted, the latest completed run is used.",
    ),
    conn=Depends(get_db),
):
    """
    Explain a demand forecast for a product using TreeSHAP.

    Looks up the actual stored forecast for the given product and date,
    reconstructs the feature row from historical demand data, and computes
    exact TreeSHAP values via the loaded LightGBM model.

    Returns:
    - Per-feature SHAP contributions (signed, ranked by magnitude).
    - Baseline (average model output over training data).
    - Actual feature values used.
    - Data freshness warnings.

    Important:
    - SHAP contributions describe this specific prediction only.
    - They do not imply causality or future demand direction.
    - The stored prediction may differ slightly from the SHAP-reconstructed
      prediction when the recursive forecast used prior predicted values as
      lag inputs (this limitation is clearly flagged in data_warnings).
    """
    pid = product_id.upper().strip()

    # Check model/SHAP availability first
    info = svc.get_model_info()
    if not info["model_available"]:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "LightGBM model artifact is not available.",
                "model_error": info.get("error"),
                "suggestion": "Ensure the model has been trained and the artifact exists at "
                              "module2_demand_forecasting/models/.",
            },
        )
    if not info["shap_available"]:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "SHAP library is not installed. TreeSHAP explanations are unavailable.",
                "suggestion": "Install SHAP: pip install shap",
            },
        )

    # If date not provided, resolve from DB
    forecast_date = date
    if forecast_date is None:
        cur = conn.cursor()
        try:
            if run_id:
                cur.execute(
                    """SELECT MIN(forecast_date) AS fd
                       FROM demand_forecast
                       WHERE forecast_run_id = %s AND product_id = %s""",
                    (run_id, pid),
                )
            else:
                cur.execute(
                    """SELECT MIN(df.forecast_date) AS fd
                       FROM demand_forecast df
                       JOIN forecast_run fr ON df.forecast_run_id = fr.forecast_run_id
                       WHERE fr.status = 'completed'
                         AND df.product_id = %s
                       ORDER BY fr.run_completed_at DESC""",
                    (pid,),
                )
            row = cur.fetchone()
        finally:
            cur.close()

        if not row or not row["fd"]:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": f"No forecast records found for product '{pid}'.",
                    "suggestion": "Ensure forecasts have been generated. "
                                  "Check /api/forecasts/overview for available runs.",
                },
            )
        forecast_date = str(row["fd"])

    try:
        result = svc.explain_forecast_from_db(conn, pid, forecast_date, run_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail={"error": str(e)})
    except (ImportError, FileNotFoundError) as e:
        raise HTTPException(status_code=503, detail={"error": str(e)})
    except Exception as e:
        logger.error(f"[ExplainabilityRoute] Forecast SHAP error for {pid}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to compute SHAP explanation. See server logs."},
        )

    return result


# ── Module 3 — Inventory assessment explanation ────────────────────────────────

@router.get("/inventory/{assessment_id}")
def explain_inventory(assessment_id: int, conn=Depends(get_db)):
    """
    Explain a stored Module 3 inventory assessment using rule-based explanations.

    Shows the actual inputs and formula steps for:
    - Safety stock (z × σ_d × √lead_time)
    - Reorder point (avg_demand × lead_time + safety_stock)
    - Replenishment quantity (max_stock − inventory_position, MOQ-adjusted)
    - Routing decision (priority rules: insufficient_data > procurement_required > excess_stock_review > normal)

    All values are sourced from the stored assessment record — no recomputation.
    Explanation method: rule_based (not SHAP).
    """
    try:
        result = svc.explain_inventory_assessment(conn, assessment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail={"error": str(e)})
    except Exception as e:
        logger.error(
            f"[ExplainabilityRoute] Inventory explanation error for assessment_id={assessment_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to generate inventory explanation. See server logs."},
        )

    return result


# ── Module 4 — Procurement recommendation explanation ─────────────────────────

@router.get("/procurement/{recommendation_id}")
def explain_procurement(recommendation_id: int, conn=Depends(get_db)):
    """
    Explain a stored Module 4 procurement recommendation using rule-based explanation.

    Shows:
    - Why each supplier is ranked first or second.
    - Recorded lead times used in the ranking.
    - Whether a lead-time tie occurred and how it was broken.
    - Explicit disclosure that lead times are modelled synthetic values.

    Explanation method: rule_based (not SHAP — ranking is deterministic, not ML).
    """
    try:
        # Check table exists first
        cur = conn.cursor()
        try:
            cur.execute(
                """SELECT EXISTS (
                       SELECT 1 FROM information_schema.tables
                       WHERE table_name = 'procurement_recommendation'
                   ) AS tbl_exists"""
            )
            if not cur.fetchone()["tbl_exists"]:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "procurement_recommendation table does not exist yet.",
                        "suggestion": "Run POST /api/procurement/import-supplier-alternatives first.",
                    },
                )
        finally:
            cur.close()

        result = svc.explain_procurement_recommendation(conn, recommendation_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail={"error": str(e)})
    except Exception as e:
        logger.error(
            f"[ExplainabilityRoute] Procurement explanation error for recommendation_id={recommendation_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to generate procurement explanation. See server logs."},
        )

    return result


# ── Module 5 — Excess-stock assessment explanation ────────────────────────────

@router.get("/excess-stock/{assessment_id}")
def explain_excess_stock(assessment_id: int, conn=Depends(get_db)):
    """
    Explain a stored Module 5 excess-stock assessment using statistical/rule-based explanation.

    Shows:
    - ABC classification: 12-month revenue ranking, cumulative share thresholds.
    - XYZ classification: 90-day demand variability (CV), classification boundaries.
    - Excess-stock detection: triggered rules and actual threshold comparisons.
    - Recommended action: derived from ABC/XYZ matrix and excess category.

    Explanation method: statistical (not SHAP — classifications are rule/statistical).
    """
    try:
        # Check table exists first
        cur = conn.cursor()
        try:
            cur.execute(
                """SELECT EXISTS (
                       SELECT 1 FROM information_schema.tables
                       WHERE table_name = 'excess_stock_assessment'
                   ) AS tbl_exists"""
            )
            if not cur.fetchone()["tbl_exists"]:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": "excess_stock_assessment table does not exist yet.",
                        "suggestion": "Run POST /api/excess-stock/assess first.",
                    },
                )
        finally:
            cur.close()

        result = svc.explain_excess_stock_assessment(conn, assessment_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail={"error": str(e)})
    except Exception as e:
        logger.error(
            f"[ExplainabilityRoute] Excess-stock explanation error for assessment_id={assessment_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": "Failed to generate excess-stock explanation. See server logs."},
        )

    return result
