import sys
from pathlib import Path

import psycopg2
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from module1_data_management.config.settings import (
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
)


@pytest.fixture
def conn():
    """Create a real Postgres connection for Module 1 database tests."""
    connection = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    connection.autocommit = False
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture
def cur(conn):
    """Provide a database cursor for Module 1 SQL assertions."""
    cursor = conn.cursor()
    try:
        yield cursor
    finally:
        conn.rollback()
        cursor.close()
