# Module 2 â€” API Endpoint Inventory and Test Coverage Analysis

**Project:** Inventory Optimization and Excess Stock Management System for Retail Stores
**Module:** Module 2 â€” Demand Forecasting & Forecast Analytics
**Date:** 2026-10-12

---

## 1. Executive Summary

During the initial technical audit, an ambiguity was identified in the test reporting: the report listed 9 API route descriptions while stating that 6 smoke tests passed. This document reconciles the total available endpoints, distinguishes between smoke test scripts and complete endpoint coverage, and documents live test verification results for all 11 endpoints across the application.

### Key Inventory Findings:
- **Total Application Endpoints:** **11 endpoints** (2 in `backend/main.py` + 9 in `backend/routes/forecasts.py`).
- **Initial Smoke Test Coverage:** **6 endpoints** (`smoke_test.py`).
- **Audit Verification Test Coverage:** **11 endpoints (100% of available endpoints)** tested live and returning valid HTTP responses.

---

## 2. Complete Endpoint Inventory

The following table provides the complete inventory of all routes registered in the FastAPI application:

| # | HTTP Method | Path | Source File | Handler Function | Description & Query Parameters |
|:---:|:---:|---|---|---|---|
| 1 | `GET` | `/` | `backend/main.py` | `root()` | Root service metadata and documentation pointer |
| 2 | `GET` | `/health` | `backend/main.py` | `health()` | Liveness/health check probe returning `{"status": "ok"}` |
| 3 | `GET` | `/api/forecast/overview` | `backend/routes/forecasts.py` | `overview()` | Latest forecast run metadata, 7-day demand total, product counts |
| 4 | `GET` | `/api/forecast/products` | `backend/routes/forecasts.py` | `list_forecast_products()` | List of all 3,046 products that have forecasts in specified run |
| 5 | `GET` | `/api/forecast/products/{product_id}` | `backend/routes/forecasts.py` | `product_forecast()` | 7-day forecast, 60-day historical sales, current stock for product |
| 6 | `GET` | `/api/forecast/top` | `backend/routes/forecasts.py` | `top_products()` | Top N products sorted by total forecasted demand (`n=1..100`) |
| 7 | `GET` | `/api/forecast/table` | `backend/routes/forecasts.py` | `forecast_table()` | Paginated forecast rows (`limit`, `offset`, `product_id`, `run_id`) |
| 8 | `GET` | `/api/forecast/runs` | `backend/routes/forecasts.py` | `list_runs()` | Historical forecast run tracking records (`limit=1..100`) |
| 9 | `GET` | `/api/forecast/runs/{run_id}` | `backend/routes/forecasts.py` | `get_run()` | Detailed status, start time, end time, rows written for run |
| 10 | `GET` | `/api/forecast/metrics` | `backend/routes/forecasts.py` | `evaluation_metrics()` | Saved validation and test split evaluation metrics from CSVs |
| 11 | `POST` | `/api/forecast/trigger` | `backend/routes/forecasts.py` | `trigger_forecast()` | Manual trigger for forecast pipeline via background task |

---

## 3. Reconciliation of Audit Smoke Tests vs. Total Endpoints

### 3.1 Initial Smoke Test Script (`smoke_test.py`)
The initial verification script `smoke_test.py` was designed as a rapid health probe executing 6 test cases:
1. `GET /health` â€” Verified API connectivity.
2. `GET /api/forecast/overview` â€” Verified latest run metadata and aggregate demand.
3. `GET /api/forecast/top?n=5` â€” Verified top demand aggregation query.
4. `GET /api/forecast/table?limit=3` â€” Verified paginated table queries.
5. `GET /api/forecast/runs?limit=3` â€” Verified forecast run history query.
6. `GET /api/forecast/metrics` â€” Verified metrics reporting from filesystem.

**Result of Initial Smoke Test:** 6/6 passed.
**Omission in Initial Smoke Test:** Endpoints `/`, `/api/forecast/products`, `/api/forecast/products/{id}`, `/api/forecast/runs/{run_id}`, and `/api/forecast/trigger` were verified in ad-hoc terminal snippets rather than formalized in `smoke_test.py`.

### 3.2 Formalized 11-Endpoint Live Audit Test
During Phase 2 of this audit, an exhaustive automated HTTP verification script was executed against all 11 endpoints with the Uvicorn server running on `http://127.0.0.1:8000`:

```powershell
.venv\Scripts\python -c "
import urllib.request, json
# [Exhaustive test suite for all 11 endpoints]
"
```

#### Live Test Results Table:

| # | Endpoint | Method | Test Input / Parameters | HTTP Status | Response Verification | Result |
|:---:|---|:---:|---|:---:|---|:---:|
| 1 | `/` | GET | None | `200 OK` | Returned application title and docs URL | **PASS** |
| 2 | `/health` | GET | None | `200 OK` | Returned `{"status": "ok"}` | **PASS** |
| 3 | `/api/forecast/overview` | GET | None | `200 OK` | Returned run ID `3f2cb884...`, 3046 products | **PASS** |
| 4 | `/api/forecast/products` | GET | None | `200 OK` | Returned array of 3,046 products with category | **PASS** |
| 5 | `/api/forecast/products/PROD00001` | GET | `product_id="PROD00001"` | `200 OK` | Returned 7 forecast rows, 60 history rows, stock=190 | **PASS** |
| 6 | `/api/forecast/top?n=5` | GET | `n=5` | `200 OK` | Returned top 5 products (PROD02955 top with 239.5u) | **PASS** |
| 7 | `/api/forecast/table?limit=5` | GET | `limit=5` | `200 OK` | Returned total=21,322, 5 items | **PASS** |
| 8 | `/api/forecast/runs?limit=5` | GET | `limit=5` | `200 OK` | Returned 1 completed run record | **PASS** |
| 9 | `/api/forecast/runs/3f2cb884...` | GET | `run_id="3f2cb884..."` | `200 OK` | Returned run details with rows_written=21,322 | **PASS** |
| 10 | `/api/forecast/metrics` | GET | None | `200 OK` | Returned validation and test evaluation metrics | **PASS** |
| 11 | `/api/forecast/trigger` | POST | `{"reason": "Audit"}` | `200 OK` | Returned `{"status": "accepted"}` | **PASS** |

**Summary:** **11/11 endpoints tested and verified passing (100% endpoint coverage).**

---

## 4. Error Handling & Edge Cases Verified

1. **Non-Existent Product:**
   - Request: `GET /api/forecast/products/INVALID_ID`
   - Response: `404 Not Found` with detail `"Product INVALID_ID not found or no forecasts."`
2. **Non-Existent Run:**
   - Request: `GET /api/forecast/runs/00000000-0000-0000-0000-000000000000`
   - Response: `404 Not Found` with detail `"Run not found."`
3. **Invalid Top N Limit:**
   - Request: `GET /api/forecast/top?n=500`
   - Response: `422 Unprocessable Entity` (FastAPI/Pydantic validation: `le=100`).
4. **Advisory Lock Concurrency on Trigger:**
   - When `/api/forecast/trigger` is invoked while a forecast run is already active, the PostgreSQL advisory lock rejects concurrent execution cleanly without throwing uncaught 500 server errors.

---

## 5. Audit Conclusion

The discrepancy between the reported 9 routes and 6 smoke tests is fully resolved:
- **Architecture has 11 registered endpoints.**
- **Initial smoke test covered a 6-endpoint critical subset.**
- **Research audit verified all 11 endpoints with 100% success.**
- The documentation in the technical audit and research validation report has been aligned with this empirical evidence.
