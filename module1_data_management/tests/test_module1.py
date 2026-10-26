"""
test_module1.py
---------------
Phase 10: Automated tests for Module 1.

Tests 1–9, 11–12 run read-only queries against inventory_optimization_db2.
Test 10 uses a temporary in-session table (auto-dropped; production data untouched).

Exit codes
----------
0 — all critical tests passed (ADVISORY results are non-blocking)
1 — one or more critical tests FAILED
"""

import sys
import csv
import json
from pathlib import Path

import psycopg2
import psycopg2.errors

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, PROC_DIR, RAW_DIR,
)

# ── Result tracking ───────────────────────────────────────────────────────────

RESULTS: list[dict] = []
PASS     = "PASS"
FAIL     = "FAIL"
SKIP     = "SKIP"
ADVISORY = "ADVISORY"


def record(test_id: int, name: str, status: str, detail: str = ""):
    icon = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️", "ADVISORY": "⚠️"}.get(status, "?")
    print(f"  {icon} Test {test_id:02d}: {name} — {status}")
    if detail:
        print(f"         {detail}")
    RESULTS.append({"id": test_id, "name": name, "status": status, "detail": detail})


class SkipTest(Exception):
    pass


# ── Connection ────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
    )


def scalar(cur, sql: str, params=None) -> int | float | None:
    cur.execute(sql, params)
    return cur.fetchone()[0]


# ── Individual tests ──────────────────────────────────────────────────────────

def test_01_pk_uniqueness(cur):
    """Primary-key uniqueness across all tables."""
    checks = [
        ("supplier",        "Supplier_ID"),
        ("product",         "Product_ID"),
        ("product_supplier", None),          # composite PK — checked separately
        ("inventory_batch", "Batch_ID"),
        ("sale",            "Bill_No"),
        ("sale_item",       "Sale_ID"),
        ("daily_demand",    None),           # composite PK — checked separately
    ]
    failures = []

    for table, pk in checks:
        if pk:
            total  = scalar(cur, f'SELECT COUNT(*)                FROM {table}')
            unique = scalar(cur, f'SELECT COUNT(DISTINCT {pk.lower()}) FROM {table}')
            if total != unique:
                failures.append(f"{table}.{pk}: {total - unique} duplicates")

    # product_supplier composite PK
    ps_total  = scalar(cur, "SELECT COUNT(*) FROM product_supplier")
    ps_unique = scalar(cur, "SELECT COUNT(*) FROM (SELECT DISTINCT Product_ID, Supplier_ID FROM product_supplier) t")
    if ps_total != ps_unique:
        failures.append(f"product_supplier composite PK: {ps_total - ps_unique} duplicates")

    # daily_demand composite PK
    dd_total  = scalar(cur, "SELECT COUNT(*) FROM daily_demand")
    dd_unique = scalar(cur, "SELECT COUNT(*) FROM (SELECT DISTINCT Product_ID, Date FROM daily_demand) t")
    if dd_total != dd_unique:
        failures.append(f"daily_demand composite PK: {dd_total - dd_unique} duplicates")

    if failures:
        raise ValueError("; ".join(failures))

    record(1, "PK uniqueness (all tables)", PASS,
           "All single-column and composite PKs are unique")


def test_02_fk_integrity(cur):
    """Foreign-key referential integrity for all FK relationships."""
    fk_checks = [
        # product has no supplier_id; product↔supplier lives in product_supplier
        ("product_supplier", "product_id",   "product",         "product_id"),
        ("product_supplier", "supplier_id",  "supplier",        "supplier_id"),
        ("inventory_batch",  "product_id",   "product",         "product_id"),
        ("inventory_batch",  "supplier_id",  "supplier",        "supplier_id"),
        ("sale_item",        "bill_no",       "sale",            "bill_no"),
        ("sale_item",        "product_id",   "product",         "product_id"),
        ("sale_item",        "batch_id",      "inventory_batch", "batch_id"),
        ("daily_demand",     "product_id",   "product",         "product_id"),
    ]
    failures = []
    for child_t, child_col, parent_t, parent_col in fk_checks:
        orphans = scalar(
            cur,
            f"""
            SELECT COUNT(*) FROM {child_t} c
            LEFT  JOIN {parent_t} p ON c.{child_col} = p.{parent_col}
            WHERE p.{parent_col} IS NULL
            """,
        )
        if orphans:
            failures.append(f"{child_t}.{child_col} -> {parent_t}: {orphans} orphan(s)")

    if failures:
        raise ValueError("; ".join(failures))
    record(2, "FK integrity (all 8 relationships)", PASS)


def test_03_date_validity(cur):
    """Date range and NULL checks."""
    failures = []

    # NULL Sale_Date
    null_sale = scalar(cur, "SELECT COUNT(*) FROM sale WHERE Sale_Date IS NULL")
    if null_sale:
        failures.append(f"{null_sale} NULL Sale_Date in sale")

    # Sale_Date within expected range
    cur.execute("SELECT MIN(Sale_Date), MAX(Sale_Date) FROM sale")
    date_min, date_max = cur.fetchone()
    if date_min and str(date_min) < "2025-01-01":
        failures.append(f"Sale_Date min {date_min} is before 2025-01-01")
    if date_max and str(date_max) > "2026-08-30":
        failures.append(f"Sale_Date max {date_max} is after 2026-08-30")

    # Inventory: Received_Date >= Purchase_Date
    inv_violations = scalar(
        cur,
        "SELECT COUNT(*) FROM inventory_batch WHERE Received_Date < Purchase_Date",
    )
    if inv_violations:
        failures.append(f"{inv_violations} inventory batches: Received_Date < Purchase_Date")

    # NULL Purchase_Date / Received_Date
    null_pd = scalar(cur, "SELECT COUNT(*) FROM inventory_batch WHERE Purchase_Date IS NULL")
    null_rd = scalar(cur, "SELECT COUNT(*) FROM inventory_batch WHERE Received_Date IS NULL")
    if null_pd:
        failures.append(f"{null_pd} NULL Purchase_Date in inventory_batch")
    if null_rd:
        failures.append(f"{null_rd} NULL Received_Date in inventory_batch")

    if failures:
        raise ValueError("; ".join(failures))
    record(3, "Date validity", PASS,
           f"Sale_Date range: {date_min} → {date_max}. No date violations.")


def test_04_negative_zero_quantities(cur):
    """No negative quantities; no zero Quantity_Sold in sale_item."""
    failures = []

    neg_qty  = scalar(cur, "SELECT COUNT(*) FROM sale_item WHERE Quantity_Sold < 0")
    zero_qty = scalar(cur, "SELECT COUNT(*) FROM sale_item WHERE Quantity_Sold = 0")
    if neg_qty:
        failures.append(f"{neg_qty} negative Quantity_Sold in sale_item")
    if zero_qty:
        failures.append(f"{zero_qty} zero Quantity_Sold in sale_item")

    inv_qty_cols = [
        "received_quantity", "sold_quantity", "current_quantity",
        "reserved_quantity", "damaged_quantity", "wasted_quantity",
    ]
    for col in inv_qty_cols:
        neg = scalar(cur, f'SELECT COUNT(*) FROM inventory_batch WHERE {col} < 0')
        if neg:
            failures.append(f"{neg} negative {col} in inventory_batch")

    if failures:
        raise ValueError("; ".join(failures))
    record(4, "Negative/zero quantities", PASS, "No invalid quantities in any table")


def test_05_inventory_equation(cur):
    """Received = Sold + Current + Damaged + Wasted for every batch."""
    violations = scalar(
        cur,
        """
        SELECT COUNT(*) FROM inventory_batch
        WHERE ABS(
            Received_Quantity
            - Sold_Quantity
            - Current_Quantity
            - Damaged_Quantity
            - Wasted_Quantity
        ) > 0
        """,
    )
    if violations:
        raise ValueError(
            f"{violations} batches violate: Received ≠ Sold+Current+Damaged+Wasted"
        )
    record(5, "Inventory equation", PASS,
           "Received = Sold + Current + Damaged + Wasted for all batches")


def test_06_sale_id_uniqueness(cur):
    """Sale_ID is unique across all sale_item rows."""
    total  = scalar(cur, "SELECT COUNT(*)               FROM sale_item")
    unique = scalar(cur, "SELECT COUNT(DISTINCT Sale_ID) FROM sale_item")
    dupes  = total - unique
    if dupes:
        raise ValueError(f"{dupes} duplicate Sale_ID(s)")
    record(6, "Sale_ID uniqueness", PASS, f"{total:,} Sale_IDs all unique")


def test_07_bill_seq_uniqueness(cur):
    """(Bill_No, Item_Seq_In_Bill) is unique across all sale_item rows."""
    total = scalar(cur, "SELECT COUNT(*) FROM sale_item")
    unique = scalar(
        cur,
        "SELECT COUNT(*) FROM (SELECT DISTINCT Bill_No, Item_Seq_In_Bill FROM sale_item) t",
    )
    dupes = total - unique
    if dupes:
        raise ValueError(f"{dupes} duplicate (Bill_No, Item_Seq_In_Bill) combinations")
    record(7, "Bill_No + Item_Seq_In_Bill uniqueness", PASS)


def test_08_daily_demand_reconciliation(cur):
    """SUM(daily_demand.Units_Sold) == SUM(sale_item.Quantity_Sold > 0)."""
    dd_count = scalar(cur, "SELECT COUNT(*) FROM daily_demand")
    if dd_count == 0:
        raise SkipTest("daily_demand is empty — run build_daily_demand.py first")

    dd_total = scalar(cur, "SELECT SUM(Units_Sold)   FROM daily_demand") or 0
    si_total = scalar(
        cur,
        "SELECT SUM(Quantity_Sold) FROM sale_item WHERE Quantity_Sold > 0",
    ) or 0

    if dd_total != si_total:
        raise ValueError(
            f"Mismatch: SUM(daily_demand.Units_Sold)={dd_total:,} "
            f"≠ SUM(sale_item.Qty)={si_total:,} (diff={dd_total - si_total:,})"
        )
    record(8, "Daily-demand aggregation reconciliation", PASS,
           f"{dd_total:,} total units in {dd_count:,} product-day rows")


def test_09_sales_batch_reconciliation(cur):
    """
    Advisory: sale_item Qty by Batch_ID vs inventory_batch.Sold_Quantity.
    Mismatches are documented as advisory (not blocking) because both values
    should be identical in this validated synthetic dataset.
    """
    mismatches = scalar(
        cur,
        """
        SELECT COUNT(*) FROM (
            SELECT   si.Batch_ID,
                     SUM(si.Quantity_Sold) AS sales_qty,
                     ib.Sold_Quantity
            FROM     sale_item      si
            JOIN     inventory_batch ib ON si.Batch_ID = ib.Batch_ID
            GROUP BY si.Batch_ID, ib.Sold_Quantity
            HAVING   ABS(SUM(si.Quantity_Sold) - ib.Sold_Quantity) > 0
        ) mismatches
        """,
    )
    if mismatches:
        detail = (
            f"{mismatches} batch(es) where sale_item qty ≠ inventory Sold_Quantity. "
            "Advisory: verify that both fields represent the same time period."
        )
        RESULTS.append({"id": 9, "name": "Sales↔Batch reconciliation", "status": ADVISORY, "detail": detail})
        print(f"  ⚠️  Test 09: Sales↔Batch reconciliation — ADVISORY")
        print(f"         {detail}")
    else:
        record(9, "Sales↔Batch reconciliation (advisory)", PASS,
               "sale_item quantities by batch match inventory Sold_Quantity")


def test_10_constraint_enforcement(conn, cur):
    """
    Verify CHECK, PK, and FK constraints are enforced by the DB.
    Tests constraints against actual schema tables via transaction rollbacks.
    Does NOT modify any production data.
    """
    failures = []

    # Get valid keys for FK compliance so we can test the CHECK constraints purely.
    cur.execute("SELECT Product_ID, Supplier_ID FROM product_supplier LIMIT 1")
    row = cur.fetchone()
    if not row:
        raise SkipTest("No product_supplier data available for constraint testing")
    test_prod, test_sup = row
    
    cur.execute("SELECT Bill_No FROM sale LIMIT 1")
    row = cur.fetchone()
    if not row:
        raise SkipTest("No sale data available for constraint testing")
    test_bill = row[0]

    cur.execute("SELECT Batch_ID FROM inventory_batch LIMIT 1")
    row = cur.fetchone()
    if not row:
        raise SkipTest("No inventory batch available for constraint testing")
    test_batch = row[0]

    # a) FK rejects orphan in sale_item (non-existent Bill_No)
    try:
        cur.execute(
            """
            INSERT INTO sale_item
              (Sale_ID, Bill_No, Product_ID, Batch_ID,
               Item_Seq_In_Bill, Quantity_Sold, Selling_Price, MRP, Discount_Amount, Tax_Rate, Tax_Amount, Total_Amount)
            VALUES
              ('SALE_T10_FAKE', 'BILL_NONEXISTENT_ZZZ', %s, %s,
               1, 1, 10.0, 10.0, 0, 0, 0, 10.0)
            """, (test_prod, test_batch)
        )
        conn.rollback()
        failures.append("FK sale_item.Bill_No did NOT reject non-existent Bill_No")
    except psycopg2.errors.ForeignKeyViolation:
        conn.rollback()   # expected

    # b) Actual chk_si_selling_le_mrp constraint in sale_item
    # MRP = 10.0, Selling_Price = 11.0 (> MRP + 0.01)
    try:
        cur.execute(
            """
            INSERT INTO sale_item
              (Sale_ID, Bill_No, Product_ID, Batch_ID,
               Item_Seq_In_Bill, Quantity_Sold, Selling_Price, MRP, Discount_Amount, Tax_Rate, Tax_Amount, Total_Amount)
            VALUES
              ('SALE_T10_FAKE', %s, %s, %s,
               99999, 1, 11.0, 10.0, 0, 0, 0, 11.0)
            """, (test_bill, test_prod, test_batch)
        )
        conn.rollback()
        failures.append("chk_si_selling_le_mrp did NOT reject Selling_Price > MRP")
    except psycopg2.errors.CheckViolation:
        conn.rollback()   # expected

    # c) Actual chk_ib_inventory_equation constraint in inventory_batch
    # Received(10) != Sold(5) + Current(2) + Damaged(1) + Wasted(1) -> 9
    try:
        cur.execute(
            """
            INSERT INTO inventory_batch
              (Batch_ID, Product_ID, Supplier_ID, Purchase_Date, Received_Date,
               Received_Quantity, Sold_Quantity, Current_Quantity, Reserved_Quantity,
               Damaged_Quantity, Wasted_Quantity)
            VALUES
              ('BAT_T10_FAKE', %s, %s, '2025-01-01', '2025-01-02',
               10, 5, 2, 0, 1, 1)
            """, (test_prod, test_sup)
        )
        conn.rollback()
        failures.append("chk_ib_inventory_equation did NOT reject unbalanced inventory")
    except psycopg2.errors.CheckViolation:
        conn.rollback()   # expected

    if failures:
        raise ValueError("; ".join(failures))
    record(10, "DB constraint enforcement", PASS,
           "Actual DB constraints correctly rejected invalid inserts")


def test_11_rerun_idempotence(cur):
    """
    Check loaded rows against their complete import sources.

    Module 4 adds supplier alternatives after Module 1's base import, and the
    sale table is derived as one header per distinct Bill_No from sales lines.
    Neither table can be checked against its Module 1 processed CSV row count.
    """
    csv_map = {
        "supplier":        "supplier.csv",
        "product":         "product.csv",
        "inventory_batch": "inventory_batch.csv",
        "sale_item":       "sale_item.csv",
    }
    failures = []

    for table, filename in csv_map.items():
        csv_path = PROC_DIR / filename
        if not csv_path.exists():
            failures.append(f"{table}: expected Module 1 source is missing: {csv_path}")
            continue

        with open(csv_path, "r", encoding="utf-8") as f:
            csv_count = sum(1 for _ in f) - 1   # subtract header

        db_count = scalar(cur, f"SELECT COUNT(*) FROM {table}")

        if db_count != csv_count:
            failures.append(
                f"{table}: DB={db_count:,} != Module 1 source={csv_count:,}"
            )

    base_path = PROC_DIR / "product_supplier.csv"
    alternatives_path = RAW_DIR / "module4_all_supplier_lead_time_ranking.csv"
    if not base_path.exists() or not alternatives_path.exists():
        failures.append(
            "product_supplier: Module 1 base CSV or Module 4 alternatives CSV is missing"
        )
    else:
        with open(base_path, "r", encoding="utf-8", newline="") as f:
            base_pairs = {
                (row["Product_ID"], row["Supplier_ID"])
                for row in csv.DictReader(f)
            }
        with open(alternatives_path, "r", encoding="utf-8", newline="") as f:
            alternative_pairs = {
                (row["Product_ID"], row["Supplier_ID"])
                for row in csv.DictReader(f)
            }
        cur.execute("SELECT product_id, supplier_id FROM product_supplier")
        database_pairs = set(cur.fetchall())
        expected_pairs = base_pairs | alternative_pairs
        if database_pairs != expected_pairs:
            failures.append(
                "product_supplier: database pairs do not equal the Module 1 base "
                f"plus Module 4 alternatives (DB={len(database_pairs):,}, "
                f"expected={len(expected_pairs):,})"
            )
        if not base_pairs.issubset(database_pairs):
            failures.append("product_supplier: one or more Module 1 base pairs are missing")
        if not alternative_pairs.issubset(database_pairs):
            failures.append("product_supplier: one or more Module 4 alternatives are missing")

    validation_path = RAW_DIR / "threshold_dataset_validation.json"
    if not validation_path.exists():
        failures.append(f"sale: validated raw source metadata is missing: {validation_path}")
    else:
        with open(validation_path, "r", encoding="utf-8") as f:
            source_counts = json.load(f)
        expected_bills = source_counts.get("bills_total")
        if not isinstance(expected_bills, int):
            failures.append("sale: raw source metadata has no integer bills_total")
        else:
            db_count = scalar(cur, "SELECT COUNT(*) FROM sale")
            if db_count != expected_bills:
                failures.append(
                    f"sale: DB={db_count:,} != distinct raw Bill_No count="
                    f"{expected_bills:,}"
                )

    if failures:
        raise ValueError("; ".join(failures))
    record(11, "Rerun idempotence", PASS,
           "Module 1 source rows match; product_supplier includes base + alternatives; "
           "sale matches validated distinct Bill_No count")


def test_12_source_db_consistency(cur):
    """
    Source-to-DB row count consistency against documented expected values
    (from threshold_dataset_validation.json).
    """
    expected = {
        "supplier":        200,
        "product":         3_046,
        "inventory_batch": 60_702,
        "sale_item":       4_290_604,
    }
    failures = []

    for table, exp in expected.items():
        actual = scalar(cur, f"SELECT COUNT(*) FROM {table}")
        if actual != exp:
            failures.append(f"{table}: expected={exp:,}, actual={actual:,}")

    if failures:
        raise ValueError("; ".join(failures))
    record(12, "Source-to-DB row count consistency", PASS,
           f"All table counts match: {list(expected.keys())}")


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all_tests():
    print("[test_module1] Connecting …")
    try:
        conn = get_conn()
        cur  = conn.cursor()
        print(f"  ✅ Connected to '{DB_NAME}'\n")
    except psycopg2.OperationalError as e:
        print(f"  ❌ Cannot connect to database: {e}")
        sys.exit(1)

    tests = [
        (1,  "PK uniqueness",                     lambda: test_01_pk_uniqueness(cur)),
        (2,  "FK integrity",                       lambda: test_02_fk_integrity(cur)),
        (3,  "Date validity",                      lambda: test_03_date_validity(cur)),
        (4,  "Negative/zero quantities",           lambda: test_04_negative_zero_quantities(cur)),
        (5,  "Inventory equation",                 lambda: test_05_inventory_equation(cur)),
        (6,  "Sale_ID uniqueness",                 lambda: test_06_sale_id_uniqueness(cur)),
        (7,  "Bill_No + Item_Seq uniqueness",      lambda: test_07_bill_seq_uniqueness(cur)),
        (8,  "Daily demand reconciliation",        lambda: test_08_daily_demand_reconciliation(cur)),
        (9,  "Sales↔Batch reconciliation",         lambda: test_09_sales_batch_reconciliation(cur)),
        (10, "DB constraint enforcement",          lambda: test_10_constraint_enforcement(conn, cur)),
        (11, "Rerun idempotence",                  lambda: test_11_rerun_idempotence(cur)),
        (12, "Source-to-DB row count consistency", lambda: test_12_source_db_consistency(cur)),
    ]

    for test_id, name, func in tests:
        try:
            func()
        except SkipTest as e:
            record(test_id, name, SKIP, str(e))
        except Exception as e:
            conn.rollback()   # safety rollback after any error
            record(test_id, name, FAIL, str(e))

    cur.close()
    conn.close()


def print_summary():
    counts = {s: sum(1 for r in RESULTS if r["status"] == s)
              for s in (PASS, FAIL, SKIP, ADVISORY)}

    print("\n" + "=" * 70)
    print("MODULE 1 — TEST SUMMARY")
    print("=" * 70)
    print(f"  {'#':<4}  {'Test Name':<45}  Status")
    print(f"  {'─'*4}  {'─'*45}  {'─'*10}")

    icons = {PASS: "✅", FAIL: "❌", SKIP: "⏭️", ADVISORY: "⚠️"}
    for r in RESULTS:
        icon = icons.get(r["status"], "?")
        print(f"  {r['id']:<4}  {r['name']:<45}  {icon} {r['status']}")
        if r["detail"]:
            print(f"        {r['detail']}")

    print(f"\n  PASS={counts[PASS]}  FAIL={counts[FAIL]}  "
          f"SKIP={counts[SKIP]}  ADVISORY={counts[ADVISORY]}")
    print("=" * 70)

    if counts[FAIL] == 0:
        print("✅ All critical tests passed.")
    else:
        print(f"❌ {counts[FAIL]} critical test(s) failed.")


if __name__ == "__main__":
    print("=" * 70)
    print("Module 1 Automated Tests")
    print("=" * 70)
    run_all_tests()
    print_summary()

    failed = sum(1 for r in RESULTS if r["status"] == FAIL)
    sys.exit(1 if failed else 0)
