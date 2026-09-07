"""Disposable synthetic profiles for 6C0.1 browser QA; loopback server only."""
import os
import signal
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from pg_temp_db import create_full_schema_temp_db, drop_temp_db, apply_brand_master_and_currency_migrations, apply_dynamic_brand_currency_migration
import psycopg2
from werkzeug.security import generate_password_hash


def main():
    dbname,dsn=create_full_schema_temp_db()
    try:
        os.environ.update(DATABASE_URL=dsn,FLASK_SECRET_KEY='disposable-browser-6c01',DISABLE_IP_ALLOWLIST='1',ENABLE_LEGACY_PASSWORD_LOGIN='0')
        import search
        import team_permissions as permissions
        from test_quote_workbook_export import make_workbook
        conn=psycopg2.connect(dsn)
        try:
            with conn,conn.cursor() as cur:
                apply_brand_master_and_currency_migrations(cur)
                apply_dynamic_brand_currency_migration(cur)
                cur.execute("INSERT INTO app_users(username,password_hash,is_admin) VALUES ('qa-admin',%s,true)",(generate_password_hash('qa-only'),))
                profiles=[('Dụng cụ & Sinh học','qa-bio',{'VIEW_CAS','VIEW_COMPLIANCE','VIEW_COMPLIANCE_NOTE'}),
                          ('Tra cứu không giá','qa-no-price',{'VIEW_PRICE'}),
                          ('Sales đầy đủ','qa-sales',set())]
                for name,username,denied in profiles:
                    cur.execute('INSERT INTO teams(name,permission_keys) VALUES (%s,%s) RETURNING id',(name,sorted(set(permissions.REGISTRY)-denied)))
                    team=cur.fetchone()[0]
                    cur.execute("INSERT INTO team_brands(team_id,brand) VALUES (%s,'TRC')",(team,))
                    cur.execute('INSERT INTO app_users(username,password_hash,team_id) VALUES (%s,%s,%s)',(username,generate_password_hash('qa-only'),team))
                cur.execute("INSERT INTO products(name,code,cas,brand,source_brand,size,ship,price,note,manual_compliance,manual_compliance_note) VALUES "
                            "('Ethanol standard','BIO-001','64-17-5','TRC','TRC','100mg','1','100','Giao trong 2 tuần','Được bán','Ghi chú kiểm soát mẫu'),"
                            "('Methanol standard','BIO-002','67-56-1','TRC','TRC','500mg','1','200','Có sẵn','Được bán','Ghi chú kiểm soát mẫu')")
                cur.execute("INSERT INTO brand_compliance_settings(brand_norm,manual_compliance_priority) VALUES ('TRC',true)")
                cur.execute((ROOT/"sql"/"migration_013_quote_templates.sql").read_text())
            search._insert_quote_template(conn,filename='QA-template.xlsx',raw=make_workbook(),mapping=search._quote_template_mapping_snapshot(),activate=True,uploaded_by='qa-admin')
        finally:
            conn.close()
        def stop(*args):
            raise SystemExit(0)
        signal.signal(signal.SIGTERM,stop)
        signal.signal(signal.SIGINT,stop)
        print('QA ready: http://127.0.0.1:5011 ; qa-admin / qa-bio / qa-no-price / qa-sales ; password qa-only',flush=True)
        search.app.run(host='127.0.0.1',port=5011,debug=False,use_reloader=False)
    finally:
        drop_temp_db(dbname)


if __name__=='__main__':
    main()
