"""
config/settings.py — Centralised configuration for Module 2.
Reads .env from the FinalProject root (shared with Module 1).
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# ── Resolve paths ─────────────────────────────────────────────────────────────
CONFIG_DIR   = Path(__file__).parent
MODULE_DIR   = CONFIG_DIR.parent
PROJECT_ROOT = MODULE_DIR.parent

# Load .env from project root (shared with Module 1)
load_dotenv(PROJECT_ROOT / ".env", override=False)

# ── Database ──────────────────────────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "5432"))
DB_NAME     = os.getenv("DB_NAME", "inventory_optimization_db2")
DB_USER     = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# ── API ───────────────────────────────────────────────────────────────────────
API_HOST = os.getenv("M2_API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("M2_API_PORT", "8000"))

# ── Scheduler ─────────────────────────────────────────────────────────────────
SCHEDULER_TIMEZONE              = os.getenv("M2_SCHEDULER_TIMEZONE", "Asia/Kolkata")
SCHEDULER_DAY_OF_WEEK          = os.getenv("M2_SCHEDULER_DAY_OF_WEEK", "sun")
SCHEDULER_HOUR                 = int(os.getenv("M2_SCHEDULER_HOUR", "2"))
SCHEDULER_MINUTE               = int(os.getenv("M2_SCHEDULER_MINUTE", "0"))
ADVISORY_LOCK_KEY              = int(os.getenv("M2_ADVISORY_LOCK_KEY", "20261001"))
INTERRUPTED_RUN_THRESHOLD_HOURS = int(os.getenv("M2_INTERRUPTED_RUN_THRESHOLD_HOURS", "6"))

# ── Forecasting ───────────────────────────────────────────────────────────────
MIN_HISTORY_DAYS  = int(os.getenv("M2_MIN_HISTORY_DAYS", "28"))
FORECAST_HORIZON  = int(os.getenv("M2_FORECAST_HORIZON", "7"))

# ── Model ─────────────────────────────────────────────────────────────────────
# Chronological split boundaries (fixed for this dataset: 2025-01-01 -> 2026-08-30)
TRAIN_END_DATE = "2026-05-31"
VAL_END_DATE   = "2026-07-15"
TEST_END_DATE  = "2026-08-30"

# ── Paths ─────────────────────────────────────────────────────────────────────
MODELS_DIR  = MODULE_DIR / "models"
REPORTS_DIR = MODULE_DIR / "reports"
LOGS_DIR    = MODULE_DIR / "logs"

# Ensure directories exist
for d in [MODELS_DIR, REPORTS_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)
