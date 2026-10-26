# Module 6 — Explainable AI (SHAP/TreeSHAP)

## 1. Model and Decision Inventory

| Component | Type | Explanation Method | SHAP? | Reason |
|---|---|---|---|---|
| **Module 2 — LightGBM Demand Forecasting** | Trained ML model (regression_l1) | `tree_shap` | Yes | TreeSHAP computes exact values for tree-based models |
| **Module 3 — Safety Stock** | Formula: SS = z x sd x sqrt(L) | `rule_based` | No | Not an ML prediction |
| **Module 3 — Reorder Point** | Formula: ROP = D x L + SS | `rule_based` | No | Not an ML prediction |
| **Module 3 — Replenishment Qty** | Formula: max(0, max_stock - pos) | `rule_based` | No | Not an ML prediction |
| **Module 3 — Routing Decision** | Priority rule cascade | `rule_based` | No | Deterministic business rules |
| **Module 4 — Supplier Ranking** | Sort by lead_time_days ASC, supplier_id tie-breaker | `rule_based` | No | Deterministic sort, not ML |
| **Module 5 — ABC Classification** | Cumulative revenue share ranking | `statistical` | No | Statistical threshold, not ML |
| **Module 5 — XYZ Classification** | Coefficient of variation (CV) thresholds | `statistical` | No | Statistical threshold, not ML |
| **Module 5 — Excess Stock Detection** | Three independent rule conditions | `rule_based` | No | Deterministic rules |

**Key finding**: Only the Module 2 LightGBM model (`lgbm_20260830.joblib`) qualifies for SHAP explanation. All other decisions are formula-based or statistical and are explained via their actual calculation inputs.

---

## 2. Supported Explanation Methods

### 2.1 TreeSHAP (forecast_shap)
- **Target**: LightGBM demand forecasting model — `lgbm_20260830.joblib`
- **Objective**: `regression_l1` (MAE-optimal; output = units_sold, non-negative floored)
- **SHAP method**: `shap.TreeExplainer` — exact TreeSHAP
- **18 Features**: lag_1..lag_28, roll_mean_7..roll_mean_28, roll_std_7..roll_std_14, day_of_week, day_of_month, week_of_year, month, is_weekend, product_code
- **Baseline**: `explainer.expected_value` = average model output over training data

### 2.2 Rule-Based (inventory_rule, procurement_rule)
Uses actual stored values from source tables. Shows formula inputs/outputs, threshold comparisons, and triggered rule conditions.

### 2.3 Statistical (excess_stock_rule)
Shows ABC/XYZ classification boundaries and actual CV/revenue-share values.

---

## 3. API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/explainability/health` | GET | Service and model availability |
| `/api/explainability/models` | GET | Available models and explanation methods |
| `/api/explainability/forecast/{product_id}` | GET | TreeSHAP forecast explanation |
| `/api/explainability/inventory/{assessment_id}` | GET | Module 3 rule explanation |
| `/api/explainability/procurement/{recommendation_id}` | GET | Module 4 rule explanation |
| `/api/explainability/excess-stock/{assessment_id}` | GET | Module 5 statistical explanation |

### Example: Forecast SHAP
```
GET /api/explainability/forecast/PROD00001?date=2026-08-31
```

Response:
```json
{
  "explanation_type": "forecast_shap",
  "explanation_method": "tree_shap",
  "prediction_value": 4.12,
  "baseline_value": 1.85,
  "shap_sum_check": 4.12,
  "feature_contributions": [
    {"rank": 1, "feature_name": "lag_1", "feature_value": 7.0,
     "shap_contribution": 1.45, "direction": "positive"},
    {"rank": 2, "feature_name": "roll_mean_7", "feature_value": 5.2,
     "shap_contribution": 0.88, "direction": "positive"}
  ],
  "data_warnings": ["STATIC_DATASET: Data ends 2026-08-30. ..."],
  "requires_manager_approval": true
}
```

### Example: Inventory Rule
```
GET /api/explainability/inventory/1001
```

Response shows safety stock formula (SS = z x sd x sqrt(L)), ROP formula, replenishment formula, and routing decision with actual values.

### Example: Procurement Rule
```
GET /api/explainability/procurement/501
```

Response shows ranked suppliers, lead times, tie handling, and disclosure that lead times are synthetic/modelled.

### Example: Excess Stock
```
GET /api/explainability/excess-stock/201
```

Response shows ABC class (revenue share), XYZ class (CV), triggered excess rules, and recommended action from the matrix.

---

## 4. Model Version and Feature Provenance

| Field | Value |
|---|---|
| Artifact | `module2_demand_forecasting/models/lgbm_20260830.joblib` |
| Model version | `20260830` |
| Feature count | 18 |
| Training objective | `regression_l1` |
| Training data end | 2026-05-31 |
| Validation MAE | 1.1658 units |
| Training products | 3,046 |

---

## 5. Explanation Interpretation and Limitations

### SHAP
- **Positive SHAP**: Feature pushes prediction above baseline — not a claim about future demand direction.
- **Negative SHAP**: Feature pushes prediction below baseline.
- **Not causal**: SHAP reflects the model's learned mapping, not causal relationships.
- **Reconstruction discrepancy**: Days 2-7 of the recursive forecast used prior predictions as lag inputs; the explanation reconstructs from historical actuals, causing a possible small discrepancy. Flagged in `data_warnings`.

### Rule-based
- Values are from stored records only — no recomputation.
- Manager approval is required before any action.

---

## 6. Data Freshness Warnings

- `STALE_FORECAST`: Forecast is more than 7 days old.
- `STATIC_DATASET`: Data ends 2026-08-30; all assessments are historical.
- `SYNTHETIC_LEAD_TIMES`: Supplier lead times are modelled, not live contract values.
- `STOCK`: Stock is from the 2026-08-01 inventory_batch snapshot.

---

## 7. Database Changes

New table: **`explanation_record`** (schema: `module3_inventory_optimization/sql/schema_m6.sql`).

Optional persistence — API works without it. Schema applied via `apply_explainability_schema(conn)`.

Key columns: `explanation_id`, `explanation_type`, `explanation_method`, `product_id`,
`source_record_id`, `model_name`, `model_version`, `prediction_value`, `baseline_value`,
`explanation_body` (JSONB), `data_warnings`.

---

## 8. Files Created or Modified

### New files
| File | Purpose |
|---|---|
| `backend/services/explainability_service.py` | Core service: TreeSHAP + rule-based explanations |
| `backend/routes/explainability.py` | FastAPI router for /api/explainability/* |
| `module3_inventory_optimization/sql/schema_m6.sql` | DB schema for explanation_record |
| `module6_explainability/__init__.py` | Package init |
| `module6_explainability/tests/__init__.py` | Test package init |
| `module6_explainability/tests/test_explainability.py` | Test suite (~100 tests) |
| `module6_explainability/pytest.ini` | pytest configuration |
| `module6_explainability/README.md` | This document |

### Modified files
| File | Change |
|---|---|
| `backend/main.py` | Added explainability router registration |
| `requirements.txt` | Added `shap>=0.44.0` |

---

## 9. Commands

```powershell
# Start the backend
.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000

# Install dependencies (including SHAP)
.venv\Scripts\python.exe -m pip install -r requirements.txt

# Run Module 6 tests
.venv\Scripts\python.exe -m pytest module6_explainability/tests/test_explainability.py -v

# Run Module 3 regression suite
.venv\Scripts\python.exe -m pytest module3_inventory_optimization/tests/ -v

# Run Module 2 regression suite
.venv\Scripts\python.exe -m pytest module2_demand_forecasting/tests/ -v
```

---

## 10. Known Limitations

1. **SHAP requires installation**: Without `shap`, forecast endpoint returns HTTP 503.
2. **Recursive forecast feature reconstruction**: Days 2-7 use prior predictions as lag inputs during inference but explanation reconstructs from historical actuals. Small discrepancy possible — documented in `data_warnings`.
3. **Static dataset**: All forecasts have origin 2026-08-31. Not live operational data.
4. **Unseen product_code**: Products not in training encoder get `product_code=-1`. Flagged in `data_warnings`.
5. **Native orchestration integration**: Module 6 remains independently callable through its supported stored-record explanation functions. Orchestration invokes an explanation where the corresponding record is persisted; explanation failures are reported separately and do not change the Module 3 routing decision or fabricate an explanation.
