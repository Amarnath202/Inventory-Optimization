"""
backend/services/forecast_service.py
--------------------------------------
Business-logic layer for forecast queries.
"""

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def get_latest_run_id(conn) -> Optional[str]:
    cur = conn.cursor()
    cur.execute(
        """SELECT forecast_run_id FROM forecast_run
           WHERE status = 'completed'
           ORDER BY run_completed_at DESC LIMIT 1"""
    )
    row = cur.fetchone()
    cur.close()
    return str(row["forecast_run_id"]) if row else None


def get_overview(conn) -> dict:
    """Dashboard overview stats."""
    run_id = get_latest_run_id(conn)
    if not run_id:
        return {"error": "No completed forecast runs found."}

    cur = conn.cursor()
    cur.execute(
        """SELECT model_version, forecast_origin, target_date_from,
                  target_date_to, run_completed_at, rows_written
           FROM forecast_run WHERE forecast_run_id = %s""",
        (run_id,)
    )
    run = dict(cur.fetchone())

    cur.execute(
        "SELECT COUNT(DISTINCT product_id) FROM demand_forecast WHERE forecast_run_id = %s",
        (run_id,)
    )
    n_products = cur.fetchone()["count"]

    cur.execute(
        "SELECT SUM(forecasted_units) FROM demand_forecast WHERE forecast_run_id = %s",
        (run_id,)
    )
    total_demand = float(cur.fetchone()["sum"] or 0)
    cur.close()

    return {
        "forecast_run_id":  run_id,
        "model_version":    run["model_version"],
        "forecast_origin":  str(run["forecast_origin"]),
        "target_date_from": str(run["target_date_from"]),
        "target_date_to":   str(run["target_date_to"]),
        "run_completed_at": str(run["run_completed_at"]),
        "eligible_products": int(n_products),
        "total_forecasted_demand": round(total_demand, 2),
        "forecast_horizon_days": 7,
        "demand_aggregation_scope": f"All {n_products} products, 7-day horizon",
    }


def get_product_forecast(conn, product_id: str, run_id: str = None) -> dict:
    """7-day forecast + 60-day historical for one product."""
    if not run_id:
        run_id = get_latest_run_id(conn)
    if not run_id:
        return {}

    cur = conn.cursor()

    # Product metadata
    cur.execute(
        "SELECT product_id, product_name, brand, category, mrp FROM product WHERE product_id = %s",
        (product_id,)
    )
    prod = cur.fetchone()
    if not prod:
        return {}

    # Forecast rows
    cur.execute(
        """SELECT forecast_date, forecasted_units
           FROM demand_forecast
           WHERE forecast_run_id = %s AND product_id = %s
           ORDER BY forecast_date""",
        (run_id, product_id)
    )
    forecasts = [{"date": str(r["forecast_date"]), "forecasted_units": float(r["forecasted_units"])}
                 for r in cur.fetchall()]

    # Historical demand (last 60 days before forecast origin)
    if forecasts:
        origin = forecasts[0]["date"]
        cur.execute(
            """SELECT date, units_sold FROM daily_demand
               WHERE product_id = %s AND date < %s
               ORDER BY date DESC LIMIT 60""",
            (product_id, origin)
        )
        history = [{"date": str(r["date"]), "units_sold": int(r["units_sold"])}
                   for r in cur.fetchall()]
        history.reverse()
    else:
        history = []

    # Current stock
    cur.execute(
        "SELECT SUM(current_quantity) AS stock FROM inventory_batch WHERE product_id = %s",
        (product_id,)
    )
    stock_row = cur.fetchone()
    current_stock = int(stock_row["stock"] or 0)
    cur.close()

    return {
        "product": dict(prod),
        "current_stock": current_stock,
        "forecast_run_id": run_id,
        "forecasts": forecasts,
        "history": history,
    }


def get_top_demand(conn, n: int = 20, run_id: str = None) -> list:
    """Top N products by total 7-day forecasted demand."""
    if not run_id:
        run_id = get_latest_run_id(conn)
    if not run_id:
        return []

    cur = conn.cursor()
    cur.execute(
        """SELECT df.product_id, p.product_name, p.category,
                  SUM(df.forecasted_units) AS total_forecast
           FROM demand_forecast df
           JOIN product p ON df.product_id = p.product_id
           WHERE df.forecast_run_id = %s
           GROUP BY df.product_id, p.product_name, p.category
           ORDER BY total_forecast DESC
           LIMIT %s""",
        (run_id, n)
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


def get_all_forecasts(conn, run_id: str = None, product_id: str = None,
                      limit: int = 100, offset: int = 0) -> dict:
    """Paginated forecast table."""
    if not run_id:
        run_id = get_latest_run_id(conn)
    if not run_id:
        return {"total": 0, "items": []}

    cur = conn.cursor()

    base = """FROM demand_forecast df
              JOIN product p ON df.product_id = p.product_id
              WHERE df.forecast_run_id = %s"""
    params = [run_id]

    if product_id:
        base += " AND df.product_id = %s"
        params.append(product_id)

    cur.execute(f"SELECT COUNT(*) AS cnt {base}", params)
    total = int(cur.fetchone()["cnt"])

    cur.execute(
        f"""SELECT df.product_id, p.product_name, p.category,
                   df.forecast_date, df.forecasted_units, df.model_version
            {base}
            ORDER BY df.forecast_date, df.product_id
            LIMIT %s OFFSET %s""",
        params + [limit, offset]
    )
    items = [dict(r) for r in cur.fetchall()]
    cur.close()
    return {"total": total, "items": items, "limit": limit, "offset": offset}


def get_all_products_with_forecasts(conn, run_id: str = None) -> list:
    """All products that have forecasts in the latest run."""
    if not run_id:
        run_id = get_latest_run_id(conn)
    if not run_id:
        return []

    cur = conn.cursor()
    cur.execute(
        """SELECT DISTINCT df.product_id, p.product_name, p.category, p.brand
           FROM demand_forecast df
           JOIN product p ON df.product_id = p.product_id
           WHERE df.forecast_run_id = %s
           ORDER BY p.product_name""",
        (run_id,)
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows
