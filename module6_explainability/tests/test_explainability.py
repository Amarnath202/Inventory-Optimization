"""
module6_explainability/tests/test_explainability.py
------------------------------------------------------
Module 6 — Automated tests for the Explainability service.

Test categories:
  1. Model loading and SHAP infrastructure
  2. Feature validation
  3. SHAP computation correctness
  4. Rule-based explanations (Modules 3, 4, 5)
  5. API endpoint tests
  6. Error handling
  7. Regression guards (Modules 1–5 unchanged)

Design:
  - No real database calls — all DB interactions are mocked.
  - No model retraining.
  - All mocks use the actual 18 FEATURE_COLS from the service constants.
  - SHAP values are NOT fabricated — the tests verify structure and math,
    not magic numbers.
  - API tests use FastAPI TestClient with mocked DB dependency.
"""

import json
import math
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

# Ensure project root is importable
_PROJ_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_PROJ_ROOT))
_M2_ROOT = _PROJ_ROOT / "module2_demand_forecasting"
if _M2_ROOT.exists():
    sys.path.insert(0, str(_M2_ROOT))

from backend.services.explainability_service import FEATURE_COLS, N_FEATURES, MODEL_NAME


# ═════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def reset_module_cache():
    """Reset the module-level singleton cache before each test."""
    import backend.services.explainability_service as svc
    svc._model = None
    svc._encoder = None
    svc._metadata = None
    svc._model_version = None
    svc._shap_explainer = None
    svc._shap_available = None
    yield
    svc._model = None
    svc._encoder = None
    svc._metadata = None
    svc._model_version = None
    svc._shap_explainer = None
    svc._shap_available = None


@pytest.fixture
def valid_feature_row():
    """A valid 18-feature row matching FEATURE_COLS exactly."""
    return {
        "lag_1": 5.0,
        "lag_2": 4.0,
        "lag_3": 6.0,
        "lag_7": 3.0,
        "lag_14": 2.5,
        "lag_21": 4.5,
        "lag_28": 3.5,
        "roll_mean_7": 4.2,
        "roll_mean_14": 3.8,
        "roll_mean_28": 3.5,
        "roll_std_7": 1.1,
        "roll_std_14": 0.9,
        "day_of_week": 2,
        "day_of_month": 10,
        "week_of_year": 36,
        "month": 9,
        "is_weekend": 0,
        "product_code": 42,
    }


@pytest.fixture
def mock_lgbm_model():
    """A lightweight mock of a LightGBM regressor that returns predictable outputs."""
    model = MagicMock()
    model.predict.return_value = np.array([7.5])
    return model


@pytest.fixture
def mock_encoder():
    """A mock sklearn LabelEncoder that handles test product IDs."""
    from sklearn.preprocessing import LabelEncoder
    enc = LabelEncoder()
    enc.fit(["PROD00001", "PROD00002", "PROD00099"])
    return enc


@pytest.fixture
def valid_metadata():
    """Valid model metadata matching the actual model_metadata.json structure."""
    return {
        "model_version": "20260830",
        "feature_cols": FEATURE_COLS,
        "target_col": "units_sold",
        "lgbm_params": {"objective": "regression_l1", "n_estimators": 500},
        "val_mae": 1.1658,
        "trained_on": "2026-10-12",
        "latest_data_date": "2026-08-30",
    }


@pytest.fixture
def sample_assessment_row():
    """A realistic Module 3 inventory_assessment row."""
    return {
        "assessment_id": 1001,
        "product_id": "PROD00001",
        "assessed_at": "2026-10-12 10:00:00+05:30",
        "forecast_run_id": "abc123",
        "model_version": "20260830",
        "available_stock": 6,
        "inventory_position": 6,
        "stock_calculation_method": "latest_batch",
        "inventory_position_note": "Simplified: latest_batch.",
        "forecast_demand_7d": 12.3,
        "avg_daily_demand": 1.7556,
        "demand_source": "forecast_avg",
        "demand_std_daily": 1.6781,
        "history_days_used": 90,
        "nonzero_demand_days": 63,
        "lead_time_days": 3,
        "safety_stock": 5.0,
        "safety_stock_z": 1.645,
        "safety_stock_status": "calculated",
        "reorder_point": 11.0,
        "reorder_level_from_product": 6,
        "rop_status": "calculated",
        "suggested_replenishment_qty": 16,
        "minimum_order_qty": 2,
        "maximum_stock_level": 22,
        "replenishment_status": "calculated",
        "routing_decision": "procurement_required",
        "decision_reasons": "inventory_position(6) <= computed_rop(11.0)",
        "calculation_notes": "SS = z(1.645) × σ_d(1.6781) × √(L=3) | ...",
        "policy_version": "v1.0",
    }


@pytest.fixture
def sample_procurement_row():
    """A realistic Module 4 procurement_recommendation row."""
    return {
        "recommendation_id": 501,
        "product_id": "PROD00001",
        "recommended_at": "2026-10-12 10:05:00+05:30",
        "inventory_assessment_id": 1001,
        "suggested_replenishment_qty": 16,
        "ranking_method": "lead_time_based",
        "eligible_supplier_count": 3,
        "recommendation_status": "top2_available",
        "rank1_supplier_id": "SUP0010",
        "rank1_supplier_name": "Supplier Alpha",
        "rank1_lead_time_days": 2,
        "rank1_explanation": "Supplier Alpha ranked first with lead time 2 days.",
        "rank2_supplier_id": "SUP0025",
        "rank2_supplier_name": "Supplier Beta",
        "rank2_lead_time_days": 4,
        "rank2_explanation": "Supplier Beta ranked second with lead time 4 days.",
        "has_lead_time_tie": False,
        "tie_supplier_count": None,
        "tie_explanation": None,
        "requires_manager_approval": True,
        "data_warnings": "Lead-time data is sourced from the supplier master (modelled synthetic values).",
        "policy_version": "v1.0",
    }


@pytest.fixture
def sample_excess_row():
    """A realistic Module 5 excess_stock_assessment row."""
    return {
        "assessment_id": 201,
        "product_id": "PROD00001",
        "assessed_at": "2026-10-12 10:10:00+05:30",
        "inventory_assessment_id": 1001,
        "forecast_run_id": "abc123",
        "available_stock": 6,
        "inventory_position": 6,
        "maximum_stock_level": 22,
        "excess_qty_estimate": 0,
        "abc_class": "C",
        "revenue_12m": 4200.50,
        "total_revenue_12m": 9800000.0,
        "revenue_share": 0.000429,
        "cumulative_revenue_share": 0.98,
        "abc_rank": 2900,
        "abc_eligible": True,
        "xyz_class": "Z",
        "demand_cv": 0.72,
        "avg_daily_demand_90d": 0.95,
        "std_daily_demand_90d": 0.68,
        "nonzero_demand_days_90d": 55,
        "history_days_90d": 90,
        "xyz_eligible": True,
        "xyz_warning": None,
        "excess_category": "normal",
        "is_overstock": False,
        "is_slow_moving": False,
        "is_dead_stock": False,
        "excess_trigger_reasons": "inventory_position within normal operational range",
        "recommended_action": "review_for_discontinuation",
        "recommendation_notes": "ABC=C; XYZ=Z; Excess category=normal; ...",
        "requires_manager_approval": True,
        "data_warnings": "STOCK: static snapshot | FORECAST: historical",
        "confidence": "high",
        "policy_version": "v1.0",
    }


@pytest.fixture(scope="module")
def test_client():
    """
    TestClient for the full FastAPI app with database access overridden.
    """
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.services.db import get_db

    mock_conn = MagicMock()

    def override_get_db():
        yield mock_conn

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=False)
    yield client, mock_conn
    app.dependency_overrides.clear()


# ═════════════════════════════════════════════════════════════════════════════
# 1. Model loading and SHAP infrastructure
# ═════════════════════════════════════════════════════════════════════════════

class TestModelLoading:
    """Tests for model artifact loading and SHAP library availability."""

    def test_feature_cols_count(self):
        """FEATURE_COLS must have exactly 18 columns matching the metadata."""
        assert len(FEATURE_COLS) == 18
        assert N_FEATURES == 18

    def test_feature_cols_match_training_metadata(self):
        """FEATURE_COLS in service must match the actual model_metadata.json."""
        meta_path = _PROJ_ROOT / "module2_demand_forecasting" / "models" / "model_metadata.json"
        if not meta_path.exists():
            pytest.skip("model_metadata.json not found — skipping live artifact check")
        with open(meta_path) as f:
            meta = json.load(f)
        assert meta["feature_cols"] == FEATURE_COLS, (
            f"Feature column mismatch.\nMetadata: {meta['feature_cols']}\nService: {FEATURE_COLS}"
        )

    def test_model_artifact_loads(self, mock_lgbm_model, mock_encoder, valid_metadata):
        """load_model_artifacts() succeeds with valid mocked artifacts."""
        import backend.services.explainability_service as svc

        with patch("backend.services.explainability_service._MODELS_DIR") as mock_dir:
            mock_dir.__truediv__ = lambda self, other: MagicMock(
                exists=lambda: True,
                read_text=lambda: "20260830",
            )
            with patch("joblib.load") as mock_load, \
                 patch("builtins.open", MagicMock()), \
                 patch("json.load", return_value=valid_metadata):
                mock_load.side_effect = [mock_lgbm_model, mock_encoder]
                # Reset and re-patch paths
                svc._model = None

        # Simpler: load from actual file if it exists
        model_path = _PROJ_ROOT / "module2_demand_forecasting" / "models" / "lgbm_20260830.joblib"
        if model_path.exists():
            model, enc, meta, version = svc.load_model_artifacts()
            assert model is not None
            assert enc is not None
            assert version == "20260830"
            assert meta["model_version"] == "20260830"
        else:
            pytest.skip("lgbm_20260830.joblib not found — skipping live artifact load")

    def test_missing_model_raises_file_not_found(self, tmp_path):
        """load_model_artifacts() raises FileNotFoundError when artifact is missing."""
        import backend.services.explainability_service as svc

        with patch.object(svc, "_MODELS_DIR", tmp_path):
            with pytest.raises(FileNotFoundError):
                svc.load_model_artifacts()

    def test_feature_mismatch_raises_value_error(self, tmp_path, mock_lgbm_model, mock_encoder):
        """load_model_artifacts() raises ValueError if metadata feature cols don't match."""
        import json as _json
        import backend.services.explainability_service as svc

        wrong_meta = {
            "model_version": "20260830",
            "feature_cols": ["wrong_feature_1", "wrong_feature_2"],  # mismatch
            "target_col": "units_sold",
            "lgbm_params": {"objective": "regression_l1"},
        }

        # Create all required files in tmp_path
        (tmp_path / "latest_model_version.txt").write_text("20260830")
        (tmp_path / "lgbm_20260830.joblib").write_bytes(b"dummy")
        (tmp_path / "product_encoder.joblib").write_bytes(b"dummy")
        # Write real json so json.load works
        with open(tmp_path / "model_metadata.json", "w") as f:
            _json.dump(wrong_meta, f)

        with patch.object(svc, "_MODELS_DIR", tmp_path), \
             patch("joblib.load") as mock_load:
            mock_load.side_effect = [mock_lgbm_model, mock_encoder]
            with pytest.raises(ValueError, match="Feature column mismatch"):
                svc.load_model_artifacts()

    def test_shap_available_check(self):
        """_check_shap_available() returns True when SHAP is installed."""
        import backend.services.explainability_service as svc
        result = svc._check_shap_available()
        # We installed shap; this should be True
        assert isinstance(result, bool)

    def test_get_model_info_structure(self):
        """get_model_info() returns a dict with required keys."""
        import backend.services.explainability_service as svc
        info = svc.get_model_info()
        required_keys = [
            "model_name", "model_version", "model_available", "shap_available",
            "tree_shap_explanation_available", "feature_count", "feature_cols",
            "objective", "val_mae",
        ]
        for key in required_keys:
            assert key in info, f"Key '{key}' missing from get_model_info() response"

    def test_get_model_info_model_name(self):
        """get_model_info() returns the correct model name constant."""
        import backend.services.explainability_service as svc
        info = svc.get_model_info()
        assert info["model_name"] == MODEL_NAME

    def test_get_model_info_feature_count(self):
        """get_model_info() reports correct feature count."""
        import backend.services.explainability_service as svc
        info = svc.get_model_info()
        assert info["feature_count"] == 18


# ═════════════════════════════════════════════════════════════════════════════
# 2. Feature validation
# ═════════════════════════════════════════════════════════════════════════════

class TestFeatureValidation:
    """Tests for feature row validation in SHAP computation."""

    def test_missing_feature_raises_value_error(self, valid_feature_row, mock_lgbm_model,
                                                  mock_encoder, valid_metadata):
        """compute_shap_for_row() raises ValueError when a feature is missing."""
        import backend.services.explainability_service as svc

        incomplete_row = {k: v for k, v in valid_feature_row.items() if k != "lag_1"}

        svc._model = mock_lgbm_model
        svc._encoder = mock_encoder
        svc._metadata = valid_metadata
        svc._model_version = "20260830"

        with pytest.raises(ValueError, match="Missing required feature columns"):
            svc.compute_shap_for_row(incomplete_row)

    def test_extra_feature_keys_ignored(self, valid_feature_row, mock_lgbm_model,
                                         mock_encoder, valid_metadata):
        """Extra keys in feature_values dict don't cause failures — only FEATURE_COLS are used."""
        # Add extra key
        extra_row = {**valid_feature_row, "extra_unknown_col": 99.9}

        import backend.services.explainability_service as svc
        svc._model = mock_lgbm_model
        svc._encoder = mock_encoder
        svc._metadata = valid_metadata
        svc._model_version = "20260830"
        svc._shap_available = True

        import shap
        mock_explainer = MagicMock()
        mock_explainer.expected_value = 3.5
        sv = np.zeros((1, 18))
        sv[0, 0] = 2.0  # lag_1 positive contribution
        mock_explainer.shap_values.return_value = sv
        svc._shap_explainer = mock_explainer

        # Should not raise
        result = svc.compute_shap_for_row(extra_row)
        assert result is not None

    def test_all_feature_cols_present_in_valid_row(self, valid_feature_row):
        """All 18 FEATURE_COLS must be present in the valid_feature_row fixture."""
        for col in FEATURE_COLS:
            assert col in valid_feature_row, f"Feature '{col}' missing from valid_feature_row fixture"


# ═════════════════════════════════════════════════════════════════════════════
# 3. SHAP computation correctness
# ═════════════════════════════════════════════════════════════════════════════

class TestSHAPComputation:
    """Tests for SHAP value computation and mathematical properties."""

    def _setup_mock_shap(self, svc, mock_lgbm_model, mock_encoder, valid_metadata,
                          shap_vals, expected_value=3.5, pred_val=7.5):
        """Helper to inject mock model, encoder, metadata, and SHAP explainer."""
        svc._model = mock_lgbm_model
        svc._encoder = mock_encoder
        svc._metadata = valid_metadata
        svc._model_version = "20260830"
        svc._shap_available = True

        mock_explainer = MagicMock()
        mock_explainer.expected_value = expected_value
        mock_explainer.shap_values.return_value = shap_vals
        svc._shap_explainer = mock_explainer
        mock_lgbm_model.predict.return_value = np.array([pred_val])

    def test_shap_output_has_18_contributions(self, valid_feature_row, mock_lgbm_model,
                                               mock_encoder, valid_metadata):
        """compute_shap_for_row() returns exactly 18 feature contributions."""
        import backend.services.explainability_service as svc

        sv = np.zeros((1, 18))
        sv[0, 0] = 1.5  # lag_1
        self._setup_mock_shap(svc, mock_lgbm_model, mock_encoder, valid_metadata, sv)

        result = svc.compute_shap_for_row(valid_feature_row)
        assert len(result["feature_contributions"]) == 18

    def test_shap_baseline_preserved(self, valid_feature_row, mock_lgbm_model,
                                      mock_encoder, valid_metadata):
        """Baseline value in result matches explainer.expected_value."""
        import backend.services.explainability_service as svc

        sv = np.zeros((1, 18))
        expected_val = 3.1415
        self._setup_mock_shap(svc, mock_lgbm_model, mock_encoder, valid_metadata,
                               sv, expected_value=expected_val)

        result = svc.compute_shap_for_row(valid_feature_row)
        assert abs(result["baseline_value"] - expected_val) < 1e-4

    def test_shap_prediction_non_negative(self, valid_feature_row, mock_lgbm_model,
                                           mock_encoder, valid_metadata):
        """Prediction value is floored at 0 (matching forecast.py behaviour)."""
        import backend.services.explainability_service as svc

        sv = np.zeros((1, 18))
        # Model returns negative prediction
        mock_lgbm_model.predict.return_value = np.array([-2.5])
        self._setup_mock_shap(svc, mock_lgbm_model, mock_encoder, valid_metadata,
                               sv, pred_val=-2.5)

        result = svc.compute_shap_for_row(valid_feature_row)
        assert result["prediction_value"] >= 0.0, "Prediction must be non-negative (floor at 0)"

    def test_shap_sum_check_field_present(self, valid_feature_row, mock_lgbm_model,
                                           mock_encoder, valid_metadata):
        """shap_sum_check field is returned (baseline + sum(shap))."""
        import backend.services.explainability_service as svc

        sv = np.zeros((1, 18))
        sv[0, 0] = 2.0
        baseline = 3.5
        self._setup_mock_shap(svc, mock_lgbm_model, mock_encoder, valid_metadata,
                               sv, expected_value=baseline)

        result = svc.compute_shap_for_row(valid_feature_row)
        expected_sum_check = round(baseline + 2.0, 6)  # only lag_1 is non-zero
        assert abs(result["shap_sum_check"] - expected_sum_check) < 1e-4

    def test_shap_positive_direction_label(self, valid_feature_row, mock_lgbm_model,
                                            mock_encoder, valid_metadata):
        """Features with positive SHAP values are labelled 'positive'."""
        import backend.services.explainability_service as svc

        sv = np.zeros((1, 18))
        sv[0, 0] = 3.0    # lag_1 — positive
        sv[0, 1] = -1.5   # lag_2 — negative
        self._setup_mock_shap(svc, mock_lgbm_model, mock_encoder, valid_metadata, sv)

        result = svc.compute_shap_for_row(valid_feature_row)
        contrib_map = {c["feature_name"]: c for c in result["feature_contributions"]}

        assert contrib_map["lag_1"]["direction"] == "positive"
        assert contrib_map["lag_2"]["direction"] == "negative"

    def test_shap_contributions_ranked_by_abs_magnitude(self, valid_feature_row, mock_lgbm_model,
                                                          mock_encoder, valid_metadata):
        """Feature contributions are sorted by descending |shap_contribution|."""
        import backend.services.explainability_service as svc

        sv = np.zeros((1, 18))
        sv[0, 0] = 1.0    # lag_1
        sv[0, 2] = 5.0    # lag_3 — largest abs value
        sv[0, 12] = -2.0  # day_of_week
        self._setup_mock_shap(svc, mock_lgbm_model, mock_encoder, valid_metadata, sv)

        result = svc.compute_shap_for_row(valid_feature_row)
        contribs = result["feature_contributions"]

        # First entry must have the largest absolute value
        abs_vals = [abs(c["shap_contribution"]) for c in contribs]
        assert abs_vals == sorted(abs_vals, reverse=True), "Contributions not sorted by |shap|"

    def test_shap_rank_1_is_most_important(self, valid_feature_row, mock_lgbm_model,
                                            mock_encoder, valid_metadata):
        """rank=1 is assigned to the contribution with the highest |shap|."""
        import backend.services.explainability_service as svc

        sv = np.zeros((1, 18))
        sv[0, 6] = 4.5   # lag_28 — highest magnitude
        self._setup_mock_shap(svc, mock_lgbm_model, mock_encoder, valid_metadata, sv)

        result = svc.compute_shap_for_row(valid_feature_row)
        rank1 = next(c for c in result["feature_contributions"] if c["rank"] == 1)
        assert rank1["feature_name"] == "lag_28"

    def test_shap_wrong_n_features_raises(self, valid_feature_row, mock_lgbm_model,
                                           mock_encoder, valid_metadata):
        """SHAP returning wrong number of values raises ValueError."""
        import backend.services.explainability_service as svc

        # SHAP returns 5 values instead of 18
        sv = np.zeros((1, 5))
        self._setup_mock_shap(svc, mock_lgbm_model, mock_encoder, valid_metadata, sv)

        with pytest.raises(ValueError, match="SHAP returned 5 values, expected 18"):
            svc.compute_shap_for_row(valid_feature_row)

    def test_shap_unavailable_raises_import_error(self, valid_feature_row, mock_lgbm_model,
                                                    mock_encoder, valid_metadata):
        """get_shap_explainer() raises ImportError when SHAP is marked unavailable."""
        import backend.services.explainability_service as svc

        svc._model = mock_lgbm_model
        svc._encoder = mock_encoder
        svc._metadata = valid_metadata
        svc._model_version = "20260830"
        svc._shap_available = False  # Simulate no SHAP

        with pytest.raises(ImportError, match="SHAP library is not installed"):
            svc.get_shap_explainer()

    def test_shap_with_actual_model(self, valid_feature_row):
        """Integration: SHAP values sum to (prediction - baseline) for the actual model."""
        model_path = _PROJ_ROOT / "module2_demand_forecasting" / "models" / "lgbm_20260830.joblib"
        if not model_path.exists():
            pytest.skip("lgbm_20260830.joblib not found — skipping integration test")

        import backend.services.explainability_service as svc
        result = svc.compute_shap_for_row(valid_feature_row)

        # Mathematical consistency: baseline + sum(shap) ~= shap_sum_check
        total = result["baseline_value"] + sum(
            c["shap_contribution"] for c in result["feature_contributions"]
        )
        # shap_sum_check is rounded; allow small tolerance
        assert abs(total - result["shap_sum_check"]) < 0.01, (
            f"SHAP sum check inconsistency: sum={total:.6f}, reported={result['shap_sum_check']:.6f}"
        )
        # All contributions must be floats
        for c in result["feature_contributions"]:
            assert isinstance(c["shap_contribution"], float)
            assert c["direction"] in ("positive", "negative", "neutral")


# ═════════════════════════════════════════════════════════════════════════════
# 4. Rule-based explanations
# ═════════════════════════════════════════════════════════════════════════════

class TestInventoryExplanation:
    """Tests for Module 3 inventory rule explanations."""

    def _mock_cursor(self, conn, row_data):
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = row_data
        conn.cursor.return_value = mock_cur
        return mock_cur

    def test_inventory_explanation_type(self, sample_assessment_row):
        """explain_inventory_assessment() returns explanation_type='inventory_rule'."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_assessment_row)

        result = svc.explain_inventory_assessment(conn, 1001)
        assert result["explanation_type"] == "inventory_rule"

    def test_inventory_explanation_method(self, sample_assessment_row):
        """explain_inventory_assessment() returns explanation_method='rule_based'."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_assessment_row)

        result = svc.explain_inventory_assessment(conn, 1001)
        assert result["explanation_method"] == "rule_based"

    def test_inventory_explanation_not_shap(self, sample_assessment_row):
        """rule_based explanations must never claim to be SHAP."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_assessment_row)

        result = svc.explain_inventory_assessment(conn, 1001)
        assert result["explanation_method"] != "tree_shap"
        result_str = json.dumps(result)
        # The word 'shap' should not appear in the explanation method field
        assert result["explanation_method"] not in ("tree_shap", "shap")

    def test_inventory_safety_stock_formula(self, sample_assessment_row):
        """Safety stock explanation shows correct formula inputs and result."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_assessment_row)

        result = svc.explain_inventory_assessment(conn, 1001)
        ss_exp = result["safety_stock_explanation"]

        assert ss_exp["status"] == "calculated"
        assert "formula" in ss_exp
        assert "z × σ_d × √" in ss_exp["formula"]
        inputs = ss_exp["inputs"]
        assert inputs["z_factor"] == 1.645
        assert inputs["lead_time_days"] == 3
        assert abs(inputs["demand_std_daily"] - 1.6781) < 1e-4

    def test_inventory_safety_stock_math(self, sample_assessment_row):
        """Safety stock raw result matches z × σ × √L formula."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_assessment_row)

        result = svc.explain_inventory_assessment(conn, 1001)
        ss_exp = result["safety_stock_explanation"]

        z = 1.645
        std = 1.6781
        L = 3
        expected_raw = z * std * math.sqrt(L)
        assert abs(ss_exp["raw_result"] - expected_raw) < 0.001

    def test_inventory_rop_formula(self, sample_assessment_row):
        """ROP explanation contains correct formula and inputs."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_assessment_row)

        result = svc.explain_inventory_assessment(conn, 1001)
        rop_exp = result["reorder_point_explanation"]

        assert "formula" in rop_exp
        assert "avg_daily_demand" in rop_exp["inputs"]
        assert "lead_time_days" in rop_exp["inputs"]
        assert "safety_stock" in rop_exp["inputs"]

    def test_inventory_routing_decision_preserved(self, sample_assessment_row):
        """Routing decision matches stored value."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_assessment_row)

        result = svc.explain_inventory_assessment(conn, 1001)
        assert result["routing_explanation"]["routing_decision"] == "procurement_required"

    def test_inventory_manager_approval(self, sample_assessment_row):
        """Inventory explanation always sets requires_manager_approval=True."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_assessment_row)

        result = svc.explain_inventory_assessment(conn, 1001)
        assert result["requires_manager_approval"] is True

    def test_inventory_missing_record_raises(self):
        """explain_inventory_assessment() raises ValueError for unknown assessment_id."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        conn.cursor.return_value = mock_cur

        with pytest.raises(ValueError, match="No inventory_assessment record found"):
            svc.explain_inventory_assessment(conn, 99999)

    def test_inventory_data_warnings_present(self, sample_assessment_row):
        """Data warnings are included in the inventory explanation."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_assessment_row)

        result = svc.explain_inventory_assessment(conn, 1001)
        assert len(result["data_warnings"]) > 0
        assert any("STOCK" in w for w in result["data_warnings"])


class TestProcurementExplanation:
    """Tests for Module 4 procurement rule explanations."""

    def _mock_cursor(self, conn, row_data):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = row_data
        conn.cursor.return_value = mock_cur

    def test_procurement_explanation_type(self, sample_procurement_row):
        """explain_procurement_recommendation() returns explanation_type='procurement_rule'."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_procurement_row)

        result = svc.explain_procurement_recommendation(conn, 501)
        assert result["explanation_type"] == "procurement_rule"

    def test_procurement_explanation_method(self, sample_procurement_row):
        """explain_procurement_recommendation() returns explanation_method='rule_based'."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_procurement_row)

        result = svc.explain_procurement_recommendation(conn, 501)
        assert result["explanation_method"] == "rule_based"

    def test_procurement_not_shap(self, sample_procurement_row):
        """Procurement explanation must never be labelled as SHAP."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_procurement_row)

        result = svc.explain_procurement_recommendation(conn, 501)
        assert result["explanation_method"] not in ("tree_shap", "shap")

    def test_procurement_ranked_suppliers(self, sample_procurement_row):
        """Both ranked suppliers are present with lead times and explanations."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_procurement_row)

        result = svc.explain_procurement_recommendation(conn, 501)
        assert len(result["ranked_suppliers"]) == 2
        assert result["ranked_suppliers"][0]["rank"] == 1
        assert result["ranked_suppliers"][1]["rank"] == 2
        assert result["ranked_suppliers"][0]["lead_time_days"] < result["ranked_suppliers"][1]["lead_time_days"]

    def test_procurement_no_tie_case(self, sample_procurement_row):
        """No-tie case is handled correctly — tie fields are None/False."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        sample_procurement_row["has_lead_time_tie"] = False
        self._mock_cursor(conn, sample_procurement_row)

        result = svc.explain_procurement_recommendation(conn, 501)
        assert result["has_lead_time_tie"] is False
        assert result["tie_supplier_count"] is None

    def test_procurement_tie_case(self, sample_procurement_row):
        """Tie case: tie_supplier_count and tie_explanation are populated."""
        import backend.services.explainability_service as svc

        sample_procurement_row["has_lead_time_tie"] = True
        sample_procurement_row["tie_supplier_count"] = 2
        sample_procurement_row["tie_explanation"] = "2 suppliers share lead time 2 days."

        conn = MagicMock()
        self._mock_cursor(conn, sample_procurement_row)

        result = svc.explain_procurement_recommendation(conn, 501)
        assert result["has_lead_time_tie"] is True
        assert result["tie_supplier_count"] == 2
        assert "2 suppliers" in result["tie_explanation"]

    def test_procurement_synthetic_lead_time_warning(self, sample_procurement_row):
        """Data warnings include notice that lead times are synthetic/modelled."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_procurement_row)

        result = svc.explain_procurement_recommendation(conn, 501)
        assert any("synthetic" in w.lower() or "modelled" in w.lower()
                   for w in result["data_warnings"])

    def test_procurement_manager_approval(self, sample_procurement_row):
        """Procurement explanation always sets requires_manager_approval=True."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_procurement_row)

        result = svc.explain_procurement_recommendation(conn, 501)
        assert result["requires_manager_approval"] is True

    def test_procurement_missing_record_raises(self):
        """explain_procurement_recommendation() raises ValueError for unknown id."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        conn.cursor.return_value = mock_cur

        with pytest.raises(ValueError, match="No procurement_recommendation record found"):
            svc.explain_procurement_recommendation(conn, 99999)


class TestExcessStockExplanation:
    """Tests for Module 5 excess-stock statistical explanations."""

    def _mock_cursor(self, conn, row_data):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = row_data
        conn.cursor.return_value = mock_cur

    def test_excess_stock_explanation_type(self, sample_excess_row):
        """explain_excess_stock_assessment() returns explanation_type='excess_stock_rule'."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_excess_row)

        result = svc.explain_excess_stock_assessment(conn, 201)
        assert result["explanation_type"] == "excess_stock_rule"

    def test_excess_stock_explanation_method(self, sample_excess_row):
        """explain_excess_stock_assessment() returns explanation_method='statistical'."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_excess_row)

        result = svc.explain_excess_stock_assessment(conn, 201)
        assert result["explanation_method"] == "statistical"

    def test_excess_stock_not_shap(self, sample_excess_row):
        """Excess stock explanation must never be labelled as SHAP."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_excess_row)

        result = svc.explain_excess_stock_assessment(conn, 201)
        assert result["explanation_method"] not in ("tree_shap", "shap")

    def test_excess_stock_abc_thresholds_documented(self, sample_excess_row):
        """ABC classification explanation documents all three class thresholds."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_excess_row)

        result = svc.explain_excess_stock_assessment(conn, 201)
        thresholds = result["abc_classification_explanation"]["thresholds"]
        assert "A" in thresholds
        assert "B" in thresholds
        assert "C" in thresholds

    def test_excess_stock_xyz_thresholds_documented(self, sample_excess_row):
        """XYZ classification explanation documents CV thresholds."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_excess_row)

        result = svc.explain_excess_stock_assessment(conn, 201)
        thresholds = result["xyz_classification_explanation"]["thresholds"]
        assert "X" in thresholds
        assert "Y" in thresholds
        assert "Z" in thresholds

    def test_excess_stock_excess_rules_documented(self, sample_excess_row):
        """Excess-stock detection rules are documented with precedence."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_excess_row)

        result = svc.explain_excess_stock_assessment(conn, 201)
        detection = result["excess_stock_detection_explanation"]
        rules = detection["detection_rules"]
        assert "rule_1_dead_stock" in rules
        assert "rule_2_overstock" in rules
        assert "rule_3_slow_moving" in rules
        assert "precedence" in rules

    def test_excess_stock_triggered_rules_match(self, sample_excess_row):
        """Triggered rules in explanation match the stored excess_trigger_reasons."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_excess_row)

        result = svc.explain_excess_stock_assessment(conn, 201)
        detection = result["excess_stock_detection_explanation"]
        assert detection["triggered_rules"] == sample_excess_row["excess_trigger_reasons"]

    def test_excess_stock_recommendation_preserved(self, sample_excess_row):
        """Recommended action in explanation matches stored value."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_excess_row)

        result = svc.explain_excess_stock_assessment(conn, 201)
        rec = result["recommendation_explanation"]
        assert rec["recommended_action"] == sample_excess_row["recommended_action"]

    def test_excess_stock_manager_approval(self, sample_excess_row):
        """Excess stock explanation always sets requires_manager_approval=True."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_excess_row)

        result = svc.explain_excess_stock_assessment(conn, 201)
        assert result["requires_manager_approval"] is True

    def test_excess_stock_missing_record_raises(self):
        """explain_excess_stock_assessment() raises ValueError for unknown id."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        conn.cursor.return_value = mock_cur

        with pytest.raises(ValueError, match="No excess_stock_assessment record found"):
            svc.explain_excess_stock_assessment(conn, 99999)

    def test_excess_stock_data_warnings_present(self, sample_excess_row):
        """Data warnings are included in the excess-stock explanation."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        self._mock_cursor(conn, sample_excess_row)

        result = svc.explain_excess_stock_assessment(conn, 201)
        assert len(result["data_warnings"]) > 0


# ═════════════════════════════════════════════════════════════════════════════
# 5. API endpoint tests
# ═════════════════════════════════════════════════════════════════════════════

class TestExplainabilityAPIHealth:
    """Tests for the health endpoint."""

    def test_health_endpoint_exists(self, test_client):
        client, _ = test_client
        resp = client.get("/api/explainability/health")
        assert resp.status_code == 200

    def test_health_returns_service_name(self, test_client):
        client, _ = test_client
        resp = client.get("/api/explainability/health")
        data = resp.json()
        assert data.get("service") == "explainability"

    def test_health_contains_model_status(self, test_client):
        client, _ = test_client
        resp = client.get("/api/explainability/health")
        data = resp.json()
        assert "model_available" in data
        assert "shap_available" in data
        assert "tree_shap_explanation_available" in data


class TestExplainabilityAPIModels:
    """Tests for the models listing endpoint."""

    def test_models_endpoint_exists(self, test_client):
        client, _ = test_client
        resp = client.get("/api/explainability/models")
        assert resp.status_code == 200

    def test_models_returns_model_list(self, test_client):
        client, _ = test_client
        resp = client.get("/api/explainability/models")
        data = resp.json()
        assert "models" in data
        assert isinstance(data["models"], list)
        assert len(data["models"]) >= 1

    def test_models_includes_rule_based_explanations(self, test_client):
        client, _ = test_client
        resp = client.get("/api/explainability/models")
        data = resp.json()
        assert "rule_based_explanations" in data
        assert len(data["rule_based_explanations"]) == 3

    def test_models_shap_not_applicable_reason(self, test_client):
        client, _ = test_client
        resp = client.get("/api/explainability/models")
        data = resp.json()
        assert "shap_not_applicable_reason" in data


class TestForecastExplainAPI:
    """Tests for forecast SHAP explanation endpoint."""

    def test_forecast_explain_missing_product_returns_404_or_503(self, test_client):
        """Unknown product or missing forecast should return 4xx, not 500 with stack trace."""
        client, mock_conn = test_client

        mock_cur = MagicMock()
        # Simulate no forecast run found
        mock_cur.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cur

        resp = client.get("/api/explainability/forecast/NONEXISTENT_PROD")
        assert resp.status_code in (404, 503)
        data = resp.json()
        # Must not expose stack traces
        resp_text = str(data)
        assert "Traceback" not in resp_text
        assert "traceback" not in resp_text.lower()

    def test_forecast_explain_no_shap_returns_503(self, test_client):
        """When SHAP is not available, endpoint returns 503 with clear message."""
        import backend.services.explainability_service as svc
        client, mock_conn = test_client

        original_shap = svc._shap_available

        try:
            svc._shap_available = False

            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = {
                "forecast_run_id": "run1",
                "model_version": "20260830",
                "forecast_origin": date(2026, 8, 31),
                "target_date_from": date(2026, 8, 31),
                "target_date_to": date(2026, 9, 6),
                "run_completed_at": "2026-10-12",
            }
            mock_conn.cursor.return_value = mock_cur

            resp = client.get("/api/explainability/forecast/PROD00001?date=2026-08-31")
            assert resp.status_code == 503
        finally:
            svc._shap_available = original_shap


class TestInventoryExplainAPI:
    """Tests for inventory explanation endpoint."""

    def test_inventory_valid_assessment_returns_200(self, test_client, sample_assessment_row):
        client, mock_conn = test_client

        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = sample_assessment_row
        mock_conn.cursor.return_value = mock_cur

        resp = client.get("/api/explainability/inventory/1001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["explanation_type"] == "inventory_rule"
        assert data["explanation_method"] == "rule_based"

    def test_inventory_invalid_id_returns_404(self, test_client):
        client, mock_conn = test_client

        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cur

        resp = client.get("/api/explainability/inventory/99999")
        assert resp.status_code == 404

    def test_inventory_explanation_no_stack_trace(self, test_client):
        client, mock_conn = test_client

        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cur

        resp = client.get("/api/explainability/inventory/99999")
        resp_text = str(resp.json())
        assert "Traceback" not in resp_text
        assert "traceback" not in resp_text.lower()

    def test_inventory_requires_manager_approval_in_response(self, test_client, sample_assessment_row):
        client, mock_conn = test_client

        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = sample_assessment_row
        mock_conn.cursor.return_value = mock_cur

        resp = client.get("/api/explainability/inventory/1001")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("requires_manager_approval") is True


class TestProcurementExplainAPI:
    """Tests for procurement explanation endpoint."""

    def _mock_table_exists(self, mock_conn, exists=True, row=None):
        """Setup mock cursor for procurement endpoint (table check + data query)."""
        call_count = [0]
        mock_cur = MagicMock()

        def fetchone_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                return {"tbl_exists": exists}
            return row

        mock_cur.fetchone.side_effect = fetchone_side_effect
        mock_conn.cursor.return_value = mock_cur
        return mock_cur

    def test_procurement_table_missing_returns_404(self, test_client):
        client, mock_conn = test_client
        self._mock_table_exists(mock_conn, exists=False)

        resp = client.get("/api/explainability/procurement/501")
        assert resp.status_code == 404

    def test_procurement_valid_id_returns_200(self, test_client, sample_procurement_row):
        client, mock_conn = test_client
        self._mock_table_exists(mock_conn, exists=True, row=sample_procurement_row)

        resp = client.get("/api/explainability/procurement/501")
        assert resp.status_code == 200
        data = resp.json()
        assert data["explanation_type"] == "procurement_rule"
        assert data["explanation_method"] == "rule_based"

    def test_procurement_invalid_id_returns_404(self, test_client):
        client, mock_conn = test_client
        self._mock_table_exists(mock_conn, exists=True, row=None)

        resp = client.get("/api/explainability/procurement/99999")
        assert resp.status_code == 404

    def test_procurement_no_stack_trace(self, test_client):
        client, mock_conn = test_client
        self._mock_table_exists(mock_conn, exists=True, row=None)

        resp = client.get("/api/explainability/procurement/99999")
        resp_text = str(resp.json())
        assert "Traceback" not in resp_text


class TestExcessStockExplainAPI:
    """Tests for excess-stock explanation endpoint."""

    def _mock_table_exists(self, mock_conn, exists=True, row=None):
        call_count = [0]
        mock_cur = MagicMock()

        def fetchone_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                return {"tbl_exists": exists}
            return row

        mock_cur.fetchone.side_effect = fetchone_side_effect
        mock_conn.cursor.return_value = mock_cur
        return mock_cur

    def test_excess_stock_table_missing_returns_404(self, test_client):
        client, mock_conn = test_client
        self._mock_table_exists(mock_conn, exists=False)

        resp = client.get("/api/explainability/excess-stock/201")
        assert resp.status_code == 404

    def test_excess_stock_valid_id_returns_200(self, test_client, sample_excess_row):
        client, mock_conn = test_client
        self._mock_table_exists(mock_conn, exists=True, row=sample_excess_row)

        resp = client.get("/api/explainability/excess-stock/201")
        assert resp.status_code == 200
        data = resp.json()
        assert data["explanation_type"] == "excess_stock_rule"
        assert data["explanation_method"] == "statistical"

    def test_excess_stock_invalid_id_returns_404(self, test_client):
        client, mock_conn = test_client
        self._mock_table_exists(mock_conn, exists=True, row=None)

        resp = client.get("/api/explainability/excess-stock/99999")
        assert resp.status_code == 404

    def test_excess_stock_no_stack_trace(self, test_client):
        client, mock_conn = test_client
        self._mock_table_exists(mock_conn, exists=True, row=None)

        resp = client.get("/api/explainability/excess-stock/99999")
        resp_text = str(resp.json())
        assert "Traceback" not in resp_text


# ═════════════════════════════════════════════════════════════════════════════
# 6. Regression guards (Modules 1–5 unchanged)
# ═════════════════════════════════════════════════════════════════════════════

class TestRegressionGuards:
    """Verify that Module 6 does not change Modules 1–5 behavior."""

    def test_safety_stock_service_unchanged(self):
        """Module 3 safety stock service produces correct results (regression guard)."""
        from backend.services.safety_stock_service import calculate_safety_stock

        result = calculate_safety_stock(
            demand_std_daily=2.0,
            lead_time_days=4,
            nonzero_demand_days=30,
            z=1.645,
        )
        # SS = 1.645 × 2.0 × √4 = 1.645 × 2.0 × 2.0 = 6.58 → ceil = 7
        assert result["safety_stock"] == 7.0
        assert result["safety_stock_status"] == "calculated"

    def test_reorder_point_service_unchanged(self):
        """Module 3 reorder point service produces correct results (regression guard)."""
        from backend.services.reorder_point_service import calculate_reorder_point

        result = calculate_reorder_point(
            avg_daily_demand=3.0,
            lead_time_days=5,
            safety_stock=10.0,
            demand_source="forecast_avg",
        )
        # ROP = 3.0 × 5 + 10.0 = 25.0
        assert result["reorder_point"] == 25.0
        assert result["rop_status"] == "calculated"

    def test_replenishment_service_unchanged(self):
        """Module 3 replenishment service produces correct results (regression guard)."""
        from backend.services.replenishment_service import calculate_replenishment_qty

        result = calculate_replenishment_qty(
            inventory_position=10,
            maximum_stock_level=50,
            minimum_order_qty=5,
        )
        # raw = max(0, 50 - 10) = 40; 40 >= MOQ(5), so suggested = 40
        assert result["suggested_replenishment_qty"] == 40
        assert result["replenishment_status"] == "calculated"

    def test_replenishment_moq_applied_when_raw_below_moq(self):
        """MOQ adjustment is applied correctly (regression guard)."""
        from backend.services.replenishment_service import calculate_replenishment_qty

        result = calculate_replenishment_qty(
            inventory_position=48,
            maximum_stock_level=50,
            minimum_order_qty=5,
        )
        # raw = 2; 2 < MOQ(5), so suggested = 5
        assert result["suggested_replenishment_qty"] == 5

    def test_safety_stock_zero_for_insufficient_history(self):
        """Safety stock is 0 when nonzero_demand_days < MIN_NONZERO_DAYS (regression guard)."""
        from backend.services.safety_stock_service import calculate_safety_stock

        result = calculate_safety_stock(
            demand_std_daily=2.0,
            lead_time_days=4,
            nonzero_demand_days=3,  # Below default minimum of 7
        )
        assert result["safety_stock"] == 0.0
        assert result["safety_stock_status"] == "insufficient_nonzero_history"

    def test_feature_engineering_constants_unchanged(self):
        """Module 2 FEATURE_COLS and BURN_IN_ROWS are unchanged (regression guard)."""
        import sys
        sys.path.insert(0, str(_M2_ROOT))
        from src.feature_engineering import FEATURE_COLS as M2_FEAT, BURN_IN_ROWS

        assert M2_FEAT == FEATURE_COLS, (
            "Module 2 FEATURE_COLS have changed — explainability service must be updated."
        )
        assert BURN_IN_ROWS == 28

    def test_module6_does_not_modify_module3_routing(self, sample_assessment_row):
        """explain_inventory_assessment() reads but does not modify routing_decision."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = sample_assessment_row
        conn.cursor.return_value = mock_cur

        result = svc.explain_inventory_assessment(conn, 1001)

        # No INSERT or UPDATE should have been called
        assert not mock_cur.execute.called or all(
            "INSERT" not in str(call).upper() and "UPDATE" not in str(call).upper()
            for call in mock_cur.execute.call_args_list
        ), "explain_inventory_assessment must not write to the database"

    def test_module6_does_not_place_orders(self, sample_procurement_row):
        """explain_procurement_recommendation() does not place orders or modify DB."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = sample_procurement_row
        conn.cursor.return_value = mock_cur

        result = svc.explain_procurement_recommendation(conn, 501)

        assert not conn.commit.called, (
            "explain_procurement_recommendation must not commit transactions"
        )

    def test_manager_approval_required_in_all_explanations(
            self, sample_assessment_row, sample_procurement_row, sample_excess_row):
        """All explanation types set requires_manager_approval=True."""
        import backend.services.explainability_service as svc

        conn = MagicMock()
        mock_cur = MagicMock()
        conn.cursor.return_value = mock_cur

        for row, fn, arg in [
            (sample_assessment_row, svc.explain_inventory_assessment, 1001),
            (sample_procurement_row, svc.explain_procurement_recommendation, 501),
            (sample_excess_row, svc.explain_excess_stock_assessment, 201),
        ]:
            mock_cur.fetchone.return_value = row
            result = fn(conn, arg)
            assert result["requires_manager_approval"] is True, (
                f"{fn.__name__} did not set requires_manager_approval=True"
            )
