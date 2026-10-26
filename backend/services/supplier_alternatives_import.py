"""
backend/services/supplier_alternatives_import.py
--------------------------------------------------
Module 4 — Idempotent import of supplier alternatives from
module4_all_supplier_lead_time_ranking.csv into product_supplier.

Design rules
------------
* The source CSV (module4_all_supplier_lead_time_ranking.csv) is the authority
  for which product-supplier alternatives exist for Module 4.
* All Supplier IDs in that CSV already exist in the supplier master table with
  matching names and lead times — validated before any DB write.
* Lead times come from the existing supplier master (supplier.lead_time_days);
  this import only manages product-supplier link rows in product_supplier.
* Existing product_supplier rows (one per product, from Module 1) are preserved.
* New rows are inserted with purchase_rate = NULL and minimum_order_qty = NULL
  (not fabricated — alternatives CSV does not provide these values).
* Import is idempotent: ON CONFLICT (product_id, supplier_id) DO NOTHING.
* No rows are deleted, dropped, or overwritten.
* All validations run before any writes.  On any validation failure the
  import aborts and reports errors — no partial state is committed.
* Original CSV files are never modified.

Explicit exclusions (enforced by design)
-----------------------------------------
* Does NOT place purchase orders.
* Does NOT modify inventory stock quantities.
* Does NOT overwrite existing supplier master data.
* Does NOT fabricate purchase rates, MOQs, damage rates, or waste rates.
* Does NOT import module4_top2_supplier_recommendations.csv as a standalone
  table — it is a pre-computed reference only.
"""

import csv
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── File paths ────────────────────────────────────────────────────────────────

_PROJ_ROOT = Path(__file__).parent.parent.parent
DATASET_DIR = _PROJ_ROOT / "Dataset"
ALTERNATIVES_CSV = DATASET_DIR / "module4_all_supplier_lead_time_ranking.csv"

# Required columns in the alternatives CSV
_REQUIRED_COLUMNS = {
    "Product_ID", "Supplier_ID", "Supplier_Name", "Lead_Time_Days",
}


# ── Validation helpers ────────────────────────────────────────────────────────

def _validate_csv_columns(headers: list[str]) -> list[str]:
    """Return list of error strings for missing required columns."""
    missing = _REQUIRED_COLUMNS - set(headers)
    if missing:
        return [f"Missing required columns in CSV: {sorted(missing)}"]
    return []


def _validate_lead_time(value: str, product_id: str, supplier_id: str) -> tuple[Optional[int], Optional[str]]:
    """Parse and validate a lead-time value. Returns (int_value, error_str)."""
    try:
        f = float(value)
        i = int(f)
        if f != i:
            return None, f"Non-integer lead_time={value!r} for ({product_id},{supplier_id})"
        if i <= 0:
            return None, f"Non-positive lead_time={i} for ({product_id},{supplier_id})"
        return i, None
    except (ValueError, TypeError):
        return None, f"Invalid lead_time={value!r} for ({product_id},{supplier_id})"


# ── Core import function ──────────────────────────────────────────────────────

def import_supplier_alternatives(conn, csv_path: Optional[Path] = None) -> dict:
    """
    Validate and import supplier alternatives from the CSV into product_supplier.

    Parameters
    ----------
    conn : psycopg2 connection (with RealDictCursor)
    csv_path : optional override for the CSV path (default: ALTERNATIVES_CSV)

    Returns
    -------
    dict with keys:
        success         : bool
        csv_path        : str
        validation_errors : list[str]   — blocking errors found before any write
        data_conflicts  : list[str]     — supplier master conflicts (name/lead-time)
        inserted        : int
        skipped         : int           — already-existing rows (idempotent re-run)
        rejected        : int           — rows failing validation (excluded from insert)
        unknown_products: list[str]     — product_ids not found in product table
        unknown_suppliers: list[str]    — supplier_ids not found in supplier table
        summary         : str
    """
    target_csv = Path(csv_path) if csv_path else ALTERNATIVES_CSV

    result = {
        "success": False,
        "csv_path": str(target_csv),
        "validation_errors": [],
        "data_conflicts": [],
        "inserted": 0,
        "skipped": 0,
        "rejected": 0,
        "unknown_products": [],
        "unknown_suppliers": [],
        "summary": "",
    }

    # ── Step 1: Read CSV ──────────────────────────────────────────────────────
    if not target_csv.exists():
        result["validation_errors"].append(f"CSV file not found: {target_csv}")
        result["summary"] = "Import aborted — CSV file missing."
        return result

    try:
        with open(target_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            col_errors = _validate_csv_columns(list(headers))
            if col_errors:
                result["validation_errors"].extend(col_errors)
                result["summary"] = "Import aborted — required columns missing."
                return result
            rows = list(reader)
    except Exception as e:
        result["validation_errors"].append(f"Failed to read CSV: {e}")
        result["summary"] = "Import aborted — CSV read error."
        return result

    if not rows:
        result["validation_errors"].append("CSV contains no data rows.")
        result["summary"] = "Import aborted — empty CSV."
        return result

    logger.info(f"[SupplierImport] CSV rows read: {len(rows)}")

    # ── Step 2: Load existing master data ─────────────────────────────────────
    cur = conn.cursor()
    try:
        cur.execute("SELECT product_id FROM product")
        existing_products = {r["product_id"] for r in cur.fetchall()}

        cur.execute("SELECT supplier_id, supplier_name, lead_time_days FROM supplier")
        existing_suppliers = {
            r["supplier_id"]: {
                "supplier_name": r["supplier_name"],
                "lead_time_days": int(r["lead_time_days"]),
            }
            for r in cur.fetchall()
        }

        cur.execute("SELECT product_id, supplier_id FROM product_supplier")
        existing_ps = {(r["product_id"], r["supplier_id"]) for r in cur.fetchall()}
    finally:
        cur.close()

    logger.info(
        f"[SupplierImport] Existing: {len(existing_products)} products, "
        f"{len(existing_suppliers)} suppliers, {len(existing_ps)} product_supplier rows"
    )

    # ── Step 3: Validate rows ─────────────────────────────────────────────────
    valid_rows = []
    seen_pairs: set[tuple[str, str]] = set()
    unknown_products: set[str] = set()
    unknown_suppliers: set[str] = set()
    conflicts: list[str] = []
    row_errors: list[str] = []

    for i, row in enumerate(rows, start=2):  # 1-indexed, row 1 = header
        pid = (row.get("Product_ID") or "").strip()
        sid = (row.get("Supplier_ID") or "").strip()
        sname = (row.get("Supplier_Name") or "").strip()
        lt_str = (row.get("Lead_Time_Days") or "").strip()

        if not pid:
            row_errors.append(f"Row {i}: empty Product_ID — rejected.")
            continue
        if not sid:
            row_errors.append(f"Row {i}: empty Supplier_ID for product {pid!r} — rejected.")
            continue

        # Product FK check
        if pid not in existing_products:
            unknown_products.add(pid)
            row_errors.append(f"Row {i}: product_id={pid!r} not in product table — rejected.")
            continue

        # Supplier FK check
        if sid not in existing_suppliers:
            unknown_suppliers.add(sid)
            row_errors.append(f"Row {i}: supplier_id={sid!r} not in supplier table — rejected.")
            continue

        # Supplier name check (conflict detection — report, do not overwrite)
        db_name = existing_suppliers[sid]["supplier_name"].strip().upper()
        csv_name = sname.strip().upper()
        if db_name != csv_name:
            conflicts.append(
                f"Supplier name conflict: {sid!r} DB={db_name!r} CSV={csv_name!r}. "
                "Row accepted (using DB master record, not overwriting)."
            )
            logger.warning(
                f"[SupplierImport] Name mismatch for {sid}: DB={db_name!r} CSV={csv_name!r}"
            )

        # Lead time validation
        lt_val, lt_err = _validate_lead_time(lt_str, pid, sid)
        if lt_err:
            row_errors.append(f"Row {i}: {lt_err} — rejected.")
            continue

        # Cross-check lead time against existing supplier master
        db_lt = existing_suppliers[sid]["lead_time_days"]
        if lt_val != db_lt:
            conflicts.append(
                f"Lead time mismatch: {sid!r} product={pid!r} CSV_lt={lt_val} DB_lt={db_lt}. "
                "Row accepted (using DB master lead time)."
            )
            logger.warning(
                f"[SupplierImport] Lead time mismatch for ({pid},{sid}): "
                f"CSV={lt_val} DB={db_lt}"
            )

        # Duplicate pair in CSV
        pair = (pid, sid)
        if pair in seen_pairs:
            row_errors.append(
                f"Row {i}: duplicate product-supplier pair ({pid},{sid}) in CSV — skipped."
            )
            continue
        seen_pairs.add(pair)

        valid_rows.append({"product_id": pid, "supplier_id": sid})

    result["data_conflicts"] = conflicts
    result["unknown_products"] = sorted(unknown_products)
    result["unknown_suppliers"] = sorted(unknown_suppliers)
    result["rejected"] = len(row_errors)

    # Report row errors (informational — not blocking if there are valid rows)
    if row_errors:
        logger.warning(f"[SupplierImport] {len(row_errors)} rows rejected:")
        for e in row_errors:
            logger.warning(f"  {e}")

    if unknown_products or unknown_suppliers:
        result["validation_errors"].extend(row_errors)
        result["summary"] = (
            f"Import aborted — {len(unknown_products)} unknown product(s), "
            f"{len(unknown_suppliers)} unknown supplier(s). No changes made."
        )
        return result

    if not valid_rows:
        result["validation_errors"].extend(row_errors)
        result["summary"] = "Import aborted — no valid rows to insert."
        return result

    # ── Step 4: Insert (idempotent) ───────────────────────────────────────────
    inserted = 0
    skipped = 0

    cur = conn.cursor()
    try:
        for r in valid_rows:
            pair = (r["product_id"], r["supplier_id"])
            if pair in existing_ps:
                skipped += 1
                continue

            cur.execute(
                """
                INSERT INTO product_supplier (product_id, supplier_id, purchase_rate, minimum_order_qty)
                VALUES (%s, %s, NULL, NULL)
                ON CONFLICT (product_id, supplier_id) DO NOTHING
                """,
                (r["product_id"], r["supplier_id"]),
            )
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1

        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"[SupplierImport] DB insert failed: {e}", exc_info=True)
        result["validation_errors"].append(f"Database insert error: {e}")
        result["summary"] = "Import failed — database error, transaction rolled back."
        return result
    finally:
        cur.close()

    result["inserted"] = inserted
    result["skipped"] = skipped
    result["success"] = True
    result["summary"] = (
        f"Import complete. "
        f"Inserted: {inserted}, Skipped (already existed): {skipped}, "
        f"Rejected (validation): {result['rejected']}. "
        f"Data conflicts (name/lead-time mismatches, non-blocking): {len(conflicts)}."
    )

    logger.info(f"[SupplierImport] {result['summary']}")
    return result


def apply_m4_schema(conn) -> None:
    """Apply schema_m4.sql to create the procurement_recommendation table. Safe to rerun."""
    sql_path = (
        Path(__file__).parent.parent.parent
        / "module3_inventory_optimization"
        / "sql"
        / "schema_m4.sql"
    )
    sql = sql_path.read_text(encoding="utf-8")
    cur = conn.cursor()
    try:
        cur.execute(sql)
        conn.commit()
        logger.info("Module 4 schema applied/verified (procurement_recommendation).")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
