"""
backend/routes/inventory_optimization.py
-----------------------------------------
Module 3 — FastAPI router for inventory optimization endpoints.

Prefix: /api/inventory-optimization

Endpoints:
  GET  /overview                       — summary counts by routing decision
  GET  /products                       — paginated assessments (filter by routing_decision)
  GET  /products/{product_id}          — single product assessment
  POST /assess                         — run/refresh inventory assessment
  GET  /recommendations                — filtered: procurement_required + excess_stock_review only

Conventions (matching existing /api/forecast routes):
  - Pydantic v2 schemas
  - HTTP 404 for missing product or no assessments
  - HTTP 422 for invalid parameters (FastAPI default)
  - HTTP 503 if no completed forecast run exists and assessment cannot proceed
  - Pagination: limit / offset query params
  - No stack traces or credentials in error responses
  - Background task for full-batch assessment (avoids HTTP timeout)
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

_PROJ_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_PROJ_ROOT))
_M2_ROOT = _PROJ_ROOT / "module2_demand_forecasting"
if _M2_ROOT.exists():
    sys.path.insert(0, str(_M2_ROOT))

from backend.services.db import get_db
from backend.services.inventory_service import (
    get_assessment_by_product,
    get_assessments_paginated,
    get_assessment_overview,
    get_latest_forecast_run,
    get_product_with_supplier,
)
from backend.services.assessment_service import run_assessment

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/inventory-optimization",
    tags=["Inventory Optimization"],
)

# Valid routing_decision values
VALID_DECISIONS = {
    "procurement_required",
    "excess_stock_review",
    "normal",
    "insufficient_data",
}


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class AssessRequest(BaseModel):
    product_id: Optional[str] = Field(
        default=None,
        description=(
            "Assess a single product (by product_id). "
            "Omit to assess all products (runs in background)."
        ),
    )
    reason: Optional[str] = Field(default="Manual trigger", description="Optional reason label.")


class AssessResponse(BaseModel):
    status: str
    message: str
    assessed: Optional[int] = None
    errors: Optional[int] = None
    error_list: Optional[list] = None
    forecast_run_id: Optional[str] = None
    reason: Optional[str] = None


# ── Background task wrapper ────────────────────────────────────────────────────

def _run_full_assessment_bg(reason: str):
    """Background task: assess all products (avoids HTTP timeout for 3,046 products)."""
    from backend.services.db import get_db_conn
    conn = None
    try:
        conn = get_db_conn()
        result = run_assessment(conn, product_id=None)
        logger.info(
            f"Background assessment complete (reason='{reason}'): "
            f"assessed={result['assessed']}, errors={result['errors']}"
        )
    except Exception as e:
        logger.error(f"Background assessment failed: {e}", exc_info=True)
    finally:
        if conn and not conn.closed:
            conn.close()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/overview")
def get_overview(conn=Depends(get_db)):
    """
    Summary counts of the latest inventory assessment per product,
    broken down by routing decision, plus forecast run reference.
    """
    data = get_assessment_overview(conn)
    if not data or data.get("total_assessed") == 0:
        # Not an error — assessments may not have been run yet
        return {
            "total_assessed": 0,
            "procurement_required": 0,
            "excess_stock_review": 0,
            "normal": 0,
            "insufficient_data": 0,
            "last_assessed_at": None,
            "latest_run_id": None,
            "message": (
                "No assessments found. Run POST /api/inventory-optimization/assess "
                "to generate assessments."
            ),
        }
    # Stringify timestamps / UUIDs for JSON serialisation
    for k in ("last_assessed_at", "latest_run_id"):
        if data.get(k) is not None:
            data[k] = str(data[k])
    return data


@router.get("/products")
def list_products(
    routing_decision: Optional[str] = Query(
        None,
        description=(
            "Filter by routing decision: procurement_required, excess_stock_review, "
            "normal, insufficient_data"
        ),
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """
    Paginated list of inventory assessments (latest per product).
    Optionally filter by routing_decision.
    """
    if routing_decision and routing_decision not in VALID_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid routing_decision '{routing_decision}'. "
                f"Valid values: {sorted(VALID_DECISIONS)}"
            ),
        )
    result = get_assessments_paginated(conn, routing_decision=routing_decision,
                                       limit=limit, offset=offset)
    # Serialise non-JSON-native types
    for item in result.get("items", []):
        for k, v in item.items():
            if hasattr(v, "isoformat"):
                item[k] = str(v)
    return result


@router.get("/products/{product_id}")
def get_product_assessment(product_id: str, conn=Depends(get_db)):
    """
    Full inventory assessment detail for a single product.
    Returns the most recent assessment.
    """
    pid = product_id.upper()

    # Verify the product exists
    product = get_product_with_supplier(conn, pid)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product '{pid}' not found.")

    assessment = get_assessment_by_product(conn, pid)
    if assessment is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No assessment found for product '{pid}'. "
                "Run POST /api/inventory-optimization/assess to generate one."
            ),
        )

    # Serialise
    for k, v in assessment.items():
        if hasattr(v, "isoformat"):
            assessment[k] = str(v)

    return {
        "product": {
            k: (str(v) if hasattr(v, "isoformat") else v)
            for k, v in product.items()
        },
        "assessment": assessment,
    }


@router.post("/assess", response_model=AssessResponse)
def trigger_assessment(
    body: AssessRequest,
    background_tasks: BackgroundTasks,
    conn=Depends(get_db),
):
    """
    Run or refresh an inventory assessment.

    - If product_id is provided: assess that single product synchronously
      (fast, suitable for real-time use).
    - If product_id is omitted: assess all 3,046 products in a background task
      (returns immediately; monitor via GET /overview).

    This endpoint is idempotent: re-running with the same forecast_run_id
    updates (upserts) the existing assessment row rather than creating duplicates.
    Assessment history across different forecast runs is preserved.

    Note: This endpoint only runs Module 3 inventory assessments.
    It does not invoke Module 4 (Procurement) or Module 5 (Excess Stock).
    Native Python orchestration is implemented separately.
    """
    pid = body.product_id.upper().strip() if body.product_id else None

    # Verify product exists if single-product assessment
    if pid:
        product = get_product_with_supplier(conn, pid)
        if product is None:
            raise HTTPException(
                status_code=404, detail=f"Product '{pid}' not found."
            )
        # Single product — run synchronously
        try:
            result = run_assessment(conn, product_id=pid)
        except Exception as e:
            logger.error(f"Assessment failed for {pid}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Assessment failed: {e}"
            )
        return AssessResponse(
            status=result["status"],
            message=f"Assessment complete for product '{pid}'.",
            assessed=result["assessed"],
            errors=result["errors"],
            error_list=result["error_list"],
            forecast_run_id=result.get("forecast_run_id"),
            reason=body.reason,
        )

    # All products — run in background
    background_tasks.add_task(_run_full_assessment_bg, body.reason or "manual")
    return AssessResponse(
        status="accepted",
        message=(
            "Full inventory assessment queued as a background task. "
            "Monitor progress via GET /api/inventory-optimization/overview."
        ),
        reason=body.reason,
    )


@router.get("/recommendations")
def get_recommendations(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """
    Return current actionable recommendations:
    products with routing_decision = 'procurement_required' or 'excess_stock_review'.

    This endpoint exposes Module 3 routing decisions only.
    The actual procurement or excess-stock actions are handled by Module 4 and
    Module 5 respectively (native orchestration, implemented separately).
    """
    proc = get_assessments_paginated(
        conn, routing_decision="procurement_required",
        limit=limit, offset=offset
    )
    excess = get_assessments_paginated(
        conn, routing_decision="excess_stock_review",
        limit=limit, offset=0
    )

    def _serialise(items):
        for item in items:
            for k, v in item.items():
                if hasattr(v, "isoformat"):
                    item[k] = str(v)
        return items

    return {
        "procurement_required": {
            "total": proc["total"],
            "items": _serialise(proc["items"]),
        },
        "excess_stock_review": {
            "total": excess["total"],
            "items": _serialise(excess["items"]),
        },
        "note": (
            "These are Module 3 routing decisions only. "
            "Procurement and excess-stock actions are handled by Module 4 and "
            "Module 5 via native Python orchestration."
        ),
    }
