CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_code_upper_trim
    ON products ((UPPER(TRIM(code))))
    WHERE code IS NOT NULL AND TRIM(code) <> '';
