"""
preprocess.py
-------------
Phase 3: Preprocessing and Normalization.

Reads the raw CSVs and produces processed outputs in data/processed/:

  supplier.csv            – 3 fields: Supplier_ID, Supplier_Name, Lead_Time_Days
  product.csv             – 14 fields per spec
  product_supplier.csv    – junction table (Product_ID, Supplier_ID, Purchase_Rate, Minimum_Order_Qty)
  inventory_batch.csv     – all 20 source fields + derived Lead_Time_Date_Difference_Days
  sale.csv                – 1 row per Bill_No (Bill_No, Sale_Date, Payment_Mode,
                            Customer_ID, Items_In_Bill)
  sale_item.csv           – 12 line-item fields per spec
  supplier_metrics.csv    – per-supplier aggregated metrics (modelled/derived values only)

Also generates: reports/schema_mapping.md

Design notes
------------
- Items_In_Bill is retained in sale.csv (informational count of lines per bill).
- Bill_Total_Amount is excluded from sale_item (bill-level aggregate, not line-level).
- Minimum_Stock_Level exists in the source product CSV but is not in the spec retain
  list; it is excluded from product.csv and documented here.
- Lead_Time_Date_Difference_Days is a date-arithmetic value
  (Received_Date – Purchase_Date) in days. It is NOT a verified operational lead time.
- All supplier-metric values are modelled or date-derived; no operational reliability
  claims are made.
"""

import sys
import io
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    SUPPLIER_CSV, PRODUCT_CSV, INVENTORY_CSV, SALES_CSV,
    PROC_DIR, REPORTS_DIR, CHUNK_SIZE,
)

SCHEMA_MAPPING: list[dict] = []   # populated as each table is processed
SUMMARY: dict = {}


def log_mapping(table: str, source_cols: list, target_cols: list, notes: str = ""):
    SCHEMA_MAPPING.append({
        "table": table,
        "source_columns": source_cols,
        "target_columns": target_cols,
        "notes": notes,
    })


# ── A. Supplier ───────────────────────────────────────────────────────────────

def process_supplier() -> pd.DataFrame:
    print("[preprocess] A. Supplier …")
    df = pd.read_csv(SUPPLIER_CSV, dtype=str, keep_default_na=False)

    retain = ["Supplier_ID", "Supplier_Name", "Lead_Time_Days"]
    missing = [c for c in retain if c not in df.columns]
    if missing:
        raise ValueError(f"Supplier CSV missing expected columns: {missing}")

    out = df[retain].copy()
    out["Lead_Time_Days"] = pd.to_numeric(out["Lead_Time_Days"], errors="coerce")

    out_path = PROC_DIR / "supplier.csv"
    out.to_csv(out_path, index=False, na_rep="")
    print(f"  Wrote {len(out):,} rows -> {out_path}")

    excluded = [c for c in df.columns if c not in retain]
    log_mapping(
        "supplier",
        list(df.columns),
        retain,
        f"Excluded {len(excluded)} contact/tax/administrative fields: "
        f"{excluded}. "
        "Preserved intact in raw CSV.",
    )
    SUMMARY["supplier_rows"] = len(out)
    return out


# ── B. Product ────────────────────────────────────────────────────────────────

def process_product() -> pd.DataFrame:
    print("[preprocess] B. Product …")
    df = pd.read_csv(PRODUCT_CSV, dtype=str, keep_default_na=False)

    retain = [
        "Product_ID", "Product_Code", "Product_Name", "Brand", "Category",
        "Unit", "Purchase_Rate", "MRP", "Sale_Rate", "Reorder_Level",
        "Maximum_Stock_Level", "Minimum_Order_Qty", "Purchase_Frequency",
        "Expected_Daily_Demand",
    ]
    missing = [c for c in retain if c not in df.columns]
    if missing:
        raise ValueError(f"Product CSV missing expected columns: {missing}")

    out = df[retain].copy()

    numeric_cols = [
        "Purchase_Rate", "MRP", "Sale_Rate", "Reorder_Level",
        "Maximum_Stock_Level", "Minimum_Order_Qty", "Expected_Daily_Demand",
    ]
    for col in numeric_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out_path = PROC_DIR / "product.csv"
    out.to_csv(out_path, index=False, na_rep="")
    print(f"  Wrote {len(out):,} rows -> {out_path}")

    excluded = [c for c in df.columns if c not in retain]
    log_mapping(
        "product",
        list(df.columns),
        retain,
        f"Excluded {len(excluded)} fields: {excluded}. "
        "Minimum_Stock_Level excluded (not in spec retain list; present in raw). "
        "Tax, HSN, barcode, sub-unit, and flag fields excluded.",
    )
    SUMMARY["product_rows"] = len(out)
    return out


# ── C. Product_Supplier (junction table) ─────────────────────────────────────

def process_product_supplier() -> pd.DataFrame:
    print("[preprocess] C. Product_Supplier …")
    df = pd.read_csv(PRODUCT_CSV, dtype=str, keep_default_na=False)

    retain = ["Product_ID", "Supplier_ID", "Purchase_Rate", "Minimum_Order_Qty"]
    out = df[retain].copy()
    out["Purchase_Rate"] = pd.to_numeric(out["Purchase_Rate"], errors="coerce")
    out["Minimum_Order_Qty"] = pd.to_numeric(out["Minimum_Order_Qty"], errors="coerce")

    before = len(out)
    out = out.drop_duplicates(subset=["Product_ID", "Supplier_ID"])
    after = len(out)
    if before != after:
        print(f"  Deduplication: {before - after} duplicate (Product_ID, Supplier_ID) pairs removed")
    else:
        print(f"  No duplicate (Product_ID, Supplier_ID) pairs found")

    out_path = PROC_DIR / "product_supplier.csv"
    out.to_csv(out_path, index=False, na_rep="")
    print(f"  Wrote {len(out):,} rows -> {out_path}")

    log_mapping(
        "product_supplier",
        list(df.columns),
        retain,
        "Junction table derived from product.csv. Composite PK: (Product_ID, Supplier_ID). "
        "Current data has one supplier per product; junction design is preserved to support "
        "multiple suppliers per product in later modules (Procurement Agent).",
    )
    SUMMARY["product_supplier_rows"] = len(out)
    return out


# ── D. Inventory_Batch ────────────────────────────────────────────────────────

def process_inventory_batch() -> pd.DataFrame:
    print("[preprocess] D. Inventory_Batch …")
    df = pd.read_csv(INVENTORY_CSV, dtype=str, keep_default_na=False)

    source_cols = list(df.columns)
    print(f"  Source columns ({len(source_cols)}): {source_cols}")

    # Parse dates for the derived field
    df["Purchase_Date"] = pd.to_datetime(df["Purchase_Date"], errors="coerce")
    df["Received_Date"] = pd.to_datetime(df["Received_Date"], errors="coerce")

    # Derived field: date-arithmetic difference only
    df["Lead_Time_Date_Difference_Days"] = (
        df["Received_Date"] - df["Purchase_Date"]
    ).dt.days.astype("Int64")   # nullable integer

    # Re-format dates as plain date strings for clean CSV output
    df["Purchase_Date"] = df["Purchase_Date"].dt.strftime("%Y-%m-%d")
    df["Received_Date"] = df["Received_Date"].dt.strftime("%Y-%m-%d")

    # Parse numeric columns for downstream use
    num_cols = [
        "Received_Quantity", "Sold_Quantity", "Current_Quantity",
        "Reserved_Quantity", "Damaged_Quantity", "Wasted_Quantity",
        "Selling_Price", "MRP", "Stock_Value",
        "Reorder_Level", "Maximum_Stock_Level", "Supplier_Lead_Time_Days",
    ]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Parse Last_Updated as date
    if "Last_Updated" in df.columns:
        df["Last_Updated"] = pd.to_datetime(
            df["Last_Updated"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")

    out_path = PROC_DIR / "inventory_batch.csv"
    df.to_csv(out_path, index=False, na_rep="")
    print(f"  Wrote {len(df):,} rows -> {out_path}")
    print(f"  Lead_Time_Date_Difference_Days: "
          f"min={df['Lead_Time_Date_Difference_Days'].min()}, "
          f"max={df['Lead_Time_Date_Difference_Days'].max()}")

    target_cols = source_cols + ["Lead_Time_Date_Difference_Days"]
    log_mapping(
        "inventory_batch",
        source_cols,
        target_cols,
        "All 20 source columns retained. "
        "Lead_Time_Date_Difference_Days is a derived field: "
        "(Received_Date – Purchase_Date) in integer days. "
        "This is a date-arithmetic value, NOT a verified operational delivery record. "
        "Supplier_Lead_Time_Days is the modelled lead time copied from the supplier model.",
    )
    SUMMARY["inventory_batch_rows"] = len(df)
    return df


# ── E. Sale (bill headers, one row per Bill_No) ───────────────────────────────

def process_sale() -> tuple[pd.DataFrame, int]:
    """
    Extracts one header row per Bill_No from the 4.3 M-row sales file.

    Reads only the 5 header columns per chunk to minimise memory usage.
    Validates within-chunk Bill_No header consistency (Sale_Date, Payment_Mode,
    Customer_ID must be the same for every line in the same bill).

    Cross-chunk consistency for bills that span a chunk boundary is not verified
    here (very unlikely in correctly generated data; validate_data.py performs the
    full Sale_ID / Bill+Seq uniqueness check).
    """
    print("[preprocess] E. Sale (bill headers, chunked) …")

    header_cols = ["Bill_No", "Sale_Date", "Payment_Mode", "Customer_ID", "Items_In_Bill"]
    bill_headers: dict[str, tuple] = {}   # bill_no -> (date, mode, cust, items)
    inconsistencies = 0
    total_rows = 0

    for chunk in pd.read_csv(
        SALES_CSV,
        usecols=header_cols,
        dtype=str,
        keep_default_na=False,
        chunksize=CHUNK_SIZE,
    ):
        total_rows += len(chunk)

        # Within-chunk consistency: every line in the same bill should share date/mode/cust
        check_cols = ["Sale_Date", "Payment_Mode", "Customer_ID"]
        nunique = chunk.groupby("Bill_No")[check_cols].nunique()
        intra_bad = int((nunique > 1).any(axis=1).sum())
        inconsistencies += intra_bad

        # Store first occurrence of each Bill_No seen in this chunk
        first = chunk.drop_duplicates("Bill_No").set_index("Bill_No")
        for bill_no, row in first.iterrows():
            val = (row["Sale_Date"], row["Payment_Mode"],
                   row["Customer_ID"], row["Items_In_Bill"])
            if bill_no not in bill_headers:
                bill_headers[bill_no] = val
            else:
                existing_val = bill_headers[bill_no]
                # Compare Sale_Date, Payment_Mode, and Customer_ID.
                if existing_val[:3] != val[:3]:
                    inconsistencies += 1

        print(f"  … {total_rows:,} rows read, {len(bill_headers):,} unique bills", end="\r")

    print()

    if inconsistencies:
        print(f"  ⚠️  {inconsistencies} within-chunk bill header inconsistencies detected")
    else:
        print(f"  ✅ All bill headers consistent within chunks")

    records = [
        {
            "Bill_No": bn,
            "Sale_Date": v[0],
            "Payment_Mode": v[1],
            "Customer_ID": v[2],
            "Items_In_Bill": v[3],
        }
        for bn, v in bill_headers.items()
    ]
    out = pd.DataFrame(records)
    out["Items_In_Bill"] = pd.to_numeric(out["Items_In_Bill"], errors="coerce")

    out_path = PROC_DIR / "sale.csv"
    out.to_csv(out_path, index=False, na_rep="")
    print(f"  Wrote {len(out):,} unique bill headers -> {out_path}")
    print(f"  (from {total_rows:,} total sales rows)")

    log_mapping(
        "sale",
        header_cols,
        list(out.columns),
        f"One row per Bill_No. {len(out):,} unique bills extracted from "
        f"{total_rows:,} sales rows. "
        "Items_In_Bill retained as informational count of line items per bill. "
        f"Bill header inconsistencies detected: {inconsistencies}. "
        "Customer_ID retained as nullable VARCHAR (no customer master table exists).",
    )
    SUMMARY["sale_rows"] = len(out)
    SUMMARY["bill_header_inconsistencies"] = inconsistencies
    return out, inconsistencies


# ── F. Sale_Item (line items) ─────────────────────────────────────────────────

def process_sale_item() -> int:
    """
    Extracts line-item fields from the 4.3 M-row sales file.

    Reads and writes in CHUNK_SIZE chunks to stay memory-safe.
    Bill_Total_Amount and Items_In_Bill are excluded (bill-level, not line-level).
    Sale_Date, Payment_Mode, Customer_ID are in the sale header table.
    """
    print("[preprocess] F. Sale_Item (chunked) …")

    retain = [
        "Sale_ID", "Bill_No", "Product_ID", "Batch_ID",
        "Item_Seq_In_Bill", "Quantity_Sold", "Selling_Price",
        "MRP", "Discount_Amount", "Tax_Rate", "Tax_Amount", "Total_Amount",
    ]

    out_path = PROC_DIR / "sale_item.csv"
    first_chunk = True
    total_rows = 0

    for chunk in pd.read_csv(
        SALES_CSV,
        usecols=retain,
        dtype=str,
        keep_default_na=False,
        chunksize=CHUNK_SIZE,
    ):
        out = chunk[retain]
        mode = "w" if first_chunk else "a"
        out.to_csv(out_path, index=False, mode=mode, header=first_chunk, na_rep="")
        total_rows += len(out)
        first_chunk = False
        print(f"  … {total_rows:,} rows written", end="\r")

    print(f"\n  Wrote {total_rows:,} rows -> {out_path}")

    all_source_cols = [
        "Sale_ID", "Bill_No", "Item_Seq_In_Bill", "Items_In_Bill",
        "Sale_Date", "Product_ID", "Batch_ID", "Quantity_Sold",
        "MRP", "Selling_Price", "Discount_Amount", "Tax_Rate",
        "Tax_Amount", "Total_Amount", "Bill_Total_Amount",
        "Payment_Mode", "Customer_ID",
    ]
    log_mapping(
        "sale_item",
        all_source_cols,
        retain,
        "Excluded: Bill_Total_Amount (bill-level aggregate, not line-level), "
        "Items_In_Bill (bill-level count, in sale table), "
        "Sale_Date / Payment_Mode / Customer_ID (bill header, in sale table). "
        "Each row represents one product line within a bill.",
    )
    SUMMARY["sale_item_rows"] = total_rows
    return total_rows


# ── G. Supplier Metrics ───────────────────────────────────────────────────────

def process_supplier_metrics(inv_df: pd.DataFrame) -> pd.DataFrame:
    """
    Computes per-supplier aggregate metrics from inventory_batch data.

    IMPORTANT: ALL values are either modelled synthetic values or date-derived
    arithmetic. None of the following are claimed or supported:
      - On-time delivery rate
      - Fill rate
      - Verified defect rate
      - Actual supplier reliability
    Supplier ranking is handled by Module 4 using recorded lead time only.
    """
    print("[preprocess] G. Supplier Metrics …")

    # Ensure numeric types
    for col in ["Received_Quantity", "Sold_Quantity", "Damaged_Quantity",
                "Wasted_Quantity", "Supplier_Lead_Time_Days",
                "Lead_Time_Date_Difference_Days"]:
        if col in inv_df.columns:
            inv_df[col] = pd.to_numeric(inv_df[col], errors="coerce")

    grp = inv_df.groupby("Supplier_ID")

    metrics = pd.DataFrame({
        "Batch_Count": grp["Batch_ID"].count(),
        "Total_Received_Quantity": grp["Received_Quantity"].sum(),
        "Total_Sold_Quantity": grp["Sold_Quantity"].sum(),
        "Total_Damaged_Quantity": grp["Damaged_Quantity"].sum(),
        "Total_Wasted_Quantity": grp["Wasted_Quantity"].sum(),
        "Avg_Lead_Time_Date_Diff_Days": grp["Lead_Time_Date_Difference_Days"].mean().round(4),
        "Modeled_Lead_Time_Days": grp["Supplier_Lead_Time_Days"].first(),
    }).reset_index()

    received = metrics["Total_Received_Quantity"].replace(0, np.nan)
    metrics["Damage_Rate"] = (
        metrics["Total_Damaged_Quantity"] / received
    ).round(6)
    metrics["Waste_Rate"] = (
        metrics["Total_Wasted_Quantity"] / received
    ).round(6)

    metrics["Metric_Caveats"] = (
        "Damage_Rate and Waste_Rate are ratios over total Received_Quantity. "
        "Avg_Lead_Time_Date_Diff_Days is the arithmetic mean of (Received_Date - Purchase_Date). "
        "Modeled_Lead_Time_Days is the synthetic lead-time value from the supplier master. "
        "NONE of these values are verified operational performance records."
    )

    out_path = PROC_DIR / "supplier_metrics.csv"
    metrics.to_csv(out_path, index=False, na_rep="")
    print(f"  Wrote {len(metrics):,} supplier metric rows -> {out_path}")

    log_mapping(
        "supplier_metrics",
        ["inventory_batch (aggregated)"],
        list(metrics.columns),
        "All values are modelled or date-derived. "
        "Damage_Rate = Damaged_Qty / Received_Qty. "
        "Waste_Rate = Wasted_Qty / Received_Qty. "
        "No on-time delivery, fill rate, or verified defect rate is claimed.",
    )
    SUMMARY["supplier_metrics_rows"] = len(metrics)
    return metrics


# ── Schema Mapping Report ─────────────────────────────────────────────────────

def write_schema_mapping_report():
    lines = [
        "# Schema Mapping Report",
        "",
        "> Generated by `preprocess.py`  ",
        f"> Date: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Overview",
        "",
        "This report documents the transformation from source CSV columns to "
        "processed (target) columns for each table in the relational schema.",
        "",
    ]

    for entry in SCHEMA_MAPPING:
        lines += [f"---", f"### `{entry['table']}`", ""]
        if entry["notes"]:
            lines += [f"> {entry['notes']}", ""]

        src = entry["source_columns"]
        tgt = entry["target_columns"]

        if isinstance(src, list) and isinstance(tgt, list):
            lines += ["| # | Source Column | Target Column | Action |"]
            lines += ["| --- | --- | --- | --- |"]
            all_src = set(src)
            all_tgt = set(tgt)
            i = 0
            for s in src:
                action = "RETAIN" if s in all_tgt else "EXCLUDE"
                lines.append(f"| {i+1} | `{s}` | `{s if action == 'RETAIN' else '—'}` | {action} |")
                i += 1
            for t in tgt:
                if t not in all_src:
                    lines.append(f"| {i+1} | *(derived)* | `{t}` | DERIVE |")
                    i += 1
        else:
            lines += [f"| Source | Target | Notes |"]
            lines += [f"| --- | --- | --- |"]
            lines.append(f"| {src} | {tgt} | — |")

        lines.append("")

    out_path = REPORTS_DIR / "schema_mapping.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[preprocess] Schema mapping report -> {out_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> dict:
    print("[preprocess] Starting preprocessing pipeline …\n")

    sup = process_supplier()
    prod = process_product()
    ps = process_product_supplier()
    inv = process_inventory_batch()
    sale, bill_inconsistencies = process_sale()
    sale_item_rows = process_sale_item()
    metrics = process_supplier_metrics(inv)

    write_schema_mapping_report()

    print("\n[preprocess] ── Summary ──────────────────────────────────────────")
    print(f"  supplier.csv         : {len(sup):>10,} rows")
    print(f"  product.csv          : {len(prod):>10,} rows")
    print(f"  product_supplier.csv : {len(ps):>10,} rows")
    print(f"  inventory_batch.csv  : {len(inv):>10,} rows")
    print(f"  sale.csv             : {len(sale):>10,} unique bills")
    print(f"  sale_item.csv        : {sale_item_rows:>10,} rows")
    print(f"  supplier_metrics.csv : {len(metrics):>10,} rows")
    print(f"  Bill header inconsistencies: {bill_inconsistencies}")
    print("[preprocess] Done.")

    return SUMMARY


if __name__ == "__main__":
    main()
