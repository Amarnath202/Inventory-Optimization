# ER Diagram — inventory\_optimization\_db2
## Module 1 · Inventory Optimization & Excess Stock Management System

> Generated: 2026-10-10  
> Database: `inventory_optimization_db2`  
> Schema: `public`

---

## Entity-Relationship Diagram

```mermaid
erDiagram

    supplier {
        VARCHAR_10  Supplier_ID       PK
        VARCHAR_150 Supplier_Name
        SMALLINT    Lead_Time_Days
    }

    product {
        VARCHAR_12  Product_ID        PK
        VARCHAR_15  Product_Code      UK
        VARCHAR_255 Product_Name
        VARCHAR_100 Brand
        VARCHAR_60  Category
        VARCHAR_20  Unit
        NUMERIC     Purchase_Rate
        NUMERIC     Sale_Rate
        NUMERIC     MRP
        INTEGER     Reorder_Level
        INTEGER     Maximum_Stock_Level
        INTEGER     Minimum_Order_Qty
        VARCHAR_20  Purchase_Frequency
        NUMERIC     Expected_Daily_Demand
    }

    product_supplier {
        VARCHAR_12  Product_ID        PK-FK
        VARCHAR_10  Supplier_ID       PK-FK
        NUMERIC     Purchase_Rate
        INTEGER     Minimum_Order_Qty
    }

    inventory_batch {
        VARCHAR_15  Batch_ID          PK
        VARCHAR_12  Product_ID        FK
        VARCHAR_10  Supplier_ID       FK
        DATE        Purchase_Date
        DATE        Received_Date
        SMALLINT    Lead_Time_Date_Difference_Days
        INTEGER     Received_Quantity
        INTEGER     Sold_Quantity
        INTEGER     Current_Quantity
        INTEGER     Reserved_Quantity
        INTEGER     Damaged_Quantity
        INTEGER     Wasted_Quantity
        NUMERIC     Selling_Price
        NUMERIC     MRP
        NUMERIC     Stock_Value
        VARCHAR_30  Storage_Location
        VARCHAR_20  Inventory_Status
        DATE        Last_Updated
        INTEGER     Reorder_Level
        INTEGER     Maximum_Stock_Level
        SMALLINT    Supplier_Lead_Time_Days
    }

    sale {
        VARCHAR_20  Bill_No           PK
        DATE        Sale_Date
        VARCHAR_20  Payment_Mode
        VARCHAR_20  Customer_ID
        INTEGER     Items_In_Bill
    }

    sale_item {
        VARCHAR_15  Sale_ID           PK
        VARCHAR_20  Bill_No           FK
        VARCHAR_12  Product_ID        FK
        VARCHAR_15  Batch_ID          FK
        INTEGER     Item_Seq_In_Bill
        INTEGER     Quantity_Sold
        NUMERIC     Selling_Price
        NUMERIC     MRP
        NUMERIC     Discount_Amount
        NUMERIC     Tax_Rate
        NUMERIC     Tax_Amount
        NUMERIC     Total_Amount
    }

    daily_demand {
        VARCHAR_12  Product_ID        PK-FK
        DATE        Date              PK
        INTEGER     Units_Sold
    }

    supplier        ||--o{ product_supplier  : "supplies"
    product         ||--o{ product_supplier  : "supplied_by"
    supplier        ||--o{ inventory_batch   : "delivers_batches"
    product         ||--o{ inventory_batch   : "stocked_as"
    sale            ||--o{ sale_item         : "contains_lines"
    product         ||--o{ sale_item         : "appears_in_sales"
    inventory_batch ||--o{ sale_item         : "fulfilled_from"
    product         ||--o{ daily_demand      : "demanded_daily"
```

---

## Relationship Summary

| Relationship | Type | Description |
|---|---|---|
| `supplier` → `product_supplier` | 1 : many | One supplier can supply many products |
| `product` → `product_supplier` | 1 : many | One product can be supplied by many suppliers |
| `supplier` → `inventory_batch` | 1 : many | One supplier delivers many batches |
| `product` → `inventory_batch` | 1 : many | One product has many inventory batches |
| `sale` → `sale_item` | 1 : many | One bill contains one or more line items |
| `product` → `sale_item` | 1 : many | One product appears in many sale lines |
| `inventory_batch` → `sale_item` | 1 : many | One batch fulfils many sale lines |
| `product` → `daily_demand` | 1 : many | One product has demand records per day |

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| `product_supplier` junction table | Supports multiple suppliers per product (Procurement Agent, later module) |
| `sale` header + `sale_item` line split | Normalises bill-level data (date, payment) from line-level data |
| `daily_demand` as regular table | Efficient for LightGBM feature matrix reads in Module 2 |
| `Customer_ID` nullable in `sale` | No customer master; ID retained for potential future use |
| `Lead_Time_Date_Difference_Days` in `inventory_batch` | Date-derived column; kept separate from modelled `Supplier_Lead_Time_Days` |

---

## Constraints Applied

| Table | Constraint | Type |
|---|---|---|
| `supplier` | `Lead_Time_Days > 0` | CHECK |
| `inventory_batch` | `Received_Date >= Purchase_Date` | CHECK |
| `inventory_batch` | `Selling_Price <= MRP` | CHECK |
| `inventory_batch` | `Received = Sold + Current + Damaged + Wasted` | CHECK |
| `sale_item` | `Quantity_Sold > 0` | CHECK |
| `sale_item` | `Selling_Price <= MRP + 0.01` | CHECK |
| `sale_item` | `(Bill_No, Item_Seq_In_Bill)` | UNIQUE |
| All monetary cols | `>= 0` | CHECK |
| All quantity cols | `>= 0` | CHECK |

---

## Planned Indexes (Module 1)

| Index | Table | Column | Purpose |
|---|---|---|---|
| `idx_sale_item_product_id` | `sale_item` | `Product_ID` | Daily demand aggregation, product joins |
| `idx_sale_item_batch_id` | `sale_item` | `Batch_ID` | Batch reconciliation joins |
| `idx_inventory_batch_product_id` | `inventory_batch` | `Product_ID` | Product-level stock lookups |
| `idx_inventory_batch_received_date` | `inventory_batch` | `Received_Date` | Date-range queries on batch receipts |

> **Not created:** `Sale_Date` index on `sale` or `sale_item` (excluded per specification).
