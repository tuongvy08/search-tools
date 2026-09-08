-- Phase 6C1.1: reversible quote-template lifecycle.
-- Additive/idempotent. Archived workbook bytes and mappings remain available
-- to admins for audit/download, but archived rows cannot be active or resolved.

BEGIN;

ALTER TABLE quote_templates
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL;

ALTER TABLE quote_templates
    ADD COLUMN IF NOT EXISTS archived_by TEXT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'quote_templates_archive_state_check'
          AND conrelid = 'quote_templates'::regclass
    ) THEN
        ALTER TABLE quote_templates
            ADD CONSTRAINT quote_templates_archive_state_check CHECK (
                (archived_at IS NULL AND archived_by IS NULL)
                OR (
                    archived_at IS NOT NULL
                    AND COALESCE(length(trim(archived_by)) > 0, FALSE)
                )
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'quote_templates_archived_not_active_check'
          AND conrelid = 'quote_templates'::regclass
    ) THEN
        ALTER TABLE quote_templates
            ADD CONSTRAINT quote_templates_archived_not_active_check CHECK (
                archived_at IS NULL OR is_active = FALSE
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS quote_templates_archived_at_idx
    ON quote_templates (archived_at DESC, id DESC)
    WHERE archived_at IS NOT NULL;

COMMIT;
