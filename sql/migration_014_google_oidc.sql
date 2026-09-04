-- Phase 5D1: Google Workspace OIDC foundation.
-- Adds Google identity + lifecycle columns to app_users and a login audit
-- table. Only touches auth tables (app_users, login_audit_events); does not
-- touch `products` (~1.34M rows), so there is no risk of a long scan/lock on
-- the large table. app_users itself is a small internal-staff table, so
-- adding NOT NULL columns with constant defaults here is a cheap,
-- metadata-only operation on PostgreSQL 11+.
--
-- Idempotent: safe to re-run. Existing rows are backfilled to auth_provider
-- = 'LOCAL' and account_status = 'ACTIVE' by column DEFAULT (no separate
-- UPDATE pass, no accidental break-glass marking).

-- 1. New columns on app_users -----------------------------------------------

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS auth_provider TEXT NOT NULL DEFAULT 'LOCAL';

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS google_sub TEXT NULL;

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS email TEXT NULL;

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS display_name TEXT NULL;

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS account_status TEXT NOT NULL DEFAULT 'ACTIVE';

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS approved_by INTEGER NULL;

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ NULL;

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ NULL;

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS auth_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS is_break_glass BOOLEAN NOT NULL DEFAULT FALSE;

-- password_hash was NOT NULL; Google-provisioned users have no password.
ALTER TABLE app_users
    ALTER COLUMN password_hash DROP NOT NULL;

-- 2. Constraints (guarded so re-running the migration is a no-op) -----------

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'app_users_auth_provider_check'
          AND conrelid = 'app_users'::regclass
    ) THEN
        ALTER TABLE app_users
            ADD CONSTRAINT app_users_auth_provider_check
            CHECK (auth_provider IN ('LOCAL', 'GOOGLE'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'app_users_account_status_check'
          AND conrelid = 'app_users'::regclass
    ) THEN
        ALTER TABLE app_users
            ADD CONSTRAINT app_users_account_status_check
            CHECK (account_status IN ('INVITED', 'PENDING', 'ACTIVE', 'SUSPENDED'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'app_users_auth_version_positive_check'
          AND conrelid = 'app_users'::regclass
    ) THEN
        ALTER TABLE app_users
            ADD CONSTRAINT app_users_auth_version_positive_check
            CHECK (auth_version > 0);
    END IF;

    -- LOCAL accounts must keep a password hash; GOOGLE accounts may omit it.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'app_users_local_requires_password_check'
          AND conrelid = 'app_users'::regclass
    ) THEN
        ALTER TABLE app_users
            ADD CONSTRAINT app_users_local_requires_password_check
            CHECK (auth_provider <> 'LOCAL' OR password_hash IS NOT NULL);
    END IF;

    -- Self-FK for the approver; SET NULL so deleting/removing an approver
    -- never cascades into deleting the users they approved.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'app_users_approved_by_fkey'
          AND conrelid = 'app_users'::regclass
    ) THEN
        ALTER TABLE app_users
            ADD CONSTRAINT app_users_approved_by_fkey
            FOREIGN KEY (approved_by) REFERENCES app_users (id) ON DELETE SET NULL;
    END IF;
END $$;

-- 3. Uniqueness -------------------------------------------------------------

-- Partial unique index: only enforced when google_sub is present.
CREATE UNIQUE INDEX IF NOT EXISTS app_users_google_sub_unique_idx
    ON app_users (google_sub)
    WHERE google_sub IS NOT NULL;

-- Case-insensitive unique email, only enforced when email is present.
CREATE UNIQUE INDEX IF NOT EXISTS app_users_email_lower_unique_idx
    ON app_users (lower(email))
    WHERE email IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_app_users_account_status ON app_users (account_status);
CREATE INDEX IF NOT EXISTS idx_app_users_auth_provider ON app_users (auth_provider);

-- 4. Login audit table --------------------------------------------------
-- No token, authorization code, client secret, or raw session cookie is
-- ever stored here — only outcome metadata for security auditing.

CREATE TABLE IF NOT EXISTS login_audit_events (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NULL REFERENCES app_users (id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason_code TEXT NULL,
    email_snapshot TEXT NULL,
    domain_snapshot TEXT NULL,
    source_ip INET NULL,
    user_agent TEXT NULL,
    request_id TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'login_audit_events_provider_check'
          AND conrelid = 'login_audit_events'::regclass
    ) THEN
        ALTER TABLE login_audit_events
            ADD CONSTRAINT login_audit_events_provider_check
            CHECK (provider IN ('LOCAL', 'GOOGLE'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'login_audit_events_outcome_check'
          AND conrelid = 'login_audit_events'::regclass
    ) THEN
        ALTER TABLE login_audit_events
            ADD CONSTRAINT login_audit_events_outcome_check
            CHECK (outcome IN ('SUCCESS', 'FAILURE', 'PENDING_APPROVAL', 'DENIED'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'login_audit_events_reason_code_len_check'
          AND conrelid = 'login_audit_events'::regclass
    ) THEN
        ALTER TABLE login_audit_events
            ADD CONSTRAINT login_audit_events_reason_code_len_check
            CHECK (reason_code IS NULL OR length(reason_code) <= 64);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'login_audit_events_email_snapshot_len_check'
          AND conrelid = 'login_audit_events'::regclass
    ) THEN
        ALTER TABLE login_audit_events
            ADD CONSTRAINT login_audit_events_email_snapshot_len_check
            CHECK (email_snapshot IS NULL OR length(email_snapshot) <= 320);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'login_audit_events_domain_snapshot_len_check'
          AND conrelid = 'login_audit_events'::regclass
    ) THEN
        ALTER TABLE login_audit_events
            ADD CONSTRAINT login_audit_events_domain_snapshot_len_check
            CHECK (domain_snapshot IS NULL OR length(domain_snapshot) <= 255);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'login_audit_events_user_agent_len_check'
          AND conrelid = 'login_audit_events'::regclass
    ) THEN
        ALTER TABLE login_audit_events
            ADD CONSTRAINT login_audit_events_user_agent_len_check
            CHECK (user_agent IS NULL OR length(user_agent) <= 512);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'login_audit_events_request_id_len_check'
          AND conrelid = 'login_audit_events'::regclass
    ) THEN
        ALTER TABLE login_audit_events
            ADD CONSTRAINT login_audit_events_request_id_len_check
            CHECK (request_id IS NULL OR length(request_id) <= 100);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_login_audit_events_user ON login_audit_events (user_id);
CREATE INDEX IF NOT EXISTS idx_login_audit_events_created_at ON login_audit_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_login_audit_events_outcome ON login_audit_events (outcome);

-- 5. Admin action audit — actor vs target (Phase 5D2B) ----------------------
-- Admin account-lifecycle actions (approve/invite/suspend/reactivate/revoke
-- sessions) need to record BOTH who performed the action (actor) and which
-- account it was performed on (target). `user_id` already means "the
-- account this audit row is about" (the target, e.g. the person who logged
-- in, or the person who got approved/suspended). `actor_user_id` is who
-- performed an admin action; it is NULL for ordinary login/logout events
-- that have no separate admin actor. No token/OAuth code/cookie/secret is
-- added by this section, same as the rest of this migration.
ALTER TABLE login_audit_events
    ADD COLUMN IF NOT EXISTS actor_user_id INTEGER NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'login_audit_events_actor_user_id_fkey'
          AND conrelid = 'login_audit_events'::regclass
    ) THEN
        ALTER TABLE login_audit_events
            ADD CONSTRAINT login_audit_events_actor_user_id_fkey
            FOREIGN KEY (actor_user_id) REFERENCES app_users (id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_login_audit_events_actor ON login_audit_events (actor_user_id);
