"""033 contract rehearsals on disposable local PostgreSQL via real psql."""
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import unittest

import psycopg2
from psycopg2 import sql
import pg_temp_db as pg

ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / 'sql/migration_033_regulatory_manual_edit.sql'
DOWN = ROOT / 'sql/rollback_033_regulatory_manual_edit.sql'


class MigrationTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get('REGULATORY_LOCAL_TEST') != '1':
            self.skipTest('Use scripts/test_regulatory_local.py after Docker local preflight')
        self.name, self.dsn = pg.create_full_schema_temp_db(include_manual=False)
        self.conn = psycopg2.connect(self.dsn)
        self.conn.autocommit = True
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.conn.close()
        pg.drop_temp_db(self.name)

    def query(self, query, args=()):
        with self.conn.cursor() as cur:
            cur.execute(query, args)
            return cur.fetchall() if cur.description else None

    def migrate(self, path=UP, ok=True):
        code, output = pg.run_migration_via_psql(self.dsn, path)
        self.assertEqual(code == 0, ok, output)

    def seed(self, active=True):
        return self.query("""INSERT INTO regulatory_rules(status_id,rule_type,rule_label,match_field,
            match_value,is_active,note) SELECT id,stable_key,label,'code','OLD',%s,'retain'
            FROM regulatory_statuses WHERE stable_key='CAM_NHAP' RETURNING id""", (active,))[0][0]

    def snapshot(self):
        return self.query("SELECT to_jsonb(r)-'manual_protected'-'revision' FROM regulatory_rules r ORDER BY id")

    def test_backfill_inactive_no_fake_audit_rerun_preserves(self):
        rule = self.seed(False)
        before = self.snapshot()
        self.migrate()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.query('SELECT manual_protected,revision FROM regulatory_rules'), [(True, 1)])
        self.assertEqual(self.query('SELECT rule_id FROM regulatory_rule_manual_keys'), [(rule,)])
        self.assertEqual(self.query('SELECT count(*) FROM regulatory_rule_manual_events'), [(0,)])
        self.query("UPDATE regulatory_rules SET note='changed' WHERE id=%s", (rule,))
        self.migrate()
        self.assertEqual(self.query('SELECT revision FROM regulatory_rules'), [(2,)])
        self.migrate(DOWN, ok=False)
        self.assertEqual(self.query('SELECT revision FROM regulatory_rules'), [(2,)])

    def test_revision_noop_and_synced_status(self):
        rule = self.seed()
        self.migrate()
        self.query('UPDATE regulatory_rules SET note=note,updated_at=now(),revision=123')
        self.assertEqual(self.query('SELECT revision FROM regulatory_rules'), [(1,)])
        self.query("UPDATE regulatory_rules SET status_id=(SELECT id FROM regulatory_statuses WHERE stable_key='DUOC_BAN') WHERE id=%s", (rule,))
        self.assertEqual(self.query('SELECT revision,rule_type FROM regulatory_rules'), [(2, 'DUOC_BAN')])
        self.migrate(DOWN, ok=False)  # revision alone blocks down

    def test_down_unused_and_repeat_up(self):
        self.seed()
        before = self.snapshot()
        self.migrate()
        self.migrate(DOWN)
        self.assertEqual(self.snapshot(), before)
        self.migrate()
        self.assertEqual(self.query('SELECT manual_protected,revision FROM regulatory_rules'), [(False, 1)])

    def test_failure_is_atomic(self):
        self.seed(False)
        before = self.snapshot()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'failed.sql'
            path.write_text(UP.read_text().replace('COMMIT;', 'SELECT intentional_missing_function_033();\nCOMMIT;'))
            self.migrate(path, ok=False)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.query("SELECT to_regclass('regulatory_rule_manual_events')"), [(None,)])
        self.assertEqual(self.query("SELECT count(*) FROM information_schema.columns WHERE table_name='regulatory_rules' AND column_name='revision'"), [(0,)])

    def test_import_event_alone_blocks_down(self):
        self.migrate()
        self.query("""INSERT INTO app_users(username,password_hash,is_admin,account_status) VALUES ('actor','x',true,'ACTIVE')""")
        self.query("""INSERT INTO regulatory_import_jobs(id,submission_key,actor,actor_user_id,actor_auth_version,
            filename,file_size,file_sha256,mode,expires_at,preview)
            SELECT '00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002',
            'actor',id,1,'test.xlsx',1,'test','upsert',now(),'{"contract_version":2}' FROM app_users WHERE username='actor'""")
        self.migrate(DOWN, ok=False)

    def test_backup_restore_to_another_disposable_database(self):
        self.seed(False)
        before = self.snapshot()
        container = 'search-tools-regulatory-test-db-1'
        dump = subprocess.run(['docker', 'exec', container, 'pg_dump', '-U', 'searchlocal', '-d', self.name], capture_output=True, check=True).stdout
        self.migrate()
        restored = 'p6a_release_gate_pgtest_restore_' + secrets.token_hex(4)
        maintenance = psycopg2.connect(pg.maintenance_dsn())
        maintenance.autocommit = True
        try:
            with maintenance.cursor() as cur:
                cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restored)))
            self.addCleanup(pg.drop_temp_db, restored)
        finally:
            maintenance.close()
        process = subprocess.run(['docker', 'exec', '-i', container, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                                  '-U', 'searchlocal', '-d', restored], input=dump, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr.decode())
        with psycopg2.connect(pg.dsn_for(restored)) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_jsonb(r) FROM regulatory_rules r ORDER BY id")
            self.assertEqual(cur.fetchall(), before)
