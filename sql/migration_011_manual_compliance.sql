-- Phase 008A: manual product compliance overrides + per-brand settings (additive).
-- Local/dev example:
--   docker compose exec -T db psql -U searchlocal -d products_local < sql/migration_011_manual_compliance.sql

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS manual_compliance TEXT,
    ADD COLUMN IF NOT EXISTS manual_compliance_note TEXT;

CREATE TABLE IF NOT EXISTS brand_compliance_settings (
    brand_norm                  TEXT PRIMARY KEY,
    manual_compliance_priority  BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON COLUMN products.manual_compliance IS 'Optional per-product compliance override (canonical display label).';
COMMENT ON COLUMN products.manual_compliance_note IS 'Note paired with manual_compliance; not products.note.';
COMMENT ON TABLE brand_compliance_settings IS 'Per-brand toggle for manual compliance priority; brand_norm = UPPER(TRIM(brand)).';
