-- =============================================================================
-- module3_inventory_optimization/sql/schema_m4.sql
-- Module 4 DDL — procurement_recommendation table
-- Database: inventory_optimization_db2 (created by Module 1)
--
-- IMPORTANT
-- ---------
-- * Module 1, Module 2, Module 3, and Module 5 tables are NOT modified.
-- * Uses CREATE TABLE IF NOT EXISTS — safe to rerun.
-- * One row per procurement run (linked to an inventory_assessment row).
-- * stores up to 2 ranked supplier recommendations per run.
-- * requires_manager_approval is always TRUE — no automatic purchasing.
-- * No stock quantities are modified by this module.
--
-- Ranking methodology: Lead-Time-Based Supplier Ranking
--   Eligible suppliers are sorted by ascending lead_time_days.
--   Ties in lead time are broken by ascending supplier_id (deterministic).
--   The top 2 ranked suppliers are returned.
--
-- Supplier alternatives data source:
--   module4_all_supplier_lead_time_ranking.csv
--   Supplier IDs and lead times are validated against the existing
--   supplier master (supplier table) before import.
--   All suppliers in the alternatives dataset already exist in supplier master.
--   Lead times in alternatives CSV match supplier master — no conflict.
-- =============================================================================

CREATE TABLE IF NOT EXISTS procurement_recommendation (
    -- ── Identity ──────────────────────────────────────────────────────────────
    recommendation_id         BIGSERIAL       PRIMARY KEY,
    product_id                VARCHAR(12)     NOT NULL,
    recommended_at            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- ── Reference to Module 3 assessment ─────────────────────────────────────
    -- Links to the inventory_assessment row that triggered this recommendation.
    inventory_assessment_id   BIGINT,         -- NULL if run independently

    -- ── Replenishment quantity (from Module 3, never overridden) ──────────────
    suggested_replenishment_qty INTEGER,

    -- ── Ranking metadata ─────────────────────────────────────────────────────
    ranking_method            VARCHAR(50)     NOT NULL DEFAULT 'lead_time_based',
    eligible_supplier_count   INTEGER         NOT NULL DEFAULT 0,
    recommendation_status     VARCHAR(30)     NOT NULL,
    -- Values: 'top2_available' | 'only_one_available' | 'no_supplier_available' | 'error'

    -- ── Rank 1 supplier ───────────────────────────────────────────────────────
    rank1_supplier_id         VARCHAR(10),
    rank1_supplier_name       VARCHAR(150),
    rank1_lead_time_days      SMALLINT,
    rank1_explanation         TEXT,

    -- ── Rank 2 supplier ───────────────────────────────────────────────────────
    rank2_supplier_id         VARCHAR(10),
    rank2_supplier_name       VARCHAR(150),
    rank2_lead_time_days      SMALLINT,
    rank2_explanation         TEXT,

    -- ── Tie information ───────────────────────────────────────────────────────
    has_lead_time_tie         BOOLEAN         NOT NULL DEFAULT FALSE,
    tie_supplier_count        INTEGER,        -- how many tied at rank 1 lead time
    tie_explanation           TEXT,           -- honest tie-break note

    -- ── Safety / approval ────────────────────────────────────────────────────
    requires_manager_approval BOOLEAN         NOT NULL DEFAULT TRUE,
    -- Always TRUE: no automatic purchasing, no stock modification.

    -- ── Data quality ─────────────────────────────────────────────────────────
    data_warnings             TEXT,           -- semicolon-separated warnings

    -- ── Policy versioning ─────────────────────────────────────────────────────
    policy_version            VARCHAR(20)     NOT NULL DEFAULT 'v1.0',

    -- ── Constraints ──────────────────────────────────────────────────────────
    CONSTRAINT fk_pr_product FOREIGN KEY (product_id)
        REFERENCES product (product_id) ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT chk_pr_status CHECK (
        recommendation_status IN (
            'top2_available',
            'only_one_available',
            'no_supplier_available',
            'error'
        )
    ),
    CONSTRAINT chk_pr_method CHECK (
        ranking_method IN ('lead_time_based')
    ),
    CONSTRAINT chk_pr_approval CHECK (
        requires_manager_approval = TRUE
    )
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_pr_product_id
    ON procurement_recommendation (product_id);

CREATE INDEX IF NOT EXISTS idx_pr_recommended_at
    ON procurement_recommendation (recommended_at DESC);

CREATE INDEX IF NOT EXISTS idx_pr_status
    ON procurement_recommendation (recommendation_status);

CREATE INDEX IF NOT EXISTS idx_pr_assessment_id
    ON procurement_recommendation (inventory_assessment_id);

COMMENT ON TABLE procurement_recommendation IS
    'Module 4 — Lead-Time-Based Procurement Recommendations. '
    'One row per procurement agent run (linked to inventory_assessment). '
    'Ranks eligible suppliers by ascending lead_time_days; '
    'supplier_id breaks ties deterministically. '
    'Returns up to 2 ranked suppliers (rank1 and rank2). '
    'requires_manager_approval is ALWAYS TRUE — no automatic purchasing. '
    'No stock quantities are modified. '
    'Supplier lead times sourced from existing supplier master table. '
    'Alternatives data: module4_all_supplier_lead_time_ranking.csv. '
    'policy_version tracks ranking rule versions.';

-- End of schema_m4.sql
