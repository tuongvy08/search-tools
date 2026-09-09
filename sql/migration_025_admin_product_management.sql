-- Phase 6C3: durable product-management delete previews, recovery copies,
-- and a focused audit trail. Additive and safe to rerun after migration 024.
--
-- This migration never changes products, Brand Master, aliases, grants,
-- regulatory rules, or import history. Recovery rows are populated only by an
-- explicit, confirmed delete in the application transaction.
BEGIN;

CREATE TABLE IF NOT EXISTS product_delete_previews (
    token UUID PRIMARY KEY,
    actor_user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    actor_auth_version INTEGER NOT NULL,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('product', 'brand')),
    product_id INTEGER,
    canonical_brand TEXT NOT NULL,
    expected_count BIGINT NOT NULL CHECK (expected_count > 0),
    fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '5 minutes',
    consumed_at TIMESTAMPTZ,
    CHECK (
        (scope_type = 'product' AND product_id IS NOT NULL AND expected_count = 1)
        OR (scope_type = 'brand' AND product_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_product_delete_previews_expiry
    ON product_delete_previews (expires_at)
    WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS product_delete_batches (
    id UUID PRIMARY KEY,
    preview_token UUID NOT NULL UNIQUE,
    scope_type TEXT NOT NULL CHECK (scope_type IN ('product', 'brand')),
    product_id INTEGER,
    canonical_brand TEXT NOT NULL,
    row_count BIGINT NOT NULL CHECK (row_count > 0),
    actor_user_id INTEGER REFERENCES app_users(id) ON DELETE SET NULL,
    actor TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    restored_at TIMESTAMPTZ,
    restored_by_user_id INTEGER REFERENCES app_users(id) ON DELETE SET NULL,
    restored_by TEXT,
    CHECK (
        (scope_type = 'product' AND product_id IS NOT NULL AND row_count = 1)
        OR (scope_type = 'brand' AND product_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_product_delete_batches_created
    ON product_delete_batches (deleted_at DESC);

CREATE TABLE IF NOT EXISTS product_deleted_rows (
    batch_id UUID NOT NULL REFERENCES product_delete_batches(id) ON DELETE RESTRICT,
    original_product_id INTEGER NOT NULL,
    original_xmin BIGINT NOT NULL,
    name TEXT,
    code TEXT,
    cas TEXT,
    brand TEXT,
    size TEXT,
    ship TEXT,
    price TEXT,
    note TEXT,
    manual_compliance TEXT,
    manual_compliance_note TEXT,
    preparation_type TEXT,
    source_brand TEXT NOT NULL,
    PRIMARY KEY (batch_id, original_product_id)
);

CREATE INDEX IF NOT EXISTS idx_product_deleted_rows_brand
    ON product_deleted_rows (batch_id, brand);

CREATE TABLE IF NOT EXISTS product_admin_events (
    id BIGSERIAL PRIMARY KEY,
    action TEXT NOT NULL CHECK (action IN (
        'create', 'update', 'delete_preview', 'delete', 'restore'
    )),
    actor_user_id INTEGER REFERENCES app_users(id) ON DELETE SET NULL,
    actor TEXT NOT NULL,
    product_id INTEGER,
    canonical_brand TEXT,
    row_count BIGINT NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    batch_id UUID REFERENCES product_delete_batches(id) ON DELETE SET NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_product_admin_events_created
    ON product_admin_events (created_at DESC);

COMMIT;

-- Brand-filtered keyset pages use equality on canonical brand plus ID order.
-- CONCURRENTLY avoids blocking product writes while this is built on the
-- production-sized catalogue. It intentionally stays outside BEGIN/COMMIT;
-- run the file with psql -v ON_ERROR_STOP=1 -f as documented in the runbook.
-- A cancelled concurrent build leaves an INVALID relation with the requested
-- name; IF NOT EXISTS would otherwise skip it forever. Fail closed before the
-- build and require the explicit operator recovery documented for Phase 6C3.
DO $$
DECLARE
    v_valid BOOLEAN;
    v_ready BOOLEAN;
    v_unique BOOLEAN;
    v_predicate TEXT;
    v_expressions TEXT;
    v_columns TEXT[];
BEGIN
    SELECT i.indisvalid, i.indisready, i.indisunique, i.indpred::text, i.indexprs::text,
           (
               SELECT array_agg(a.attname::text ORDER BY key.ord)
               FROM unnest(i.indkey::smallint[]) WITH ORDINALITY AS key(attnum, ord)
               JOIN pg_attribute a
                 ON a.attrelid=i.indrelid AND a.attnum=key.attnum
               WHERE key.ord <= i.indnkeyatts
           )
      INTO v_valid, v_ready, v_unique, v_predicate, v_expressions, v_columns
      FROM pg_index i
     WHERE i.indexrelid=to_regclass('idx_products_admin_brand_id')
       AND i.indrelid='products'::regclass;

    IF FOUND AND (
        NOT v_valid OR NOT v_ready OR v_unique
        OR v_predicate IS NOT NULL OR v_expressions IS NOT NULL
        OR v_columns IS DISTINCT FROM ARRAY['brand','id']::text[]
    ) THEN
        RAISE EXCEPTION USING
            MESSAGE = 'idx_products_admin_brand_id exists but is invalid or has the wrong definition',
            HINT = 'Inspect pg_index. Only after confirming this exact index is unusable, run DROP INDEX CONCURRENTLY idx_products_admin_brand_id, then rerun migration 025.';
    END IF;
END $$;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_products_admin_brand_id
    ON products (brand, id);

DO $$
DECLARE
    v_ok BOOLEAN;
BEGIN
    SELECT i.indisvalid AND i.indisready AND NOT i.indisunique
           AND i.indpred IS NULL AND i.indexprs IS NULL
           AND (
               SELECT array_agg(a.attname::text ORDER BY key.ord)
               FROM unnest(i.indkey::smallint[]) WITH ORDINALITY AS key(attnum, ord)
               JOIN pg_attribute a
                 ON a.attrelid=i.indrelid AND a.attnum=key.attnum
               WHERE key.ord <= i.indnkeyatts
           ) = ARRAY['brand','id']::text[]
      INTO v_ok
      FROM pg_index i
     WHERE i.indexrelid=to_regclass('idx_products_admin_brand_id')
       AND i.indrelid='products'::regclass;

    IF v_ok IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'idx_products_admin_brand_id was not created as a valid (brand, id) index';
    END IF;
END $$;
