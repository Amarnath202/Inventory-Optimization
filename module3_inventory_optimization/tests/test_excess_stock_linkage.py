"""Cross-module Module 5 persistence-linkage tests.

Set M5_LINKAGE_TEST_DATABASE to a disposable database name to enable the live
PostgreSQL test. The test refuses the shared development database.
"""

import os
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "module2_demand_forecasting"))


def test_latest_assessment_selection_has_deterministic_tie_breaker():
    from backend.services.excess_stock_service import (
        get_latest_linked_inventory_assessment,
    )

    cursor = _Cursor({
        "assessment_id": 11,
        "product_id": "PROD00001",
        "forecast_run_id": "run-11",
        "model_version": "model-v1",
        "linked_model_version": "model-v1",
        "linked_forecast_status": "completed",
        "has_product_forecast": True,
        "has_mismatched_forecast_model": False,
    })
    conn = _Connection(cursor)

    result = get_latest_linked_inventory_assessment(conn, "PROD00001")

    assert result["assessment_id"] == 11
    assert "ORDER BY ia.assessed_at DESC, ia.assessment_id DESC" in cursor.query


def test_latest_assessment_rejects_missing_module3_record():
    from backend.services.excess_stock_service import (
        AssessmentLinkageError,
        get_latest_linked_inventory_assessment,
    )

    with pytest.raises(AssessmentLinkageError, match="No persisted Module 3"):
        get_latest_linked_inventory_assessment(
            _Connection(_Cursor(None)), "UNKNOWN_PRODUCT"
        )


def test_assessment_rejects_unrelated_or_incomplete_forecast_links():
    from backend.services.excess_stock_service import (
        AssessmentLinkageError,
        get_latest_linked_inventory_assessment,
    )

    cases = [
        (
            {
                "assessment_id": 11,
                "product_id": "PROD00001",
                "forecast_run_id": "run-11",
                "model_version": "model-v1",
                "linked_model_version": "model-v2",
                "linked_forecast_status": "completed",
                "has_product_forecast": True,
                "has_mismatched_forecast_model": False,
            },
            "model version does not match",
        ),
        (
            {
                "assessment_id": 11,
                "product_id": "PROD00001",
                "forecast_run_id": "run-11",
                "model_version": "model-v1",
                "linked_model_version": "model-v1",
                "linked_forecast_status": "completed",
                "has_product_forecast": False,
                "has_mismatched_forecast_model": False,
            },
            "has no forecast rows",
        ),
        (
            {
                "assessment_id": 11,
                "product_id": "PROD00001",
                "forecast_run_id": "run-11",
                "model_version": "model-v1",
                "linked_model_version": "model-v1",
                "linked_forecast_status": "running",
                "has_product_forecast": True,
                "has_mismatched_forecast_model": False,
            },
            "not completed",
        ),
    ]

    for row, message in cases:
        with pytest.raises(AssessmentLinkageError, match=message):
            get_latest_linked_inventory_assessment(
                _Connection(_Cursor(row)), "PROD00001"
            )


class _Cursor:
    def __init__(self, row):
        self.row = row
        self.query = ""
        self.params = None

    def execute(self, query, params=None):
        self.query = query
        self.params = params

    def fetchone(self):
        return self.row

    def close(self):
        pass


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_module5_assessment_links_and_api_use_persisted_records():
    expected_database = os.getenv("M5_LINKAGE_TEST_DATABASE")
    if not expected_database:
        pytest.skip("Set M5_LINKAGE_TEST_DATABASE to a disposable PostgreSQL database.")

    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.services.db import get_db, get_db_conn
    from backend.services.excess_stock_service import (
        AssessmentLinkageError,
        apply_excess_schema,
        assess_excess_stock_product,
        get_latest_linked_inventory_assessment,
        upsert_excess_assessment,
    )
    from config.settings import DB_NAME

    if expected_database == "inventory_optimization_db2":
        pytest.fail("Refusing to run Module 5 linkage integration tests on the shared database.")
    if DB_NAME != expected_database:
        pytest.fail(
            f"Configured application database '{DB_NAME}' does not match "
            f"the disposable database '{expected_database}'."
        )

    conn = get_db_conn()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT current_database() AS database_name")
            assert cursor.fetchone()["database_name"] == expected_database
        apply_excess_schema(conn)
        linked_assessment = get_latest_linked_inventory_assessment(
            conn, "PROD00001"
        )
        product_id = linked_assessment["product_id"]

        result = assess_excess_stock_product(conn, product_id)
        assert result["inventory_assessment_id"] == linked_assessment["assessment_id"]
        assert str(result["forecast_run_id"]) == str(linked_assessment["forecast_run_id"])
        first_id = upsert_excess_assessment(conn, result)
    finally:
        conn.close()

    # Verify both references from a new database session, joined back to their
    # authoritative rows and to this product's forecast records.
    verification = get_db_conn()
    try:
        with verification.cursor() as cursor:
            cursor.execute(
                """
                SELECT esa.assessment_id,
                       esa.product_id,
                       esa.inventory_assessment_id,
                       esa.forecast_run_id,
                       ia.product_id AS inventory_product_id,
                       ia.forecast_run_id AS inventory_forecast_run_id,
                       fr.status AS forecast_status,
                       fr.model_version AS run_model_version,
                       COUNT(df.forecast_id) AS product_forecast_rows
                FROM excess_stock_assessment esa
                JOIN inventory_assessment ia
                  ON ia.assessment_id = esa.inventory_assessment_id
                 AND ia.product_id = esa.product_id
                JOIN forecast_run fr
                  ON fr.forecast_run_id = esa.forecast_run_id
                 AND fr.forecast_run_id = ia.forecast_run_id
                JOIN demand_forecast df
                  ON df.forecast_run_id = fr.forecast_run_id
                 AND df.product_id = esa.product_id
                WHERE esa.assessment_id = %s
                GROUP BY esa.assessment_id, ia.product_id, ia.forecast_run_id,
                         fr.status, fr.model_version
                """,
                (first_id,),
            )
            persisted = cursor.fetchone()
        assert persisted is not None
        assert persisted["inventory_assessment_id"] == linked_assessment["assessment_id"]
        assert str(persisted["forecast_run_id"]) == str(
            linked_assessment["forecast_run_id"]
        )
        assert persisted["product_id"] == persisted["inventory_product_id"] == product_id
        assert str(persisted["inventory_forecast_run_id"]) == str(
            persisted["forecast_run_id"]
        )
        assert persisted["forecast_status"] == "completed"
        assert persisted["product_forecast_rows"] > 0
    finally:
        verification.close()

    # Reject a Module 3 assessment belonging to a different product.
    other_product_conn = get_db_conn()
    try:
        with other_product_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT assessment_id
                FROM inventory_assessment
                WHERE product_id <> %s
                ORDER BY assessed_at DESC, assessment_id DESC
                LIMIT 1
                """,
                (product_id,),
            )
            other_assessment = cursor.fetchone()
            cursor.execute(
                """
                SELECT forecast_run_id
                FROM forecast_run
                WHERE forecast_run_id <> %s
                  AND status = 'completed'
                ORDER BY run_completed_at DESC, forecast_run_id DESC
                LIMIT 1
                """,
                (linked_assessment["forecast_run_id"],),
            )
            unrelated_run = cursor.fetchone()
        assert other_assessment is not None, "No other persisted product assessment to validate."
        assert unrelated_run is not None, "No unrelated completed forecast run to validate."
        with pytest.raises(AssessmentLinkageError, match="does not belong"):
            get_latest_linked_inventory_assessment(
                other_product_conn,
                product_id,
                assessment_id=other_assessment["assessment_id"],
            )

        mismatched_product = dict(result, product_id="PROD99999")
        with pytest.raises(AssessmentLinkageError, match="does not belong"):
            upsert_excess_assessment(other_product_conn, mismatched_product)

        mismatched_run = dict(result, forecast_run_id=unrelated_run["forecast_run_id"])
        with pytest.raises(AssessmentLinkageError, match="does not belong"):
            upsert_excess_assessment(other_product_conn, mismatched_run)

        unlinked = dict(result, inventory_assessment_id=None, forecast_run_id=None)
        with pytest.raises(AssessmentLinkageError, match="requires both"):
            upsert_excess_assessment(other_product_conn, unlinked)
    finally:
        other_product_conn.rollback()
        other_product_conn.close()

    # Repeated assessments remain historical rows, and the latest-row API
    # continues to select the newest assessment with existing filters/paging.
    conn = get_db_conn()
    try:
        repeated = assess_excess_stock_product(conn, product_id)
        second_id = upsert_excess_assessment(conn, repeated)
    finally:
        conn.close()

    latest_conn = get_db_conn()
    try:
        latest = get_latest_linked_inventory_assessment(latest_conn, product_id)
        with latest_conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT assessment_id, inventory_assessment_id, forecast_run_id
                FROM excess_stock_assessment
                WHERE product_id = %s
                ORDER BY assessed_at DESC, assessment_id DESC
                LIMIT 1
                """,
                (product_id,),
            )
            latest_excess = cursor.fetchone()
        assert latest["assessment_id"] == linked_assessment["assessment_id"]
        assert latest_excess["assessment_id"] == second_id
        assert latest_excess["assessment_id"] > first_id
        assert latest_excess["inventory_assessment_id"] == latest["assessment_id"]
        assert str(latest_excess["forecast_run_id"]) == str(latest["forecast_run_id"])
    finally:
        latest_conn.close()

    def integration_db_dependency():
        api_conn = get_db_conn()
        try:
            with api_conn.cursor() as cursor:
                cursor.execute("SELECT current_database() AS database_name")
                if cursor.fetchone()["database_name"] != expected_database:
                    raise RuntimeError("Module 5 API connected to an unexpected database.")
            yield api_conn
        finally:
            api_conn.close()

    app.dependency_overrides[get_db] = integration_db_dependency
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/excess-stock/assess",
                json={"product_id": product_id, "reason": "linkage integration test"},
            )
            assert response.status_code == 200, response.text
            api_assessment_id = response.json()["assessment_id"]
            assert api_assessment_id is not None

            api_verification = get_db_conn()
            try:
                with api_verification.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT esa.inventory_assessment_id,
                               esa.forecast_run_id,
                               esa.product_id,
                               ia.product_id AS inventory_product_id,
                               ia.forecast_run_id AS inventory_forecast_run_id
                        FROM excess_stock_assessment esa
                        JOIN inventory_assessment ia
                          ON ia.assessment_id = esa.inventory_assessment_id
                        JOIN forecast_run fr
                          ON fr.forecast_run_id = esa.forecast_run_id
                         AND fr.forecast_run_id = ia.forecast_run_id
                        JOIN demand_forecast df
                          ON df.forecast_run_id = fr.forecast_run_id
                         AND df.product_id = esa.product_id
                        WHERE esa.assessment_id = %s
                        LIMIT 1
                        """,
                        (api_assessment_id,),
                    )
                    api_link = cursor.fetchone()
                assert api_link is not None
                assert api_link["inventory_assessment_id"] == linked_assessment["assessment_id"]
                assert str(api_link["forecast_run_id"]) == str(
                    linked_assessment["forecast_run_id"]
                )
                assert api_link["product_id"] == api_link["inventory_product_id"] == product_id
                assert str(api_link["inventory_forecast_run_id"]) == str(
                    api_link["forecast_run_id"]
                )
            finally:
                api_verification.close()

            detail = client.get(f"/api/excess-stock/assessments/{product_id}")
            assert detail.status_code == 200
            assert detail.json()["excess_stock_assessment"]["inventory_assessment_id"] == (
                linked_assessment["assessment_id"]
            )
            assert str(
                detail.json()["excess_stock_assessment"]["forecast_run_id"]
            ) == str(linked_assessment["forecast_run_id"])

            category = detail.json()["excess_stock_assessment"]["excess_category"]
            filtered = client.get(
                "/api/excess-stock/assessments",
                params={"excess_category": category, "limit": 1, "offset": 0},
            )
            assert filtered.status_code == 200
            assert filtered.json()["total"] >= 1
            assert len(filtered.json()["items"]) == 1

            summary = client.get("/api/excess-stock/summary")
            assert summary.status_code == 200
            assert summary.json()["total_assessed"] >= 1
    finally:
        app.dependency_overrides.pop(get_db, None)
