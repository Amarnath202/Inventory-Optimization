from pathlib import Path
import sys
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.forecast_service import get_product_forecast


def test_product_forecast_uses_latest_batch_stock():
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "product_id": "PROD00001",
        "product_name": "Example product",
        "brand": "Example",
        "category": "Example",
        "mrp": 10.0,
    }
    cursor.fetchall.side_effect = [
        [{"forecast_date": "2026-08-31", "forecasted_units": 1.0}],
        [],
    ]
    connection = MagicMock()
    connection.cursor.return_value = cursor

    with patch(
        "backend.services.forecast_service.get_latest_batch_stock",
        return_value={"available_stock": 6},
    ) as get_stock:
        result = get_product_forecast(connection, "PROD00001", "run-id")

    assert result["current_stock"] == 6
    get_stock.assert_called_once_with(connection, "PROD00001")
