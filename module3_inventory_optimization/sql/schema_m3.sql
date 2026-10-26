-- =============================================================================
-- module3_inventory_optimization/sql/schema_m3.sql
-- Module 3 DDL — inventory_assessment table
-- Database: inventory_optimization_db2 (created by Module 1)
--
-- IMPORTANT
-- ---------
-- * Module 1 and Module 2 tables are NOT modified.
-- * Uses CREATE TABLE IF NOT EXISTS and UNIQUE constraints — safe to rerun.
-- * Idempotency: ON CONFLICT (product_id, forecast_run_id) DO UPDATE
--   is used at the application layer. The uq_ia_product_run constraint
--   enforces one row per (product, forecast_run). Assessments from
--   different forecast runs are preserved as separate rows (audit history).
--
-- stock_calculation_method:
--   'latest_batch' — current_quantity from the most recent inventory_batch
--   row per product (ordered by received_date DESC, batch_id DESC).
--   The sum of current_quantity across all historical batches is NOT used;
--   that figure is a cumulative dataset artifact, not physical stock.
--
-- Column types:
--   product_id      VARCHAR(12) — matches product.product_id (VARCHAR(12))
--   forecast_run_id UUID        — matches forecast_run.forecast_run_id (UUID)
-- =============================================================================

CREATE TABLE IF NOT EXISTS inventory_assessment (
    -- ── Identity ──────────────────────────────────────────────────────────────
    assessment_id             BIGSERIAL       PRIMARY KEY,
    product_id                VARCHAR(12)     NOT NULL,
    assessed_at               TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- ── Forecast run reference ────────────────────────────────────────────────
    forecast_run_id           UUID,                           -- NULL if no forecast available
    model_version             VARCHAR(50),

    -- ── Stock inputs ─────────────────────────────────────────────────────────
    -- available_stock: current_quantity from the latest inventory_batch row.
    -- inventory_position: equals available_stock (no purchase orders tracked).
    available_stock           INTEGER         NOT NULL,
    inventory_position        INTEGER         NOT NULL,
    stock_calculation_method  VARCHAR(30)     NOT NULL DEFAULT 'latest_batch',
    inventory_position_note   TEXT,           -- documents simplification + date

    -- ── Demand inputs ────────────────────────────────────────────────────────
    forecast_demand_7d        NUMERIC(10,4),  -- sum of 7-day forecasted_units
    avg_daily_demand          NUMERIC(10,4),  -- mean of gap-filled 90-day window
    demand_source             VARCHAR(30),    -- 'forecast_avg' | 'historical_avg' | 'product_field'
    demand_std_daily          NUMERIC(10,4),  -- sample std dev of gap-filled window
    history_days_used         INTEGER,        -- calendar days in window (including zeros)
    nonzero_demand_days       INTEGER,        -- actual sale days in window

    -- ── Lead time ────────────────────────────────────────────────────────────
    lead_time_days            SMALLINT,       -- from supplier via product_supplier

    -- ── Safety stock ─────────────────────────────────────────────────────────
    safety_stock              NUMERIC(10,4),
    safety_stock_z            NUMERIC(6,4),   -- z-factor applied (e.g. 1.645)
    -- 'calculated' | 'zero_variability' | 'insufficient_nonzero_history'
    safety_stock_status       VARCHAR(40),

    -- ── Reorder point ────────────────────────────────────────────────────────
    reorder_point             NUMERIC(10,4),  -- computed ROP
    reorder_level_from_product INTEGER,       -- product.reorder_level (static field)
    -- 'calculated' | 'lead_time_exceeds_horizon' | 'insufficient_data' | 'fallback_product_field'
    rop_status                VARCHAR(40),

    -- ── Replenishment ────────────────────────────────────────────────────────
    suggested_replenishment_qty INTEGER,
    minimum_order_qty         INTEGER,
    maximum_stock_level       INTEGER,
    -- 'calculated' | 'not_required' | 'insufficient_data'
    replenishment_status      VARCHAR(30),

    -- ── Routing decision ─────────────────────────────────────────────────────
    routing_decision          VARCHAR(30)     NOT NULL,
    decision_reasons          TEXT,           -- semicolon-separated list of reason codes
    calculation_notes         TEXT,           -- human-readable limitations and assumptions

    -- ── Versioning ───────────────────────────────────────────────────────────
    policy_version            VARCHAR(20)     NOT NULL DEFAULT 'v1.0',

    -- ── Constraints ──────────────────────────────────────────────────────────
    CONSTRAINT fk_ia_product FOREIGN KEY (product_id)
        REFERENCES product (product_id) ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_ia_forecast_run FOREIGN KEY (forecast_run_id)
        REFERENCES forecast_run (forecast_run_id) ON DELETE SET NULL ON UPDATE CASCADE,
    CONSTRAINT chk_ia_routing CHECK (
        routing_decision IN (
            'procurement_required',
            'excess_stock_review',
            'normal',
            'insufficient_data'
        )
    ),
    CONSTRAINT chk_ia_stock_method CHECK (
        stock_calculation_method IN ('latest_batch', 'unavailable')
    ),
    -- Idempotency: one assessment per (product, forecast_run).
    -- NULL forecast_run_id is excluded from this unique constraint
    -- (handled by partial index below).
    CONSTRAINT uq_ia_product_run UNIQUE (product_id, forecast_run_id)
);

-- Partial unique index for rows with NULL forecast_run_id
-- (standard UNIQUE constraint treats two NULLs as distinct in PostgreSQL).
CREATE UNIQUE INDEX IF NOT EXISTS uq_ia_product_no_run
    ON inventory_assessment (product_id)
    WHERE forecast_run_id IS NULL;

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_ia_product_id
    ON inventory_assessment (product_id);

CREATE INDEX IF NOT EXISTS idx_ia_assessed_at
    ON inventory_assessment (assessed_at DESC);

CREATE INDEX IF NOT EXISTS idx_ia_routing
    ON inventory_assessment (routing_decision);

CREATE INDEX IF NOT EXISTS idx_ia_forecast_run
    ON inventory_assessment (forecast_run_id);

COMMENT ON TABLE inventory_assessment IS
    'Module 3 — one row per (product_id, forecast_run_id). '
    'available_stock = current_quantity from the latest inventory_batch row '
    '(received_date DESC). Summing all batches is intentionally avoided '
    '(cumulative dataset artifact). '
    'Idempotent: re-assessing the same forecast_run_id performs ON CONFLICT DO UPDATE. '
    'Different forecast_run_id values produce separate rows (audit history preserved). '
    'policy_version tracks the calculation rules version.';

-- End of schema_m3.sql
