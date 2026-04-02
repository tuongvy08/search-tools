-- Tách dữ liệu kiểm soát pháp lý ra khỏi cột brand trong products.
-- Chạy sau sql/schema.sql

CREATE TABLE IF NOT EXISTS regulatory_rules (
    id            BIGSERIAL PRIMARY KEY,
    rule_type     TEXT NOT NULL CHECK (rule_type IN ('CAM_NHAP', 'PHU_LUC_II', 'PHU_LUC_III', 'TON_KHO')),
    rule_label    TEXT NOT NULL,
    match_field   TEXT NOT NULL CHECK (match_field IN ('cas', 'name', 'code')),
    match_value   TEXT NOT NULL,
    priority      INTEGER NOT NULL DEFAULT 100,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    note          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reg_rules_active_field_value
    ON regulatory_rules (is_active, match_field, match_value);

CREATE UNIQUE INDEX IF NOT EXISTS uq_reg_rules_norm
    ON regulatory_rules (rule_type, match_field, UPPER(TRIM(match_value)));
