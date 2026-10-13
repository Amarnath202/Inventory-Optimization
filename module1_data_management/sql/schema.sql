-- =============================================================================
-- schema.sql
-- PostgreSQL DDL for inventory_optimization_db2
-- Module 1 – Inventory Optimization & Excess Stock Management System
--
-- Database  : inventory_optimization_db2
-- Created   : 2026-10-10
-- Run via   : src/build_schema.py (inspects existing tables; uses IF NOT EXISTS)
--
-- IMPORTANT
-- ---------
-- * All statements use CREATE TABLE IF NOT EXISTS.
-- * DO NOT execute DROP TABLE, TRUNCATE, or ALTER statements without
--   explicit user approval.
-- * Execute through build_schema.py, which inspects existing tables for
--   conflicts before running this file.
-- =============================================================================


-- ─────────────────────────────────────────────────────────────────────────────
-- 1. supplier
-- Source: supplier_THRESHOLD_FINAL.csv (3 of 29 columns retained)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supplier (
    Supplier_ID    VARCHAR(10)  NOT NULL,
    Supplier_Name  VARCHAR(150) NOT NULL,
    Lead_Time_Days SMALLINT     NOT NULL
        CONSTRAINT chk_supplier_lead_time CHECK (Lead_Time_Days > 0),
    CONSTRAINT pk_supplier PRIMARY KEY (Supplier_ID)
);

COMMENT ON TABLE  supplier IS
    'Supplier master. Three fields from the 29-column source CSV. '
    'Lead_Time_Days is a modelled synthetic value, not a verified operational delivery record.';
COMMENT ON COLUMN supplier.Lead_Time_Days IS
    'Modelled lead time (range: 1–10 days). Source: supplier_THRESHOLD_FINAL.csv.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. product
-- Source: product_THRESHOLD_FINAL.csv (14 of 34 columns retained)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS product (
    Product_ID            VARCHAR(12)   NOT NULL,
    Product_Code          VARCHAR(15)   NOT NULL,
    Product_Name          VARCHAR(255)  NOT NULL,
    Brand                 VARCHAR(100),
    Category              VARCHAR(60),
    Unit                  VARCHAR(20),
    Purchase_Rate         NUMERIC(12,2)
        CONSTRAINT chk_product_purchase_rate  CHECK (Purchase_Rate  >= 0),
    Sale_Rate             NUMERIC(12,2)
        CONSTRAINT chk_product_sale_rate      CHECK (Sale_Rate      >= 0),
    MRP                   NUMERIC(12,2)
        CONSTRAINT chk_product_mrp            CHECK (MRP            >= 0),
    Reorder_Level         INTEGER
        CONSTRAINT chk_product_reorder        CHECK (Reorder_Level  >= 0),
    Maximum_Stock_Level   INTEGER
        CONSTRAINT chk_product_max_stock      CHECK (Maximum_Stock_Level >= 0),
    Minimum_Order_Qty     INTEGER
        CONSTRAINT chk_product_min_order      CHECK (Minimum_Order_Qty   >= 0),
    Purchase_Frequency    VARCHAR(20),
    Expected_Daily_Demand NUMERIC(10,4)
        CONSTRAINT chk_product_daily_demand   CHECK (Expected_Daily_Demand >= 0),
    CONSTRAINT pk_product        PRIMARY KEY (Product_ID),
    CONSTRAINT uq_product_code   UNIQUE      (Product_Code)
);

COMMENT ON TABLE product IS
    'Product master. 14 fields from the 34-column source CSV. '
    'Excluded: Barcode, tax rates, HSN_Code, Sub_Unit, Conversion_Factor, '
    'Minimum_Stock_Level, Urgent_Order_Qty, flag fields, and lookup IDs.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. product_supplier  (junction table)
-- Source: product_THRESHOLD_FINAL.csv (one supplier per product in current data)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS product_supplier (
    Product_ID        VARCHAR(12)   NOT NULL,
    Supplier_ID       VARCHAR(10)   NOT NULL,
    Purchase_Rate     NUMERIC(12,2)
        CONSTRAINT chk_ps_purchase_rate   CHECK (Purchase_Rate  >= 0),
    Minimum_Order_Qty INTEGER
        CONSTRAINT chk_ps_min_order       CHECK (Minimum_Order_Qty >= 0),
    CONSTRAINT pk_product_supplier  PRIMARY KEY (Product_ID, Supplier_ID),
    CONSTRAINT fk_ps_product        FOREIGN KEY (Product_ID)
        REFERENCES product  (Product_ID) ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_ps_supplier       FOREIGN KEY (Supplier_ID)
        REFERENCES supplier (Supplier_ID) ON DELETE RESTRICT ON UPDATE CASCADE
);

COMMENT ON TABLE product_supplier IS
    'Junction table: product–supplier relationships. '
    'Composite PK (Product_ID, Supplier_ID) supports multiple suppliers per product. '
    'Current dataset contains one supplier per product; design is forward-compatible '
    'for the Procurement Agent (later module).';


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. inventory_batch
-- Source: inventory_THRESHOLD_FINAL.csv (all 20 columns + 1 derived)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS inventory_batch (
    Batch_ID                       VARCHAR(15)   NOT NULL,
    Product_ID                     VARCHAR(12)   NOT NULL,
    Supplier_ID                    VARCHAR(10)   NOT NULL,
    Purchase_Date                  DATE          NOT NULL,
    Received_Date                  DATE          NOT NULL,
    Lead_Time_Date_Difference_Days SMALLINT,              -- derived; nullable for safety
    Received_Quantity              INTEGER       NOT NULL
        CONSTRAINT chk_ib_received_qty  CHECK (Received_Quantity  >= 0),
    Sold_Quantity                  INTEGER       NOT NULL
        CONSTRAINT chk_ib_sold_qty      CHECK (Sold_Quantity      >= 0),
    Current_Quantity               INTEGER       NOT NULL
        CONSTRAINT chk_ib_current_qty   CHECK (Current_Quantity   >= 0),
    Reserved_Quantity              INTEGER       NOT NULL
        CONSTRAINT chk_ib_reserved_qty  CHECK (Reserved_Quantity  >= 0),
    Damaged_Quantity               INTEGER       NOT NULL
        CONSTRAINT chk_ib_damaged_qty   CHECK (Damaged_Quantity   >= 0),
    Wasted_Quantity                INTEGER       NOT NULL
        CONSTRAINT chk_ib_wasted_qty    CHECK (Wasted_Quantity    >= 0),
    Selling_Price                  NUMERIC(12,2)
        CONSTRAINT chk_ib_selling_price CHECK (Selling_Price      >= 0),
    MRP                            NUMERIC(12,2)
        CONSTRAINT chk_ib_mrp          CHECK (MRP                 >= 0),
    Stock_Value                    NUMERIC(14,2),
    Storage_Location               VARCHAR(30),
    Inventory_Status               VARCHAR(20),
    Last_Updated                   DATE,
    Reorder_Level                  INTEGER
        CONSTRAINT chk_ib_reorder      CHECK (Reorder_Level       >= 0),
    Maximum_Stock_Level            INTEGER
        CONSTRAINT chk_ib_max_stock    CHECK (Maximum_Stock_Level >= 0),
    Supplier_Lead_Time_Days        SMALLINT,              -- modelled value from supplier master
    CONSTRAINT pk_inventory_batch           PRIMARY KEY (Batch_ID),
    CONSTRAINT fk_ib_product                FOREIGN KEY (Product_ID)
        REFERENCES product  (Product_ID)  ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_ib_supplier               FOREIGN KEY (Supplier_ID)
        REFERENCES supplier (Supplier_ID) ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT chk_ib_received_ge_purchase  CHECK (Received_Date >= Purchase_Date),
    CONSTRAINT chk_ib_selling_le_mrp        CHECK (
        Selling_Price IS NULL OR MRP IS NULL OR Selling_Price <= MRP
    ),
    CONSTRAINT chk_ib_inventory_equation    CHECK (
        Received_Quantity =
            Sold_Quantity + Current_Quantity + Damaged_Quantity + Wasted_Quantity
    )
);

COMMENT ON TABLE  inventory_batch IS
    'Monthly inventory replenishment batches. All 20 source columns retained. '
    'One derived column added: Lead_Time_Date_Difference_Days.';
COMMENT ON COLUMN inventory_batch.Lead_Time_Date_Difference_Days IS
    'Derived: (Received_Date - Purchase_Date) in integer days. '
    'Date-arithmetic value ONLY. NOT a verified operational delivery record.';
COMMENT ON COLUMN inventory_batch.Supplier_Lead_Time_Days IS
    'Modelled lead-time value from the supplier master at batch-creation time. '
    'Not a verified operational lead time.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. sale  (bill header table)
-- Source: sales_THRESHOLD_FINAL.csv — one row per unique Bill_No
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sale (
    Bill_No       VARCHAR(20) NOT NULL,
    Sale_Date     DATE        NOT NULL,
    Payment_Mode  VARCHAR(20),
    Customer_ID   VARCHAR(20),           -- nullable; no customer master table
    Items_In_Bill INTEGER
        CONSTRAINT chk_sale_items_in_bill CHECK (Items_In_Bill > 0),
    CONSTRAINT pk_sale PRIMARY KEY (Bill_No)
);

COMMENT ON TABLE  sale IS
    'Bill header table. One row per unique Bill_No. '
    'Bill_No is used as PK after validating uniqueness of header attributes.';
COMMENT ON COLUMN sale.Customer_ID IS
    'Nullable VARCHAR. No customer master table. '
    'Customer IDs are present in the source but no customer entity is modelled.';
COMMENT ON COLUMN sale.Items_In_Bill IS
    'Informational count of line items per bill. Source column: Items_In_Bill.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 6. sale_item  (transaction line items)
-- Source: sales_THRESHOLD_FINAL.csv — one row per Sale_ID
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sale_item (
    Sale_ID          VARCHAR(15)   NOT NULL,
    Bill_No          VARCHAR(20)   NOT NULL,
    Product_ID       VARCHAR(12)   NOT NULL,
    Batch_ID         VARCHAR(15)   NOT NULL,
    Item_Seq_In_Bill INTEGER       NOT NULL
        CONSTRAINT chk_si_item_seq    CHECK (Item_Seq_In_Bill > 0),
    Quantity_Sold    INTEGER       NOT NULL
        CONSTRAINT chk_si_qty_sold    CHECK (Quantity_Sold    > 0),
    Selling_Price    NUMERIC(12,2) NOT NULL
        CONSTRAINT chk_si_sell_price  CHECK (Selling_Price    >= 0),
    MRP              NUMERIC(12,2) NOT NULL
        CONSTRAINT chk_si_mrp         CHECK (MRP              >= 0),
    Discount_Amount  NUMERIC(12,2)
        CONSTRAINT chk_si_discount    CHECK (Discount_Amount  >= 0),
    Tax_Rate         NUMERIC(5,2)
        CONSTRAINT chk_si_tax_rate    CHECK (Tax_Rate         >= 0),
    Tax_Amount       NUMERIC(12,2)
        CONSTRAINT chk_si_tax_amount  CHECK (Tax_Amount       >= 0),
    Total_Amount     NUMERIC(12,2) NOT NULL
        CONSTRAINT chk_si_total       CHECK (Total_Amount     >= 0),
    CONSTRAINT pk_sale_item         PRIMARY KEY (Sale_ID),
    CONSTRAINT uq_bill_item_seq     UNIQUE      (Bill_No, Item_Seq_In_Bill),
    CONSTRAINT fk_si_sale           FOREIGN KEY (Bill_No)
        REFERENCES sale            (Bill_No)    ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_si_product        FOREIGN KEY (Product_ID)
        REFERENCES product         (Product_ID) ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_si_batch          FOREIGN KEY (Batch_ID)
        REFERENCES inventory_batch (Batch_ID)   ON DELETE RESTRICT ON UPDATE CASCADE,
    -- Small tolerance (0.01) for floating-point rounding in source data
    CONSTRAINT chk_si_selling_le_mrp CHECK (Selling_Price <= MRP + 0.01)
);

COMMENT ON TABLE  sale_item IS
    'Sale line items. One row per Sale_ID. Multiple items per bill. '
    'Excludes Bill_Total_Amount (bill-level), Items_In_Bill (bill-level), '
    'and Sale_Date / Payment_Mode / Customer_ID (in sale header table).';
COMMENT ON COLUMN sale_item.Discount_Amount IS
    'Absolute currency discount (not a percentage). '
    'Computed from MRP and Selling_Price in the source.';


-- ─────────────────────────────────────────────────────────────────────────────
-- 7. daily_demand
-- Aggregated from sale_item; built by build_daily_demand.py
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_demand (
    Product_ID  VARCHAR(12) NOT NULL,
    Date        DATE        NOT NULL,
    Units_Sold  INTEGER     NOT NULL
        CONSTRAINT chk_dd_units CHECK (Units_Sold > 0),
    CONSTRAINT pk_daily_demand  PRIMARY KEY (Product_ID, Date),
    CONSTRAINT fk_dd_product    FOREIGN KEY (Product_ID)
        REFERENCES product (Product_ID) ON DELETE RESTRICT ON UPDATE CASCADE
);

COMMENT ON TABLE daily_demand IS
    'Daily product demand aggregated from sale_item. '
    'Built by: SELECT Product_ID, Sale_Date, SUM(Quantity_Sold) FROM sale_item '
    'JOIN sale ON Bill_No GROUP BY Product_ID, Sale_Date. '
    'No calendar gap-filling. No dates outside the observed sale range.';


-- ─────────────────────────────────────────────────────────────────────────────
-- Indexes
-- Note: PK constraints already create B-tree indexes on PK columns.
-- Do NOT create a Sale_Date index on sale or sale_item (per specification).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_sale_item_product_id
    ON sale_item (Product_ID);

CREATE INDEX IF NOT EXISTS idx_sale_item_batch_id
    ON sale_item (Batch_ID);

CREATE INDEX IF NOT EXISTS idx_inventory_batch_product_id
    ON inventory_batch (Product_ID);

CREATE INDEX IF NOT EXISTS idx_inventory_batch_received_date
    ON inventory_batch (Received_Date);

-- End of schema.sql
