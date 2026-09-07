"""Isolated real-PostgreSQL rehearsal for migration 020.

Creates and drops a brand-new database with a guarded prefix. It never opens
the application database named in DATABASE_URL; only the maintenance database
and the throwaway database are used.
"""
import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg2


ROOT = Path(__file__).resolve().parents[1]
PREFIX = "p6c0_rehearsal_"
BASE_SQL = """
CREATE TABLE teams (id SERIAL PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE app_users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    team_id INTEGER REFERENCES teams(id) ON DELETE SET NULL,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    ip_bypass_allowlist BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE team_brands (
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    brand TEXT NOT NULL,
    PRIMARY KEY (team_id, brand)
);
"""


def dsn_for(dbname):
    parsed = urlparse(os.environ["DATABASE_URL"])
    return urlunparse(parsed._replace(path="/" + dbname))


def sql(name):
    return (ROOT / "sql" / name).read_text(encoding="utf-8")


def main():
    dbname = PREFIX + secrets.token_hex(4)
    maint = psycopg2.connect(dsn_for("postgres"))
    maint.autocommit = True
    try:
        with maint.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        maint.close()

    results = {"database": dbname, "temporary_only": True}
    try:
        conn = psycopg2.connect(dsn_for(dbname))
        try:
            with conn, conn.cursor() as cur:
                cur.execute(BASE_SQL)
                for name in (
                    "migration_014_google_oidc.sql",
                    "migration_015_team_policy.sql",
                    "migration_016_team_permission_previews.sql",
                ):
                    cur.execute(sql(name))
                migration = sql("migration_020_admin_lifecycle.sql")
                cur.execute(migration)
                cur.execute(migration)

                cur.execute("INSERT INTO teams(name) VALUES ('Source'), ('Replacement') RETURNING id")
                source_id, replacement_id = [row[0] for row in cur.fetchall()]
                cur.execute(
                    "INSERT INTO app_users(username, password_hash, team_id, is_admin) "
                    "VALUES ('admin', 'x', NULL, TRUE), ('staff', 'x', %s, FALSE) RETURNING id",
                    (source_id,),
                )
                admin_id, staff_id = [row[0] for row in cur.fetchall()]
                cur.execute(
                    "UPDATE app_users SET account_status = 'SUSPENDED', archived_at = NOW(), "
                    "archived_by = %s, auth_version = auth_version + 1 WHERE id = %s",
                    (admin_id, staff_id),
                )
                # An old binary only reads these pre-020 columns; it still sees
                # SUSPENDED and cannot authenticate the archived LOCAL user.
                cur.execute(
                    "SELECT account_status, auth_version FROM app_users WHERE id = %s",
                    (staff_id,),
                )
                old_status, old_version = cur.fetchone()
                results["old_code_local_login_blocked"] = old_status == "SUSPENDED" and old_version == 2

                cur.execute("UPDATE app_users SET team_id = %s WHERE id = %s", (replacement_id, staff_id))
                cur.execute(
                    "UPDATE teams SET lifecycle_status = 'ARCHIVED', archived_at = NOW(), "
                    "archived_by = %s WHERE id = %s",
                    (admin_id, source_id),
                )
                cur.execute("SAVEPOINT archived_assignment_check")
                try:
                    cur.execute("UPDATE app_users SET team_id = %s WHERE id = %s", (source_id, staff_id))
                    results["old_code_archived_team_assignment_blocked"] = False
                except psycopg2.Error:
                    cur.execute("ROLLBACK TO SAVEPOINT archived_assignment_check")
                    results["old_code_archived_team_assignment_blocked"] = True
                cur.execute("RELEASE SAVEPOINT archived_assignment_check")

            results["migration_idempotent"] = True
        finally:
            conn.close()
    finally:
        if not dbname.startswith(PREFIX):
            raise RuntimeError("unsafe temporary database name")
        maint = psycopg2.connect(dsn_for("postgres"))
        maint.autocommit = True
        try:
            with maint.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (dbname,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        finally:
            maint.close()

    if not all(value is True for key, value in results.items() if key not in {"database"}):
        raise SystemExit(json.dumps(results, ensure_ascii=False))
    print(json.dumps(results, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
