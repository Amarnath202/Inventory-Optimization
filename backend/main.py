"""
backend/main.py
---------------
FastAPI application entry-point.
Now shared across all modules — lives in FinalProject/backend/.

Runs on port 8000 (default). CORS is open during development.
Start with (from FinalProject/ root, with root .venv active):
    .venv\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000
"""

import sys
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# FinalProject root  (so 'backend.*' is importable)
_PROJ_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJ_ROOT))
# module2_demand_forecasting root  (so 'config.settings' is importable)
_M2_ROOT = _PROJ_ROOT / "module2_demand_forecasting"
if _M2_ROOT.exists():
    sys.path.insert(0, str(_M2_ROOT))
from backend.routes.forecasts import router as forecast_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

app = FastAPI(
    title="Inventory Optimization API",
    description=(
        "Inventory Optimization and Excess Stock Management System — "
        "Shared backend for all modules"
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(forecast_router)


@app.get("/")
def root():
    return {
        "service": "Inventory Optimization & Excess Stock Management — Backend API",
        "docs": "/docs",
        "status": "running",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    from config.settings import API_HOST, API_PORT
    uvicorn.run("backend.main:app", host=API_HOST, port=API_PORT, reload=True)
