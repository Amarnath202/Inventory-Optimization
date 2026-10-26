"""
backend/routes/procurement.py
------------------------------
Module 4 — Procurement-specific API endpoints.

Prefix: /api/procurement

Endpoints:
  POST /api/procurement/import-supplier-alternatives
      — Import supplier alternatives from module4_all_supplier_lead_time_ranking.csv.
        Idempotent. Safe to re-run. Reports validation results.

  GET  /api/procurement/recommend/{product_id}
      — Run lead-time-based Top-2 supplier ranking for a product
        using the existing product_supplier alternatives (without Module 3 assessment).
        Returns advisory recommendations only. Manager approval always required.

  GET  /api/procurement/recommendations
      — List recent procurement recommendations from the DB (paginated).

  GET  /api/procurement/suppliers/{product_id}
      — List all supplier alternatives for a product, sorted by lead time.

Safety guarantees:
  - No purchase orders are placed automatically.
  - No stock quantities are modified.
  - requires_manager_approval is always True.
  - ranking_method is always lead_time_based.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.services.db import get_db
from backend.services.supplier_alternatives_import import import_supplier_alternatives, apply_m4_schema
from backend.services.inventory_service import get_product_with_supplier
from backend.agents.procurement_agent import run_procurement_agent

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/procurement",
    tags=["Procurement (Module 4)"],
)


# ── Pydantic response schemas ─────────────────────────────────────────────────

class ImportResult(BaseModel):
    success: bool
    csv_path: str
    validation_errors: list
    data_conflicts: list
    inserted: int
    skipped: int
    rejected: int
    unknown_products: list
    unknown_suppliers: list
    summary: str


class SupplierEntry(BaseModel):
    supplier_id: str
    supplier_name: str
    lead_time_days: int


class RankedSupplier(BaseModel):
    rank: int
    supplier_id: str
    supplier_name: str
    lead_time_days: int
    explanation: str


class ProcurementRecommendationResponse(BaseModel):
    product_id: str
    product_name: Optional[str]
    suggested_replenishment_qty: Optional[int]
    ranking_method: str
    eligible_supplier_count: int
    recommendation_status: str
    ranked_suppliers: list
    has_lead_time_tie: bool
    tie_supplier_count: Optional[int]
    tie_explanation: Optional[str]
    requires_manager_approval: bool
    data_warnings: Optional[str]
    recommendation_notes: str
    warning: Optional[str]
    recommendation_id: Optional[int]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/import-supplier-alternatives", response_model=ImportResult)
def import_supplier_alternatives_endpoint(conn=Depends(get_db)):
    """
    Import supplier alternatives from module4_all_supplier_lead_time_ranking.csv
    into the product_supplier table.

    This endpoint:
    - Validates all CSV rows before writing.
    - Checks all product IDs and supplier IDs against existing master tables.
    - Checks for supplier name and lead-time conflicts.
    - Inserts new product-supplier relationships (ON CONFLICT DO NOTHING).
    - Is idempotent: safe to run multiple times without duplicate rows.
    - Does NOT delete or overwrite existing product_supplier rows.
    - Does NOT fabricate purchase rates or minimum order quantities.

    Returns a detailed import summary including inserted, skipped, and rejected counts.
    """
    try:
        # Ensure M4 schema exists first
        apply_m4_schema(conn)
    except Exception as e:
        logger.warning(f"[ImportEndpoint] M4 schema application failed: {e}")
        # Non-fatal — schema may already exist or procurement table optional

    result = import_supplier_alternatives(conn)

    if not result["success"] and result["validation_errors"]:
        # Still return 200 with details — the import ran but found blocking issues
        pass

    return ImportResult(**result)


@router.get("/recommend/{product_id}", response_model=ProcurementRecommendationResponse)
def get_procurement_recommendation(product_id: str, conn=Depends(get_db)):
    """
    Get a lead-time-based Top-2 supplier recommendation for a product.

    This endpoint runs the procurement ranking directly (without Module 3 assessment).
    Use POST /api/orchestration/run for the full Module 3 → Module 4 workflow.

    Ranking method: lead_time_based
      - Sorts eligible suppliers by ascending lead_time_days.
      - supplier_id breaks ties deterministically.
      - Returns up to 2 ranked suppliers.

    Safety:
      - No purchase orders are placed.
      - No stock quantities are modified.
      - requires_manager_approval is always True.
    """
    pid = product_id.upper().strip()

    # Validate product exists
    product = get_product_with_supplier(conn, pid)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product '{pid}' not found.")

    # Minimal state with just the product_id (no assessment context)
    minimal_state = {
        "product_id": pid,
        "assessment": {
            "suggested_replenishment_qty": None,
            "assessment_id": None,
        },
        "data_notices": [],
    }

    try:
        result_update = run_procurement_agent(conn, minimal_state)
    except Exception as e:
        logger.error(f"[ProcurementRoute] Error for {pid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Procurement agent error: {e}")

    pr = result_update.get("procurement_result", {})

    return ProcurementRecommendationResponse(
        product_id=pr.get("product_id", pid),
        product_name=pr.get("product_name"),
        suggested_replenishment_qty=pr.get("suggested_replenishment_qty"),
        ranking_method=pr.get("ranking_method", "lead_time_based"),
        eligible_supplier_count=pr.get("eligible_supplier_count", 0),
        recommendation_status=pr.get("recommendation_status", "error"),
        ranked_suppliers=pr.get("ranked_suppliers", []),
        has_lead_time_tie=pr.get("has_lead_time_tie", False),
        tie_supplier_count=pr.get("tie_supplier_count"),
        tie_explanation=pr.get("tie_explanation"),
        requires_manager_approval=True,
        data_warnings=pr.get("data_warnings"),
        recommendation_notes=pr.get("recommendation_notes", ""),
        warning=pr.get("warning"),
        recommendation_id=pr.get("recommendation_id"),
    )


@router.get("/suppliers/{product_id}")
def get_product_suppliers(product_id: str, conn=Depends(get_db)):
    """
    List all supplier alternatives for a product, sorted by ascending lead time.
    Uses existing product_supplier and supplier tables.
    """
    pid = product_id.upper().strip()

    product = get_product_with_supplier(conn, pid)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product '{pid}' not found.")

    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT
                s.supplier_id,
                s.supplier_name,
                s.lead_time_days
            FROM product_supplier ps
            JOIN supplier s ON ps.supplier_id = s.supplier_id
            WHERE ps.product_id = %s
            ORDER BY s.lead_time_days ASC, s.supplier_id ASC
            """,
            (pid,),
        )
        suppliers = [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()

    return {
        "product_id": pid,
        "product_name": product.get("product_name"),
        "eligible_supplier_count": len(suppliers),
        "suppliers": suppliers,
        "ranking_method": "lead_time_based",
        "note": (
            "Suppliers sorted by ascending lead_time_days. "
            "Lead times are modelled synthetic values from the supplier master table. "
            "Manager approval is required for any procurement action."
        ),
    }


@router.get("/recommendations")
def list_recommendations(
    product_id: Optional[str] = Query(default=None, description="Filter by product ID"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    conn=Depends(get_db),
):
    """
    List recent procurement recommendations from procurement_recommendation table.
    Requires the Module 4 schema to have been applied.
    """
    cur = conn.cursor()
    try:
        # Check if table exists
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'procurement_recommendation'
            ) AS tbl_exists
            """
        )
        if not cur.fetchone()["tbl_exists"]:
            return {
                "total": 0,
                "items": [],
                "limit": limit,
                "offset": offset,
                "note": "procurement_recommendation table does not exist yet. "
                        "Run POST /api/procurement/import-supplier-alternatives to create it.",
            }

        where_clause = "WHERE product_id = %s" if product_id else ""
        params: list = [product_id.upper().strip()] if product_id else []

        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM procurement_recommendation {where_clause}",
            params,
        )
        total = int(cur.fetchone()["cnt"])

        cur.execute(
            f"""
            SELECT *
            FROM procurement_recommendation
            {where_clause}
            ORDER BY recommended_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        items = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"[ProcurementRoute] Error listing recommendations: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        cur.close()

    return {
        "total": total,
        "items": items,
        "limit": limit,
        "offset": offset,
    }
