"""Phase 6D4: real PostgreSQL auth/RBAC, route coverage and races.
All databases are uniquely named and destroyed only by pg_temp_db's guard.
"""
import os
from pathlib import Path
import threading
import time
import unittest
from unittest import mock
from urllib.parse import urlencode

import psycopg2
from psycopg2.extras import RealDictCursor
import admin_permissions as rbac
import search
from tests.pg_temp_db import create_full_schema_temp_db, drop_temp_db, probe_postgres_reachable

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / 'sql/migration_030_admin_menu_permissions.sql').read_text()


class RegistryTests(unittest.TestCase):
    def test_all_admin_routes_explicitly_mapped(self):
        import json, subprocess, sys
        output = subprocess.check_output([sys.executable, '-c',
            "import json,search,admin_permissions as p; print(json.dumps([r.endpoint for r in search.app.url_map.iter_rules() if p.is_admin_path(r.rule)]))"], text=True)
        actual = set(json.loads(output))
        self.assertEqual(actual, set(rbac.ENDPOINT_PERMISSIONS))
        self.assertEqual(len(rbac.MENU_LABELS), 11)
        self.assertEqual(set(rbac.ENDPOINT_PERMISSIONS.values()), set(rbac.MENU_LABELS))

    def test_unmapped_admin_route_fails_closed_even_super(self):
        from flask import Flask
        app = Flask(__name__)
        app.secret_key = 'isolated-test'
        rbac.init_app(app)
        app.add_url_rule('/admin/new-feature', 'unmapped', lambda: 'unsafe')
        app.add_url_rule('/api/admin/new-feature', 'unmapped_api', lambda: 'unsafe')
        for path in ('/admin/new-feature', '/api/admin/new-feature'):
            self.assertEqual(app.test_client().get(path).status_code, 403)

    def test_mapping_applies_without_admin_path(self):
        from flask import Flask
        app = Flask(__name__)
        app.secret_key = 'isolated-test'
        rbac.init_app(app)
        app.add_url_rule('/moved-product-page', 'admin_products', lambda: 'unsafe')
        client=app.test_client()
        with client.session_transaction() as sess:sess.update(authenticated=True,is_admin=False)
        self.assertEqual(client.get('/moved-product-page').status_code, 403)


@unittest.skipUnless(probe_postgres_reachable(), 'isolated PostgreSQL required')
class AdminMenuPgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        cls.conn = psycopg2.connect(cls.dsn)
        cls.conn.autocommit = True
        with cls.conn.cursor() as cur:
            cur.execute(MIGRATION)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        drop_temp_db(cls.db_name)

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {'DATABASE_URL': self.dsn,
            'GOOGLE_WORKSPACE_ALLOWED_DOMAINS': 'example.test'})
        self.env.start()
        self.addCleanup(self.env.stop)
        search.app.testing = True
        with self.conn.cursor() as cur:
            cur.execute('TRUNCATE app_users,teams CASCADE')
            cur.execute("INSERT INTO teams(name) VALUES ('staff-team') RETURNING id")
            self.team = cur.fetchone()[0]
        self.root = self.user('root', super_admin=True)
        self.other_root = self.user('other-root', provider='GOOGLE', super_admin=True)
        self.admin = self.user('delegate')
        self.staff = self.user('staff', admin=False)

    def user(self, name, provider='LOCAL', admin=True, super_admin=False):
        with self.conn.cursor() as cur:
            cur.execute('''INSERT INTO app_users(username,password_hash,auth_provider,email,google_sub,
                is_admin,is_super_admin,account_status,team_id)
                VALUES(%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s) RETURNING id''',
                (name,'x' if provider=='LOCAL' else None,provider,
                 name+'@example.test' if provider=='GOOGLE' else None,
                 name if provider=='GOOGLE' else None,admin,super_admin,None if admin else self.team))
            return cur.fetchone()[0]

    def version(self, uid):
        with self.conn.cursor() as cur:
            cur.execute('SELECT auth_version FROM app_users WHERE id=%s',(uid,))
            return cur.fetchone()[0]

    def client(self, uid):
        with self.conn.cursor() as cur:
            cur.execute('SELECT is_admin,auth_provider,team_id,username FROM app_users WHERE id=%s',(uid,))
            admin, provider, team, name = cur.fetchone()
        client = search.app.test_client()
        with client.session_transaction() as sess:
            sess.update(authenticated=True,user_id=uid,auth_version=self.version(uid),
                        is_admin=admin,auth_provider=provider,team_id=team,username=name,csrf_token='test-csrf')
        return client

    def grant(self, uid, keys, super_admin=False):
        conn = psycopg2.connect(self.dsn)
        try:
            with conn,conn.cursor() as cur:
                rbac.update_admin_access(cur,self.root,self.version(self.root),uid,
                                         'super_admin' if super_admin else 'admin',keys)
        finally: conn.close()

    def test_migration_twice_preserves_downgrades_grants_and_staff(self):
        self.grant(self.other_root, ['users','stock'])
        before=self.version(self.other_root)
        with self.conn.cursor() as cur:
            cur.execute(MIGRATION);cur.execute(MIGRATION)
            cur.execute('SELECT is_super_admin,auth_version FROM app_users WHERE id=%s',(self.other_root,))
            self.assertEqual(cur.fetchone(),(False,before))
            cur.execute('SELECT permission_key FROM admin_menu_grants WHERE user_id=%s ORDER BY 1',(self.other_root,))
            self.assertEqual(cur.fetchall(),[('stock',),('users',)])
            cur.execute('SELECT is_admin,is_super_admin,auth_version FROM app_users WHERE id=%s',(self.staff,))
            self.assertEqual(cur.fetchone(),(False,False,1))

    def test_migration_backfills_every_existing_admin_once(self):
        with mock.patch('tests.pg_temp_db._FULL_SCHEMA_SQL_FILES', tuple(x for x in __import__('tests.pg_temp_db',fromlist=[''])._FULL_SCHEMA_SQL_FILES if '030' not in x)):
            name,dsn=create_full_schema_temp_db()
        c=psycopg2.connect(dsn)
        try:
            with c,c.cursor() as cur:
                cur.execute("INSERT INTO app_users(username,password_hash,is_admin,account_status) VALUES ('a','x',true,'ACTIVE'),('b','x',true,'SUSPENDED'),('staff','x',false,'ACTIVE')")
                cur.execute(MIGRATION);cur.execute(MIGRATION)
                cur.execute('SELECT username,is_super_admin,auth_version FROM app_users ORDER BY username')
                self.assertEqual(cur.fetchall(),[('a',True,2),('b',True,2),('staff',False,1)])
        finally:c.close();drop_temp_db(name)

    def test_all_direct_urls_and_apis_denied_local_google(self):
        for uid in (self.admin,self.user('google-delegate',provider='GOOGLE')):
            client=self.client(uid)
            for rule in search.app.url_map.iter_rules():
                if rule.endpoint not in rbac.ENDPOINT_PERMISSIONS:continue
                values={arg: ('00000000-0000-0000-0000-000000000001' if 'id' in arg and arg not in ('user_id','product_id','template_id') else 1) for arg in rule.arguments}
                values.update({k:v for k,v in {'action':'cancel','kind':'product','field':'cas'}.items() if k in values})
                with search.app.test_request_context():
                    from flask import url_for
                    url=url_for(rule.endpoint,**values)
                for method in rule.methods-{'HEAD','OPTIONS'}:
                    with self.subTest(provider=uid,endpoint=rule.endpoint,method=method):
                        response=client.open(url,method=method,data={'csrf_token':'test-csrf'})
                        self.assertEqual(response.status_code,403)

    def test_single_key_grants_only_its_endpoints(self):
        for key in rbac.MENU_LABELS:
            self.grant(self.admin,[key])
            client=self.client(self.admin)
            with client.session_transaction() as sess: state=dict(sess)
            with search.app.test_request_context('/'):
                from flask import session
                session.update(state)
                for endpoint,required in rbac.ENDPOINT_PERMISSIONS.items():
                    self.assertEqual(rbac.can_endpoint(endpoint),required==key)

    def test_menu_renders_only_granted_links_shared_desktop_mobile(self):
        self.grant(self.admin,['users','network'])
        html=self.client(self.admin).get('/').get_data(as_text=True)
        self.assertIn('id="sqShellToggle"',html)
        self.assertIn('id="sqAdminMenu"',html)
        self.assertIn('href="/admin/users"',html)
        self.assertIn('href="/admin/network"',html)
        self.assertNotIn('href="/admin/products"',html)
        self.assertIn('sq-shell-role">Admin',html)
        html=self.client(self.root).get('/').get_data(as_text=True)
        self.assertIn('sq-shell-role">Super Admin',html)
        self.assertIn('href="/admin/products"',html)

    def test_super_admin_bypass_both_providers(self):
        for uid in (self.root,self.other_root):
            self.assertEqual(self.client(uid).get('/admin/quote-templates').status_code,200)

    def test_session_invalidated_immediately_local_google(self):
        for uid in (self.admin,self.user('delegate-google',provider='GOOGLE')):
            client=self.client(uid)
            self.grant(uid,['users'])
            self.assertEqual(client.get('/api/admin/quote-templates').status_code,401)
            self.assertEqual(client.get('/admin/users').status_code,302)

    def test_csrf_required_and_cannot_self_elevate(self):
        self.grant(self.admin,['users'])
        self.assertEqual(self.client(self.admin).post('/admin/users/admin-access',data={'user_id':self.admin,'admin_level':'super_admin','csrf_token':'test-csrf'}).status_code,403)
        self.assertEqual(self.client(self.root).post('/admin/users/admin-access',data={'user_id':self.admin,'admin_level':'super_admin'}).status_code,400)
        self.assertEqual(self.client(self.root).post('/admin/network',data={'cidr':'192.0.2.1'}).status_code,400)

    def test_delegated_user_admin_cannot_modify_admin_or_promote_staff(self):
        self.grant(self.admin,['users'])
        client=self.client(self.admin)
        for uid in (self.root,self.admin,self.staff):
            before=self.version(uid)
            response=client.post('/admin/users',data={'action':'update_user','user_id':uid,'role':'admin','password':'changed','csrf_token':'test-csrf'})
            self.assertEqual(response.status_code,302)
            self.assertEqual(self.version(uid),before)
        for ep in ('suspend','update','reactivate','revoke-sessions','approve'):
            before=self.version(self.other_root)
            client.post('/admin/users/google/'+ep,data={'user_id':self.other_root,'role':'staff','team_id':self.team,'csrf_token':'test-csrf'})
            self.assertEqual(self.version(self.other_root),before)

    def test_delegated_can_manage_staff_both_providers(self):
        self.grant(self.admin,['users'])
        client=self.client(self.admin)
        before=self.version(self.staff)
        client.post('/admin/users',data={'action':'update_user','user_id':self.staff,'role':'user','team_id':self.team,'csrf_token':'test-csrf'})
        self.assertEqual(self.version(self.staff),before+1)
        uid=self.user('google-staff',provider='GOOGLE',admin=False)
        before=self.version(uid)
        client.post('/admin/users/google/revoke-sessions',data={'user_id':uid,'csrf_token':'test-csrf'})
        self.assertEqual(self.version(uid),before+1)

    def test_last_super_admin_protected_with_secondary_admin_remaining(self):
        self.grant(self.other_root,[])
        conn=psycopg2.connect(self.dsn)
        try:
            for update in ("is_super_admin=false", "is_admin=false", "account_status='SUSPENDED'", "account_status='SUSPENDED',archived_at=now(),archived_by=id"):
                with self.assertRaises(psycopg2.IntegrityError):
                    with conn,conn.cursor() as cur:
                        cur.execute('SELECT pg_advisory_xact_lock(%s)',(rbac.ACCOUNT_LOCK,))
                        cur.execute('UPDATE app_users SET '+update+' WHERE id=%s',(self.root,))
        finally:conn.close()

    def test_concurrent_cross_demotion_leaves_one_active_super(self):
        barrier=threading.Barrier(2); outcomes=[]
        def demote(actor,target):
            c=psycopg2.connect(self.dsn)
            try:
                barrier.wait(5)
                with c,c.cursor() as cur:rbac.update_admin_access(cur,actor,1,target,'admin',[])
                outcomes.append('ok')
            except (rbac.PermissionDenied,psycopg2.IntegrityError):outcomes.append('denied')
            finally:c.close()
        threads=[threading.Thread(target=demote,args=(self.root,self.other_root)),threading.Thread(target=demote,args=(self.other_root,self.root))]
        for t in threads:t.start()
        for t in threads:t.join(10);self.assertFalse(t.is_alive())
        self.assertCountEqual(outcomes,['ok','denied'])
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM app_users WHERE is_super_admin AND account_status='ACTIVE'")
            self.assertEqual(cur.fetchone()[0],1)

    def wait_for_db_lock(self, pid):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with self.conn.cursor() as cur:
                cur.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",(pid,))
                row=cur.fetchone()
                if row and row[0]=='Lock':return
            time.sleep(.01)
        self.fail('Connection never reached the expected PostgreSQL lock wait')

    def test_actor_revoked_while_waiting_for_domain_lock(self):
        self.grant(self.admin,['products'])
        old_version=self.version(self.admin)
        blocker=psycopg2.connect(self.dsn); waiting=threading.Event();result=[]
        key=630004
        worker_pid=[]
        with blocker.cursor() as cur:cur.execute('SELECT pg_advisory_xact_lock(%s)',(key,))
        def writer():
            c=psycopg2.connect(self.dsn)
            worker_pid.append(c.get_backend_pid())
            try:
                with c,c.cursor() as cur:
                    waiting.set();cur.execute('SELECT pg_advisory_xact_lock(%s)',(key,))
                    rbac.require_actor(cur,self.admin,old_version,'products')
                    cur.execute("INSERT INTO office_ip_allowlist(cidr) VALUES ('192.0.2.123/32')")
                result.append('unsafe')
            except rbac.PermissionDenied:result.append('denied')
            finally:c.close()
        t=threading.Thread(target=writer);t.start();self.assertTrue(waiting.wait(5))
        self.wait_for_db_lock(worker_pid[0])
        self.grant(self.admin,[])
        blocker.commit();blocker.close();t.join(10)
        self.assertEqual(result,['denied'])

    def test_actor_row_lock_fences_revocation_until_write_commits(self):
        self.grant(self.admin,['stock'])
        c=psycopg2.connect(self.dsn)
        with c.cursor() as cur:rbac.require_actor(cur,self.admin,self.version(self.admin),'stock')
        waiting=threading.Event();done=threading.Event()
        worker_pid=[]
        version=self.version(self.root)
        def revoke():
            conn=psycopg2.connect(self.dsn)
            try:
                worker_pid.append(conn.get_backend_pid());waiting.set()
                with conn,conn.cursor() as cur:
                    rbac.update_admin_access(cur,self.root,version,self.admin,'admin',[])
                done.set()
            finally:conn.close()
        t=threading.Thread(target=revoke);t.start();self.assertTrue(waiting.wait(5))
        self.wait_for_db_lock(worker_pid[0])
        self.assertFalse(done.is_set())
        c.commit();c.close();t.join(10);self.assertTrue(done.is_set())

    def test_audit_actor_target_before_after_without_secrets(self):
        self.grant(self.admin,['users','stock'])
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM admin_rbac_events WHERE event='menu_access_changed' ORDER BY id DESC LIMIT 1")
            event=cur.fetchone()
        self.assertEqual(event['actor_user_id'],self.root)
        self.assertEqual(event['target_user_id'],self.admin)
        self.assertEqual(event['after_state']['menus'],['stock','users'])
        self.assertNotIn('password',str(event));self.assertNotIn('token',str(event))

    def test_revoked_grant_cannot_reappear_on_staff_repromotion(self):
        self.grant(self.admin,['stock'])
        with self.conn.cursor() as cur:
            cur.execute('UPDATE app_users SET is_admin=false,team_id=%s WHERE id=%s',(self.team,self.admin))
            cur.execute('UPDATE app_users SET is_admin=true,team_id=NULL WHERE id=%s',(self.admin,))
            cur.execute('SELECT count(*) FROM admin_menu_grants WHERE user_id=%s',(self.admin,))
            self.assertEqual(cur.fetchone()[0],0)

    def test_real_psql_030_twice_preserves_business_data(self):
        from tests.pg_temp_db import run_migration_via_psql, psql_runner_available
        import tests.pg_temp_db as fixture
        if not psql_runner_available(): self.skipTest('psql required for production invocation')
        with mock.patch.object(fixture, '_FULL_SCHEMA_SQL_FILES', tuple(x for x in fixture._FULL_SCHEMA_SQL_FILES if '030' not in x)):
            name,dsn=create_full_schema_temp_db()
        conn=psycopg2.connect(dsn)
        conn.autocommit=True
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO app_users(username,password_hash,is_admin) VALUES ('pre030','x',true)")
                cur.execute("INSERT INTO products(name,code,brand) VALUES ('sentinel','sentinel','TRC') RETURNING id,xmin::text")
                before=cur.fetchone()
            for _ in range(2):
                code,output=run_migration_via_psql(dsn,ROOT/'sql/migration_030_admin_menu_permissions.sql')
                self.assertEqual(code,0,output)
                with conn.cursor() as cur:
                    cur.execute('SELECT is_super_admin,auth_version FROM app_users')
                    self.assertEqual(cur.fetchone(),(True,2))
            with conn.cursor() as cur:
                cur.execute('SELECT id,xmin::text FROM products WHERE id=%s',(before[0],))
                self.assertEqual(cur.fetchone(),before)
        finally:conn.close();drop_temp_db(name)

    def test_unknown_grant_rejected_atomically(self):
        version=self.version(self.admin)
        with self.assertRaises(ValueError):self.grant(self.admin,['unknown'])
        self.assertEqual(self.version(self.admin),version)

    def test_admin_any_ip_and_staff_still_restricted(self):
        self.grant(self.admin,['quote_templates'])
        google=self.user('ip-google',provider='GOOGLE')
        self.grant(google,['quote_templates'])
        with mock.patch.dict(os.environ,{'DISABLE_IP_ALLOWLIST':'0','OFFICE_IP_ALLOWLIST':'192.0.2.0/24'}):
            for uid in (self.root,self.other_root,self.admin,google):
                self.assertEqual(self.client(uid).get('/admin/quote-templates',environ_overrides={'REMOTE_ADDR':'203.0.113.77'}).status_code,200)
            self.assertEqual(self.client(self.staff).get('/',environ_overrides={'REMOTE_ADDR':'203.0.113.77'}).status_code,403)

    def test_last_super_archive_and_suspend_http_paths(self):
        # Another super can act, but suspended supers do not count as ACTIVE.
        with self.conn.cursor() as cur:
            cur.execute("UPDATE app_users SET account_status='SUSPENDED' WHERE id=%s",(self.other_root,))
        before=self.version(self.root)
        self.client(self.root).post('/admin/users/local/archive',data={'user_id':self.root,'csrf_token':'test-csrf'})
        self.assertEqual(self.version(self.root),before)
        # Direct downgrade of self to secondary is the permitted path when
        # another ACTIVE super exists, and must fail here.
        response=self.client(self.root).post('/admin/users/admin-access',data={'user_id':self.root,'admin_level':'admin','csrf_token':'test-csrf'})
        self.assertEqual(response.status_code,302)
        self.assertIn('err=',response.location)
        self.assertEqual(self.version(self.root),before)

    def test_users_page_protects_admin_forms_for_both_providers(self):
        self.grant(self.admin,['users'])
        html=self.client(self.admin).get('/admin/users').get_data(as_text=True)
        self.assertNotIn('id="adminAccessPanel"',html)
        self.assertNotIn('value="admin"',html)
        self.assertNotIn('href="/admin/teams"',html)
        self.assertIn('form_update_'+str(self.staff),html)
        self.assertNotIn('form_update_'+str(self.root),html)

    def test_last_super_race_mixed_suspend_and_archive(self):
        barrier=threading.Barrier(2);result=[]
        def action(actor,target,sql):
            conn=psycopg2.connect(self.dsn)
            try:
                barrier.wait(5)
                with conn,conn.cursor() as cur:
                    cur.execute('SELECT pg_advisory_xact_lock(%s)',(rbac.ACCOUNT_LOCK,))
                    rbac.require_actor(cur,actor,1,'users',super_only=True)
                    cur.execute(sql,(target,))
                result.append('ok')
            except (rbac.PermissionDenied,psycopg2.IntegrityError):result.append('denied')
            finally:conn.close()
        threads=[threading.Thread(target=action,args=(self.root,self.other_root,"UPDATE app_users SET account_status='SUSPENDED',auth_version=auth_version+1 WHERE id=%s")),threading.Thread(target=action,args=(self.other_root,self.root,"UPDATE app_users SET account_status='SUSPENDED',archived_at=now(),archived_by=id,auth_version=auth_version+1 WHERE id=%s"))]
        for t in threads:t.start()
        for t in threads:t.join(10);self.assertFalse(t.is_alive())
        self.assertCountEqual(result,['ok','denied'])

    def test_upload_size_limits_run_before_csrf_multipart_parsing(self):
        for path,size in (('/admin/stock/upload',34*1024**2),('/admin/imports/upload',130*1024**2)):
            response=self.client(self.root).post(path,data=b'not-a-workbook',
                content_type='application/octet-stream',environ_overrides={'CONTENT_LENGTH':str(size)})
            self.assertEqual(response.status_code,413,path)
