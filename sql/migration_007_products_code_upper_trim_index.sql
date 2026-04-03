-- Tăng tốc /find_code_batch (và các truy vấn WHERE UPPER(TRIM(p.code)) = ...).
-- Chạy sau khi đã có bảng products. Trên DB lớn có thể mất vài phút.
--
-- psql "$DATABASE_URL" -f sql/migration_007_products_code_upper_trim_index.sql

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_code_upper_trim
    ON products ((UPPER(TRIM(code))))
    WHERE code IS NOT NULL AND TRIM(code) <> '';
