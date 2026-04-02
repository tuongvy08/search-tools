-- Phân quyền theo team + brand (chạy sau sql/schema.sql)
-- psql "$DATABASE_URL" -f sql/migration_002_team_rbac.sql

CREATE TABLE IF NOT EXISTS teams (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS team_brands (
    team_id INTEGER NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    brand   TEXT NOT NULL,
    PRIMARY KEY (team_id, brand)
);

CREATE INDEX IF NOT EXISTS idx_team_brands_team ON team_brands (team_id);

CREATE TABLE IF NOT EXISTS app_users (
    id             SERIAL PRIMARY KEY,
    username       TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    team_id        INTEGER REFERENCES teams (id) ON DELETE SET NULL,
    is_admin       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_app_users_team ON app_users (team_id);

-- Team mẫu (gán brand sau bằng script seed_team_brands.py)
INSERT INTO teams (name) VALUES ('Team mẫu')
ON CONFLICT (name) DO NOTHING;
