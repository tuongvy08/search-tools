import os
import tempfile
import unittest
from unittest import mock
import psycopg2
from psycopg2.extras import RealDictCursor
import pg_temp_db as pg


class RegulatoryCase(unittest.TestCase):
    def setUp(self):
        if os.environ.get('REGULATORY_LOCAL_TEST') != '1':
            self.skipTest('Run with scripts/test_regulatory_local.py')
        self.db_name, self.dsn = pg.create_full_schema_temp_db()
        self.addCleanup(pg.drop_temp_db, self.db_name)
        self.uploads = tempfile.TemporaryDirectory(prefix='regulatory-manual-')
        self.addCleanup(self.uploads.cleanup)
        self.env = mock.patch.dict(os.environ, {'DATABASE_URL': self.dsn, 'IMPORT_UPLOAD_DIR': self.uploads.name})
        self.env.start(); self.addCleanup(self.env.stop)
        self.conn = psycopg2.connect(self.dsn)
        self.addCleanup(self.conn.close)
        with self.conn, self.conn.cursor() as cur:
            cur.execute("INSERT INTO app_users(username,password_hash,is_admin,account_status) VALUES ('manual_admin','x',true,'ACTIVE') RETURNING id")
            self.actor = cur.fetchone()[0]
            cur.execute("INSERT INTO admin_menu_grants(user_id,permission_key) VALUES (%s,'regulatory')", (self.actor,))
            cur.execute('SELECT auth_version FROM app_users WHERE id=%s', (self.actor,))
            self.version = cur.fetchone()[0]
            cur.execute('SELECT stable_key,id FROM regulatory_statuses')
            self.statuses = dict(cur.fetchall())

    def rows(self, query, args=()):
        with psycopg2.connect(self.dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, args)
            return cur.fetchall() if cur.description else []

    def mutate(self, payload):
        import regulatory_manual
        with self.conn, self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            return regulatory_manual.mutate(cur, payload, self.actor, self.version)

    def create(self, value='ABC', field='code', status='CAM_NHAP', note='manual'):
        import uuid
        payload = dict(action='create', request_id=str(uuid.uuid4()), match_field=field,
                       match_value=value, status_id=self.statuses[status], note=note)
        result = self.mutate(payload)
        return result['rule_id'], payload

    def client(self):
        import search
        search.app.testing = True
        client = search.app.test_client()
        with client.session_transaction() as session:
            session.update(authenticated=True, user_id=self.actor, auth_version=self.version,
                           is_admin=True, username='manual_admin', csrf_token='regulatory-test-csrf')
        return client
