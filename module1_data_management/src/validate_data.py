"""
validate_data.py
----------------
Phase 2: Detailed data validation.

Checks:
  - PK uniqueness for all tables
  - FK referential integrity (product→supplier, inventory→product/supplier, sales→product/batch)
  - Invalid date values
  - Negative / zero quantity fields
  - Inventory equation: Received = Sold + Current + Damaged + Wasted
  - Sales-to-inventory Sold_Quantity reconciliation
  - Price sanity: Selling_Price <= MRP
  - Duplicate Bill_No+Item_Seq_In_Bill in sales

Writes: reports/data_quality_report.md
"""

import sys, json
from pathlib import Path
from datetime import date

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    SUPPLIER_CSV, PRODUCT_CSV, INVENTORY_CSV, SALES_CSV,
    REPORTS_DIR, CHUNK_SIZE
)

ISSUES: list[dict] = []
STATS:  dict = {}


def log_issue(table: str, check: str, severity: str, count: int, detail: str = ""):
    ISSUES.append({"table": table, "check": check, "severity": severity,
                   "count": count, "detail": detail})
    tag = "❌" if severity == "ERROR" else "⚠️" if severity == "WARNING" else "ℹ️"
    print(f"  {tag} [{table}] {check}: {count}  {detail}")


def ok(table: str, check: str, detail: str = ""):
    print(f"  ✅ [{table}] {check}  {detail}")


# ── Load smaller files fully ──────────────────────────────────────────────────
def load_supplier():
    df = pd.read_csv(SUPPLIER_CSV, dtype=str, keep_default_na=False)
    df["Lead_Time_Days"] = pd.to_numeric(df["Lead_Time_Days"], errors="coerce")
    return df


def load_product():
    df = pd.read_csv(PRODUCT_CSV, dtype=str, keep_default_na=False)
    for col in ["Purchase_Rate","Cost_Price","Sale_Rate","MRP",
                "Minimum_Order_Qty","Reorder_Level","Maximum_Stock_Level",
                "Expected_Daily_Demand","Supplier_Lead_Time_Days"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_inventory():
    df = pd.read_csv(INVENTORY_CSV, dtype=str, keep_default_na=False)
    for col in ["Received_Quantity","Sold_Quantity","Current_Quantity",
                "Reserved_Quantity","Damaged_Quantity","Wasted_Quantity",
                "Stock_Value","Reorder_Level","Maximum_Stock_Level",
                "Supplier_Lead_Time_Days"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["Purchase_Date","Received_Date","Last_Updated"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


# ── Validation functions ──────────────────────────────────────────────────────
def validate_supplier(sup: pd.DataFrame):
    print("\n[validate_data] Supplier …")
    STATS["supplier_rows"] = len(sup)
    STATS["supplier_unique"] = sup["Supplier_ID"].nunique()

    dupes = len(sup) - sup["Supplier_ID"].nunique()
    if dupes:
        log_issue("Supplier", "Duplicate Supplier_ID", "ERROR", dupes)
    else:
        ok("Supplier", "Supplier_ID unique")

    blank_name = (sup["Supplier_Name"].str.strip() == "").sum()
    if blank_name:
        log_issue("Supplier", "Blank Supplier_Name", "WARNING", int(blank_name))
    else:
        ok("Supplier", "Supplier_Name all populated")

    lead_neg = (sup["Lead_Time_Days"] <= 0).sum()
    if lead_neg:
        log_issue("Supplier", "Lead_Time_Days <= 0", "WARNING", int(lead_neg))
    else:
        ok("Supplier", "Lead_Time_Days all positive")


def validate_product(prod: pd.DataFrame, sup: pd.DataFrame):
    print("\n[validate_data] Product …")
    STATS["product_rows"] = len(prod)
    STATS["product_unique"] = prod["Product_ID"].nunique()

    dupes = len(prod) - prod["Product_ID"].nunique()
    if dupes:
        log_issue("Product", "Duplicate Product_ID", "ERROR", dupes)
    else:
        ok("Product", "Product_ID unique")

    name_dupes = len(prod) - prod["Product_Name"].nunique()
    if name_dupes:
        log_issue("Product", "Duplicate Product_Name", "WARNING", int(name_dupes))
    else:
        ok("Product", "Product_Name all unique")

    # FK: Supplier_ID must exist in supplier
    valid_sups = set(sup["Supplier_ID"])
    bad_sup = (~prod["Supplier_ID"].isin(valid_sups)).sum()
    if bad_sup:
        log_issue("Product", "Invalid Supplier_ID FK", "ERROR", int(bad_sup))
    else:
        ok("Product", "All Supplier_IDs valid FK")

    # Price checks
    mrp_viol = (prod["Sale_Rate"] > prod["MRP"]).sum()
    if mrp_viol:
        log_issue("Product", "Sale_Rate > MRP", "WARNING", int(mrp_viol))
    else:
        ok("Product", "Sale_Rate <= MRP")

    neg_purchase = (prod["Purchase_Rate"] < 0).sum()
    if neg_purchase:
        log_issue("Product", "Negative Purchase_Rate", "ERROR", int(neg_purchase))
    else:
        ok("Product", "Purchase_Rate all non-negative")

    STATS["category_count"] = int(prod["Category"].nunique())
    STATS["brand_count"] = int(prod["Brand"].nunique())
    STATS["purchase_frequency"] = prod["Purchase_Frequency"].value_counts().to_dict()


def validate_inventory(inv: pd.DataFrame, prod: pd.DataFrame, sup: pd.DataFrame):
    print("\n[validate_data] Inventory …")
    STATS["inventory_rows"] = len(inv)
    STATS["inventory_batch_unique"] = inv["Batch_ID"].nunique()

    dupes = len(inv) - inv["Batch_ID"].nunique()
    if dupes:
        log_issue("Inventory", "Duplicate Batch_ID", "ERROR", dupes)
    else:
        ok("Inventory", "Batch_ID unique")

    # FK checks
    valid_prods = set(prod["Product_ID"])
    valid_sups  = set(sup["Supplier_ID"])

    bad_prod = (~inv["Product_ID"].isin(valid_prods)).sum()
    bad_sup  = (~inv["Supplier_ID"].isin(valid_sups)).sum()
    if bad_prod:
        log_issue("Inventory", "Invalid Product_ID FK", "ERROR", int(bad_prod))
    else:
        ok("Inventory", "All Product_IDs valid FK")
    if bad_sup:
        log_issue("Inventory", "Invalid Supplier_ID FK", "ERROR", int(bad_sup))
    else:
        ok("Inventory", "All Supplier_IDs valid FK")

    # Date checks
    bad_purchase  = inv["Purchase_Date"].isna().sum()
    bad_received  = inv["Received_Date"].isna().sum()
    if bad_purchase:
        log_issue("Inventory", "Invalid Purchase_Date", "WARNING", int(bad_purchase))
    else:
        ok("Inventory", "Purchase_Date all valid")
    if bad_received:
        log_issue("Inventory", "Invalid Received_Date", "WARNING", int(bad_received))
    else:
        ok("Inventory", "Received_Date all valid")

    # Lead time sanity: Received_Date >= Purchase_Date
    neg_lead = (inv["Received_Date"] < inv["Purchase_Date"]).sum()
    if neg_lead:
        log_issue("Inventory", "Received_Date < Purchase_Date", "ERROR", int(neg_lead))
    else:
        ok("Inventory", "Received_Date >= Purchase_Date for all batches")

    # Inventory equation: Received = Sold + Current + Damaged + Wasted
    inv["_check"] = (
        inv["Received_Quantity"] -
        inv["Sold_Quantity"] -
        inv["Current_Quantity"] -
        inv["Damaged_Quantity"] -
        inv["Wasted_Quantity"]
    )
    equation_errors = (inv["_check"].abs() > 0.01).sum()
    if equation_errors:
        log_issue("Inventory", "Inventory equation mismatch (Recv≠Sold+Cur+Dmg+Wst)",
                  "ERROR", int(equation_errors))
    else:
        ok("Inventory", "Inventory equation balances for all batches")

    neg_current = (inv["Current_Quantity"] < 0).sum()
    if neg_current:
        log_issue("Inventory", "Negative Current_Quantity", "ERROR", int(neg_current))
    else:
        ok("Inventory", "No negative Current_Quantity")

    reserved_gt_current = (inv["Reserved_Quantity"] > inv["Current_Quantity"]).sum()
    if reserved_gt_current:
        log_issue("Inventory", "Reserved_Quantity > Current_Quantity", "WARNING",
                  int(reserved_gt_current))
    else:
        ok("Inventory", "Reserved_Quantity <= Current_Quantity")

    STATS["inventory_date_min"] = str(inv["Received_Date"].min().date()) if not pd.isna(inv["Received_Date"].min()) else "N/A"
    STATS["inventory_date_max"] = str(inv["Received_Date"].max().date()) if not pd.isna(inv["Received_Date"].max()) else "N/A"

    return inv


def validate_sales_chunked(valid_prod_ids: set, valid_batch_ids: set):
    """Chunked validation of the 4M-row sales file."""
    print("\n[validate_data] Sales (chunked) …")

    total_rows = 0
    sale_id_seen = set()
    duplicate_sale_ids = 0
    invalid_product_fk = 0
    invalid_batch_fk = 0
    negative_qty = 0
    zero_qty = 0
    price_violations = 0
    invalid_dates = 0
    bill_seq_dupes = 0
    bill_header_inconsistencies = 0   # Bill_No with mismatched Sale_Date/Payment_Mode/Customer_ID
    date_min = None
    date_max = None
    bill_seq_seen = set()
    sample_neg_qty = []

    for chunk in pd.read_csv(SALES_CSV, dtype=str, keep_default_na=False,
                              chunksize=CHUNK_SIZE):
        total_rows += len(chunk)

        # Sale_ID duplicates
        dups = chunk["Sale_ID"][chunk["Sale_ID"].isin(sale_id_seen)]
        duplicate_sale_ids += len(dups)
        sale_id_seen.update(chunk["Sale_ID"].tolist())

        # FK checks
        invalid_product_fk += int((~chunk["Product_ID"].isin(valid_prod_ids)).sum())
        invalid_batch_fk   += int((~chunk["Batch_ID"].isin(valid_batch_ids)).sum())

        # Quantity
        qty = pd.to_numeric(chunk["Quantity_Sold"], errors="coerce")
        neg_mask = qty < 0
        negative_qty += int(neg_mask.sum())
        if neg_mask.any() and len(sample_neg_qty) < 5:
            sample_neg_qty.extend(chunk[neg_mask][["Sale_ID","Product_ID","Quantity_Sold"]].to_dict("records"))
        zero_qty += int((qty == 0).sum())

        # Price vs MRP
        sp  = pd.to_numeric(chunk["Selling_Price"], errors="coerce")
        mrp = pd.to_numeric(chunk["MRP"], errors="coerce")
        price_violations += int((sp > mrp + 0.01).sum())

        # Dates
        dates = pd.to_datetime(chunk["Sale_Date"], errors="coerce")
        invalid_dates += int(dates.isna().sum())
        mn = dates.min()
        mx = dates.max()
        if not pd.isna(mn):
            date_min = min(date_min, mn) if date_min else mn
        if not pd.isna(mx):
            date_max = max(date_max, mx) if date_max else mx

        # Bill_No + Item_Seq duplicate check
        key = chunk["Bill_No"] + "|" + chunk["Item_Seq_In_Bill"]
        new_dupes = key[key.isin(bill_seq_seen)]
        bill_seq_dupes += len(new_dupes)
        bill_seq_seen.update(key.tolist())

        # Bill_No header consistency (within this chunk)
        # Checks that all lines for the same Bill_No share the same
        # Sale_Date, Payment_Mode, and Customer_ID.
        # Limitation: bills that span a chunk boundary are not cross-chunk
        # verified here; validate in preprocess.py bill-header extraction.
        header_nunique = chunk.groupby("Bill_No")[
            ["Sale_Date", "Payment_Mode", "Customer_ID"]
        ].nunique()
        bill_header_inconsistencies += int(
            (header_nunique > 1).any(axis=1).sum()
        )

    STATS["sales_rows"] = total_rows
    STATS["sales_sale_id_unique"] = len(sale_id_seen)
    STATS["sales_date_min"] = str(date_min.date()) if date_min else "N/A"
    STATS["sales_date_max"] = str(date_max.date()) if date_max else "N/A"

    STATS["bill_header_inconsistencies"] = bill_header_inconsistencies

    checks = [
        ("Duplicate Sale_ID",              duplicate_sale_ids,          "ERROR"),
        ("Invalid Product_ID FK",          invalid_product_fk,          "ERROR"),
        ("Invalid Batch_ID FK",            invalid_batch_fk,            "ERROR"),
        ("Negative Quantity_Sold",         negative_qty,                "WARNING"),
        ("Zero Quantity_Sold",             zero_qty,                    "WARNING"),
        ("Selling_Price > MRP",            price_violations,            "WARNING"),
        ("Invalid Sale_Date",              invalid_dates,               "ERROR"),
        ("Duplicate Bill_No+Seq",          bill_seq_dupes,              "ERROR"),
        ("Bill header inconsistency",      bill_header_inconsistencies, "ERROR"),
    ]
    for check, cnt, sev in checks:
        if cnt:
            detail = f"  (sample: {sample_neg_qty[:2]})" if check.startswith("Negative") else ""
            log_issue("Sales", check, sev, cnt, detail)
        else:
            ok("Sales", check)

    return total_rows


def validate_sold_qty_reconciliation(inv: pd.DataFrame):
    """
    Aggregate sales Sold_Qty by Batch_ID and compare to inventory Sold_Quantity.
    Done in chunks to stay memory-safe.
    """
    print("\n[validate_data] Sales ↔ Inventory Sold_Quantity reconciliation …")

    batch_sales = {}
    for chunk in pd.read_csv(SALES_CSV, usecols=["Batch_ID","Quantity_Sold"],
                              dtype=str, keep_default_na=False, chunksize=CHUNK_SIZE):
        chunk["Quantity_Sold"] = pd.to_numeric(chunk["Quantity_Sold"], errors="coerce").fillna(0)
        agg = chunk.groupby("Batch_ID")["Quantity_Sold"].sum()
        for bid, qty in agg.items():
            batch_sales[bid] = batch_sales.get(bid, 0) + qty

    sales_df = pd.DataFrame(list(batch_sales.items()), columns=["Batch_ID","sales_qty"])
    merged = inv[["Batch_ID","Sold_Quantity"]].merge(sales_df, on="Batch_ID", how="left")
    merged["sales_qty"] = merged["sales_qty"].fillna(0)
    mismatches = (abs(merged["Sold_Quantity"] - merged["sales_qty"]) > 0.01).sum()

    STATS["sold_qty_mismatches"] = int(mismatches)
    if mismatches:
        log_issue("Inventory↔Sales", "Sold_Quantity mismatch", "ERROR", int(mismatches))
    else:
        ok("Inventory↔Sales", "Sold_Quantity reconciles with sales")


# ── Report writer ─────────────────────────────────────────────────────────────
def write_report(out_path: Path):
    lines = [
        "# Data Quality Report",
        "",
        f"> Generated by `validate_data.py`  ",
        f"> Date: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary Statistics",
        "",
        f"| Metric | Value |",
        f"| --- | --- |",
    ]
    for k, v in STATS.items():
        lines.append(f"| {k} | {v} |")

    lines += ["", "## Validation Issues", ""]
    if not ISSUES:
        lines.append("✅ **No issues found.** All checks passed.")
    else:
        lines.append("| Table | Check | Severity | Count | Detail |")
        lines.append("| --- | --- | --- | --- | --- |")
        for issue in ISSUES:
            lines.append(
                f"| {issue['table']} | {issue['check']} | {issue['severity']} "
                f"| {issue['count']} | {issue.get('detail', '')} |"
            )

    lines += [
        "", "## Known Limitations", "",
        "- Dataset is a synthetic simulation (Robins Hypermarket threshold-based). "
          "Lead times are modeled, not confirmed operational values.",
        "- Inventory batches represent monthly replenishment windows, not individual purchase orders.",
        "- Customer IDs are present but no customer master table exists; customer-level analysis is not supported.",
        "- No returns/refund transaction type is present in the sales file.",
        "- Credit_Days in supplier table represents payment terms, not delivery lead time.",
        "- `Supplier_Lead_Time_Days` in both Product and Inventory tables are modeled values.",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[validate_data] Report written -> {out_path}")


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    print("[validate_data] Loading reference data …")
    sup  = load_supplier()
    prod = load_product()
    inv  = load_inventory()

    validate_supplier(sup)
    validate_product(prod, sup)
    inv = validate_inventory(inv, prod, sup)

    valid_prod_ids  = set(prod["Product_ID"])
    valid_batch_ids = set(inv["Batch_ID"])
    validate_sales_chunked(valid_prod_ids, valid_batch_ids)
    validate_sold_qty_reconciliation(inv)

    report_path = REPORTS_DIR / "data_quality_report.md"
    write_report(report_path)

    json_path = REPORTS_DIR / "validation_stats.json"
    json_path.write_text(json.dumps({"stats": STATS, "issues": ISSUES}, indent=2),
                          encoding="utf-8")
    print("[validate_data] Done.")
    return ISSUES, STATS


if __name__ == "__main__":
    main()
