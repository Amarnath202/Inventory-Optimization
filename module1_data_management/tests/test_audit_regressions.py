import sys
import pandas as pd
import psycopg2
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
from src.preprocess import process_sale
from src.load_database import count_value_conflicts

def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )

def test_cross_chunk_bill_header_mismatch():
    import tempfile
    import src.preprocess
    # Create fake chunked sales CSV
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as f:
        f.write("Bill_No,Sale_Date,Payment_Mode,Customer_ID,Items_In_Bill\n")
        f.write("B1,2025-01-01,Cash,C1,2\n")
        f.write("B1,2025-01-02,Card,C2,2\n") # Mismatch
    
    old_csv = src.preprocess.SALES_CSV
    old_chunk = src.preprocess.CHUNK_SIZE
    src.preprocess.SALES_CSV = Path(f.name)
    src.preprocess.CHUNK_SIZE = 1 # Force cross chunk
    
    try:
        out, inconsistencies = src.preprocess.process_sale()
        assert inconsistencies == 1, f"Expected 1 inconsistency, got {inconsistencies}"
        print("✅ test_cross_chunk_bill_header_mismatch: PASS")
    except Exception as e:
        print(f"❌ test_cross_chunk_bill_header_mismatch: FAIL - {e}")
    finally:
        src.preprocess.SALES_CSV = old_csv
        src.preprocess.CHUNK_SIZE = old_chunk
        Path(f.name).unlink()

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

def main():
    print("Running audit regression tests...")
    test_cross_chunk_bill_header_mismatch()
    test_same_pk_changed_value_beyond_8th_col()
    test_null_vs_non_null_conflict()
    test_csv_column_order_mismatch()
    # Others are already covered by test_module1.py (test 10 covers invalid price, inventory balance; test 07 covers duplicate bill-item, etc.)

if __name__ == '__main__':
    main()
