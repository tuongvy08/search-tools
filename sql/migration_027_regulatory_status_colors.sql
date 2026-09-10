-- Phase 6D1.1: finite presentation colours for stable regulatory statuses.
-- Additive/rerunnable; run after migration 026. This migration never updates
-- products or rules and never derives identity from a mutable display label.

BEGIN;

DO $$
DECLARE
    added_now BOOLEAN := FALSE;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'regulatory_statuses'
          AND column_name = 'color_key'
    ) THEN
        ALTER TABLE regulatory_statuses
            ADD COLUMN color_key TEXT NOT NULL DEFAULT 'gray';
        added_now := TRUE;
    END IF;

    -- Preserve the familiar legacy tones exactly once, when the column is
    -- introduced. Rerunning this migration must not overwrite admin choices.
    IF added_now THEN
        UPDATE regulatory_statuses
        SET color_key = CASE stable_key
            WHEN 'CAM_NHAP' THEN 'red'
            WHEN 'PHU_LUC_II' THEN 'amber'
            WHEN 'PHU_LUC_III' THEN 'teal'
            WHEN 'DUOC_BAN' THEN 'green'
            WHEN 'CAN_GIAY_PHEP' THEN 'blue'
            WHEN 'CHUA_XAC_DINH' THEN 'gray'
            ELSE 'gray'
        END;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'regulatory_statuses'::regclass
          AND conname = 'regulatory_statuses_color_key_check'
    ) THEN
        ALTER TABLE regulatory_statuses
            ADD CONSTRAINT regulatory_statuses_color_key_check
            CHECK (color_key IN ('gray','red','amber','teal','green','blue','purple'));
    END IF;
END $$;

COMMIT;
