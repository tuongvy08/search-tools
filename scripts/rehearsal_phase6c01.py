"""Rehearse 021 via real psql twice; only guarded temporary local databases."""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse
import psycopg2

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from pg_temp_db import (create_full_schema_temp_db,drop_temp_db,run_migration_via_psql,
                        apply_brand_master_and_currency_migrations,apply_dynamic_brand_currency_migration)


def main():
    if urlparse(os.environ['DATABASE_URL']).hostname not in {'127.0.0.1','localhost'}:
        raise RuntimeError('Rehearsal requires localhost')
    dbname,dsn=create_full_schema_temp_db()
    report={}
    try:
        conn=psycopg2.connect(dsn)
        with conn,conn.cursor() as cur:
            apply_brand_master_and_currency_migrations(cur)
            apply_dynamic_brand_currency_migration(cur)
            # Remove only the NEW column in this throwaway fixture to emulate
            # a migrated 015–020 database containing pre-existing teams.
            cur.execute('ALTER TABLE teams DROP COLUMN permission_keys')
            cur.execute("INSERT INTO teams(name) VALUES ('Existing active')")
            cur.execute("INSERT INTO teams(name,lifecycle_status,archived_at) VALUES ('Existing archived','ARCHIVED',NOW())")
            cur.execute("INSERT INTO app_users(username,password_hash,team_id) SELECT 'legacy-staff','x',id FROM teams WHERE name='Existing active'")
            cur.execute('SELECT id,name,lifecycle_status,updated_at FROM teams ORDER BY id')
            before=cur.fetchall()
            cur.execute('SELECT id,team_id,auth_version FROM app_users ORDER BY id')
            members_before=cur.fetchall()
        conn.close()
        code,output=run_migration_via_psql(dsn,ROOT/'sql/migration_021_team_capabilities.sql')
        if code: raise RuntimeError(output)
        conn=psycopg2.connect(dsn)
        with conn,conn.cursor() as cur:
            cur.execute('SELECT id,name,lifecycle_status,updated_at FROM teams ORDER BY id')
            report['existing_teams_unchanged']=cur.fetchall()==before
            cur.execute('SELECT id,team_id,auth_version FROM app_users ORDER BY id')
            report['existing_members_unchanged']=cur.fetchall()==members_before
            cur.execute('SELECT cardinality(permission_keys) FROM teams')
            report['all_existing_teams_keep_17_grants']=all(row[0]==17 for row in cur.fetchall())
            cur.execute("UPDATE teams SET permission_keys=ARRAY['SEARCH','VIEW_NAME'] WHERE name='Existing active'")
        conn.close()
        code,output=run_migration_via_psql(dsn,ROOT/'sql/migration_021_team_capabilities.sql')
        if code: raise RuntimeError(output)
        conn=psycopg2.connect(dsn)
        with conn,conn.cursor() as cur:
            cur.execute("SELECT permission_keys FROM teams WHERE name='Existing active'")
            report['rerun_preserves_admin_policy']=cur.fetchone()[0]==['SEARCH','VIEW_NAME']
            cur.execute('SELECT COUNT(*) FROM team_capability_history')
            report['rerun_no_audit_noise']=cur.fetchone()[0]==0
        conn.close()
        report['psql_autocommit_invocation_twice']=True
    finally:
        drop_temp_db(dbname)
    report['temporary_db_removed']=True
    print(json.dumps(report,sort_keys=True))
    if not all(report.values()): raise SystemExit(1)


if __name__=='__main__':
    main()
