-- Phase 6D2: immutable inventory snapshots and durable full-replacement jobs.
-- Additive/rerunnable. Apply after migration 028.
BEGIN;

CREATE TABLE IF NOT EXISTS stock_snapshots (
    id UUID PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('IMPORT', 'RESTORE')),
    source_job_id UUID,
    restored_from_snapshot_id UUID REFERENCES stock_snapshots(id),
    replaced_snapshot_id UUID REFERENCES stock_snapshots(id),
    actor TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    content_sha256 TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stock_items (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id UUID NOT NULL REFERENCES stock_snapshots(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    code TEXT NOT NULL,
    cas TEXT,
    brand TEXT NOT NULL,
    size TEXT NOT NULL,
    stock_price_vnd NUMERIC(18,2),
    quantity INTEGER NOT NULL CHECK (quantity >= 0),
    expiry_date DATE,
    brand_norm TEXT NOT NULL,
    code_norm TEXT NOT NULL,
    size_norm TEXT NOT NULL,
    cas_norm TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (stock_price_vnd IS NULL OR stock_price_vnd >= 0),
    CHECK (length(name) BETWEEN 1 AND 500),
    CHECK (length(code) BETWEEN 1 AND 500),
    CHECK (length(brand) BETWEEN 1 AND 180),
    CHECK (length(size) BETWEEN 1 AND 500),
    UNIQUE NULLS NOT DISTINCT (snapshot_id, brand_norm, code_norm, size_norm, expiry_date)
);
CREATE INDEX IF NOT EXISTS idx_stock_items_snapshot_code
    ON stock_items(snapshot_id, code_norm);
CREATE INDEX IF NOT EXISTS idx_stock_items_snapshot_cas
    ON stock_items(snapshot_id, cas_norm) WHERE cas_norm IS NOT NULL;

CREATE TABLE IF NOT EXISTS stock_state (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    active_snapshot_id UUID REFERENCES stock_snapshots(id) ON DELETE RESTRICT,
    revision BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO stock_state(singleton) VALUES (TRUE) ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS stock_import_jobs (
    id UUID PRIMARY KEY,
    submission_key UUID NOT NULL,
    actor TEXT NOT NULL,
    actor_user_id INTEGER NOT NULL REFERENCES app_users(id),
    actor_auth_version INTEGER NOT NULL,
    filename TEXT NOT NULL CHECK (length(filename) <= 180),
    file_size BIGINT NOT NULL CHECK (file_size >= 0),
    file_sha256 TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'replace_all' CHECK (mode = 'replace_all'),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','completed','failed','cancelled')),
    phase TEXT NOT NULL DEFAULT 'preview' CHECK (phase IN ('preview','apply')),
    preview_ready BOOLEAN NOT NULL DEFAULT FALSE,
    row_count INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    processed_count INTEGER NOT NULL DEFAULT 0 CHECK (processed_count >= 0),
    inserted_count INTEGER NOT NULL DEFAULT 0 CHECK (inserted_count >= 0),
    deleted_count INTEGER NOT NULL DEFAULT 0 CHECK (deleted_count >= 0),
    error_count INTEGER NOT NULL DEFAULT 0 CHECK (error_count >= 0),
    errors JSONB NOT NULL DEFAULT '[]',
    preview JSONB,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    UNIQUE(actor_user_id, submission_key)
);
CREATE INDEX IF NOT EXISTS stock_import_queue
    ON stock_import_jobs(created_at) WHERE status='queued';

CREATE TABLE IF NOT EXISTS stock_import_events (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES stock_import_jobs(id) ON DELETE RESTRICT,
    actor TEXT NOT NULL,
    event TEXT NOT NULL,
    detail JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS stock_snapshot_events (
    id BIGSERIAL PRIMARY KEY,
    snapshot_id UUID NOT NULL REFERENCES stock_snapshots(id) ON DELETE RESTRICT,
    actor TEXT NOT NULL,
    event TEXT NOT NULL,
    detail JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'stock_snapshots'::regclass
          AND conname = 'stock_snapshots_source_job_id_fkey'
    ) THEN
        ALTER TABLE stock_snapshots
            ADD CONSTRAINT stock_snapshots_source_job_id_fkey
            FOREIGN KEY (source_job_id) REFERENCES stock_import_jobs(id) ON DELETE RESTRICT;
    END IF;
END $$;

COMMIT;
