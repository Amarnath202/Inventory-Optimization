# Inventory Optimization Frontend

The React/Vite application provides a single responsive workspace for the backend capabilities that are exposed today. It preserves the forecast dashboard and adds inventory assessment, excess-stock, supplier alternatives, orchestration, and explanation views. It remains a decision-support interface: it never submits purchase orders or changes stock quantities.

## Run locally

From the repository root in PowerShell, start the FastAPI service using the root `.venv` and configured PostgreSQL environment:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000
```

In another terminal:

```powershell
$env:VITE_API_BASE_URL = "http://127.0.0.1:8000"
npm --prefix frontend run dev
```

Vite normally serves the frontend at `http://localhost:5173`; FastAPI's Swagger UI is at `http://127.0.0.1:8000/docs`. `VITE_API_BASE_URL` is optional and defaults to `http://localhost:8000`. Set it in the shell before starting Vite; do not put credentials in the frontend environment.

## Pages

| Page | Available behavior |
|---|---|
| Overview | Latest saved forecast, forecasted demand, top products, and run metadata. A manual forecast request is queued through the API. |
| Products & forecasts | Forecast-eligible product list, search/category filter/sort/pagination, per-product historical demand, forecast horizon, latest stock snapshot, and forecast explanation link. |
| Forecast runs | Recent runs, text filter, refresh, and explicit forecast request. |
| Model evaluation | Separate validation/test tables and MAE, RMSE, WAPE, and sMAPE views from the metrics endpoint. |
| Inventory optimization | Assessment summary, routing filter, paginated assessments, product details, backend calculation fields, and explicit single/full assessments. |
| Excess stock | Assessment summary, ABC/XYZ and category filters, paginated results, detail/explanation, and explicit per-product assessment. |
| Supplier review | Product supplier alternatives, stored recommendations, lead-time ranking request, and recommendation explanation. TOPSIS and supplier-performance scoring are not implemented by the backend. |
| Decision workflow | Capability/limitation status and an explicit, confirmed single-product Module 3 → conditional Module 4/5 orchestration request. |
| Explainability | Model/artifact availability plus forecast, inventory, procurement, and excess-stock explanation endpoints. |

## API coverage and contracts

The response fields below are sourced from the registered FastAPI routes, response models, service queries, and checked-in schemas. A working UI request is not evidence that the connected database contains the necessary data or schema.

| Module / page | Endpoint(s) | Request / response fields used | Integration status and limits |
|---|---|---|---|
| 1 — Product and demand views | `GET /api/forecast/products`; `GET /api/forecast/products/{product_id}` | Product list: `product_id`, `product_name`, `brand`, `category`. Detail: `product`, `history[{date,units_sold}]`, `forecasts[{date,forecasted_units}]`, `current_stock`. | Read-only view over forecast-eligible products; history is returned demand data. `current_stock` is derived from the latest inventory batch; the backend currently reports zero when no batch is found. No Module 1 CRUD, raw sales ledger, inventory ledger, or master supplier listing endpoints are registered. |
| 1 / 4 — Supplier alternatives | `GET /api/procurement/suppliers/{product_id}` | `product_id`, `product_name`, `eligible_supplier_count`, `suppliers[{supplier_id,supplier_name,lead_time_days}]`, `ranking_method`, `note`. | Product-specific alternatives only; recorded lead times are modelled/synthetic. Not a supplier-performance endpoint. |
| 2 — Forecast dashboard | `GET /api/forecast/overview`; `GET /api/forecast/top?n=15` | Overview: run/model/date/horizon/product/demand fields. Top rows: `product_id`, `product_name`, `category`, `total_forecast`. | Connected to saved API data. Dates describe the returned run, not necessarily fresh operational data. |
| 2 — Product forecast | `GET /api/forecast/products`; `GET /api/forecast/products/{product_id}` | Forecast-eligible product list; product metadata, up to 60 historical observations and the run's daily forecast values. | Connected. No product create/update/delete endpoints exist. |
| 2 — Runs and execution | `GET /api/forecast/runs?limit=100`; `POST /api/forecast/trigger` | Run fields include UUID, model/status, origin/target dates, timestamps, rows, trigger, and error detail. Trigger sends `{reason}` and returns an accepted status/message. | Connected. Trigger queues forecasting work; it does not retrain the model. |
| 2 — Evaluation | `GET /api/forecast/metrics` | `val` and `test` arrays with model, MAE, RMSE, WAPE, sMAPE, and row count when report files exist. | Connected. Missing split files yield `null`; no values are invented. |
| 3 — Inventory optimization | `GET /api/inventory-optimization/overview`; `GET /api/inventory-optimization/products`; `GET /api/inventory-optimization/products/{product_id}` | Overview counts: total, routing decisions, latest assessment/run. List: `{total,items,limit,offset}`; items include stock/demand inputs, safety stock, reorder point, suggested quantity, routing, reasons, timestamps, and product labels. Detail: `{product,assessment}`. | Connected to persisted assessments when the M3 schema/data exist. Uses backend calculations; stock is the latest-batch snapshot, not a live ledger. |
| 3 — Assessment action | `POST /api/inventory-optimization/assess` | Optional `{product_id,reason}`. Single-product results include status/message/counts/run; omitting ID queues a full assessment. | Connected behind confirmation. Writes/upserts assessments; full run is asynchronous. |
| 3 — Routed recommendations | `GET /api/inventory-optimization/recommendations` | `procurement_required` and `excess_stock_review`, each with `total` and `items`. | Read-only routing summary; downstream actions are separate. |
| 3–5 — Orchestration | `GET /api/orchestration/status`; `POST /api/orchestration/run`; `GET /api/orchestration/run/{run_id}` | Request `{product_id,trigger_reason}`. Result includes request ID, status, routing, assessment summary, procurement/excess results, errors/notices, approval flag, and message. | Connected behind confirmation. The POST writes decision records; run history is process-local and cleared on server restart. No live database-backed POST is claimed as verified. |
| 4 — Procurement | `GET /api/procurement/recommendations`; `GET /api/procurement/suppliers/{product_id}`; `GET /api/procurement/recommend/{product_id}` | Paged saved records; supplier alternatives; response fields include ranked suppliers, lead times, tie details, status, notes, recommendation ID, and manager approval. | Connected. The recommend route is HTTP GET but persists a recommendation, so the UI requires explicit confirmation. Ranking is lead-time-only with ID tie-break, not TOPSIS; price, quality, and reliability scoring are unavailable. |
| 5 — Excess stock | `GET /api/excess-stock/summary`; `GET /api/excess-stock/assessments`; `GET /api/excess-stock/assessments/{product_id}` | Summary counts for categories and ABC/XYZ; paged `{total,items,limit,offset}`; detail returns product and assessment including available stock, excess quantity estimate, revenue, variability, classifications, reasons, action, and warnings. | Connected when the optional Module 5 table exists. No monetary excess-stock valuation is exposed. |
| 5 — Assessment action | `POST /api/excess-stock/assess` | `{product_id,reason}`; response includes category/classes/action/confidence/assessment ID/approval and warnings. | Connected behind confirmation. Persists an assessment and may apply its schema; it does not modify stock. |
| 6 — Explainability | `GET /api/explainability/health`; `/models`; `/forecast/{product_id}`; `/inventory/{assessment_id}`; `/procurement/{recommendation_id}`; `/excess-stock/{assessment_id}` | Actual health/model metadata, TreeSHAP forecast response, and stored rule/statistical explanation results. | Connected. TreeSHAP is forecast-only and requires model artifacts/SHAP; M3–5 use backend rule/statistical explanations. No fabricated SHAP data. |

### Not exposed by the current backend

- Module 1 product, supplier, sales, and inventory create/update/delete or raw-ledger endpoints.
- A supplier performance dashboard, TOPSIS ranking, purchase-rate/MOQ comparison, or verified reliability/quality scores.
- XGBoost stockout prediction; the backend documents it as deferred because valid stockout labels are unavailable.
- A persisted orchestration-history endpoint; run lookup is process-local.
- Any automatic purchase-order submission or stock mutation operation.

## Validation

From the repository root:

```powershell
npm --prefix frontend run lint
npm --prefix frontend run build
npm --prefix frontend run test
```

Backend regression test commands and database prerequisites are documented in the root [README](../README.md). Live database-backed POST workflows should be exercised only against an authorized disposable test database.
