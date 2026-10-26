# Backend Handover

## Status

This is a handover for the current working tree, not a frozen release. The 2026-10-26 audit passed all four regression suites and basic API startup/GET checks, but the shared database lacks the Module 5 assessment table and no live database-backed orchestration POST was run. See [the audit report](../reports/final_backend_audit_2026-10-26.md) before frontend integration.

## Environment and startup

- Windows, Python 3.11.9, root virtual environment at `.venv`.
- Install/restore only from the reviewed root `requirements.txt`; do not add packages during an audit.
- Provide PostgreSQL connection settings through the project's `.env` configuration. Do not commit `.env` or credentials.
- From the repository root, start the API:

  ```powershell
  .\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000
  ```

- Interactive API documentation: `http://127.0.0.1:8000/docs`
- Basic health check: `GET /health`
- Orchestration capability and limitation summary: `GET /api/orchestration/status`
- Module 2 forecasting routes are served by the shared API. The separate Module 2 scheduler is an independent process and should not be started as part of a read-only verification.

## Regression commands

Run at the repository root with the project `.venv`. Set `PYTHONPATH` to the repository root and `module2_demand_forecasting`; set `PYTHONUTF8=1` for Module 1 on Windows.

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = "$PWD;$PWD\module2_demand_forecasting;$env:PYTHONPATH"
.\.venv\Scripts\python.exe -m pytest module1_data_management\tests -q --tb=short
.\.venv\Scripts\python.exe -m pytest module2_demand_forecasting\tests -q --tb=short -c module2_demand_forecasting\pytest.ini
.\.venv\Scripts\python.exe -m pytest module3_inventory_optimization\tests -q --tb=short
.\.venv\Scripts\python.exe -m pytest module6_explainability\tests -q --tb=short -c module6_explainability\pytest.ini
```

The last verified results were 17, 26, 197, and 82 passed, respectively. Module 1 checks the configured PostgreSQL database; Module 3–6 API/service tests primarily use mocks. Do not assume a passing mocked suite verifies a live database-backed POST.

## API overview

- `/api/forecast/*` — forecast overview, products, runs, metrics, tables, and trigger.
- `/api/inventory-optimization/*` — assessments, products, overview, and recommendations.
- `/api/orchestration/status`, `/api/orchestration/run`, `/api/orchestration/run/{run_id}` — native Python orchestration and process-local result retrieval.
- `/api/procurement/*` — supplier alternatives and procurement recommendations.
- `/api/excess-stock/*` — excess-stock assessments and summary.
- `/api/explainability/*` — model/service health and forecast, inventory, procurement, and excess-stock explanations.
- `POST /api/orchestration/run` can persist assessments and downstream recommendations and may apply schemas. It is not a read-only health check; use a separate test database for integration testing.

Module 3 determines the routing decision. Native orchestration invokes at most one downstream agent. Manager approval is required; the system does not place purchase orders or update inventory quantities. These safety claims still need confirmation on a disposable integration database before release.

## Data assumptions and known limitations

- PostgreSQL database name defaults to `inventory_optimization_db2`; use the configured `.env` values and never print credentials.
- Sales in the database comprise 2,439,949 sale headers and 4,290,604 sale items in the last audit. The processed `module1_data_management/data/processed/sale.csv` is only a fixture row; never use it for a complete reload.
- Forecast data is historical. The last audited forecast targets 2026-08-31 through 2026-09-06; refresh it before operational recommendations. Do not refresh as part of a read-only audit.
- Stock comes from the latest inventory batch snapshot (2026-08-01); purchase orders and backorders are not tracked.
- Supplier lead times are synthetic/modelled. Alternative suppliers can lack purchase rates and minimum-order quantities.
- The shared database did not have `excess_stock_assessment` or optional `explanation_record` tables at audit time. Do not apply schemas on the shared database as a verification shortcut.
- XGBoost stockout prediction is deferred because reliable stockout labels are unavailable.
- Orchestration history is in-memory and lost at server restart.

## Release gate

Before frontend integration, use a disposable test database to provision and verify the required Module 3–6 schemas, run live API route/exception tests with controlled fixtures, and confirm by before/after snapshots that there are no purchase-order or stock-quantity mutations. Re-run all regression commands and review the full dirty working tree. The current commit is `f82ab8cb470d857306c1a6442c6832166315858d`; pre-existing uncommitted and untracked files mean it is not, by itself, the audited candidate.

