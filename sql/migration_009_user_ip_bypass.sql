-- Cho phép một số user bỏ qua giới hạn IP (quản lý từ admin/users).
-- psql "$DATABASE_URL" -f sql/migration_009_user_ip_bypass.sql

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS ip_bypass_allowlist BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_app_users_ip_bypass
    ON app_users (ip_bypass_allowlist);
