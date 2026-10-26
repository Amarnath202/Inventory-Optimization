"""
Native Python orchestration for one inventory optimization run.

Module 3 remains the sole source of the routing decision. This service invokes
at most one downstream agent and only produces recommendations; purchase orders
and inventory quantities are never changed here.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from backend.agents.excess_stock_agent import run_excess_stock_agent
from backend.agents.orchestration_state import OrchestrationState
from backend.agents.procurement_agent import run_procurement_agent
from backend.services.assessment_service import assess_product
from backend.services.explainability_service import (
    explain_excess_stock_assessment,
    explain_inventory_assessment,
    explain_procurement_recommendation,
)
from backend.services.inventory_service import (
    apply_schema,
    get_latest_forecast_run,
    upsert_assessment,
)

logger = logging.getLogger(__name__)

FORECAST_NOTICE = (
    "FORECAST: The demand forecast has origin 2026-08-31. This is a static "
    "historical dataset. Forecasts, ROP calculations, and recommendations "
    "are based on this snapshot, not a live production forecast."
)

STOCK_NOTICE = (
    "STOCK: Available stock is the current_quantity from the most recent "
    "inventory_batch (2026-08-01). No purchase orders or backorders are "
    "tracked. Inventory position equals available stock."
)

ROUTES = {
    "procurement_required": "procurement",
    "excess_stock_review": "excess_stock",
    "normal": "skip",
    "insufficient_data": "safe_stop",
}


def route_by_decision(state: OrchestrationState) -> str:
    """Return a safe native route for Module 3's immutable decision."""
    if state.get("execution_status") == "ERROR":
        return "safe_stop"
    return ROUTES.get(state.get("routing_decision") or "", "safe_stop")


def _record_error(state: OrchestrationState, stage: str, error: Exception) -> None:
    """Add a structured error without discarding prior audit details."""
    errors = list(state.get("errors", []))
    errors.append({"node": stage, "message": str(error), "detail": repr(error)})
    state["errors"] = errors
    logger.error("Orchestration %s failed: %s", stage, error, exc_info=True)


def _assess(conn, state: OrchestrationState) -> None:
    """Run Module 3 and persist its assessment before any routing."""
    notices = list(state.get("data_notices", []))
    if FORECAST_NOTICE not in notices:
        notices.insert(0, FORECAST_NOTICE)
    if STOCK_NOTICE not in notices:
        notices.insert(1, STOCK_NOTICE)
    state["data_notices"] = notices

    try:
        apply_schema(conn)
        forecast_run = get_latest_forecast_run(conn)
        if forecast_run is None:
            notices.append(
                "WARNING: No completed forecast run found. "
                "Assessment will use historical demand fallback."
            )
        assessment = assess_product(conn, state["product_id"], forecast_run)
        assessment["assessment_id"] = upsert_assessment(conn, assessment)
        state["assessment"] = assessment
        state["routing_decision"] = assessment.get(
            "routing_decision", "insufficient_data"
        )
        state["execution_status"] = "ASSESSED"
    except Exception as error:
        _record_error(state, "assess_node", error)
        state["assessment"] = None
        state["routing_decision"] = "insufficient_data"
        state["execution_status"] = "ERROR"


def _agent_error_result(agent: str, product_id: str, assessment: dict,
                        error: Exception) -> dict:
    """Build a safe recommendation-shaped failure without implying an action."""
    result = {
        "agent": agent,
        "status": "error",
        "product_id": product_id,
        "recommendation_notes": f"Agent execution failed: {error}",
        "requires_manager_approval": True,
    }
    if agent == "procurement_agent":
        result.update({
            "recommendation_status": "error",
            "suggested_replenishment_qty": assessment.get(
                "suggested_replenishment_qty"
            ),
            "ranking_method": "lead_time_based",
            "eligible_supplier_count": 0,
            "ranked_suppliers": [],
            "warning": str(error),
        })
    else:
        result.update({
            "abc_class": None,
            "xyz_class": None,
            "excess_qty_estimate": None,
            "recommended_action": None,
        })
    return result


def _run_downstream_agent(conn, state: OrchestrationState, route: str) -> None:
    """Run only the selected Module 4 or Module 5 agent."""
    product_id = state["product_id"]
    assessment = state.get("assessment") or {}
    agent_name, result_key, runner, status = (
        ("procurement_agent", "procurement_result", run_procurement_agent, "M4_COMPLETE")
        if route == "procurement"
        else ("excess_stock_agent", "excess_stock_result", run_excess_stock_agent, "M5_COMPLETE")
    )
    try:
        agent_result = runner(conn, state)
        if not isinstance(agent_result, dict) or agent_result.get(result_key) is None:
            raise ValueError(f"{agent_name} returned no {result_key}.")
        recommendation = agent_result[result_key]
        if not isinstance(recommendation, dict):
            raise TypeError(f"{agent_name} returned an invalid {result_key}.")
        if recommendation.get("requires_manager_approval") is not True:
            raise ValueError(f"{agent_name} recommendation must require manager approval.")
        state[result_key] = recommendation
        state["data_notices"] = list(
            agent_result.get("data_notices", state.get("data_notices", []))
        )
        if recommendation.get("status") == "error":
            message = (
                recommendation.get("warning")
                or recommendation.get("recommendation_notes")
                or f"{agent_name} returned an error result."
            )
            errors = list(state.get("errors", []))
            errors.append({
                "node": f"{route}_node",
                "message": str(message),
                "detail": str(message),
            })
            state["errors"] = errors
            logger.error("%s returned an error result: %s", agent_name, message)
            state["execution_status"] = "ERROR"
        else:
            state["execution_status"] = status
    except Exception as error:
        _record_error(state, f"{route}_node", error)
        state[result_key] = _agent_error_result(agent_name, product_id, assessment, error)
        state["execution_status"] = "ERROR"


def _explain(conn, state: OrchestrationState) -> None:
    """Use an existing Module 6 stored-record explanation when an ID exists."""
    assessment = state.get("assessment") or {}
    procurement = state.get("procurement_result") or {}
    excess_stock = state.get("excess_stock_result") or {}
    explanation: Optional[dict] = None
    explanation_type = "inventory_rule"
    record_id = assessment.get("assessment_id")
    explain = explain_inventory_assessment

    if (
        state.get("routing_decision") == "procurement_required"
        and procurement.get("recommendation_id") is not None
    ):
        explanation_type = "procurement_rule"
        record_id = procurement["recommendation_id"]
        explain = explain_procurement_recommendation
    elif (
        state.get("routing_decision") == "excess_stock_review"
        and excess_stock.get("m5_assessment_id") is not None
    ):
        explanation_type = "excess_stock_rule"
        record_id = excess_stock["m5_assessment_id"]
        explain = explain_excess_stock_assessment

    if record_id is None:
        state["explanation_result"] = {
            "status": "unavailable",
            "explanation_type": explanation_type,
            "message": "No persisted assessment or recommendation is available to explain.",
        }
        return

    try:
        explanation = explain(conn, record_id)
        if not isinstance(explanation, dict):
            raise ValueError("Module 6 returned no explanation object.")
    except Exception as error:
        _record_error(state, "module6_explanation", error)
        state["explanation_result"] = {
            "status": "error",
            "explanation_type": explanation_type,
            "message": "Module 6 could not generate an explanation from the stored record.",
        }
        return

    state["explanation_result"] = {
        "status": "available",
        "explanation_type": explanation_type,
        "result": explanation,
    }


def _finalize(state: OrchestrationState) -> None:
    """Assemble the response while retaining the route's terminal status."""
    assessment = state.get("assessment") or {}
    decision = state.get("routing_decision")
    if decision is None:
        decision = "insufficient_data"
    if decision == "insufficient_data" or decision not in ROUTES:
        reasons = assessment.get("decision_reasons", "unknown")
        notices = list(state.get("data_notices", []))
        notices.append(
            "HALT: No downstream agent was invoked. Manual review is required. "
            f"Routing decision: {decision}. Reasons: {reasons}."
        )
        state["data_notices"] = notices
        if state.get("execution_status") != "ERROR":
            state["execution_status"] = "INSUFFICIENT_DATA"
    elif route_by_decision(state) == "skip":
        state["execution_status"] = "SKIPPED"

    summary = {
        "assessment_id": assessment.get("assessment_id"),
        "assessed_at": assessment.get("assessed_at"),
        "available_stock": assessment.get("available_stock"),
        "inventory_position": assessment.get("inventory_position"),
        "safety_stock": assessment.get("safety_stock"),
        "reorder_point": assessment.get("reorder_point"),
        "suggested_replenishment_qty": assessment.get(
            "suggested_replenishment_qty"
        ),
        "routing_decision": decision,
        "decision_reasons": assessment.get("decision_reasons"),
        "rop_status": assessment.get("rop_status"),
        "forecast_run_id": assessment.get("forecast_run_id"),
    }
    if state.get("explanation_result") is not None:
        summary["module6_explanation"] = state["explanation_result"]

    state["orchestration_result"] = {
        "request_id": state["request_id"],
        "product_id": state["product_id"],
        "trigger_reason": state.get("trigger_reason", "unknown"),
        "execution_status": state.get("execution_status", "ERROR"),
        "routing_decision": decision,
        "assessment_summary": summary,
        "procurement_result": state.get("procurement_result"),
        "excess_stock_result": state.get("excess_stock_result"),
        "errors": state.get("errors", []),
        "data_notices": state.get("data_notices", []),
        "manager_action_required": True,
        "finalized_at": datetime.now(timezone.utc).isoformat(),
    }
    state["execution_status"] = "DONE"


def run_orchestration(
    conn, product_id: str, trigger_reason: str = "api"
) -> OrchestrationState:
    """Run Module 3 assessment, one safe conditional route, Module 6, and finalize."""
    state: OrchestrationState = {
        "product_id": product_id.upper().strip(),
        "request_id": str(uuid.uuid4()),
        "trigger_reason": trigger_reason,
        "execution_status": "PENDING",
        "errors": [],
        "data_notices": [],
        "assessment": None,
        "routing_decision": None,
        "procurement_result": None,
        "excess_stock_result": None,
        "explanation_result": None,
        "orchestration_result": None,
    }
    logger.info(
        "Starting orchestration: product=%s request_id=%s",
        state["product_id"],
        state["request_id"],
    )

    _assess(conn, state)
    route = route_by_decision(state)
    if route in ("procurement", "excess_stock"):
        _run_downstream_agent(conn, state, route)
    elif route == "safe_stop":
        state["execution_status"] = (
            "ERROR" if state.get("execution_status") == "ERROR" else "INSUFFICIENT_DATA"
        )
    _explain(conn, state)
    _finalize(state)
    logger.info(
        "Orchestration complete: product=%s status=%s",
        state["product_id"],
        state["orchestration_result"]["execution_status"],
    )
    return state
