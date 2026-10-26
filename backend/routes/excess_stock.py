"""
backend/routes/excess_stock.py
--------------------------------
Module 5 — Excess Stock Management API endpoints.

Prefix: /api/excess-stock

Endpoints:
  POST /api/excess-stock/assess               — run/refresh assessment for one product
  GET  /api/excess-stock/assessments          — paginated assessments (filterable)
  GET  /api/excess-stock/assessments/{pid}    — single product detail
  GET  /api/excess-stock/summary              — summary counts and category breakdown

Follows existing API conventions:
  - Pydantic v2 schemas
  - HTTP 404 for missing products or no assessments
  - HTTP 422 for invalid parameters
  - HTTP 409 when a persisted Module 3 assessment and matching forecast run
    cannot be safely linked
  - HTTP 503 if a critical dependency is unavailable
  - No stack traces or credentials in error responses
  - Consistent pagination: limit / offset query params

Safety guarantees (mirrored from orchestration):
  - requires_manager_approval is always True.
  - No stock quantities are modified by these endpoints.
  - No purchase orders are placed.
  - All stock and demand data is historical (2026-08-31 origin).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.services.db import get_db
from backend.services.inventory_service import get_product_with_supplier
from backend.services.excess_stock_service import (
    AssessmentLinkageError,
    apply_excess_schema,
    assess_excess_stock_product,
    upsert_excess_assessment,
    get_excess_assessment_by_product,
    get_excess_assessments_paginated,
    get_excess_assessment_summary,
    compute_population_abc,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/excess-stock",
    tags=["Excess Stock Management"],
)

VALID_EXCESS_CATEGORIES = {"overstock", "slow_moving", "dead_stock", "normal"}
VALID_ABC_CLASSES = {"A", "B", "C"}
VALID_XYZ_CLASSES = {"X", "Y", "Z"}


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class AssessExcessRequest(BaseModel):
    product_id: str = Field(
        ...,
        description="Product ID to assess (e.g. PROD00001).",
    )
    reason: Optional[str] = Field(
        default="Manual trigger",
        description="Optional reason label for audit trail.",
    )


class AssessExcessResponse(BaseModel):
    status: str
    message: str
    product_id: str
    abc_class: Optional[str] = None
    xyz_class: Optional[str] = None
    excess_category: Optional[str] = None
    recommended_action: Optional[str] = None
    confidence: Optional[str] = None
    requires_manager_approval: bool = True
    assessment_id: Optional[int] = None
    data_warnings: Optional[str] = None
    reason: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/assess", response_model=AssessExcessResponse)
def assess_excess_stock(body: AssessExcessRequest, conn=Depends(get_db)):
    """
    Run or refresh the Module 5 excess-stock assessment for a single product.

    Computes:
      - ABC class (population-level cumulative 12-month revenue ranking)
      - XYZ class (CV of 90-day gap-filled daily demand)
      - Excess stock category: overstock / slow_moving / dead_stock / normal
      - Recommended action with confidence level

    Safety guarantees:
      - requires_manager_approval is always True.
      - No stock quantities are modified.
      - No purchase orders are placed.
      - Stock and demand data are historical (snapshot from 2026-08-31).
    """
    pid = body.product_id.upper().strip()

    # Validate product exists
    product = get_product_with_supplier(conn, pid)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product '{pid}' not found.")

    # Apply schema (idempotent)
    try:
        apply_excess_schema(conn)
    except Exception as e:
        logger.warning(f"[ExcessStock API] Schema warning: {e}")

    try:
        # Compute ABC for entire population (needed for proper ranking)
        abc_population = compute_population_abc(conn)

        # Run full assessment
        result_row = assess_excess_stock_product(
            conn=conn,
            product_id=pid,
            assessment=None,  # standalone, no M3 context
            abc_population=abc_population,
        )

        # Persist
        assessment_id = upsert_excess_assessment(conn, result_row)

        return AssessExcessResponse(
            status="completed",
            message=(
                f"Excess stock assessment complete for product '{pid}'. "
                f"Category: {result_row.get('excess_category')}. "
                f"Recommended action: {result_row.get('recommended_action')}."
            ),
            product_id=pid,
            abc_class=result_row.get("abc_class"),
            xyz_class=result_row.get("xyz_class"),
            excess_category=result_row.get("excess_category"),
            recommended_action=result_row.get("recommended_action"),
            confidence=result_row.get("confidence"),
            requires_manager_approval=True,
            assessment_id=assessment_id,
            data_warnings=result_row.get("data_warnings"),
            reason=body.reason,
        )

    except AssessmentLinkageError as e:
        logger.warning(f"[ExcessStock API] Assessment linkage failed for {pid}: {e}")
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"[ExcessStock API] Assessment error for {pid}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Excess stock assessment failed: {e}",
        )


@router.get("/assessments")
def list_excess_assessments(
    excess_category: Optional[str] = Query(
        None,
        description="Filter: overstock | slow_moving | dead_stock | normal",
    ),
    abc_class: Optional[str] = Query(None, description="Filter by ABC class: A | B | C"),
    xyz_class: Optional[str] = Query(None, description="Filter by XYZ class: X | Y | Z"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    conn=Depends(get_db),
):
    """
    Paginated list of excess stock assessments (latest per product).
    Filters: excess_category, abc_class, xyz_class.
    Results are ordered: dead_stock first, then overstock, slow_moving, normal.
    """
    if excess_category and excess_category not in VALID_EXCESS_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid excess_category '{excess_category}'. "
                f"Valid values: {sorted(VALID_EXCESS_CATEGORIES)}"
            ),
        )
    if abc_class and abc_class.upper() not in VALID_ABC_CLASSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid abc_class '{abc_class}'. Valid values: A, B, C",
        )
    if xyz_class and xyz_class.upper() not in VALID_XYZ_CLASSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid xyz_class '{xyz_class}'. Valid values: X, Y, Z",
        )

    try:
        result = get_excess_assessments_paginated(
            conn,
            excess_category=excess_category,
            abc_class=abc_class.upper() if abc_class else None,
            xyz_class=xyz_class.upper() if xyz_class else None,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        logger.error(f"[ExcessStock API] List error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")

    # Serialise non-JSON-native types
    for item in result.get("items", []):
        for k, v in list(item.items()):
            if hasattr(v, "isoformat"):
                item[k] = str(v)

    return result


@router.get("/assessments/{product_id}")
def get_product_excess_assessment(product_id: str, conn=Depends(get_db)):
    """
    Full excess-stock assessment detail for a single product.
    Returns the most recent assessment.
    """
    pid = product_id.upper().strip()

    product = get_product_with_supplier(conn, pid)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product '{pid}' not found.")

    try:
        assessment = get_excess_assessment_by_product(conn, pid)
    except Exception as e:
        logger.error(f"[ExcessStock API] Detail error for {pid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")

    if assessment is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No excess-stock assessment found for product '{pid}'. "
                "Run POST /api/excess-stock/assess to generate one."
            ),
        )

    # Serialise
    for k, v in list(assessment.items()):
        if hasattr(v, "isoformat"):
            assessment[k] = str(v)

    product_serialised = {
        k: (str(v) if hasattr(v, "isoformat") else v)
        for k, v in product.items()
    }

    return {
        "product": product_serialised,
        "excess_stock_assessment": assessment,
        "requires_manager_approval": True,
        "data_notice": (
            "Stock and demand data are historical (snapshot: 2026-08-01 / "
            "2026-08-30). No live inventory feed. "
            "Manager approval required before any action."
        ),
    }


@router.get("/summary")
def get_excess_summary(conn=Depends(get_db)):
    """
    Summary counts of the latest excess-stock assessment per product,
    broken down by excess_category, ABC class, and XYZ class.
    """
    try:
        summary = get_excess_assessment_summary(conn)
    except Exception as e:
        logger.error(f"[ExcessStock API] Summary error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Summary query failed: {e}")

    if not summary or summary.get("total_assessed", 0) == 0:
        return {
            "total_assessed": 0,
            "overstock": 0,
            "slow_moving": 0,
            "dead_stock": 0,
            "normal": 0,
            "abc_a": 0, "abc_b": 0, "abc_c": 0,
            "xyz_x": 0, "xyz_y": 0, "xyz_z": 0,
            "last_assessed_at": None,
            "message": (
                "No excess-stock assessments found. "
                "Run POST /api/excess-stock/assess to generate assessments."
            ),
        }

    if summary.get("last_assessed_at") is not None:
        summary["last_assessed_at"] = str(summary["last_assessed_at"])

    return summary
