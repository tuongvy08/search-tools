-- Phase 6C1: versioned flexible quote mappings and one template assignment per team.
-- Additive/idempotent; mapping_json and its BG_V1 constraint remain untouched
-- for rollback compatibility with Phase 6C0.1.

BEGIN;

ALTER TABLE quote_templates ADD COLUMN IF NOT EXISTS mapping_v2_json JSONB NULL;

-- Enrich existing immutable versions without changing their legacy mapping.
UPDATE quote_templates
SET mapping_v2_json = mapping_json
    || '{"mapping_version":1,"total_formula_column":"J"}'::jsonb
WHERE mapping_v2_json IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'quote_templates_mapping_v2_json_check'
          AND conrelid = 'quote_templates'::regclass
    ) THEN
        ALTER TABLE quote_templates
            ADD CONSTRAINT quote_templates_mapping_v2_json_check CHECK (
                mapping_v2_json IS NULL OR CASE
                    WHEN jsonb_typeof(mapping_v2_json) <> 'object'
                      OR NOT (mapping_v2_json ?& ARRAY[
                          'profile_version','mapping_version','sheet','header_row',
                          'product_start_row','total_label','total_formula_column','mapping'
                      ])
                      OR jsonb_typeof(mapping_v2_json->'mapping_version') <> 'number'
                      OR jsonb_typeof(mapping_v2_json->'header_row') <> 'number'
                      OR jsonb_typeof(mapping_v2_json->'product_start_row') <> 'number'
                      OR jsonb_typeof(mapping_v2_json->'mapping') <> 'object'
                    THEN FALSE
                    ELSE COALESCE(
                        mapping_v2_json->>'profile_version' = profile_version
                        AND (mapping_v2_json->>'mapping_version')::integer = 1
                        AND length(trim(mapping_v2_json->>'sheet')) > 0
                        AND (mapping_v2_json->>'header_row')::integer >= 1
                        AND (mapping_v2_json->>'product_start_row')::integer
                            > (mapping_v2_json->>'header_row')::integer
                        AND length(trim(mapping_v2_json->>'total_label')) > 0
                        AND length(trim(mapping_v2_json->>'total_formula_column')) > 0
                        AND jsonb_typeof(mapping_v2_json->'mapping'->'sequence') = 'string'
                        AND length(trim(mapping_v2_json->'mapping'->>'sequence')) > 0
                        AND jsonb_typeof(mapping_v2_json->'mapping'->'Name') = 'string'
                        AND length(trim(mapping_v2_json->'mapping'->>'Name')) > 0
                        AND jsonb_typeof(mapping_v2_json->'mapping'->'Code') = 'string'
                        AND length(trim(mapping_v2_json->'mapping'->>'Code')) > 0
                        AND jsonb_typeof(mapping_v2_json->'mapping'->'Unit_Price_Value') = 'string'
                        AND length(trim(mapping_v2_json->'mapping'->>'Unit_Price_Value')) > 0,
                        FALSE
                    )
                END
            );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS team_quote_templates (
    team_id INTEGER PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
    template_id BIGINT NOT NULL REFERENCES quote_templates(id) ON DELETE RESTRICT,
    assigned_by TEXT NOT NULL,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS team_quote_templates_template_idx
    ON team_quote_templates(template_id);

COMMIT;
