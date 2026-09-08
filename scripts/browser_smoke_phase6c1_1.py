"""Disposable Phase 6C1.1 browser QA server; loopback and temporary DB only."""
import hashlib
import json
import os
import signal
import sys
from pathlib import Path

import psycopg2
from werkzeug.security import generate_password_hash


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from pg_temp_db import (  # noqa: E402
    apply_brand_master_and_currency_migrations,
    apply_dynamic_brand_currency_migration,
    create_full_schema_temp_db,
    drop_temp_db,
)


def main():
    dbname, dsn = create_full_schema_temp_db()
    try:
        os.environ.update(
            DATABASE_URL=dsn,
            FLASK_SECRET_KEY="disposable-browser-6c11",
            DISABLE_IP_ALLOWLIST="1",
            ENABLE_LEGACY_PASSWORD_LOGIN="0",
        )
        import search
        import team_permissions
        from test_quote_workbook_export import make_workbook

        raw = make_workbook()
        mapping = json.dumps(search._quote_template_mapping_snapshot(), ensure_ascii=False)
        conn = psycopg2.connect(dsn)
        try:
            with conn, conn.cursor() as cur:
                apply_brand_master_and_currency_migrations(cur)
                apply_dynamic_brand_currency_migration(cur)
                cur.execute(
                    "INSERT INTO app_users(username,password_hash,is_admin) VALUES ('qa-admin',%s,true)",
                    (generate_password_hash("qa-only"),),
                )
                cur.execute(
                    "INSERT INTO teams(name,permission_keys) VALUES ('Kinh doanh miền Nam',%s),('Kinh doanh miền Bắc',%s) RETURNING id",
                    (sorted(team_permissions.REGISTRY), sorted(team_permissions.REGISTRY)),
                )
                team_south, team_north = [row[0] for row in cur.fetchall()]
                cur.execute(
                    "INSERT INTO team_brands(team_id,brand) VALUES (%s,'TRC'),(%s,'TRC')",
                    (team_south, team_north),
                )
                cur.execute(
                    "INSERT INTO app_users(username,password_hash,team_id) VALUES ('qa-sales',%s,%s)",
                    (generate_password_hash("qa-only"), team_south),
                )
                cur.execute(
                    """
                    INSERT INTO products(name,code,cas,brand,source_brand,size,ship,price,note,manual_compliance,manual_compliance_note)
                    VALUES
                      ('Sản phẩm cần kiểm tra','QA-UNKNOWN','64-17-5','TRC','TRC','100mg','1','1000','Có sẵn','Chưa xác định','Cần kiểm tra hồ sơ'),
                      ('Sản phẩm cấm nhập','QA-BANNED','67-56-1','TRC','TRC','500mg','1','2000','Không báo giá','CẤM NHẬP','Không được xuất báo giá')
                    """
                )
                cur.execute("INSERT INTO brand_compliance_settings(brand_norm,manual_compliance_priority) VALUES ('TRC',true)")

                template_ids = []
                for filename, active in (("Mau-mac-dinh.xlsx", True),
                                         ("Mau-mien-Nam.xlsx", False),
                                         ("Mau-audit-cu.xlsx", False)):
                    cur.execute(
                        """
                        INSERT INTO quote_templates(
                            filename,content,content_sha256,content_size,profile_version,
                            mapping_json,mapping_v2_json,is_active,uploaded_by,activated_at
                        ) VALUES (%s,%s,%s,%s,'BG_V1',%s::jsonb,%s::jsonb,%s,'qa-admin',CASE WHEN %s THEN NOW() END)
                        RETURNING id
                        """,
                        (filename, psycopg2.Binary(raw), hashlib.sha256(raw).hexdigest(),
                         len(raw), mapping, mapping, active, active),
                    )
                    template_ids.append(cur.fetchone()[0])
                cur.execute(
                    "INSERT INTO team_quote_templates(team_id,template_id,assigned_by) VALUES (%s,%s,'qa-admin')",
                    (team_south, template_ids[1]),
                )
                cur.execute(
                    "UPDATE quote_templates SET archived_at=NOW(),archived_by='qa-admin' WHERE id=%s",
                    (template_ids[2],),
                )
        finally:
            conn.close()

        def stop(*_args):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        print("PHASE6C11_SMOKE http://127.0.0.1:5012 qa-admin/qa-sales qa-only", flush=True)
        search.app.run(host="127.0.0.1", port=5012, debug=False, use_reloader=False)
    finally:
        drop_temp_db(dbname)


if __name__ == "__main__":
    main()
