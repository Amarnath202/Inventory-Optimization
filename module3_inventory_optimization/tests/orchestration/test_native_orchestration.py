"""Regression coverage for the native orchestration service and API."""

import uuid
from unittest.mock import MagicMock, patch

from backend.agents import orchestration


def _assessment(decision="procurement_required", quantity=16):
    return {
        "product_id": "PROD00001",
        "assessment_id": None,
        "assessed_at": "2026-10-22T10:00:00+00:00",
        "available_stock": 6,
        "inventory_position": 6,
        "safety_stock": 5,
        "reorder_point": 9,
        "suggested_replenishment_qty": quantity,
        "routing_decision": decision,
        "decision_reasons": "test routing reason",
        "rop_status": "calculated",
        "forecast_run_id": "forecast-run-1",
    }


def _prepare(monkeypatch, decision="procurement_required", quantity=16):
    monkeypatch.setattr(orchestration, "apply_schema", MagicMock())
    monkeypatch.setattr(orchestration, "get_latest_forecast_run", MagicMock(return_value={}))
    monkeypatch.setattr(
        orchestration, "assess_product", MagicMock(return_value=_assessment(decision, quantity))
    )
    monkeypatch.setattr(orchestration, "upsert_assessment", MagicMock(return_value=73))
    monkeypatch.setattr(
        orchestration,
        "explain_inventory_assessment",
        MagicMock(return_value={"explanation_type": "inventory_rule"}),
    )
    monkeypatch.setattr(
        orchestration,
        "explain_procurement_recommendation",
        MagicMock(return_value={"explanation_type": "procurement_rule"}),
    )
    monkeypatch.setattr(
        orchestration,
        "explain_excess_stock_assessment",
        MagicMock(return_value={"explanation_type": "excess_stock_rule"}),
    )


def _procurement_result(quantity=16):
    return {
        "procurement_result": {
            "status": "recommendation_ready",
            "recommendation_id": 81,
            "suggested_replenishment_qty": quantity,
            "requires_manager_approval": True,
        },
        "data_notices": [],
    }


def _excess_result():
    return {
        "excess_stock_result": {
            "status": "review_ready",
            "m5_assessment_id": 91,
            "requires_manager_approval": True,
        },
        "data_notices": [],
    }


def test_procurement_route_passes_original_product_and_quantity(monkeypatch):
    _prepare(monkeypatch, quantity=0)
    conn = MagicMock()
    received = []

    def run_agent(conn_arg, state):
        received.append((conn_arg, state["product_id"],
                         state["assessment"]["suggested_replenishment_qty"]))
        return _procurement_result(quantity=0)

    monkeypatch.setattr(orchestration, "run_procurement_agent", run_agent)
    monkeypatch.setattr(orchestration, "run_excess_stock_agent", MagicMock())

    state = orchestration.run_orchestration(conn, " prod00001 ", "manual")
    result = state["orchestration_result"]

    assert received == [(conn, "PROD00001", 0)]
    assert result["routing_decision"] == "procurement_required"
    assert result["procurement_result"]["requires_manager_approval"] is True
    assert result["excess_stock_result"] is None
    assert result["execution_status"] == "M4_COMPLETE"
    assert state["execution_status"] == "DONE"
    assert result["assessment_summary"]["module6_explanation"]["status"] == "available"
    assert orchestration.explain_procurement_recommendation.call_args.args == (conn, 81)


def test_excess_stock_route_invokes_only_module5(monkeypatch):
    _prepare(monkeypatch, decision="excess_stock_review")
    conn = MagicMock()
    m5 = MagicMock(return_value=_excess_result())
    m4 = MagicMock()
    monkeypatch.setattr(orchestration, "run_excess_stock_agent", m5)
    monkeypatch.setattr(orchestration, "run_procurement_agent", m4)

    result = orchestration.run_orchestration(conn, "PROD00001")["orchestration_result"]

    m5.assert_called_once()
    m4.assert_not_called()
    assert result["execution_status"] == "M5_COMPLETE"
    assert result["excess_stock_result"]["requires_manager_approval"] is True
    assert result["procurement_result"] is None
    assert orchestration.explain_excess_stock_assessment.call_args.args == (conn, 91)


def test_normal_route_skips_downstream_agents(monkeypatch):
    _prepare(monkeypatch, decision="normal")
    m4, m5 = MagicMock(), MagicMock()
    monkeypatch.setattr(orchestration, "run_procurement_agent", m4)
    monkeypatch.setattr(orchestration, "run_excess_stock_agent", m5)

    result = orchestration.run_orchestration(MagicMock(), "PROD00001")["orchestration_result"]

    m4.assert_not_called()
    m5.assert_not_called()
    assert result["execution_status"] == "SKIPPED"
    assert result["procurement_result"] is None
    assert result["excess_stock_result"] is None


def test_insufficient_data_stops_safely(monkeypatch):
    _prepare(monkeypatch, decision="insufficient_data")
    m4, m5 = MagicMock(), MagicMock()
    monkeypatch.setattr(orchestration, "run_procurement_agent", m4)
    monkeypatch.setattr(orchestration, "run_excess_stock_agent", m5)

    result = orchestration.run_orchestration(MagicMock(), "PROD00001")["orchestration_result"]

    m4.assert_not_called()
    m5.assert_not_called()
    assert result["execution_status"] == "INSUFFICIENT_DATA"
    assert any("HALT" in notice and "Manual review" in notice for notice in result["data_notices"])


def test_unknown_decision_safely_stops_and_preserves_source_value(monkeypatch):
    _prepare(monkeypatch, decision="unexpected")
    m4, m5 = MagicMock(), MagicMock()
    monkeypatch.setattr(orchestration, "run_procurement_agent", m4)
    monkeypatch.setattr(orchestration, "run_excess_stock_agent", m5)

    result = orchestration.run_orchestration(MagicMock(), "PROD00001")["orchestration_result"]

    m4.assert_not_called()
    m5.assert_not_called()
    assert result["routing_decision"] == "unexpected"
    assert result["execution_status"] == "INSUFFICIENT_DATA"
    assert any("unexpected" in notice for notice in result["data_notices"])


def test_procurement_exception_is_captured_without_action(monkeypatch):
    _prepare(monkeypatch)
    conn = MagicMock()
    monkeypatch.setattr(
        orchestration, "run_procurement_agent", MagicMock(side_effect=RuntimeError("M4 down"))
    )

    result = orchestration.run_orchestration(conn, "PROD00001")["orchestration_result"]

    assert result["execution_status"] == "ERROR"
    assert result["procurement_result"]["status"] == "error"
    assert result["procurement_result"]["requires_manager_approval"] is True
    assert result["procurement_result"]["suggested_replenishment_qty"] == 16
    assert result["errors"][0]["node"] == "procurement_node"
    conn.cursor.assert_not_called()


def test_procurement_without_manager_approval_is_rejected(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(
        orchestration,
        "run_procurement_agent",
        MagicMock(return_value={
            "procurement_result": {"status": "recommendation_ready",
                                   "requires_manager_approval": False},
            "data_notices": [],
        }),
    )

    result = orchestration.run_orchestration(
        MagicMock(), "PROD00001"
    )["orchestration_result"]

    assert result["execution_status"] == "ERROR"
    assert result["procurement_result"]["status"] == "error"
    assert result["procurement_result"]["requires_manager_approval"] is True
    assert any("must require manager approval" in error["message"]
               for error in result["errors"])


def test_agent_error_result_sets_final_status_to_error(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(
        orchestration,
        "run_procurement_agent",
        MagicMock(return_value={
            "procurement_result": {
                "status": "error",
                "warning": "supplier query failed",
                "requires_manager_approval": True,
            },
            "data_notices": [],
        }),
    )

    result = orchestration.run_orchestration(
        MagicMock(), "PROD00001"
    )["orchestration_result"]

    assert result["execution_status"] == "ERROR"
    assert result["procurement_result"]["status"] == "error"
    assert result["errors"][0]["message"] == "supplier query failed"


def test_excess_stock_exception_is_captured_without_modification(monkeypatch):
    _prepare(monkeypatch, decision="excess_stock_review")
    conn = MagicMock()
    monkeypatch.setattr(
        orchestration,
        "run_excess_stock_agent",
        MagicMock(side_effect=RuntimeError("M5 down")),
    )

    result = orchestration.run_orchestration(conn, "PROD00001")["orchestration_result"]

    assert result["execution_status"] == "ERROR"
    assert result["excess_stock_result"]["status"] == "error"
    assert result["excess_stock_result"]["requires_manager_approval"] is True
    assert result["excess_stock_result"]["recommended_action"] is None
    assert result["errors"][0]["node"] == "excess_stock_node"
    conn.cursor.assert_not_called()


def test_module6_failure_is_explicit_without_fabrication_or_status_change(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(
        orchestration,
        "run_procurement_agent",
        MagicMock(return_value=_procurement_result()),
    )
    monkeypatch.setattr(
        orchestration,
        "explain_procurement_recommendation",
        MagicMock(side_effect=ValueError("record missing")),
    )

    result = orchestration.run_orchestration(MagicMock(), "PROD00001")["orchestration_result"]
    explanation = result["assessment_summary"]["module6_explanation"]

    assert result["execution_status"] == "M4_COMPLETE"
    assert explanation["status"] == "error"
    assert "result" not in explanation
    assert "record missing" not in str(explanation)
    assert result["errors"][-1]["node"] == "module6_explanation"


def test_module6_unavailable_when_no_persisted_assessment(monkeypatch):
    _prepare(monkeypatch, decision="normal")
    orchestration.upsert_assessment.return_value = None

    result = orchestration.run_orchestration(MagicMock(), "PROD00001")["orchestration_result"]

    explanation = result["assessment_summary"]["module6_explanation"]
    assert explanation["status"] == "unavailable"
    assert "No persisted assessment" in explanation["message"]
    orchestration.explain_inventory_assessment.assert_not_called()


def test_assessment_error_stops_before_agents(monkeypatch):
    _prepare(monkeypatch)
    monkeypatch.setattr(orchestration, "apply_schema", MagicMock(side_effect=RuntimeError("DB down")))
    m4, m5 = MagicMock(), MagicMock()
    monkeypatch.setattr(orchestration, "run_procurement_agent", m4)
    monkeypatch.setattr(orchestration, "run_excess_stock_agent", m5)

    state = orchestration.run_orchestration(MagicMock(), "PROD00001")
    result = state["orchestration_result"]

    m4.assert_not_called()
    m5.assert_not_called()
    assert result["execution_status"] == "ERROR"
    assert result["routing_decision"] == "insufficient_data"
    assert result["errors"][0]["node"] == "assess_node"
    assert state["execution_status"] == "DONE"


def test_repeated_runs_have_unique_request_ids_and_reassess(monkeypatch):
    _prepare(monkeypatch, decision="normal")
    conn = MagicMock()

    first = orchestration.run_orchestration(conn, "PROD00001")["orchestration_result"]
    second = orchestration.run_orchestration(conn, "PROD00001")["orchestration_result"]

    assert first["request_id"] != second["request_id"]
    assert orchestration.assess_product.call_count == 2
    assert orchestration.upsert_assessment.call_count == 2
    assert first["assessment_summary"]["assessment_id"] == 73
    assert second["assessment_summary"]["assessment_id"] == 73


def test_route_status_preserves_supported_api_contract(test_client):
    client, _ = test_client
    expected_keys = {
        "request_id", "product_id", "execution_status", "routing_decision",
        "assessment_summary", "procurement_result", "excess_stock_result",
        "errors", "data_notices", "manager_action_required", "finalized_at", "message",
    }
    run_id = str(uuid.uuid4())
    mocked_result = {
        "orchestration_result": {
            "request_id": run_id,
            "product_id": "PROD00001",
            "trigger_reason": "api",
            "execution_status": "SKIPPED",
            "routing_decision": "normal",
            "assessment_summary": {"module6_explanation": {"status": "available"}},
            "procurement_result": None,
            "excess_stock_result": None,
            "errors": [],
            "data_notices": [],
            "manager_action_required": True,
            "finalized_at": "2026-10-24T12:00:00+00:00",
        }
    }
    with patch(
        "backend.routes.orchestration.get_product_with_supplier",
        return_value={"product_id": "PROD00001"},
    ), patch(
        "backend.routes.orchestration.run_orchestration", return_value=mocked_result
    ):
        response = client.post("/api/orchestration/run", json={"product_id": "prod00001"})

    assert response.status_code == 200
    assert set(response.json()) == expected_keys
    assert response.json()["execution_status"] == "SKIPPED"
    assert response.json()["manager_action_required"] is True
    assert client.get("/api/orchestration/status").json()["orchestration_engine"] == "native_python"


def test_route_keeps_missing_product_http_behavior(test_client):
    client, _ = test_client
    with patch(
        "backend.routes.orchestration.get_product_with_supplier", return_value=None
    ):
        response = client.post("/api/orchestration/run", json={"product_id": "missing"})
    assert response.status_code == 404


def test_route_keeps_invalid_request_http_behavior(test_client):
    client, _ = test_client
    response = client.post("/api/orchestration/run", json={})
    assert response.status_code == 422
