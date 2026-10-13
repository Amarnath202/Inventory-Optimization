"""
build_daily_demand.py
---------------------
Phase 7: Aggregate daily product demand from sale_item into daily_demand.

Schema
------
daily_demand (Product_ID, Date, Units_Sold)
PRIMARY KEY  (Product_ID, Date)

Aggregation SQL
---------------
SELECT si.Product_ID, s.Sale_Date AS Date, SUM(si.Quantity_Sold) AS Units_Sold
FROM   sale_item si
JOIN   sale      s  ON si.Bill_No = s.Bill_No
WHERE  si.Quantity_Sold > 0
GROUP  BY si.Product_ID, s.Sale_Date

Design decisions
----------------
* Uses Quantity_Sold > 0 filter (preserves only valid positive sales).
* No calendar gap-filling: dates exist only for days where a product was sold.
* No dates before first or after last observed sale for each product.
* Reconciles: SUM(daily_demand.Units_Sold) == SUM(sale_item.Quantity_Sold > 0).
* Rerun-safe: TRUNCATES and rebuilds daily_demand each run.
* Exports data/processed/daily_demand.csv using server-side COPY for efficiency.
* Runs EXPLAIN on the aggregation query to validate index usage.
"""

import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, PROC_DIR


def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )


# ── Build ─────────────────────────────────────────────────────────────────────

def build_daily_demand(conn) -> int:
    print("[build_daily_demand] Rebuilding daily_demand …")
    cur = conn.cursor()

    # Safe truncate (daily_demand has no dependents in current schema)
    cur.execute("TRUNCATE TABLE daily_demand")
    conn.commit()
    print("  Cleared daily_demand (TRUNCATE for fresh rebuild).")

    cur.execute(
        """
        INSERT INTO daily_demand (Product_ID, Date, Units_Sold)
        SELECT
            si.Product_ID,
            s.Sale_Date            AS Date,
            SUM(si.Quantity_Sold)  AS Units_Sold
        FROM   sale_item si
        JOIN   sale      s  ON si.Bill_No = s.Bill_No
        WHERE  si.Quantity_Sold > 0
        GROUP  BY si.Product_ID, s.Sale_Date
        ON CONFLICT (Product_ID, Date) DO UPDATE
            SET Units_Sold = EXCLUDED.Units_Sold
        """
    )
    inserted = cur.rowcount
    conn.commit()
    cur.close()

    print(f"  Inserted {inserted:,} (Product_ID, Date) rows into daily_demand.")
    return inserted


# ── Validate ──────────────────────────────────────────────────────────────────

def validate_daily_demand(conn) -> dict:
    print("\n[build_daily_demand] Validating totals …")
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*), SUM(Units_Sold) FROM daily_demand")
    dd_count, dd_total = cur.fetchone()
    dd_count = dd_count or 0
    dd_total = dd_total or 0

    cur.execute(
        "SELECT SUM(Quantity_Sold) FROM sale_item WHERE Quantity_Sold > 0"
    )
    si_total = cur.fetchone()[0] or 0

    cur.execute("SELECT MIN(Date), MAX(Date) FROM daily_demand")
    date_min, date_max = cur.fetchone()

    cur.execute("SELECT COUNT(DISTINCT Product_ID) FROM daily_demand")
    distinct_products = cur.fetchone()[0]

    cur.close()

    reconciles = (dd_total == si_total)

    print(f"  daily_demand rows       : {dd_count:,}")
    print(f"  Distinct products       : {distinct_products:,}")
    print(f"  Date range              : {date_min} -> {date_max}")
    print(f"  SUM(Units_Sold) in DD   : {dd_total:,}")
    print(f"  SUM(Qty_Sold) in si     : {si_total:,}")

    if reconciles:
        print("  ✅ Totals match — daily_demand reconciles with sale_item.")
    else:
        diff = dd_total - si_total
        print(f"  ❌ MISMATCH: difference = {diff:,}")

    return {
        "daily_demand_rows": dd_count,
        "distinct_products": distinct_products,
        "date_min": str(date_min),
        "date_max": str(date_max),
        "dd_total": dd_total,
        "si_total": si_total,
        "reconciles": reconciles,
    }


# ── Export CSV ────────────────────────────────────────────────────────────────

def export_csv(conn) -> int:
    out_path = PROC_DIR / "daily_demand.csv"
    print(f"\n[build_daily_demand] Exporting -> {out_path} …")

    cur = conn.cursor()
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write("Product_ID,Date,Units_Sold\n")
        cur.copy_expert(
            """
            COPY (
                SELECT Product_ID, Date, Units_Sold
                FROM   daily_demand
                ORDER  BY Product_ID, Date
            ) TO STDOUT WITH (FORMAT CSV)
            """,
            f,
        )
    cur.close()

    with open(out_path, "r", encoding="utf-8") as f:
        rows = sum(1 for _ in f) - 1   # subtract header

    print(f"  Exported {rows:,} rows.")
    return rows


# ── Query plan ────────────────────────────────────────────────────────────────

def run_query_plan_test(conn):
    """
    Run EXPLAIN (no execution) on the daily-demand aggregation to verify
    that the planned indexes are visible to the query planner.
    """
    print("\n[build_daily_demand] Query plan test …")
    cur = conn.cursor()
    cur.execute(
        """
        EXPLAIN (FORMAT TEXT, ANALYZE FALSE, VERBOSE FALSE)
        SELECT si.Product_ID, s.Sale_Date, SUM(si.Quantity_Sold)
        FROM   sale_item si
        JOIN   sale      s  ON si.Bill_No = s.Bill_No
        GROUP  BY si.Product_ID, s.Sale_Date
        """
    )
    rows = cur.fetchall()
    cur.close()

    print("  EXPLAIN output (first 15 lines):")
    for i, (line,) in enumerate(rows[:15]):
        print(f"    {line}")
    if len(rows) > 15:
        print(f"    … ({len(rows) - 15} more lines)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> dict:
    conn = get_conn()
    print(f"  ✅ Connected to '{DB_NAME}'")

    try:
        build_daily_demand(conn)
        stats = validate_daily_demand(conn)
        export_csv(conn)
        run_query_plan_test(conn)
        print("\n[build_daily_demand] Done.")
        return stats
    finally:
        conn.close()


if __name__ == "__main__":
    main()
