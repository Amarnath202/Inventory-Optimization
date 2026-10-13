"""
backend/services/db.py
----------------------
Database connection utilities for the FastAPI backend.
"""

import sys
from pathlib import Path
from typing import Generator

import psycopg2
import psycopg2.extras

_PROJ_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_PROJ_ROOT))
_M2_ROOT = _PROJ_ROOT / "module2_demand_forecasting"
if _M2_ROOT.exists():
    sys.path.insert(0, str(_M2_ROOT))
from config.settings import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD


def get_db_conn():
    """Return a raw psycopg2 connection with RealDictCursor."""
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def get_db() -> Generator:
    """FastAPI dependency: yields a DB connection, closes after request."""
    conn = get_db_conn()
    try:
        yield conn
    finally:
        conn.close()
