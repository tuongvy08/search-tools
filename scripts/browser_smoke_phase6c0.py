"""Run a disposable Phase 6C0 browser-smoke server on a temporary DB."""
import os
import signal
import sys
from pathlib import Path

import psycopg2
from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from pg_temp_db import create_full_schema_temp_db, drop_temp_db  # noqa: E402


def main():
    db_name, dsn = create_full_schema_temp_db()
    os.environ.update(
        DATABASE_URL=dsn,
        FLASK_SECRET_KEY="phase6c0-browser-smoke-only",
        DISABLE_IP_ALLOWLIST="1",
        ENABLE_LEGACY_PASSWORD_LOGIN="0",
    )
    import search  # noqa: E402

    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products(name, code, brand, size, ship, price) "
                "VALUES ('Smoke product', 'SMOKE-1', 'Smoke Brand', '1L', '1', '1000')"
            )
            cur.execute("INSERT INTO teams(name) VALUES ('Team nguồn') RETURNING id")
            source = cur.fetchone()[0]
            cur.execute("INSERT INTO teams(name) VALUES ('Team thay thế') RETURNING id")
            replacement = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO teams(name, lifecycle_status, archived_at) "
                "VALUES ('Team đã lưu trữ', 'ARCHIVED', NOW())"
            )
            cur.execute(
                "INSERT INTO team_brands(team_id, brand) VALUES (%s, 'Smoke Brand')",
                (source,),
            )
            cur.execute(
                "INSERT INTO app_users(username, password_hash, is_admin, auth_provider, account_status) "
                "VALUES ('smoke-admin', %s, TRUE, 'LOCAL', 'ACTIVE') RETURNING id",
                (generate_password_hash("smoke-pass"),),
            )
            admin_id = cur.fetchone()[0]
            for username in ("staff-one", "staff-two"):
                cur.execute(
                    "INSERT INTO app_users(username, password_hash, team_id, is_admin, auth_provider, account_status) "
                    "VALUES (%s, %s, %s, FALSE, 'LOCAL', 'ACTIVE')",
                    (username, generate_password_hash("pw"), source),
                )
            cur.execute(
                "INSERT INTO app_users(username, password_hash, is_admin, auth_provider, account_status, "
                "archived_at, archived_by) VALUES ('local-archived', %s, FALSE, 'LOCAL', "
                "'SUSPENDED', NOW(), %s)",
                (generate_password_hash("pw"), admin_id),
            )
    finally:
        conn.close()

    cleaned = False

    def cleanup(*_args):
        nonlocal cleaned
        if not cleaned:
            cleaned = True
            drop_temp_db(db_name)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    print("PHASE6C0_SMOKE http://127.0.0.1:5010 smoke-admin smoke-pass", flush=True)
    try:
        search.app.run(host="127.0.0.1", port=5010, debug=False, use_reloader=False)
    finally:
        if not cleaned:
            drop_temp_db(db_name)


if __name__ == "__main__":
    main()
