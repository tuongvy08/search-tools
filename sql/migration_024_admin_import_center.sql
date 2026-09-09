-- Additive, rerunnable. Apply after 019 and existing application migrations.
BEGIN;
CREATE TABLE IF NOT EXISTS product_import_jobs (
    id UUID PRIMARY KEY,
    submission_key UUID NOT NULL,
    actor TEXT NOT NULL,
    actor_user_id INTEGER NOT NULL REFERENCES app_users(id),
    actor_auth_version INTEGER NOT NULL,
    filename TEXT NOT NULL CHECK (length(filename) <= 180),
    file_size BIGINT NOT NULL,
    file_sha256 TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('append','upsert','replace_by_brand')),
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed','cancelled')),
    phase TEXT NOT NULL DEFAULT 'preview' CHECK (phase IN ('preview','apply')),
    preview_ready BOOLEAN NOT NULL DEFAULT FALSE,
    row_count INTEGER NOT NULL DEFAULT 0,
    processed_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    deleted_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    errors JSONB NOT NULL DEFAULT '[]',
    preview JSONB,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    purged_at TIMESTAMPTZ,
    UNIQUE(actor_user_id, submission_key)
);
CREATE INDEX IF NOT EXISTS product_import_queue ON product_import_jobs(created_at) WHERE status='queued';
CREATE INDEX IF NOT EXISTS product_import_expiry ON product_import_jobs(expires_at) WHERE purged_at IS NULL;
CREATE TABLE IF NOT EXISTS product_import_rows (
    job_id UUID NOT NULL REFERENCES product_import_jobs(id) ON DELETE CASCADE,
    row_number INTEGER NOT NULL,
    data JSONB NOT NULL,
    PRIMARY KEY(job_id,row_number)
);
CREATE TABLE IF NOT EXISTS product_import_events (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES product_import_jobs(id) ON DELETE CASCADE,
    actor TEXT NOT NULL,
    event TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Small legacy rules previews also survive Gunicorn worker changes.
CREATE TABLE IF NOT EXISTS admin_rule_import_previews (
    token UUID PRIMARY KEY, actor TEXT NOT NULL, payload JSONB NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '30 minutes'
);
CREATE INDEX IF NOT EXISTS products_import_identity ON products (upper(trim(brand)),upper(trim(code)));
COMMIT;
