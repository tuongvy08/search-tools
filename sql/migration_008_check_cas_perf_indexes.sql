-- Tăng tốc Check license (CAS list): /check_cas_batch và EXISTS trong /check_cas
-- Chạy sau khi đã có bảng products và regulatory_rules.
--
-- psql "$DATABASE_URL" -f sql/migration_008_check_cas_perf_indexes.sql

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_cas_upper_trim
    ON products ((UPPER(TRIM(cas))))
    WHERE cas IS NOT NULL AND TRIM(cas) <> '';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_reg_rules_cas_match_value_upper
    ON regulatory_rules ((UPPER(TRIM(match_value))))
    WHERE is_active = TRUE AND match_field = 'cas';
