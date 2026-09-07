-- Phase 6C0.1: additive, atomic, idempotent team capability grants.
-- Explicit legacy defaults preserve all existing team access. New registry
-- keys are NOT implicitly granted. No SQL enum constrains registry expansion.
BEGIN;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS permission_keys TEXT[] NOT NULL DEFAULT
    ARRAY['SEARCH','CHECK_LICENSE','FIND_CODE','ADVANCED_SEARCH','SEARCH_BY_CAS',
          'QUICK_QUOTE','COPY','EXPORT','VIEW_NAME','VIEW_CODE','VIEW_CAS','VIEW_BRAND',
          'VIEW_SIZE','VIEW_PRICE','VIEW_NOTE','VIEW_COMPLIANCE','VIEW_COMPLIANCE_NOTE']::TEXT[];
-- NULL means an older preview changes only brands/IP, preserving current grants.
ALTER TABLE team_permission_previews ADD COLUMN IF NOT EXISTS new_permissions TEXT[] NULL;
CREATE TABLE IF NOT EXISTS team_capability_history (
    id BIGSERIAL PRIMARY KEY,
    team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL,
    actor_user_id INTEGER REFERENCES app_users(id) ON DELETE SET NULL,
    old_permissions TEXT[] NOT NULL,
    new_permissions TEXT[] NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_team_capability_history_team
    ON team_capability_history(team_id, changed_at DESC);
COMMIT;
