-- Phase 6C0: reversible User/Team lifecycle.
-- Additive and idempotent. Existing rows remain active; no row is deleted or
-- reassigned by this migration. Old application code remains able to read the
-- schema. LOCAL archives also use the pre-existing SUSPENDED account_status so
-- a code rollback cannot accidentally re-enable an archived LOCAL account.

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL;

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS archived_by INTEGER NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'app_users_archived_by_fkey'
          AND conrelid = 'app_users'::regclass
    ) THEN
        ALTER TABLE app_users
            ADD CONSTRAINT app_users_archived_by_fkey
            FOREIGN KEY (archived_by) REFERENCES app_users(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'app_users_local_archive_consistency_check'
          AND conrelid = 'app_users'::regclass
    ) THEN
        ALTER TABLE app_users
            ADD CONSTRAINT app_users_local_archive_consistency_check
            CHECK (
                (archived_at IS NULL AND archived_by IS NULL)
                OR (archived_at IS NOT NULL AND auth_provider = 'LOCAL'
                    AND account_status = 'SUSPENDED')
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_app_users_archived_at
    ON app_users (archived_at) WHERE archived_at IS NOT NULL;

ALTER TABLE teams
    ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE';

ALTER TABLE teams
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL;

ALTER TABLE teams
    ADD COLUMN IF NOT EXISTS archived_by INTEGER NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'teams_lifecycle_status_check'
          AND conrelid = 'teams'::regclass
    ) THEN
        ALTER TABLE teams
            ADD CONSTRAINT teams_lifecycle_status_check
            CHECK (lifecycle_status IN ('ACTIVE', 'ARCHIVED'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'teams_archived_by_fkey'
          AND conrelid = 'teams'::regclass
    ) THEN
        ALTER TABLE teams
            ADD CONSTRAINT teams_archived_by_fkey
            FOREIGN KEY (archived_by) REFERENCES app_users(id) ON DELETE SET NULL;
    END IF;


    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'teams_archive_consistency_check'
          AND conrelid = 'teams'::regclass
    ) THEN
        ALTER TABLE teams
            ADD CONSTRAINT teams_archive_consistency_check
            CHECK (
                (lifecycle_status = 'ACTIVE' AND archived_at IS NULL AND archived_by IS NULL)
                OR (lifecycle_status = 'ARCHIVED' AND archived_at IS NOT NULL)
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_teams_lifecycle_status
    ON teams (lifecycle_status);

-- DB-backed, one-time preview store. DELETE ... RETURNING makes confirmation
-- atomic across workers; captured_updated_at + member_count reject stale impact.
CREATE TABLE IF NOT EXISTS team_lifecycle_previews (
    token TEXT PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    replacement_team_id INTEGER NULL REFERENCES teams(id) ON DELETE SET NULL,
    captured_updated_at TIMESTAMPTZ NOT NULL,
    captured_member_count INTEGER NOT NULL CHECK (captured_member_count >= 0),
    created_by INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_team_lifecycle_previews_created_at
    ON team_lifecycle_previews (created_at);

-- Defense in depth and rollback compatibility: even older app code cannot
-- assign a user to an archived team. The trigger performs one indexed PK read
-- only when team_id is inserted/changed, never on ordinary request paths.
CREATE OR REPLACE FUNCTION enforce_active_team_membership()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.team_id IS NOT NULL AND (
        TG_OP = 'INSERT' OR OLD.team_id IS DISTINCT FROM NEW.team_id
    ) THEN
        -- FOR SHARE conflicts with the lifecycle UPDATE's FOR UPDATE lock.
        -- Whichever transaction locks first wins: archive either sees and
        -- transfers the new member, or the assignment wakes after archive and
        -- fails because the row is no longer ACTIVE.
        PERFORM id FROM teams
        WHERE id = NEW.team_id AND lifecycle_status = 'ACTIVE'
        FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'team is not active' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS app_users_active_team_membership_trigger ON app_users;
CREATE TRIGGER app_users_active_team_membership_trigger
    BEFORE INSERT OR UPDATE OF team_id ON app_users
    FOR EACH ROW EXECUTE FUNCTION enforce_active_team_membership();
