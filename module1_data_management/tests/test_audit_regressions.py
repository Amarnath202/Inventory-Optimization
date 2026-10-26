import sys
import pandas as pd
import psycopg2
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
from src.preprocess import process_sale
from src.load_database import count_value_conflicts, load_table

def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )

def test_cross_chunk_bill_header_mismatch(tmp_path, monkeypatch):
    import src.preprocess
    source_csv = tmp_path / "sales.csv"
    source_csv.write_text(
        "Bill_No,Sale_Date,Payment_Mode,Customer_ID,Items_In_Bill\n"
        "B1,2025-01-01,Cash,C1,2\n"
        "B1,2025-01-02,Card,C2,2\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "processed"
    output_dir.mkdir()
    monkeypatch.setattr(src.preprocess, "SALES_CSV", source_csv)
    monkeypatch.setattr(src.preprocess, "CHUNK_SIZE", 1)
    monkeypatch.setattr(src.preprocess, "PROC_DIR", output_dir)

    _, inconsistencies = src.preprocess.process_sale()

    assert inconsistencies == 1
    output_path = output_dir / "sale.csv"
    assert output_path.exists()
    generated = pd.read_csv(output_path, dtype=str)
    assert generated["Bill_No"].tolist() == ["B1"]

def test_same_pk_changed_value_beyond_8th_col():
    conn = get_conn()
    cur = conn.cursor()
    # Create mock tables
    cur.execute("CREATE TEMP TABLE _target (pk int, c1 int, c2 int, c3 int, c4 int, c5 int, c6 int, c7 int, c8 int, c9 int)")
    cur.execute("CREATE TEMP TABLE _staging (pk int, c1 int, c2 int, c3 int, c4 int, c5 int, c6 int, c7 int, c8 int, c9 int)")
    cur.execute("INSERT INTO _target VALUES (1, 1, 1, 1, 1, 1, 1, 1, 1, 1)")
    cur.execute("INSERT INTO _staging VALUES (1, 1, 1, 1, 1, 1, 1, 1, 1, 2)") # 9th non-pk col changed
    
    pk_cols = ["pk"]
    load_cols = ["pk", "c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9"]
    
    conflicts = count_value_conflicts(cur, "_staging", "_target", pk_cols, load_cols)
    if conflicts == 1:
        print("✅ test_same_pk_changed_value_beyond_8th_col: PASS")
    else:
        print(f"❌ test_same_pk_changed_value_beyond_8th_col: FAIL - {conflicts}")
    
    conn.rollback()
    conn.close()

def test_null_vs_non_null_conflict():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("CREATE TEMP TABLE _target (pk int, val int)")
    cur.execute("CREATE TEMP TABLE _staging (pk int, val int)")
    cur.execute("INSERT INTO _target VALUES (1, NULL)")
    cur.execute("INSERT INTO _staging VALUES (1, 5)")
    
    conflicts = count_value_conflicts(cur, "_staging", "_target", ["pk"], ["pk", "val"])
    if conflicts == 1:
        print("✅ test_null_vs_non_null_conflict: PASS")
    else:
        print(f"❌ test_null_vs_non_null_conflict: FAIL - conflicts={conflicts}")
    
    conn.rollback()
    conn.close()

def test_csv_column_order_mismatch():
    from src.load_database import resolve_load_columns
    csv_cols = ["c2", "c1"]
    db_cols = ["c1", "c2"]
    res = resolve_load_columns(csv_cols, db_cols)
    if res == ["c2", "c1"]:
        print("✅ test_csv_column_order_mismatch: PASS")
    else:
        print(f"❌ test_csv_column_order_mismatch: FAIL - {res}")

def test_load_table_rerun_is_idempotent_and_preserves_unrelated_rows(tmp_path):
    import src.load_database as loader

    csv_path = tmp_path / "product_supplier.csv"
    csv_path.write_text(
        "Product_ID,Supplier_ID,Purchase_Rate,Minimum_Order_Qty\n"
        "P1,S1,10.00,2\n"
        "P2,S2,12.00,3\n",
        encoding="utf-8",
    )
    conn = get_conn()
    cur = conn.cursor()
    try:
        # The temporary relation shadows public.product_supplier on this
        # connection, keeping both importer runs isolated from production data.
        cur.execute(
            """
            CREATE TEMP TABLE product_supplier (
                product_id text NOT NULL,
                supplier_id text NOT NULL,
                purchase_rate numeric,
                minimum_order_qty integer,
                PRIMARY KEY (product_id, supplier_id)
            )
            """
        )
        cur.execute(
            "INSERT INTO product_supplier VALUES (%s, %s, %s, %s), (%s, %s, %s, %s)",
            ("P1", "S1", 10, 2, "UNRELATED", "S9", 99, 9),
        )
        conn.commit()
        report_start = len(loader.LOAD_REPORT)

        with patch.object(
            loader, "get_target_columns",
            return_value=[
                "product_id", "supplier_id", "purchase_rate", "minimum_order_qty"
            ],
        ):
            load_table(conn, "product_supplier", csv_path, ["Product_ID", "Supplier_ID"])
            after_first = loader.LOAD_REPORT[-1].copy()
            cur.execute("SELECT * FROM product_supplier ORDER BY product_id, supplier_id")
            first_rows = cur.fetchall()

            load_table(conn, "product_supplier", csv_path, ["Product_ID", "Supplier_ID"])
            after_second = loader.LOAD_REPORT[-1].copy()
            cur.execute("SELECT * FROM product_supplier ORDER BY product_id, supplier_id")
            second_rows = cur.fetchall()

        assert len(loader.LOAD_REPORT) == report_start + 2
        assert after_first["inserted"] == 1
        assert after_second["inserted"] == 0
        assert len(first_rows) == len(second_rows) == 3
        assert second_rows == first_rows
        assert ("UNRELATED", "S9", 99, 9) in second_rows
    finally:
        conn.rollback()
        cur.close()
        conn.close()

def main():
    print("Running audit regression tests...")
    test_cross_chunk_bill_header_mismatch()
    test_same_pk_changed_value_beyond_8th_col()
    test_null_vs_non_null_conflict()
    test_csv_column_order_mismatch()
    # Others are already covered by test_module1.py (test 10 covers invalid price, inventory balance; test 07 covers duplicate bill-item, etc.)

if __name__ == '__main__':
    main()
