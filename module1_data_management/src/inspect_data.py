"""
inspect_data.py
---------------
Phase 1: Source-data inventory & profiling.

Reads every source CSV, reports:
  - Row counts, column names, inferred dtypes
  - Missing-value counts
  - Numeric range statistics
  - Duplicate primary-key checks
  - Date range
  - Sample rows

Writes:   reports/data_profile.md
Does NOT modify or load any data.
"""

import sys, os, csv, json
from pathlib import Path

import pandas as pd
import numpy as np

# Allow running from any cwd
sys.path.insert(0, str(Path(__file__).parent.parent))
from config.settings import (
    SUPPLIER_CSV, PRODUCT_CSV, INVENTORY_CSV, SALES_CSV,
    REPORTS_DIR, CHUNK_SIZE
)


# ── helpers ───────────────────────────────────────────────────────────────────
def md_table(df: pd.DataFrame) -> str:
    lines = ["| " + " | ".join(str(c) for c in df.columns) + " |"]
    lines.append("| " + " | ".join("---" for _ in df.columns) + " |")
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(lines)


def profile_csv(path: Path, pk_col: str | None = None,
                date_cols: list[str] | None = None,
                numeric_cols: list[str] | None = None,
                chunk_pk: str | None = None,
                large: bool = False) -> dict:
    """
    Profile a CSV file.
    large=True uses chunked reading to avoid OOM on 4M-row sales file.
    """
    result: dict = {"path": str(path), "name": path.name}

    if not large:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
        result["row_count"] = len(df)
        result["columns"] = list(df.columns)
        result["col_count"] = len(df.columns)

        # Missing values (empty string treated as missing in source)
        miss = {}
        for col in df.columns:
            n = (df[col].str.strip() == "").sum() + df[col].isna().sum()
            if n:
                miss[col] = int(n)
        result["missing"] = miss

        # PK duplicate check
        if pk_col:
            result["pk_col"] = pk_col
            result["pk_unique"] = int(df[pk_col].nunique())
            result["pk_duplicates"] = int(len(df) - df[pk_col].nunique())

        # Date range
        if date_cols:
            for dc in date_cols:
                if dc in df.columns:
                    dates = pd.to_datetime(df[dc], errors="coerce")
                    result[f"{dc}_min"] = str(dates.min().date()) if not pd.isna(dates.min()) else "N/A"
                    result[f"{dc}_max"] = str(dates.max().date()) if not pd.isna(dates.max()) else "N/A"
                    result[f"{dc}_invalid"] = int(dates.isna().sum())

        # Numeric stats
        if numeric_cols:
            stats = {}
            for nc in numeric_cols:
                if nc in df.columns:
                    s = pd.to_numeric(df[nc], errors="coerce")
                    stats[nc] = {
                        "min": float(s.min()) if not pd.isna(s.min()) else None,
                        "max": float(s.max()) if not pd.isna(s.max()) else None,
                        "mean": round(float(s.mean()), 4) if not pd.isna(s.mean()) else None,
                        "nulls": int(s.isna().sum()),
                        "negatives": int((s < 0).sum()),
                        "zeros": int((s == 0).sum()),
                    }
            result["numeric_stats"] = stats

        # Sample rows
        result["sample_rows"] = df.head(3).to_dict(orient="records")

    else:
        # Chunked reading for large file
        row_count = 0
        col_sample = None
        pk_seen = set()
        pk_dupes = 0
        date_min = {}
        date_max = {}
        date_invalid = {}
        num_min = {}
        num_max = {}
        num_neg = {}
        num_zero = {}
        miss_counts = {}
        sample_rows = []

        for chunk in pd.read_csv(path, dtype=str, keep_default_na=False,
                                  chunksize=CHUNK_SIZE):
            if col_sample is None:
                col_sample = list(chunk.columns)
                for col in col_sample:
                    miss_counts[col] = 0
                if date_cols:
                    for dc in date_cols:
                        date_min[dc] = None
                        date_max[dc] = None
                        date_invalid[dc] = 0
                if numeric_cols:
                    for nc in numeric_cols:
                        num_min[nc] = None
                        num_max[nc] = None
                        num_neg[nc] = 0
                        num_zero[nc] = 0

            row_count += len(chunk)

            for col in col_sample:
                miss_counts[col] += int(
                    (chunk[col].str.strip() == "").sum() + chunk[col].isna().sum()
                )

            if chunk_pk and chunk_pk in chunk.columns:
                dups = chunk[chunk_pk][chunk[chunk_pk].isin(pk_seen)]
                pk_dupes += len(dups)
                pk_seen.update(chunk[chunk_pk].tolist())

            if date_cols:
                for dc in date_cols:
                    if dc in chunk.columns:
                        d = pd.to_datetime(chunk[dc], errors="coerce")
                        date_invalid[dc] += int(d.isna().sum())
                        mn = d.min()
                        mx = d.max()
                        if not pd.isna(mn):
                            date_min[dc] = min(date_min[dc], mn) if date_min[dc] else mn
                        if not pd.isna(mx):
                            date_max[dc] = max(date_max[dc], mx) if date_max[dc] else mx

            if numeric_cols:
                for nc in numeric_cols:
                    if nc in chunk.columns:
                        s = pd.to_numeric(chunk[nc], errors="coerce")
                        mn = s.min()
                        mx = s.max()
                        if not pd.isna(mn):
                            num_min[nc] = min(num_min[nc], mn) if num_min[nc] is not None else mn
                        if not pd.isna(mx):
                            num_max[nc] = max(num_max[nc], mx) if num_max[nc] is not None else mx
                        num_neg[nc] += int((s < 0).sum())
                        num_zero[nc] += int((s == 0).sum())

            if len(sample_rows) < 3:
                sample_rows.extend(chunk.head(3 - len(sample_rows)).to_dict(orient="records"))

        result["row_count"] = row_count
        result["columns"] = col_sample
        result["col_count"] = len(col_sample) if col_sample else 0
        result["missing"] = {k: v for k, v in miss_counts.items() if v > 0}
        result["pk_col"] = chunk_pk
        result["pk_duplicates"] = pk_dupes
        result["pk_unique"] = len(pk_seen)
        if date_cols:
            for dc in date_cols:
                result[f"{dc}_min"] = str(date_min[dc].date()) if date_min.get(dc) else "N/A"
                result[f"{dc}_max"] = str(date_max[dc].date()) if date_max.get(dc) else "N/A"
                result[f"{dc}_invalid"] = date_invalid.get(dc, 0)
        if numeric_cols:
            result["numeric_stats"] = {
                nc: {
                    "min": float(num_min[nc]) if num_min[nc] is not None else None,
                    "max": float(num_max[nc]) if num_max[nc] is not None else None,
                    "negatives": num_neg[nc],
                    "zeros": num_zero[nc],
                }
                for nc in numeric_cols
            }
        result["sample_rows"] = sample_rows

    return result


def write_report(profiles: list[dict], out_path: Path):
    lines = [
        "# Source Data Profile Report",
        "",
        "> Generated by `inspect_data.py`  ",
        f"> Date: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    for p in profiles:
        lines += [f"---", f"## {p['name']}", ""]
        lines += [f"- **Path:** `{p['path']}`"]
        lines += [f"- **Rows:** {p['row_count']:,}"]
        lines += [f"- **Columns:** {p['col_count']}"]
        lines += [""]

        lines += ["### Columns"]
        for i, col in enumerate(p.get("columns", []), 1):
            lines.append(f"{i}. `{col}`")
        lines += [""]

        if p.get("pk_col"):
            lines += ["### Primary Key"]
            lines += [f"- **Column:** `{p['pk_col']}`"]
            lines += [f"- **Unique values:** {p.get('pk_unique', 'N/A'):,}"]
            lines += [f"- **Duplicates:** {p.get('pk_duplicates', 'N/A')}"]
            lines += [""]

        if p.get("missing"):
            lines += ["### Missing Values"]
            for col, cnt in p["missing"].items():
                lines.append(f"- `{col}`: {cnt:,}")
            lines += [""]

        # Date ranges
        for col in p.get("columns", []):
            mn_key = f"{col}_min"
            if mn_key in p:
                lines += [f"### Date Range: `{col}`"]
                lines += [f"- Min: {p[mn_key]}"]
                lines += [f"- Max: {p.get(f'{col}_max', 'N/A')}"]
                lines += [f"- Invalid dates: {p.get(f'{col}_invalid', 0)}"]
                lines += [""]

        if p.get("numeric_stats"):
            lines += ["### Numeric Statistics"]
            for col, stats in p["numeric_stats"].items():
                lines += [f"**`{col}`**: min={stats['min']}, max={stats['max']}, "
                           f"negatives={stats.get('negatives', 0)}, zeros={stats.get('zeros', 0)}"]
            lines += [""]

        if p.get("sample_rows"):
            lines += ["### Sample Rows (first 3)"]
            sample_df = pd.DataFrame(p["sample_rows"])
            lines.append(md_table(sample_df))
            lines += [""]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[inspect_data] Report written -> {out_path}")


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    print("[inspect_data] Profiling source files …")

    profiles = []

    # 1. Supplier
    print("  Profiling supplier …")
    profiles.append(profile_csv(
        SUPPLIER_CSV,
        pk_col="Supplier_ID",
        date_cols=["Created_At", "Updated_At"],
        numeric_cols=["Opening_Balance", "Credit_Days", "Credit_Limit", "Lead_Time_Days"],
    ))

    # 2. Product
    print("  Profiling product …")
    profiles.append(profile_csv(
        PRODUCT_CSV,
        pk_col="Product_ID",
        numeric_cols=["Purchase_Rate", "Cost_Price", "Sale_Rate", "MRP",
                      "Minimum_Order_Qty", "Reorder_Level", "Maximum_Stock_Level",
                      "Expected_Daily_Demand", "Supplier_Lead_Time_Days"],
    ))

    # 3. Inventory
    print("  Profiling inventory …")
    profiles.append(profile_csv(
        INVENTORY_CSV,
        pk_col="Batch_ID",
        date_cols=["Purchase_Date", "Received_Date", "Last_Updated"],
        numeric_cols=["Received_Quantity", "Sold_Quantity", "Current_Quantity",
                      "Reserved_Quantity", "Damaged_Quantity", "Wasted_Quantity",
                      "Stock_Value", "Reorder_Level", "Maximum_Stock_Level",
                      "Supplier_Lead_Time_Days"],
    ))

    # 4. Sales (large — chunked)
    print("  Profiling sales (chunked – may take ~60 s) …")
    profiles.append(profile_csv(
        SALES_CSV,
        chunk_pk="Sale_ID",
        date_cols=["Sale_Date"],
        numeric_cols=["Quantity_Sold", "MRP", "Selling_Price",
                      "Discount_Amount", "Tax_Amount", "Total_Amount"],
        large=True,
    ))

    # Write markdown report
    report_path = REPORTS_DIR / "data_profile.md"
    write_report(profiles, report_path)

    # Also dump JSON for downstream use
    json_path = REPORTS_DIR / "data_profile.json"
    json_path.write_text(
        json.dumps(profiles, indent=2, default=str), encoding="utf-8"
    )
    print(f"[inspect_data] JSON profile written -> {json_path}")
    print("[inspect_data] Done.")
    return profiles


if __name__ == "__main__":
    main()
