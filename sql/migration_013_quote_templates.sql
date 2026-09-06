-- Phase Mapping Wizard #1: admin-managed quote workbook templates.
-- Stores validated .xlsx template versions in PostgreSQL; no binary seed data.

CREATE TABLE IF NOT EXISTS quote_templates (
    id BIGSERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    content BYTEA NOT NULL,
    content_sha256 TEXT NOT NULL,
    content_size INTEGER NOT NULL,
    profile_version TEXT NOT NULL,
    mapping_json JSONB NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    uploaded_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'quote_templates_filename_check'
          AND conrelid = 'quote_templates'::regclass
    ) THEN
        ALTER TABLE quote_templates
            ADD CONSTRAINT quote_templates_filename_check
            CHECK (
                length(trim(filename)) > 0
                AND filename = regexp_replace(filename, '^.*[\\/]', '')
                AND lower(filename) LIKE '%.xlsx'
                AND lower(filename) NOT LIKE '%.xlsm'
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'quote_templates_content_size_check'
          AND conrelid = 'quote_templates'::regclass
    ) THEN
        ALTER TABLE quote_templates
            ADD CONSTRAINT quote_templates_content_size_check
            CHECK (
                content_size > 0
                AND content_size <= 10485760
                AND octet_length(content) = content_size
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'quote_templates_content_sha256_check'
          AND conrelid = 'quote_templates'::regclass
    ) THEN
        ALTER TABLE quote_templates
            ADD CONSTRAINT quote_templates_content_sha256_check
            CHECK (content_sha256 ~ '^[0-9a-f]{64}$');
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'quote_templates_profile_version_check'
          AND conrelid = 'quote_templates'::regclass
    ) THEN
        ALTER TABLE quote_templates
            ADD CONSTRAINT quote_templates_profile_version_check
            CHECK (profile_version = 'BG_V1');
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'quote_templates_mapping_json_check'
          AND conrelid = 'quote_templates'::regclass
    ) THEN
        ALTER TABLE quote_templates
            ADD CONSTRAINT quote_templates_mapping_json_check
            CHECK (
                jsonb_typeof(mapping_json) = 'object'
                AND mapping_json->>'profile_version' = profile_version
                AND mapping_json->>'sheet' = 'BG'
                AND (mapping_json->>'header_row')::integer = 16
                AND (mapping_json->>'product_start_row')::integer = 17
                AND mapping_json->>'total_label' = 'Tổng giá'
                AND jsonb_typeof(mapping_json->'mapping') = 'object'
            );
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS quote_templates_one_active_idx
    ON quote_templates (is_active)
    WHERE is_active = TRUE;

CREATE INDEX IF NOT EXISTS quote_templates_created_at_idx
    ON quote_templates (created_at DESC, id DESC);
