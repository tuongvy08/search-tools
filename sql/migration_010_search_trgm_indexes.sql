-- Speed up /search ILIKE on products.name, products.code, products.cas.
-- Local/dev first; run on production only during a planned maintenance window.
--
-- psql "$DATABASE_URL" -f sql/migration_010_search_trgm_indexes.sql

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_name_trgm
    ON products USING gin (name gin_trgm_ops);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_code_trgm
    ON products USING gin (code gin_trgm_ops);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_cas_trgm
    ON products USING gin (cas gin_trgm_ops);
