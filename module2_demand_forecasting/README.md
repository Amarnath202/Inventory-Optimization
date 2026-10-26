# Module 2 — Demand Forecasting & Forecast Analytics

Part of: **Inventory Optimization and Excess Stock Management System for Retail Stores**

---

## Overview

This module delivers:
- **Global LightGBM demand forecasting** across 3,046 eligible products.
- **7-calendar-day moving-average baseline** (calendar-day corrected, including zero-demand days).
- **Chronological evaluation** (Train / Val / Test) with MAE, RMSE, WAPE, sMAPE.
- **Recursive 7-day production forecasts** stored in PostgreSQL.
- **FastAPI backend** (port 8000) serving forecast data and run management.
- **React + Vite + Plotly dashboard** (port 5173).
- **Weekly APScheduler** with PostgreSQL advisory locking.

---

## Folder Structure

```
module2_demand_forecasting/
├── config/
│   └── settings.py          # Centralised config (reads root .env)
├── src/
│   ├── data_loader.py       # DB load + freshness verification
│   ├── preprocessing.py     # Gap-fill + split assignment
│   ├── feature_engineering.py  # Leakage-safe lag/rolling features
│   ├── train.py             # LightGBM training + evaluation (run manually)
│   ├── evaluate.py          # Metrics: MAE, RMSE, WAPE, sMAPE + MA7 baseline
│   ├── forecast.py          # Recursive 7-day forecast generation + DB persistence
│   ├── pipeline.py          # Pipeline coordinator + advisory lock
│   └── scheduler.py        # Dedicated weekly scheduler process
├── backend/
│   ├── main.py              # FastAPI app
│   ├── routes/forecasts.py  # API endpoints
│   └── services/
│       ├── db.py            # DB dependency
│       └── forecast_service.py  # Business logic
├── frontend/                # React + Vite + Plotly dashboard
├── sql/schema_m2.sql        # DDL for demand_forecast + forecast_run
├── models/                  # Trained model artifacts (git-ignored)
├── reports/                 # Evaluation metrics and reports
├── logs/                    # Pipeline + scheduler logs
└── tests/                   # pytest unit tests
```

---

## Quick Start

The repository-root [`README.md`](../README.md) is the authoritative setup guide
for prerequisites, the single root `.venv`, root `requirements.txt`, shared root
`.env`, database setup, and application startup. Run these Module 2 commands
from the repository root after completing Module 1 and configuring PostgreSQL:

```powershell
.\.venv\Scripts\python.exe module2_demand_forecasting\src\pipeline.py --create-schema
.\.venv\Scripts\python.exe module2_demand_forecasting\src\pipeline.py --train
.\.venv\Scripts\python.exe module2_demand_forecasting\src\pipeline.py --forecast
```

Training is a manual operation; it is not scheduled. Start the optional weekly
scheduler in a separate terminal with:

```powershell
.\.venv\Scripts\python.exe module2_demand_forecasting\src\scheduler.py
```

---

## Data Policies

### Gap-fill boundary
Calendar gap-fill runs from each product's `first_sale` to `last_sale` date only.
Missing days within this range = 0 demand. No extension beyond `last_sale`.

### Evaluation exclusion (Case C)
Products whose `last_sale` precedes the start of an evaluation window are **excluded from that window's evaluation** — they have no evaluation rows, not zero rows. They still receive production forecasts.

### WAPE
WAPE is reported as `None` / "N/A" (not zero) when total actual demand = 0 in an evaluation window. Aggregate WAPE excludes zero-actual products from the denominator.

### Data freshness
Before every forecast run, the pipeline cross-checks `MAX(daily_demand.date)` against `MAX(sale.sale_date)`. If they disagree, a `DataFreshnessError` is raised and the run aborts. **Dataset is static through 2026-08-30** — the source CSVs represent the complete ingested history.

---

## Scheduler Recovery

If the scheduler was offline and a scheduled run was missed:
1. The `forecast_run` table will show no `completed` row for that period.
2. On restart, any `running` rows older than 6 hours are marked `interrupted`.
3. To generate forecasts manually: `POST /api/forecast/trigger` or `python src/scheduler.py --run-now`.
4. **Automatic catch-up is NOT enabled.** Manual review before triggering is recommended.
5. If the model may be stale (trained many weeks ago), retrain first: `python src/train.py`.

**Local deployment note:** The scheduler process requires the computer to remain running.
Scheduled forecasts will not execute while the machine is off or in sleep mode.
For reliable weekly scheduling, deploy on an always-on host (cloud VM, dedicated server).

---

## Verified Data Counts

| Metric | Value |
|---|---|
| daily_demand rows | 1,045,477 |
| Products eligible | 3,046 |
| Calendar rows after gap-fill | 1,838,702 |
| Zero-demand rows added | 793,225 |
| Burn-in rows (binding: rolling_28 shifted 1) | 29 |
| Train rows (usable after burn-in) | confirmed at runtime |
| Val rows | 137,061 (3,046 products) |
| Test rows | 134,778 (3,044 products) |
| Products excluded from test eval (Case C) | 2 |
| Products with undefined val WAPE | 3 |
| Expected forecast rows per run | 21,322 (3,046 × 7) |

---

## Module 1 Constraints

- **Module 1 is frozen.** No table, source file, or data is modified by Module 2.
- `demand_forecast` and `forecast_run` are the only new tables created.
- Foreign keys reference Module 1's `product` table with `ON DELETE RESTRICT`.

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/forecast/overview` | Dashboard stats for latest run |
| GET | `/api/forecast/products` | List all products with forecasts |
| GET | `/api/forecast/products/{id}` | Product forecast + history + stock |
| GET | `/api/forecast/top?n=20` | Top N by 7-day demand |
| GET | `/api/forecast/table` | Paginated forecast rows |
| GET | `/api/forecast/runs` | Recent forecast runs |
| GET | `/api/forecast/runs/{id}` | Run details |
| POST | `/api/forecast/trigger` | Manual forecast trigger |
| GET | `/api/forecast/metrics` | Saved evaluation metrics |
| GET | `/docs` | OpenAPI interactive docs |
