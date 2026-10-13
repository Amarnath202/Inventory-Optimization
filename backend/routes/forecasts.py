"""
backend/routes/forecasts.py
---------------------------
FastAPI routes for forecast data access and manual trigger.
"""

import sys
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel

_PROJ_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_PROJ_ROOT))
_M2_ROOT = _PROJ_ROOT / "module2_demand_forecasting"
if _M2_ROOT.exists():
    sys.path.insert(0, str(_M2_ROOT))
from backend.services.db import get_db
from backend.services.forecast_service import (
    get_overview, get_product_forecast, get_top_demand,
    get_all_forecasts, get_all_products_with_forecasts, get_latest_run_id
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/forecast", tags=["Forecasts"])


class TriggerRequest(BaseModel):
    reason: Optional[str] = "Manual trigger"


# ── Overview / Dashboard ──────────────────────────────────────────────────────

@router.get("/overview")
def overview(conn=Depends(get_db)):
    """Dashboard overview: latest run metadata, product count, total demand."""
    result = get_overview(conn)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


# ── Forecast data ─────────────────────────────────────────────────────────────

@router.get("/products")
def list_forecast_products(
    run_id: Optional[str] = Query(None, description="forecast_run_id; defaults to latest"),
    conn=Depends(get_db),
):
    """List all products that have forecasts in the specified (or latest) run."""
    return get_all_products_with_forecasts(conn, run_id=run_id)


@router.get("/products/{product_id}")
def product_forecast(
    product_id: str,
    run_id: Optional[str] = Query(None),
    conn=Depends(get_db),
):
    """Full 7-day forecast + 60-day history + stock for one product."""
    data = get_product_forecast(conn, product_id.upper(), run_id=run_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found or no forecasts.")
    return data


@router.get("/top")
def top_products(
    n: int = Query(20, ge=1, le=100),
    run_id: Optional[str] = Query(None),
    conn=Depends(get_db),
):
    """Top N products by total 7-day forecasted demand."""
    return get_top_demand(conn, n=n, run_id=run_id)


@router.get("/table")
def forecast_table(
    product_id: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """Paginated forecast table. Optionally filtered by product_id or run_id."""
    return get_all_forecasts(conn, run_id=run_id, product_id=product_id,
                             limit=limit, offset=offset)


# ── Run management ────────────────────────────────────────────────────────────

@router.get("/runs")
def list_runs(
    limit: int = Query(20, ge=1, le=100),
    conn=Depends(get_db),
):
    """List recent forecast runs."""
    cur = conn.cursor()
    cur.execute(
        """SELECT forecast_run_id, model_version, status, forecast_origin,
                  target_date_from, target_date_to, run_started_at,
                  run_completed_at, rows_written, triggered_by, error_detail
           FROM forecast_run
           ORDER BY run_started_at DESC
           LIMIT %s""",
        (limit,)
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


@router.get("/runs/{run_id}")
def get_run(run_id: str, conn=Depends(get_db)):
    """Details of a specific forecast run."""
    cur = conn.cursor()
    cur.execute(
        """SELECT * FROM forecast_run WHERE forecast_run_id = %s""",
        (run_id,)
    )
    row = cur.fetchone()
    cur.close()
    if not row:
        raise HTTPException(status_code=404, detail="Run not found.")
    return dict(row)


def _run_forecast_bg(triggered_by: str):
    """Background task wrapper for forecast pipeline."""
    try:
        # pipeline lives in module2_demand_forecasting/src/
        m2_src = Path(__file__).parent.parent.parent / "module2_demand_forecasting" / "src"
        sys.path.insert(0, str(m2_src))
        from pipeline import run_forecast_pipeline
        run_id = run_forecast_pipeline(triggered_by=triggered_by)
        logger.info(f"Background forecast complete. run_id={run_id}")
    except Exception as e:
        logger.error(f"Background forecast failed: {e}", exc_info=True)


@router.post("/trigger")
def trigger_forecast(
    body: TriggerRequest,
    background_tasks: BackgroundTasks,
    conn=Depends(get_db),
):
    """
    Manually trigger a forecast run.
    The pipeline acquires the PostgreSQL advisory lock; if already running,
    it returns immediately without creating a duplicate run.

    Catch-up note: this always uses the current trained model.
    Retrain first (python src/train.py) if the model may be stale.
    """
    background_tasks.add_task(_run_forecast_bg, "manual")
    return {
        "status": "accepted",
        "message": "Forecast run queued. Check GET /api/forecast/runs for status.",
        "reason": body.reason,
    }


# ── Evaluation metrics ────────────────────────────────────────────────────────

@router.get("/metrics")
def evaluation_metrics():
    """
    Return the saved evaluation metrics from the last training run.
    Reads from reports/forecasting_metrics_test.csv and reports/forecasting_metrics_val.csv.
    """
    import pandas as pd
    from config.settings import REPORTS_DIR

    result = {}
    for split in ["val", "test"]:
        path = REPORTS_DIR / f"forecasting_metrics_{split}.csv"
        if path.exists():
            result[split] = pd.read_csv(path).to_dict(orient="records")
        else:
            result[split] = None
    return result
