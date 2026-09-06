-- Phase 6A: team-level IP policy + audit target for team-level admin
-- actions. Additive/idempotent only -- never changes existing team_id /
-- team_brands / app_users permission data, and never touches `products`.
-- psql "$DATABASE_URL" -f sql/migration_015_team_policy.sql

-- 1. Team IP policy ----------------------------------------------------------
-- 'INHERIT'           -- current global/env/DB allowlist behaviour, incl.
--                         the existing per-user ip_bypass_allowlist exception
--                         (kept for backward compatibility).
-- 'ALLOWLIST_ONLY'     -- team members must match an active allowlist rule;
--                         no rule configured => deny (never implicit allow-all).
--                         Personal ip_bypass_allowlist does NOT apply.
-- 'ANY_AUTHENTICATED'  -- any IP, but still requires an authenticated ACTIVE
--                         account with a valid session (never anonymous).
-- Existing teams default to 'INHERIT' -- zero behaviour change for anyone
-- until an admin explicitly picks a different policy on the new Team page.
ALTER TABLE teams
    ADD COLUMN IF NOT EXISTS ip_policy TEXT NOT NULL DEFAULT 'INHERIT';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'teams_ip_policy_check'
          AND conrelid = 'teams'::regclass
    ) THEN
        ALTER TABLE teams
            ADD CONSTRAINT teams_ip_policy_check
            CHECK (ip_policy IN ('INHERIT', 'ALLOWLIST_ONLY', 'ANY_AUTHENTICATED'));
    END IF;
END $$;

-- 2. Optimistic-concurrency stamp for the preview -> confirm flow -----------
-- Bumped by the app (SET updated_at = NOW()) every time team_brands or
-- ip_policy actually changes for this team. The admin UI captures this
-- value when it builds a permission-change preview and re-checks it at
-- confirm time; a mismatch means someone else changed the team meanwhile,
-- and the app must require a fresh preview instead of silently overwriting
-- a newer configuration.
ALTER TABLE teams
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- 3. Audit target for team-level admin actions -------------------------------
-- Team CRUD / permission changes are not "about" a single target user, so
-- they need their own object reference distinct from `user_id` (which
-- already means "the account this audit row is about"). Nullable, ON
-- DELETE SET NULL -- never blocks deleting a team, never required by any
-- pre-existing row.
ALTER TABLE login_audit_events
    ADD COLUMN IF NOT EXISTS target_team_id INTEGER NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'login_audit_events_target_team_id_fkey'
          AND conrelid = 'login_audit_events'::regclass
    ) THEN
        ALTER TABLE login_audit_events
            ADD CONSTRAINT login_audit_events_target_team_id_fkey
            FOREIGN KEY (target_team_id) REFERENCES teams (id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_login_audit_events_target_team ON login_audit_events (target_team_id);
