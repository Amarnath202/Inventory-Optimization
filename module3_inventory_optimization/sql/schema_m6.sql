-- =============================================================================
-- module3_inventory_optimization/sql/schema_m6.sql
-- Module 6 DDL — explanation_record table
-- Database: inventory_optimization_db2 (created by Module 1)
--
-- IMPORTANT
-- ---------
-- * Module 1–5 tables are NOT modified.
-- * Uses CREATE TABLE IF NOT EXISTS — safe to rerun.
-- * One row per explanation request.
-- * Stores explanation metadata, provenance, and the actual output as JSONB.
-- * No business data (stock quantities, purchase orders, assessments) is
--   modified by this module.
--
-- explanation_type values:
--   'forecast_shap'          — LightGBM TreeSHAP explanation of a demand forecast
--   'inventory_rule'         — Module 3 formula-based inventory decision explanation
--   'procurement_rule'       — Module 4 lead-time ranking explanation
--   'excess_stock_rule'      — Module 5 ABC/XYZ/excess-stock classification explanation
--
-- explanation_method values:
--   'tree_shap'              — TreeSHAP via shap.TreeExplainer
--   'rule_based'             — Deterministic formula/rule explanation
--   'statistical'            — Statistical classification explanation
-- =============================================================================

CREATE TABLE IF NOT EXISTS explanation_record (
    -- ── Identity ──────────────────────────────────────────────────────────────
    explanation_id            BIGSERIAL       PRIMARY KEY,
    created_at                TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- ── Type and method ───────────────────────────────────────────────────────
    explanation_type          VARCHAR(30)     NOT NULL,
    explanation_method        VARCHAR(20)     NOT NULL,

    -- ── Subject identifiers ───────────────────────────────────────────────────
    product_id                VARCHAR(12),
    source_record_id          TEXT,           -- assessment_id, recommendation_id, etc.

    -- ── Model provenance (SHAP explanations only) ─────────────────────────────
    model_name                VARCHAR(50),
    model_version             VARCHAR(50),

    -- ── Prediction and baseline (SHAP explanations only) ─────────────────────
    prediction_value          NUMERIC(14,6),
    baseline_value            NUMERIC(14,6),
    prediction_units          VARCHAR(30),    -- e.g. 'units_sold'

    -- ── Explanation body (JSONB — structured for all types) ───────────────────
    explanation_body          JSONB           NOT NULL,

    -- ── Data quality ─────────────────────────────────────────────────────────
    data_warnings             TEXT,

    -- ── Constraints ──────────────────────────────────────────────────────────
    CONSTRAINT chk_er_type CHECK (
        explanation_type IN (
            'forecast_shap',
            'inventory_rule',
            'procurement_rule',
            'excess_stock_rule'
        )
    ),
    CONSTRAINT chk_er_method CHECK (
        explanation_method IN ('tree_shap', 'rule_based', 'statistical')
    )
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_er_product_id
    ON explanation_record (product_id);

CREATE INDEX IF NOT EXISTS idx_er_created_at
    ON explanation_record (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_er_type
    ON explanation_record (explanation_type);

CREATE INDEX IF NOT EXISTS idx_er_source_record
    ON explanation_record (source_record_id);

COMMENT ON TABLE explanation_record IS
    'Module 6 — Explainability records. One row per explanation request. '
    'Stores structured explanation output, provenance, and data warnings. '
    'Does not modify any business data from Modules 1–5. '
    'SHAP explanations reference the exact LightGBM model version and feature values. '
    'Rule-based explanations reference the stored assessment or recommendation record.';

-- End of schema_m6.sql
