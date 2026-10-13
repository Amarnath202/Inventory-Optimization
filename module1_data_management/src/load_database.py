"""
load_database.py
----------------
Phase 6: Bulk-load processed CSVs into PostgreSQL.

Load order (strict dependency order)
-------------------------------------
1. supplier
2. product
3. product_supplier
4. inventory_batch
5. sale
6. sale_item         ← 4.3 M rows; loaded in CHUNK_SIZE blocks

Strategy per table
------------------
1. Read target-table columns from information_schema.
2. Create a TEMP TABLE (no constraints) using AS SELECT ... LIMIT 0.
3. COPY the processed CSV into the temp table via copy_expert (streaming).
4. Count rows already present in the target with the same PK
   (these are "existing_before" rows — could be from a prior run).
5. INSERT INTO target ... ON CONFLICT ({pk}) DO NOTHING.
6. Report: staged / inserted / skipped(already existed) counts.
7. Separately report any rows whose PK exists in target with DIFFERENT values
   (true conflict — same key, changed data).
8. Drop the temp table.

Rerun safety
------------
Re-running this script is idempotent: duplicate rows (same PK, same values) are
silently skipped via ON CONFLICT DO NOTHING. True conflicts (same PK, different
values) are detected and printed but NOT automatically overwritten.
"""

import sys
import io
import csv
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD,
    PROC_DIR, CHUNK_SIZE,
)

LOAD_REPORT: list[dict] = []


# ── Connection ────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
        options="-c client_encoding=UTF8",
    )


# ── Column discovery ──────────────────────────────────────────────────────────

def get_target_columns(cur, table_name: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name
        FROM   information_schema.columns
        WHERE  table_schema = 'public'
          AND  table_name   = %s
        ORDER  BY ordinal_position
        """,
        (table_name,),
    )
    return [row[0] for row in cur.fetchall()]


def get_csv_columns(csv_path: Path) -> list[str]:
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        return next(csv.reader(f))


def resolve_load_columns(csv_cols: list[str], target_cols: list[str]) -> list[str]:
    """
    Return the ordered list of columns that exist in BOTH the CSV and the DB table.

    IMPORTANT: Order follows the CSV column order, NOT the DB column order.
    This is required because PostgreSQL COPY does not use the CSV header to map
    columns — it reads data positionally. The COPY column list must therefore
    match the order in which values appear in each CSV row.

    Uses case-insensitive matching; output uses DB-stored column names (lowercase).
    """
    target_lower = {c.lower(): c for c in target_cols}
    # Iterate CSV columns in their order; map each to the DB-stored name
    return [target_lower[c.lower()] for c in csv_cols if c.lower() in target_lower]


# ── Staging helpers ───────────────────────────────────────────────────────────

def create_staging(cur, conn, table_name: str) -> str:
    staging = f"_stg_{table_name}"
    cur.execute(f'DROP TABLE IF EXISTS "{staging}"')
    # Create with no constraints so COPY always succeeds
    cur.execute(
        f'CREATE TEMP TABLE "{staging}" AS SELECT * FROM {table_name} LIMIT 0'
    )
    conn.commit()
    return staging


def copy_csv_to_staging(cur, conn, csv_path: Path,
                         staging: str, load_cols: list[str],
                         chunk_size: int | None = None):
    """
    Stream the CSV into the staging table using copy_expert.
    For large files, reads in chunks via pandas and writes each chunk to a
    StringIO buffer to bound memory usage.
    Returns total rows staged.
    """
    col_list  = ", ".join(f'"{c}"' for c in load_cols)
    copy_sql  = (
        f'COPY "{staging}" ({col_list}) '
        f"FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL '')"
    )

    if chunk_size is None:
        # Small tables: stream the entire file at once
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            cur.copy_expert(copy_sql, f)
        conn.commit()
        cur.execute(f'SELECT COUNT(*) FROM "{staging}"')
        return cur.fetchone()[0]

    # Large files (sale_item): read in pandas chunks -> write each to StringIO.
    # Do NOT use usecols=load_cols here: load_cols contains lowercase DB names
    # but the CSV header has mixed-case names; pandas usecols is case-sensitive.
    # Instead: read all columns, then select+reorder by case-insensitive match.
    import pandas as pd
    total = 0
    for chunk in pd.read_csv(
        csv_path,
        dtype=str,
        keep_default_na=False,
        chunksize=chunk_size,
    ):
        # Map CSV header (mixed-case) -> lowercase for matching against load_cols
        csv_col_map = {c.lower(): c for c in chunk.columns}
        # Select and reorder columns to match the COPY column list exactly
        ordered = [csv_col_map[lc] for lc in load_cols if lc in csv_col_map]
        buf = io.StringIO()
        chunk[ordered].to_csv(buf, index=False)
        buf.seek(0)
        cur.copy_expert(copy_sql, buf)
        total += len(chunk)
        print(f"    Staged {total:,} rows ...", end="\r")

    conn.commit()
    print()
    return total


# ── Conflict detection ────────────────────────────────────────────────────────

def count_existing_pks(cur, staging: str, table_name: str,
                        pk_cols: list[str]) -> int:
    """Count staging rows whose PK already exists in the target table."""
    join_cond = " AND ".join(
        f's."{c}" = t."{c}"' for c in pk_cols
    )
    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM   "{staging}" s
        JOIN   {table_name} t ON {join_cond}
        """
    )
    return cur.fetchone()[0]


def count_value_conflicts(cur, staging: str, table_name: str,
                           pk_cols: list[str], load_cols: list[str]) -> int:
    """
    Count staging rows that share a PK with the target but differ in at least
    one non-PK column (true conflict = same key, changed values).
    """
    non_pk = [c for c in load_cols if c not in pk_cols]
    if not non_pk:
        return 0

    join_cond   = " AND ".join(f's."{c}" = t."{c}"' for c in pk_cols)
    differ_cond = " OR ".join(
        f's."{c}" IS DISTINCT FROM t."{c}"' for c in non_pk
    )
    cur.execute(
        f"""
        SELECT COUNT(*)
        FROM   "{staging}" s
        JOIN   {table_name} t ON {join_cond}
        WHERE  {differ_cond}
        """
    )
    return cur.fetchone()[0]


# ── Core load function ────────────────────────────────────────────────────────

def load_table(
    conn,
    table_name: str,
    csv_path: Path,
    pk_cols: list[str],
    chunk_size: int | None = None,
):
    print(f"\n[load_database] Loading -> {table_name}")
    print(f"  Source: {csv_path.name}")

    if not csv_path.exists():
        msg = f"File not found: {csv_path}"
        print(f"  ❌ {msg}")
        LOAD_REPORT.append({
            "table": table_name, "staged": "N/A",
            "inserted": 0, "skipped": 0, "conflicts": 0, "status": "FILE_MISSING",
        })
        return

    cur = conn.cursor()

    # 1. Resolve columns
    target_cols = get_target_columns(cur, table_name)
    csv_cols    = get_csv_columns(csv_path)
    load_cols   = resolve_load_columns(csv_cols, target_cols)

    if not load_cols:
        print(f"  ❌ No matching columns between CSV and {table_name}")
        cur.close()
        return

    # Normalise pk_cols to the DB-stored column names (PostgreSQL lowercases
    # unquoted identifiers; load_cols already contains the correct stored names).
    load_cols_lower_map = {c.lower(): c for c in load_cols}
    pk_cols = [load_cols_lower_map.get(p.lower(), p) for p in pk_cols]

    extra_csv  = [c for c in csv_cols    if c.lower() not in {x.lower() for x in load_cols}]
    extra_db   = [c for c in target_cols if c.lower() not in {x.lower() for x in load_cols}]
    print(f"  Loading {len(load_cols)} column(s). "
          f"CSV-only: {extra_csv or '—'}. DB-only (skipped): {extra_db or '—'}.")

    # 2. Create staging table
    staging = create_staging(cur, conn, table_name)

    # 3. COPY CSV into staging
    staged = copy_csv_to_staging(cur, conn, csv_path, staging, load_cols, chunk_size)
    print(f"  Staged      : {staged:,}")

    # 4. Pre-insert counts
    existing_before = count_existing_pks(cur, staging, table_name, pk_cols)
    conflicts       = count_value_conflicts(cur, staging, table_name, pk_cols, load_cols)

    if conflicts > 0:
        print(f"  ⚠️  {conflicts} staging row(s) share a PK with the target but "
              f"have DIFFERENT values — these will be SKIPPED (not overwritten).")

    # 5. INSERT ... ON CONFLICT DO NOTHING
    col_list   = ", ".join(f'"{c}"' for c in load_cols)
    pk_conflict = ", ".join(f'"{c}"' for c in pk_cols)
    cur.execute(
        f"""
        INSERT INTO {table_name} ({col_list})
        SELECT {col_list} FROM "{staging}"
        ON CONFLICT ({pk_conflict}) DO NOTHING
        """
    )
    inserted = cur.rowcount
    conn.commit()

    # 6. Cleanup
    cur.execute(f'DROP TABLE IF EXISTS "{staging}"')
    conn.commit()
    cur.close()

    skipped = staged - inserted
    status  = "CONFLICTS" if conflicts > 0 else "OK"

    print(f"  Inserted    : {inserted:,}")
    print(f"  Skipped     : {skipped:,}  (already existed in target)")
    print(f"  Conflicts   : {conflicts:,}  (same PK, different values — NOT inserted)")
    print(f"  Status      : {status}")

    LOAD_REPORT.append({
        "table": table_name, "staged": staged, "inserted": inserted,
        "skipped": skipped, "conflicts": conflicts, "status": status,
    })


# ── Post-load verification ────────────────────────────────────────────────────

def verify_counts(conn):
    print("\n[load_database] Final table row counts:")
    tables = [
        "supplier", "product", "product_supplier",
        "inventory_batch", "sale", "sale_item", "daily_demand",
    ]
    cur = conn.cursor()
    print(f"  {'Table':<25} {'Rows':>14}")
    print(f"  {'─'*25} {'─'*14}")
    for table in tables:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            print(f"  {table:<25} {count:>14,}")
        except Exception as e:
            print(f"  {table:<25} {'ERROR':>14}  ({e})")
    cur.close()


def print_load_summary():
    print("\n[load_database] ── Load Summary ──────────────────────────────────")
    header = f"  {'Table':<25} {'Staged':>10} {'Inserted':>10} {'Skipped':>10} {'Conflicts':>10}  Status"
    print(header)
    print("  " + "─" * (len(header) - 2))
    for r in LOAD_REPORT:
        print(
            f"  {r['table']:<25} "
            f"{str(r['staged']):>10} "
            f"{str(r['inserted']):>10} "
            f"{str(r['skipped']):>10} "
            f"{str(r['conflicts']):>10}  "
            f"{r['status']}"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("[load_database] Connecting …")
    conn = get_conn()
    print(f"  ✅ Connected to '{DB_NAME}'")

    try:
        load_table(conn, "supplier",         PROC_DIR / "supplier.csv",
                   pk_cols=["Supplier_ID"])

        load_table(conn, "product",           PROC_DIR / "product.csv",
                   pk_cols=["Product_ID"])

        load_table(conn, "product_supplier",  PROC_DIR / "product_supplier.csv",
                   pk_cols=["Product_ID", "Supplier_ID"])

        load_table(conn, "inventory_batch",   PROC_DIR / "inventory_batch.csv",
                   pk_cols=["Batch_ID"])

        load_table(conn, "sale",              PROC_DIR / "sale.csv",
                   pk_cols=["Bill_No"])

        load_table(conn, "sale_item",         PROC_DIR / "sale_item.csv",
                   pk_cols=["Sale_ID"],
                   chunk_size=CHUNK_SIZE)

        verify_counts(conn)
        print_load_summary()
        print("\n[load_database] Done.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
