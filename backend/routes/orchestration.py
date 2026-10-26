"""
backend/routes/orchestration.py
---------------------------------
Module 3/4/5 Orchestration API endpoints.

Prefix: /api/orchestration

Endpoints:
  POST /api/orchestration/run          — run full orchestration for one product
  GET  /api/orchestration/run/{run_id} — retrieve a past orchestration result
  GET  /api/orchestration/status       — engine and capability summary

Follows existing API conventions:
  - Pydantic v2 schemas
  - HTTP 404 for missing products
  - HTTP 422 for invalid input
  - HTTP 503 if DB unavailable
  - No stack traces or credentials in error responses
  - Consistent JSON structure with existing /api/inventory-optimization routes
"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.services.db import get_db
from backend.services.inventory_service import get_product_with_supplier
from backend.agents.orchestration import run_orchestration

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/orchestration",
    tags=["Orchestration"],
)

# In-memory run history (process-scoped, non-persistent).
# Production would persist to a DB table. Sufficient for demo/test.
_run_cache: dict = {}


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class OrchestrationRequest(BaseModel):
    product_id: str = Field(..., description="Product ID to assess and route (e.g. PROD00001).")
    trigger_reason: Optional[str] = Field(
        default="api",
        description="Reason label: 'api' | 'manual' | 'scheduled'.",
    )


class OrchestrationResponse(BaseModel):
    request_id: str
    product_id: str
    execution_status: str
    routing_decision: Optional[str]
    assessment_summary: Optional[dict]
    procurement_result: Optional[dict]
    excess_stock_result: Optional[dict]
    errors: list
    data_notices: list
    manager_action_required: bool
    finalized_at: Optional[str]
    message: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/run", response_model=OrchestrationResponse)
def run_orchestration_endpoint(body: OrchestrationRequest, conn=Depends(get_db)):
    """
    Run native Python orchestration for a single product.

    Workflow:
      1. Module 3 assesses the product and produces a routing_decision.
      2. Native conditional routing selects:
         - Module 4 (Procurement Agent) if routing_decision = procurement_required
         - Module 5 (Excess Stock Agent) if routing_decision = excess_stock_review
         - Skip downstream agents if routing_decision = normal
         - Stop safely if routing_decision = insufficient_data
      3. Module 6 explains a persisted result where supported; failures are
         reported separately and do not fabricate an explanation.
      4. Finalize assembles the unified result.

    Safety guarantees:
      - routing_decision is immutable after Module 3 sets it.
      - No purchase orders are placed. No stock quantities are modified.
      - manager_action_required is always True.
      - Forecast data is historical (origin 2026-08-31); staleness notice is
        included in every response.

    This endpoint is idempotent per product+forecast_run combination:
    re-running for the same product updates the assessment row in-place
    (ON CONFLICT DO UPDATE) without creating duplicates.
    """
    pid = body.product_id.upper().strip()

    # Validate product exists before running orchestration.
    product = get_product_with_supplier(conn, pid)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Product '{pid}' not found.")

    try:
        final_state = run_orchestration(
            conn=conn,
            product_id=pid,
            trigger_reason=body.trigger_reason or "api",
        )
    except Exception as e:
        logger.error(f"Orchestration failed for {pid}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Orchestration error: {e}",
        )

    result = final_state.get("orchestration_result") or {}

    # Cache for GET by run_id
    request_id = result.get("request_id") or final_state.get("request_id", str(uuid.uuid4()))
    _run_cache[request_id] = result

    routing = result.get("routing_decision", "unknown")
    status = result.get("execution_status", "UNKNOWN")
    errors = result.get("errors", [])

    msg = f"Orchestration complete. Routing: {routing}. Status: {status}."
    if errors:
        msg += f" {len(errors)} error(s) encountered — see 'errors' field."

    return OrchestrationResponse(
        request_id=request_id,
        product_id=pid,
        execution_status=status,
        routing_decision=routing,
        assessment_summary=result.get("assessment_summary"),
        procurement_result=result.get("procurement_result"),
        excess_stock_result=result.get("excess_stock_result"),
        errors=errors,
        data_notices=result.get("data_notices", []),
        manager_action_required=True,
        finalized_at=result.get("finalized_at"),
        message=msg,
    )


@router.get("/run/{run_id}")
def get_orchestration_run(run_id: str):
    """
    Retrieve a past orchestration result by request_id.
    Results are held in process memory — restarting the server clears history.
    """
    result = _run_cache.get(run_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Orchestration run '{run_id}' not found. "
            "Results are process-scoped and cleared on server restart.",
        )
    return result


@router.get("/status")
def orchestration_status():
    """
    Orchestration capability summary and known limitations.
    """
    return {
        "orchestration_engine": "native_python",
        "workflow_steps": [
            "Module 3 assessment",
            "conditional decision routing",
            "Module 4 procurement or Module 5 excess-stock agent, when routed",
            "Module 6 explanation for persisted records, when supported",
            "final response",
        ],
        "routing_decisions": {
            "procurement_required": "→ Module 4 Procurement Agent",
            "excess_stock_review": "→ Module 5 Excess Stock Agent",
            "normal": "→ skip (no downstream agent)",
            "insufficient_data": "→ safe halt (no handoff)",
        },
        "safety_guarantees": [
            "No purchase orders placed automatically",
            "No stock quantities modified by orchestration",
            "manager_action_required is always True",
            "routing_decision immutable after Module 3 assessment",
            "M4 and M5 are mutually exclusive per run",
        ],
        "known_limitations": [
            "Forecast origin: 2026-08-31 (static historical dataset)",
            "Stock: latest_batch.current_quantity only (no live updates)",
            "No purchase orders / backorders tracked",
            "XGBoost stockout prediction deferred (no valid labels)",
            "ABC class uses single-product threshold (full Module 5 deliverable)",
            "M4 ranking: lead-time only; does not compare price or overall supplier quality",
            "Supplier lead times are modelled synthetic values, not verified operational records",
            "Run history is in-process only (not persisted to DB)",
        ],
        "active_runs_cached": len(_run_cache),
    }
