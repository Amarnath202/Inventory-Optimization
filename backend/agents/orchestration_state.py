"""
backend/agents/orchestration_state.py
--------------------------------------
Typed state shared by the native inventory orchestration service and agents.

State lifecycle:
  1. PENDING      — request received, not yet assessed
  2. ASSESSED     — Module 3 assessment complete
  3. ROUTING      — native conditional routing chooses a downstream path
  4. M4_COMPLETE  — Module 4 (Procurement) result captured
  5. M5_COMPLETE  — Module 5 (Excess Stock) result captured
  6. DONE         — orchestration result has been finalized
  7. ERROR        — a processing stage failed and was recorded

Design constraints (enforced by the orchestration service, not here):
  - Orchestration NEVER places purchase orders or modifies stock quantities.
  - An assessment result is advisory only; manager approval is required.
  - Module 3's routing_decision is immutable after assessment.
  - Downstream agents may NOT overwrite routing_decision or assessment fields.
  - Forecast data is static/historical (origin 2026-08-31). Every response
    carries a staleness notice. This is a known limitation.
"""

from __future__ import annotations

from typing import Any, Optional
from typing_extensions import TypedDict


class AssessmentResult(TypedDict, total=False):
    """Module 3 inventory assessment output."""
    product_id: str
    assessment_id: Optional[int]
    assessed_at: Optional[str]
    forecast_run_id: Optional[str]
    model_version: Optional[str]
    available_stock: int
    inventory_position: int
    stock_calculation_method: str
    inventory_position_note: str
    avg_daily_demand: Optional[float]
    demand_std_daily: Optional[float]
    forecast_demand_7d: Optional[float]
    demand_source: Optional[str]
    history_days_used: Optional[int]
    nonzero_demand_days: Optional[int]
    lead_time_days: Optional[int]
    safety_stock: Optional[float]
    safety_stock_status: Optional[str]
    reorder_point: Optional[float]
    reorder_level_from_product: Optional[int]
    rop_status: Optional[str]
    suggested_replenishment_qty: Optional[int]
    minimum_order_qty: Optional[int]
    maximum_stock_level: Optional[int]
    replenishment_status: Optional[str]
    routing_decision: str          # final routing outcome — immutable post-assess
    decision_reasons: Optional[str]
    calculation_notes: Optional[str]
    policy_version: str


class ProcurementResult(TypedDict, total=False):
    """
    Module 4 Procurement Agent output.
    Lead-Time-Based Supplier Ranking: returns Top 2 suppliers sorted by
    ascending lead_time_days, with supplier_id as deterministic tie-breaker.

    Fields
    ------
    agent                   : "procurement_agent"
    status                  : "recommendation_ready" | "no_eligible_supplier" | "error"
    recommendation_status   : "top2_available" | "only_one_available" |
                              "no_supplier_available" | "error"
    product_id              : str
    product_name            : str | None
    suggested_replenishment_qty : int   — from Module 3, never overridden
    ranking_method          : "lead_time_based"
    eligible_supplier_count : int       — total suppliers considered
    ranked_suppliers        : list      — up to 2 dicts with rank/id/name/lead_time/explanation
    has_lead_time_tie       : bool      — True if ≥2 suppliers tied at rank-1 lead time
    tie_supplier_count      : int|None  — how many tied at rank-1 (if tied)
    tie_explanation         : str|None  — honest tie-break note (if tied)
    requires_manager_approval : bool    — always True — no automatic ordering
    data_warnings           : str|None  — semicolon-separated warnings
    recommendation_notes    : str       — human-readable summary
    warning                 : str|None  — critical agent-level warning

    Flat backward-compatible fields (rank 1 info)
    ---------------------------------------------
    recommended_supplier_id   : str|None
    recommended_supplier_name : str|None
    supplier_lead_time_days   : int|None
    rank2_supplier_id         : str|None
    rank2_supplier_name       : str|None
    rank2_lead_time_days      : int|None
    topsis_score              : None (legacy compatibility field; not used by ranking)
    recommendation_id         : int|None (DB persistence row id, if persisted)
    """
    agent: str
    status: str
    recommendation_status: str
    product_id: str
    product_name: Optional[str]
    suggested_replenishment_qty: int
    ranking_method: str
    eligible_supplier_count: int
    ranked_suppliers: list
    has_lead_time_tie: bool
    tie_supplier_count: Optional[int]
    tie_explanation: Optional[str]
    requires_manager_approval: bool
    data_warnings: Optional[str]
    recommendation_notes: str
    warning: Optional[str]
    # Backward-compatible flat fields
    recommended_supplier_id: Optional[str]
    recommended_supplier_name: Optional[str]
    supplier_lead_time_days: Optional[int]
    rank2_supplier_id: Optional[str]
    rank2_supplier_name: Optional[str]
    rank2_lead_time_days: Optional[int]
    topsis_score: None
    recommendation_id: Optional[int]


class ExcessStockResult(TypedDict, total=False):
    """
    Module 5 Excess Stock Management Agent output.
    ABC/XYZ analysis (to be implemented in full Module 5).
    This version provides structured stub output with all required fields.
    """
    agent: str                          # "excess_stock_agent"
    status: str                         # "review_ready" | "no_action" | "error"
    product_id: str
    abc_class: Optional[str]            # None until full M5 is implemented
    xyz_class: Optional[str]            # None until full M5 is implemented
    excess_qty_estimate: Optional[int]
    recommended_action: Optional[str]   # e.g. "markdown", "return_to_supplier", "liquidate"
    recommendation_notes: str
    requires_manager_approval: bool     # always True


class OrchestrationState(TypedDict, total=False):
    """
    Full state for one native Python orchestration run.

    Fields must only be written by their designated node.
    routing_decision is written by assess_node and treated as read-only thereafter.
    """
    # ── Request ────────────────────────────────────────────────────────────────
    product_id: str                         # input: the product to assess
    request_id: str                         # UUID string for idempotency tracking
    trigger_reason: str                     # e.g. "manual", "scheduled", "api"

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    execution_status: str                   # PENDING | ASSESSED | ROUTING | DONE | ERROR
    errors: list                            # accumulated error dicts {node, message, detail}

    # ── Module 3 output (written by assess_node, read-only thereafter) ─────────
    assessment: Optional[AssessmentResult]
    routing_decision: Optional[str]         # mirrors assessment.routing_decision for routing

    # ── Downstream agent outputs (written by their respective nodes only) ──────
    procurement_result: Optional[ProcurementResult]
    excess_stock_result: Optional[ExcessStockResult]
    explanation_result: Optional[dict]

    # ── Final unified result ────────────────────────────────────────────────────
    orchestration_result: Optional[dict]    # assembled by finalize_node

    # ── Data-quality notices (appended by any node, never cleared) ─────────────
    data_notices: list                      # list of str — limitations / stale-data warnings
