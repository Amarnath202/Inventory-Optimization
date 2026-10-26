-- =============================================================================
-- module3_inventory_optimization/sql/schema_m5.sql
-- Module 5 DDL — excess_stock_assessment table
-- Database: inventory_optimization_db2 (created by Module 1)
--
-- IMPORTANT
-- ---------
-- * Module 1, Module 2, and Module 3 tables are NOT modified.
-- * Uses CREATE TABLE IF NOT EXISTS — safe to rerun.
-- * Each assessment is inserted as a historical row; assessment_id is the
--   primary key. Read paths select the latest row per product by assessed_at.
-- * There is no unique constraint or partial index on product_id.
--
-- ABC methodology:
--   Population-level cumulative revenue ranking.
--   All eligible products are sorted by 12-month revenue DESC.
--   Cumulative revenue share is computed; thresholds applied:
--     A: cumulative share ≤ 0.70 (top 70% of revenue)
--     B: cumulative share ≤ 0.90 (next 20%)
--     C: remainder
--   Products with zero revenue are classified as C.
--
-- XYZ methodology:
--   Coefficient of Variation (CV = std / mean) of gap-filled daily demand
--   over the 90-day window ending 2026-08-30.
--     X: CV ≤ 0.30  (stable demand)
--     Y: 0.30 < CV ≤ 0.60  (moderate variability)
--     Z: CV > 0.60  (highly variable / intermittent)
--   Products with insufficient history or zero mean: classified as Z
--   with a data-quality warning.
--
-- Excess stock detection rules:
--   1. overstock:      inventory_position > maximum_stock_level × EXCESS_RATIO (1.5)
--                      Same threshold as Module 3 routing — consistent.
--   2. slow_moving:    nonzero_demand_days < 7 in 90-day window  (< MIN_NONZERO_DAYS)
--   3. dead_stock:     zero demand in entire 90-day window AND stock > 0
--   4. normal:         none of the above triggered
-- =============================================================================

CREATE TABLE IF NOT EXISTS excess_stock_assessment (
    -- ── Identity ──────────────────────────────────────────────────────────────
    assessment_id             BIGSERIAL       PRIMARY KEY,
    product_id                VARCHAR(12)     NOT NULL,
    assessed_at               TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- ── Reference to Module 3 inventory_assessment ────────────────────────────
    -- Links to the most recent inventory_assessment row used as input.
    inventory_assessment_id   BIGINT,         -- NULL if assessed independently
    forecast_run_id           UUID,           -- mirrors inventory_assessment.forecast_run_id

    -- ── Stock inputs (read from inventory_assessment or computed) ─────────────
    available_stock           INTEGER,
    inventory_position        INTEGER,
    maximum_stock_level       INTEGER,
    excess_qty_estimate       INTEGER,        -- max(0, inventory_position - maximum_stock_level)

    -- ── ABC classification ────────────────────────────────────────────────────
    abc_class                 VARCHAR(1),     -- 'A' | 'B' | 'C' | NULL (insufficient data)
    revenue_12m               NUMERIC(14,4),  -- product's 12-month revenue
    total_revenue_12m         NUMERIC(14,4),  -- all-product 12-month revenue (snapshot)
    revenue_share             NUMERIC(8,6),   -- product_rev / total_rev
    cumulative_revenue_share  NUMERIC(8,6),   -- cumulative share at this product's rank
    abc_rank                  INTEGER,        -- rank by revenue DESC (1 = highest)
    abc_eligible              BOOLEAN,        -- True = enough data for ABC classification

    -- ── XYZ classification ────────────────────────────────────────────────────
    xyz_class                 VARCHAR(1),     -- 'X' | 'Y' | 'Z' | NULL (insufficient data)
    demand_cv                 NUMERIC(10,6),  -- coefficient of variation (sample)
    avg_daily_demand_90d      NUMERIC(10,4),  -- mean of gap-filled 90-day window
    std_daily_demand_90d      NUMERIC(10,4),  -- sample std dev of gap-filled 90-day window
    nonzero_demand_days_90d   INTEGER,        -- actual sale days in 90-day window
    history_days_90d          INTEGER,        -- calendar days in gap-filled window
    xyz_eligible              BOOLEAN,        -- True = enough data for XYZ classification
    xyz_warning               TEXT,           -- e.g. 'zero_mean', 'no_history', 'insufficient_nonzero'

    -- ── Excess stock detection ────────────────────────────────────────────────
    excess_category           VARCHAR(20),    -- 'overstock'|'slow_moving'|'dead_stock'|'normal'
    is_overstock              BOOLEAN         NOT NULL DEFAULT FALSE,
    is_slow_moving            BOOLEAN         NOT NULL DEFAULT FALSE,
    is_dead_stock             BOOLEAN         NOT NULL DEFAULT FALSE,
    excess_trigger_reasons    TEXT,           -- semicolon-separated reason codes

    -- ── Recommendation ───────────────────────────────────────────────────────
    recommended_action        VARCHAR(50),    -- see RECOMMENDATION_MAP in agent
    recommendation_notes      TEXT,
    requires_manager_approval BOOLEAN         NOT NULL DEFAULT TRUE,

    -- ── Data quality ─────────────────────────────────────────────────────────
    data_warnings             TEXT,           -- general data-quality warnings
    confidence                VARCHAR(20),    -- 'high' | 'medium' | 'low' | 'insufficient'

    -- ── Policy versioning ─────────────────────────────────────────────────────
    policy_version            VARCHAR(20)     NOT NULL DEFAULT 'v1.0',

    -- ── Constraints ──────────────────────────────────────────────────────────
    CONSTRAINT fk_esa_product FOREIGN KEY (product_id)
        REFERENCES product (product_id) ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_esa_forecast_run FOREIGN KEY (forecast_run_id)
        REFERENCES forecast_run (forecast_run_id) ON DELETE SET NULL ON UPDATE CASCADE,
    CONSTRAINT chk_esa_abc CHECK (
        abc_class IS NULL OR abc_class IN ('A', 'B', 'C')
    ),
    CONSTRAINT chk_esa_xyz CHECK (
        xyz_class IS NULL OR xyz_class IN ('X', 'Y', 'Z')
    ),
    CONSTRAINT chk_esa_excess_category CHECK (
        excess_category IS NULL OR
        excess_category IN ('overstock', 'slow_moving', 'dead_stock', 'normal')
    )
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_esa_product_id
    ON excess_stock_assessment (product_id);

CREATE INDEX IF NOT EXISTS idx_esa_assessed_at
    ON excess_stock_assessment (assessed_at DESC);

CREATE INDEX IF NOT EXISTS idx_esa_excess_category
    ON excess_stock_assessment (excess_category);

CREATE INDEX IF NOT EXISTS idx_esa_abc_class
    ON excess_stock_assessment (abc_class);

CREATE INDEX IF NOT EXISTS idx_esa_xyz_class
    ON excess_stock_assessment (xyz_class);

COMMENT ON TABLE excess_stock_assessment IS
    'Module 5 — Excess Stock Management assessments. '
    'One row per product per assessment run. ABC uses population-level '
    'cumulative revenue ranking. XYZ uses CV of 90-day gap-filled demand. '
    'requires_manager_approval is always TRUE — no automatic stock actions. '
    'policy_version tracks classification rule versions.';

-- End of schema_m5.sql
