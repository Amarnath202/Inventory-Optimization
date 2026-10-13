-- =============================================================================
-- sql/schema_m2.sql
-- Module 2 DDL — demand_forecast and forecast_run tables
-- Database: inventory_optimization_db2 (created by Module 1)
--
-- IMPORTANT
-- ---------
-- * Module 1 tables are NOT modified.
-- * Run via: python src/pipeline.py --create-schema
-- * Uses CREATE TABLE IF NOT EXISTS — safe to rerun.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. forecast_run  — one row per forecast execution
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS forecast_run (
    forecast_run_id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    model_version            VARCHAR(50)   NOT NULL,
    run_started_at           TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    run_completed_at         TIMESTAMPTZ,
    -- status values: 'running' | 'completed' | 'failed' | 'interrupted'
    status                   VARCHAR(20)   NOT NULL DEFAULT 'running'
        CONSTRAINT chk_fr_status CHECK (status IN ('running','completed','failed','interrupted')),
    forecast_origin          DATE          NOT NULL,
    target_date_from         DATE          NOT NULL,
    target_date_to           DATE          NOT NULL,
    rows_written             INTEGER,
    error_detail             TEXT,
    -- triggered_by values: 'scheduler' | 'manual' | 'pipeline'
    triggered_by             VARCHAR(20)   NOT NULL DEFAULT 'pipeline'
        CONSTRAINT chk_fr_triggered CHECK (triggered_by IN ('scheduler','manual','pipeline')),
    CONSTRAINT chk_fr_dates CHECK (target_date_to >= target_date_from)
);

COMMENT ON TABLE forecast_run IS
    'One row per forecast execution. model_version identifies the trained LightGBM artifact. '
    'forecast_run_id (UUID) is unique per execution. Historical runs are preserved. '
    'status lifecycle: running -> completed | failed | interrupted.';
COMMENT ON COLUMN forecast_run.model_version IS
    'Identifies the trained LightGBM artifact (e.g. lgbm_20260831). '
    'Fixed at training time. Does not change between forecast runs using the same model.';
COMMENT ON COLUMN forecast_run.forecast_run_id IS
    'UUID generated at the start of every forecast execution. '
    'Unique per run. Different from model_version.';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. demand_forecast  — one row per (run, product, date)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS demand_forecast (
    forecast_id       BIGSERIAL     PRIMARY KEY,
    forecast_run_id   UUID          NOT NULL,
    product_id        VARCHAR(12)   NOT NULL,
    forecast_date     DATE          NOT NULL,
    forecasted_units  NUMERIC(10,4) NOT NULL
        CONSTRAINT chk_df_units CHECK (forecasted_units >= 0),
    model_version     VARCHAR(50)   NOT NULL,
    CONSTRAINT fk_df_run FOREIGN KEY (forecast_run_id)
        REFERENCES forecast_run (forecast_run_id) ON DELETE CASCADE,
    CONSTRAINT fk_df_product FOREIGN KEY (product_id)
        REFERENCES product (product_id) ON DELETE RESTRICT ON UPDATE CASCADE,
    -- Unique per run: idempotent re-run of same forecast_run_id is blocked
    CONSTRAINT uq_df_run_product_date UNIQUE (forecast_run_id, product_id, forecast_date)
);

COMMENT ON TABLE demand_forecast IS
    'Forecast rows. One row per (forecast_run_id, product_id, forecast_date). '
    'Re-running the same forecast_run_id is blocked by the unique constraint. '
    'Historical runs are preserved for auditability. '
    'forecasted_units is the raw model output; apply ROUND() at application layer if needed.';

-- ─────────────────────────────────────────────────────────────────────────────
-- Indexes
-- ─────────────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_df_product_date
    ON demand_forecast (product_id, forecast_date);

CREATE INDEX IF NOT EXISTS idx_df_run_id
    ON demand_forecast (forecast_run_id);

CREATE INDEX IF NOT EXISTS idx_fr_status
    ON forecast_run (status);

CREATE INDEX IF NOT EXISTS idx_fr_started
    ON forecast_run (run_started_at DESC);

-- End of schema_m2.sql
