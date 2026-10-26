# Module 3 — Inventory Optimization

## Overview

Module 3 monitors inventory and determines the required action for each product.
It is the routing layer between Module 1 (Data Management) and the downstream
action modules (Module 4 — Procurement Agent, Module 5 — Excess Stock Agent).

**Module 3 produces routing decisions only. It does not invoke Module 4 or Module 5.**
The native Python orchestration service reads the Module 3 decision and routes to at
most one downstream agent.

---

## Responsibilities

| Responsibility | Implemented |
|---|---|
| Retrieve current stock from `inventory_batch` (latest batch) | ✅ |
| Retrieve 7-day LightGBM forecast from `demand_forecast` | ✅ |
| Compute 90-day gap-filled demand statistics | ✅ |
| Calculate safety stock (SS = z × σ_d × √L) | ✅ |
| Calculate reorder point (ROP = avg_daily × L + SS) | ✅ |
| Calculate suggested replenishment quantity (order-up-to policy) | ✅ |
| Produce routing decision: `procurement_required` / `excess_stock_review` / `normal` / `insufficient_data` | ✅ |
| Persist results to `inventory_assessment` table (idempotent upsert) | ✅ |
| Expose FastAPI endpoints | ✅ |
| XGBoost stockout prediction | ❌ Pending (see below) |

---

## Stock Calculation — Verified Semantics

`inventory_batch.current_quantity` = unsold remainder from **that specific batch only**.
Each product has ~20 monthly batches. **The sum across all batches is NOT used** —
it is a cumulative dataset artifact, not physical on-hand stock.

**Correct calculation**: `DISTINCT ON (product_id) ... ORDER BY received_date DESC`
→ `current_quantity` from the most recent batch.

---

## XGBoost Stockout Prediction — Prerequisite Not Met

**Status: Deferred. Not implemented.**

The `daily_demand` table records only dates with actual sales. Missing calendar
dates within a product's observed range represent zero-sale days. However, since
no inventory-level snapshots exist in this dataset, it is impossible to
distinguish:

- Zero sales because stock was genuinely depleted (**stockout**)
- Zero sales because no customer placed an order that day (**zero demand**)

Without reliable binary stockout labels, an XGBoost model cannot be trained or
evaluated honestly. Claiming predictive accuracy without valid labels would
misrepresent the system's capabilities.

**Prerequisite to unlock XGBoost**: A reliable mechanism to label stockout days,
such as inventory-level snapshots, manual stockout records, or a validated
rule that zero-sales days following confirmed low-stock states can be treated
as stockouts.

The deterministic ROP/reorder-level routing rules serve as the operational
risk classifier until valid labels are available.

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/inventory-optimization/overview` | Summary counts by routing decision |
| `GET` | `/api/inventory-optimization/products` | Paginated assessments |
| `GET` | `/api/inventory-optimization/products/{product_id}` | Single product assessment |
| `POST` | `/api/inventory-optimization/assess` | Run/refresh assessment |
| `GET` | `/api/inventory-optimization/recommendations` | Procurement + excess review items |

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `M3_SERVICE_LEVEL_Z` | `1.645` | Safety stock z-factor (95% service level) |
| `M3_EXCESS_STOCK_RATIO` | `1.5` | Threshold multiplier on `maximum_stock_level` |
| `M3_DEMAND_HISTORY_DAYS` | `90` | Demand statistics lookback window (days) |
| `M3_MIN_HISTORY_DAYS` | `7` | Minimum nonzero sale days for σ estimation |

---

## Running Tests

```bash
# From FinalProject root, with .venv active:
.venv\Scripts\python.exe -m pytest module3_inventory_optimization/tests/ -v
```

Expected: **197 tests pass**.

---

## Files

```
module3_inventory_optimization/
├── __init__.py
├── README.md                            ← this file
├── sql/
│   └── schema_m3.sql                    ← inventory_assessment DDL
└── tests/
    ├── conftest.py
    ├── test_safety_stock.py  (15 tests)
    ├── test_reorder_point.py (14 tests)
    ├── test_replenishment.py (14 tests)
    ├── test_routing.py       (14 tests)
    ├── test_api.py           (20 tests)
    ├── test_procurement_agent.py
    ├── test_excess_stock.py
    └── orchestration/
        └── test_native_orchestration.py

backend/
├── agents/
│   ├── orchestration.py                  ← native Module 3–6 orchestration
│   └── orchestration_state.py            ← shared typed run state
├── routes/inventory_optimization.py     ← FastAPI router
└── services/
    ├── inventory_service.py             ← DB data access layer
    ├── safety_stock_service.py          ← pure calculation
    ├── reorder_point_service.py         ← pure calculation
    ├── replenishment_service.py         ← pure calculation
    └── assessment_service.py            ← orchestration
```
