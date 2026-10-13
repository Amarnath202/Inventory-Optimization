"""
settings.py – centralised configuration for Module 1.
Reads .env from the FinalProject root directory.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# ── Resolve paths ────────────────────────────────────────────────────────────
# FinalProject/module1_data_management/config/settings.py
CONFIG_DIR    = Path(__file__).parent
MODULE_DIR    = CONFIG_DIR.parent
PROJECT_ROOT  = MODULE_DIR.parent

# Load .env from project root
load_dotenv(PROJECT_ROOT / ".env", override=False)

# ── Database ─────────────────────────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "5432"))
DB_NAME     = os.getenv("DB_NAME", "inventory_optimization_db2")
DB_USER     = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# ── Source files ─────────────────────────────────────────────────────────────
RAW_DIR     = PROJECT_ROOT / "Dataset"
PROC_DIR    = MODULE_DIR / "data" / "processed"
REPORTS_DIR = MODULE_DIR / "reports"
SQL_DIR     = MODULE_DIR / "sql"

SUPPLIER_CSV  = RAW_DIR / "supplier_THRESHOLD_FINAL.csv"
PRODUCT_CSV   = RAW_DIR / "product_THRESHOLD_FINAL.csv"
INVENTORY_CSV = RAW_DIR / "inventory_THRESHOLD_FINAL.csv"
SALES_CSV     = RAW_DIR / "sales_THRESHOLD_FINAL.csv"

# ── Pipeline constants ────────────────────────────────────────────────────────
CHUNK_SIZE = 200_000       # rows per chunk for large CSV reads
SCHEMA_NAME = "public"

# ── Ensure output dirs exist ─────────────────────────────────────────────────
PROC_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
