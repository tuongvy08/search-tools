-- Log lịch sử import (products/rules) để audit và rollback thủ công
CREATE TABLE IF NOT EXISTS import_jobs (
    id              BIGSERIAL PRIMARY KEY,
    dataset         TEXT NOT NULL CHECK (dataset IN ('products', 'regulatory_rules')),
    mode            TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    filename        TEXT,
    row_count       INTEGER NOT NULL DEFAULT 0,
    inserted_count  INTEGER NOT NULL DEFAULT 0,
    updated_count   INTEGER NOT NULL DEFAULT 0,
    deleted_count   INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    created_by      TEXT,
    meta_json       JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_import_jobs_dataset_created_at
    ON import_jobs (dataset, created_at DESC);
