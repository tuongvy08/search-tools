-- Phase 6D4. PostgreSQL 14. Auth/RBAC only; run with web and workers stopped.
BEGIN;
SELECT pg_advisory_xact_lock(891273465);
CREATE TABLE IF NOT EXISTS admin_rbac_events (
    id BIGSERIAL PRIMARY KEY,
    actor_user_id INTEGER REFERENCES app_users(id) ON DELETE SET NULL,
    target_user_id INTEGER REFERENCES app_users(id) ON DELETE SET NULL,
    event TEXT NOT NULL,
    before_state JSONB NOT NULL,
    after_state JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Column existence is the one-time backfill marker, in this atomic transaction.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema=current_schema() AND table_name='app_users'
                     AND column_name='is_super_admin') THEN
        ALTER TABLE app_users ADD COLUMN is_super_admin BOOLEAN NOT NULL DEFAULT FALSE;
        WITH migrated AS (
            UPDATE app_users SET is_super_admin=TRUE, auth_version=auth_version+1 WHERE is_admin RETURNING id
        )
        INSERT INTO admin_rbac_events(target_user_id,event,before_state,after_state)
        SELECT id,'migration_030', '{"is_admin":true,"is_super_admin":false}'::jsonb,
               '{"is_admin":true,"is_super_admin":true}'::jsonb FROM migrated;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS admin_menu_grants (
    user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    permission_key TEXT NOT NULL CHECK (permission_key IN (
        'products','imports','teams','users','network','quote_templates',
        'exchange_rates','regulatory','stock','manual_priority','login_history')),
    granted_by INTEGER REFERENCES app_users(id) ON DELETE SET NULL,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(user_id, permission_key)
);


-- Defense in depth for existing account writers, including older scripts.
-- HTTP account writers acquire this lock BEFORE actor/target row locks.
CREATE OR REPLACE FUNCTION enforce_admin_rbac_user() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE actor INTEGER;
BEGIN
    actor := NULLIF(current_setting('app.rbac_actor', true), '')::INTEGER;
    IF NOT NEW.is_admin THEN NEW.is_super_admin := FALSE; END IF;
    IF TG_OP='UPDATE' THEN
        IF OLD.is_super_admin AND OLD.account_status='ACTIVE' AND OLD.archived_at IS NULL
           AND (NOT NEW.is_super_admin OR NEW.account_status<>'ACTIVE' OR NEW.archived_at IS NOT NULL) THEN
            PERFORM pg_advisory_xact_lock(891273465);
            IF NOT EXISTS (SELECT 1 FROM app_users WHERE id<>OLD.id AND is_super_admin
                           AND is_admin AND account_status='ACTIVE' AND archived_at IS NULL) THEN
                RAISE EXCEPTION 'Phải còn ít nhất một Super Admin đang hoạt động.' USING ERRCODE='23514';
            END IF;
        END IF;
        IF (OLD.is_admin,OLD.is_super_admin) IS DISTINCT FROM (NEW.is_admin,NEW.is_super_admin) THEN
            NEW.auth_version := GREATEST(NEW.auth_version, OLD.auth_version+1);
            INSERT INTO admin_rbac_events(actor_user_id,target_user_id,event,before_state,after_state)
            VALUES(actor,OLD.id,'role_changed',
                jsonb_build_object('is_admin',OLD.is_admin,'is_super_admin',OLD.is_super_admin),
                jsonb_build_object('is_admin',NEW.is_admin,'is_super_admin',NEW.is_super_admin));
        END IF;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS app_users_admin_rbac ON app_users;
CREATE TRIGGER app_users_admin_rbac BEFORE INSERT OR UPDATE ON app_users
FOR EACH ROW EXECUTE FUNCTION enforce_admin_rbac_user();
CREATE OR REPLACE FUNCTION audit_admin_rbac_create() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.is_admin THEN
        INSERT INTO admin_rbac_events(actor_user_id,target_user_id,event,before_state,after_state)
        VALUES(NULLIF(current_setting('app.rbac_actor', true), '')::INTEGER,NEW.id,'admin_created',
               '{}'::jsonb,jsonb_build_object('is_admin',NEW.is_admin,'is_super_admin',NEW.is_super_admin));
    END IF;
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS app_users_admin_created ON app_users;
CREATE TRIGGER app_users_admin_created AFTER INSERT ON app_users
FOR EACH ROW EXECUTE FUNCTION audit_admin_rbac_create();

-- Any grant change invalidates sessions, including administrative SQL repair.
CREATE OR REPLACE FUNCTION audit_admin_menu_grant() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE target_id INTEGER; actor INTEGER;
BEGIN
    target_id := CASE WHEN TG_OP='DELETE' THEN OLD.user_id ELSE NEW.user_id END;
    actor := NULLIF(current_setting('app.rbac_actor', true), '')::INTEGER;
    UPDATE app_users SET auth_version=auth_version+1 WHERE id=target_id;
    INSERT INTO admin_rbac_events(actor_user_id,target_user_id,event,before_state,after_state)
    VALUES(actor,target_id,'grant_' || lower(TG_OP),
        CASE WHEN TG_OP='INSERT' THEN '{}'::jsonb ELSE jsonb_build_object('permission_key',OLD.permission_key) END,
        CASE WHEN TG_OP='DELETE' THEN '{}'::jsonb ELSE jsonb_build_object('permission_key',NEW.permission_key) END);
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS admin_menu_grant_audit ON admin_menu_grants;
CREATE TRIGGER admin_menu_grant_audit AFTER INSERT OR UPDATE OR DELETE ON admin_menu_grants
FOR EACH ROW EXECUTE FUNCTION audit_admin_menu_grant();

CREATE OR REPLACE FUNCTION clear_demoted_admin_grants() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.is_admin AND NOT NEW.is_admin THEN
        DELETE FROM admin_menu_grants WHERE user_id=NEW.id;
    END IF;
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS app_users_clear_admin_grants ON app_users;
CREATE TRIGGER app_users_clear_admin_grants AFTER UPDATE OF is_admin ON app_users
FOR EACH ROW EXECUTE FUNCTION clear_demoted_admin_grants();
COMMIT;
