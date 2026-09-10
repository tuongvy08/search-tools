-- Phase 6D1.1 follow-up: optional custom RGB seed for each stable status.
-- Additive/rerunnable; run after migration 027.  NULL means the exact 027
-- color_key palette remains authoritative.  A valid color_hex overrides
-- color_key at read time.  This migration never touches products or rules.

BEGIN;

ALTER TABLE regulatory_statuses
    ADD COLUMN IF NOT EXISTS color_hex TEXT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'regulatory_statuses'::regclass
          AND conname = 'regulatory_statuses_color_hex_check'
    ) THEN
        ALTER TABLE regulatory_statuses
            ADD CONSTRAINT regulatory_statuses_color_hex_check
            CHECK (color_hex IS NULL OR color_hex ~ '^#[0-9A-F]{6}$');
    END IF;
END $$;

COMMENT ON COLUMN regulatory_statuses.color_hex IS
    'Optional canonical #RRGGBB seed; overrides color_key without changing export policy.';

COMMIT;
