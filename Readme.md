# Inventory Optimization & Excess Stock Management System

A full-stack, multi-module final year project for intelligent inventory management using demand forecasting and excess stock analytics.

---

## Project Structure

```
FinalProject/
├── .env                          # Shared environment variables (create from .env.example)
├── .gitignore
├── Readme.md
├── requirements.txt              # Single shared Python requirements for all modules
│
├── .venv/                        # Single shared virtual environment (not committed to git)
│
├── Dataset/                      # Raw CSV source data (not committed to git)
│
├── backend/                      # Shared FastAPI backend (all modules)
│   ├── main.py
│   ├── routes/
│   │   └── forecasts.py
│   └── services/
│       ├── db.py
│       └── forecast_service.py
│
├── frontend/                     # Shared React + Vite frontend (all modules)
│   ├── src/
│   │   ├── api.js
│   │   ├── App.jsx
│   │   ├── pages/
│   │   └── components/
│   └── package.json
│
├── module1_data_management/      # Module 1 — Data ingestion & DB schema
│   ├── config/settings.py
│   ├── src/
│   └── sql/
│
└── module2_demand_forecasting/   # Module 2 — Demand Forecasting & Analytics
    ├── config/settings.py
    ├── src/
    ├── models/                   # Trained model binaries (not committed to git)
    └── reports/
```

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | ≥ 3.10 | For backend & ML modules |
| Node.js | ≥ 18 | For frontend |
| npm | ≥ 9 | Bundled with Node.js |
| PostgreSQL | ≥ 14 | Must be running locally |
| Git | any | For cloning |

---

## Installation

### 1. Clone the repository

```bash
git clone <your-repo-url>
cd FinalProject
```

---

### 2. Create the environment file

```powershell
# Windows (PowerShell)
Copy-Item module2_demand_forecasting\.env.example .env
```
```bash
# macOS / Linux
cp module2_demand_forecasting/.env.example .env
```

Open `.env` and fill in your database credentials:

```dotenv
DB_HOST=localhost
DB_PORT=5432
DB_NAME=inventory_optimization_db2
DB_USER=postgres
DB_PASSWORD=your_postgres_password
```

---

### 3. Create & activate the virtual environment

> ⚠️ There is **one shared `.venv`** at the project root — used by all modules and the backend.

```powershell
# Windows (PowerShell) — run from FinalProject\ root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

---

### 4. Install Python dependencies

```powershell
pip install -r requirements.txt
```

---

### 5. Set up the database (Module 1)

> Skip if your PostgreSQL database is already populated.

```powershell
# With venv active, from FinalProject\ root:
python -m module1_data_management.src.build_schema
python -m module1_data_management.src.load_database
```

---

### 6. Train the forecasting model (Module 2)

> Skip if pre-trained model files already exist in `module2_demand_forecasting/models/`.

```powershell
# With venv active, from FinalProject\ root:
python -m module2_demand_forecasting.src.train
```

---

### 7. Install frontend dependencies

```powershell
cd frontend
npm install
cd ..
```

---

## Running the Application

Open **two terminal windows**, both from the `FinalProject\` root.

### Terminal 1 — Backend (FastAPI)

> ⚠️ **Must be run from the `FinalProject\` root directory.**

```powershell
# Windows — activate venv first, then:
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --port 8000
```
```bash
# macOS / Linux
source .venv/bin/activate
python -m uvicorn backend.main:app --reload --port 8000
```

| URL | Description |
|-----|-------------|
| http://localhost:8000 | API root |
| http://localhost:8000/docs | Swagger UI (interactive) |
| http://localhost:8000/redoc | ReDoc |

---

### Terminal 2 — Frontend (React + Vite)

```powershell
cd frontend
npm run dev
```

Frontend: **http://localhost:5173**

---

## API Endpoints (Module 2)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Service info |
| GET | `/health` | Health check |
| GET | `/api/forecast/overview` | Dashboard summary (latest run) |
| GET | `/api/forecast/products` | All products with forecasts |
| GET | `/api/forecast/products/{id}` | 7-day forecast + 60-day history for one product |
| GET | `/api/forecast/top?n=20` | Top N products by forecasted demand |
| GET | `/api/forecast/table` | Paginated forecast table |
| GET | `/api/forecast/runs` | Forecast run history |
| GET | `/api/forecast/runs/{run_id}` | Single run details |
| POST | `/api/forecast/trigger` | Manually trigger a new forecast run |
| GET | `/api/forecast/metrics` | Model evaluation metrics (val + test) |

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `localhost` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `inventory_optimization_db2` | Database name |
| `DB_USER` | `postgres` | DB username |
| `DB_PASSWORD` | — | DB password **(required)** |
| `M2_API_HOST` | `0.0.0.0` | Backend bind host |
| `M2_API_PORT` | `8000` | Backend port |
| `M2_FORECAST_HORIZON` | `7` | Days to forecast ahead |
| `M2_MIN_HISTORY_DAYS` | `28` | Min history needed per product |
| `M2_SCHEDULER_TIMEZONE` | `Asia/Kolkata` | Timezone for auto-scheduler |

---

## Troubleshooting

### `CouldNotAutoLoadModule` on Windows PowerShell
Happens when running with a relative path like `"module2_demand_forecasting\.venv\..."`. Always use the root venv: `.\.venv\Scripts\python.exe`.

### `ModuleNotFoundError: No module named 'config'`
The backend must be started from the `FinalProject\` root so Python can resolve `module2_demand_forecasting/config/settings.py`.

### Database connection refused
- Confirm PostgreSQL service is running.
- Verify credentials in `.env` match your local setup.
- Confirm the database `inventory_optimization_db2` exists.

### Frontend CORS errors
- Confirm the backend is running on port `8000`.
- The backend allows `http://localhost:5173` and `http://localhost:3000` by default.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19, Vite 8, Plotly.js, Axios |
| Backend | FastAPI, Uvicorn, Pydantic v2 |
| Database | PostgreSQL 14+ |
| ML / Forecasting | LightGBM, scikit-learn, pandas, numpy |
| Scheduling | APScheduler |
| DB Driver | psycopg2-binary |
