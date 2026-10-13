"""
build_schema.py
---------------
Phase 5: Safe PostgreSQL schema creation.

Workflow
--------
1.  Connect to inventory_optimization_db2 using env-based credentials.
2.  Inspect existing table definitions in the public schema.
3.  Report conflicts (existing tables with differing column sets).
4.  Execute sql/schema.sql which uses CREATE TABLE IF NOT EXISTS throughout.
5.  Create the 4 planned indexes (CREATE INDEX IF NOT EXISTS).
6.  Verify all expected tables and indexes exist after execution.

Safety guarantees
-----------------
* NEVER drops or truncates any table automatically.
* NEVER overwrites existing data.
* If a table already exists with incompatible columns, the conflict is reported
  and the script exits without modifying the database.
"""

import sys
from pathlib import Path

import psycopg2
from psycopg2 import sql as psql

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, SQL_DIR

EXPECTED_TABLES = [
    "supplier", "product", "product_supplier",
    "inventory_batch", "sale", "sale_item", "daily_demand",
]

EXPECTED_TABLE_COLS = {
    "supplier":         {"Supplier_ID", "Supplier_Name", "Lead_Time_Days"},
    "product":          {"Product_ID", "Product_Code", "Product_Name", "Brand", "Category",
                         "Unit", "Purchase_Rate", "Sale_Rate", "MRP", "Reorder_Level",
                         "Maximum_Stock_Level", "Minimum_Order_Qty",
                         "Purchase_Frequency", "Expected_Daily_Demand"},
    "product_supplier": {"Product_ID", "Supplier_ID", "Purchase_Rate", "Minimum_Order_Qty"},
    "inventory_batch":  {"Batch_ID", "Product_ID", "Supplier_ID", "Purchase_Date",
                         "Received_Date", "Lead_Time_Date_Difference_Days",
                         "Received_Quantity", "Sold_Quantity", "Current_Quantity",
                         "Reserved_Quantity", "Damaged_Quantity", "Wasted_Quantity",
                         "Selling_Price", "MRP", "Stock_Value", "Storage_Location",
                         "Inventory_Status", "Last_Updated",
                         "Reorder_Level", "Maximum_Stock_Level", "Supplier_Lead_Time_Days"},
    "sale":             {"Bill_No", "Sale_Date", "Payment_Mode", "Customer_ID", "Items_In_Bill"},
    "sale_item":        {"Sale_ID", "Bill_No", "Product_ID", "Batch_ID",
                         "Item_Seq_In_Bill", "Quantity_Sold", "Selling_Price", "MRP",
                         "Discount_Amount", "Tax_Rate", "Tax_Amount", "Total_Amount"},
    "daily_demand":     {"Product_ID", "Date", "Units_Sold"},
}

EXPECTED_INDEXES = [
    "idx_sale_item_product_id",
    "idx_sale_item_batch_id",
    "idx_inventory_batch_product_id",
    "idx_inventory_batch_received_date",
]


# ── Connection ────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )


# ── Inspection ────────────────────────────────────────────────────────────────

def inspect_existing_tables(conn) -> dict[str, list[tuple]]:
    """
    Returns {table_name: [(col_name, data_type, is_nullable), ...]}
    for any of EXPECTED_TABLES already present in the public schema.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT table_name, column_name, data_type, is_nullable
        FROM   information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = ANY(%s)
        ORDER  BY table_name, ordinal_position
        """,
        (EXPECTED_TABLES,),
    )
    tables: dict[str, list] = {}
    for table, col, dtype, nullable in cur.fetchall():
        tables.setdefault(table, []).append((col, dtype, nullable))
    cur.close()
    return tables


def report_existing(existing: dict) -> bool:
    """
    Prints existing table info and checks for column-level conflicts.
    Returns True if incompatible tables are detected.
    """
    if not existing:
        print("  No target tables exist yet — clean schema install.")
        return False

    incompatible = False
    print(f"  Found {len(existing)} existing table(s):")

    for table, cols in existing.items():
        existing_col_names = {c[0].lower() for c in cols}
        expected_col_names = {c.lower() for c in EXPECTED_TABLE_COLS.get(table, set())}
        missing_in_db = expected_col_names - existing_col_names
        extra_in_db   = existing_col_names - expected_col_names

        status = "✅ compatible"
        if missing_in_db:
            status = f"❌ CONFLICT — missing columns: {missing_in_db}"
            incompatible = True

        print(f"\n  • {table}: {len(cols)} columns  [{status}]")
        if extra_in_db and not missing_in_db:
            print(f"    Extra columns in DB (ignored): {extra_in_db}")
        for col, dtype, nullable in cols[:6]:
            print(f"    {col:<40} {dtype:<20} {'NULL' if nullable == 'YES' else 'NOT NULL'}")
        if len(cols) > 6:
            print(f"    … ({len(cols) - 6} more columns)")

    return incompatible


# ── Schema execution ──────────────────────────────────────────────────────────

def execute_schema(conn, sql_path: Path):
    """Execute the reviewed schema.sql file."""
    schema_sql = sql_path.read_text(encoding="utf-8")
    cur = conn.cursor()
    try:
        cur.execute(schema_sql)
        conn.commit()
        print("  ✅ Schema SQL executed successfully.")
    except psycopg2.Error as e:
        conn.rollback()
        print(f"  ❌ Schema execution failed:\n     {e}")
        raise
    finally:
        cur.close()


# ── Verification ──────────────────────────────────────────────────────────────

def verify_tables(conn) -> list[str]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT table_name
        FROM   information_schema.tables
        WHERE  table_schema = 'public'
          AND  table_name   = ANY(%s)
        ORDER  BY table_name
        """,
        (EXPECTED_TABLES,),
    )
    found = {row[0] for row in cur.fetchall()}
    cur.close()

    print("\n  Table verification:")
    for table in EXPECTED_TABLES:
        status = "✅" if table in found else "❌ MISSING"
        print(f"    {status}  {table}")

    return [t for t in EXPECTED_TABLES if t not in found]


def verify_indexes(conn) -> list[str]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT indexname
        FROM   pg_indexes
        WHERE  schemaname = 'public'
          AND  indexname  = ANY(%s)
        """,
        (EXPECTED_INDEXES,),
    )
    found = {row[0] for row in cur.fetchall()}
    cur.close()

    print("\n  Index verification:")
    for idx in EXPECTED_INDEXES:
        status = "✅" if idx in found else "❌ MISSING"
        print(f"    {status}  {idx}")

    return [i for i in EXPECTED_INDEXES if i not in found]


def print_table_sizes(conn):
    """Print row counts for any tables that already contain data."""
    cur = conn.cursor()
    print("\n  Current row counts:")
    for table in EXPECTED_TABLES:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            print(f"    {table:<25} {count:>12,} rows")
        except Exception:
            print(f"    {table:<25} {'—':>12}")
    cur.close()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("[build_schema] Connecting to database …")
    try:
        conn = get_conn()
        print(f"  ✅ Connected to '{DB_NAME}' on {DB_HOST}:{DB_PORT}")
    except psycopg2.OperationalError as e:
        print(f"  ❌ Connection failed: {e}")
        sys.exit(1)

    try:
        # ── Step 1: Inspect existing state ────────────────────────────────────
        print("\n[build_schema] Step 1: Inspecting existing tables …")
        existing = inspect_existing_tables(conn)
        incompatible = report_existing(existing)

        if incompatible:
            print(
                "\n  ❌ Incompatible existing tables detected.\n"
                "     Review the conflicts above. Do NOT auto-drop or alter tables.\n"
                "     Resolve manually then re-run this script."
            )
            sys.exit(1)

        # ── Step 2: Execute schema ────────────────────────────────────────────
        sql_path = SQL_DIR / "schema.sql"
        if not sql_path.exists():
            print(f"  ❌ schema.sql not found at {sql_path}")
            sys.exit(1)

        print(f"\n[build_schema] Step 2: Executing {sql_path.name} …")
        execute_schema(conn, sql_path)

        # ── Step 3: Verify ────────────────────────────────────────────────────
        print("\n[build_schema] Step 3: Verification …")
        missing_tables  = verify_tables(conn)
        missing_indexes = verify_indexes(conn)

        # ── Step 4: Report current sizes ─────────────────────────────────────
        print_table_sizes(conn)

        if missing_tables:
            print(f"\n  ❌ {len(missing_tables)} table(s) still missing: {missing_tables}")
            sys.exit(1)
        if missing_indexes:
            print(f"\n  ⚠️  {len(missing_indexes)} index(es) missing: {missing_indexes}")
        else:
            print(f"\n  ✅ All {len(EXPECTED_TABLES)} tables and "
                  f"{len(EXPECTED_INDEXES)} indexes verified.")

        print("\n[build_schema] Schema is ready. Safe to proceed to data loading.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
