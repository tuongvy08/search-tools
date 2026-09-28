import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
import psycopg2
from psycopg2.extras import RealDictCursor
import regulatory_manual as manual
from regulatory_manual_support import RegulatoryCase


class HttpTests(RegulatoryCase):
    def post(self, client, payload, csrf=True):
        return client.post('/admin/regulatory/rules/save',json=payload,
                           headers={'X-CSRF-Token':'regulatory-test-csrf'} if csrf else {})

    def test_ui_http_create_edit_history_escaped_and_csrf(self):
        client=self.client()
        for url in ['/admin/regulatory','/admin/regulatory/rules','/admin/regulatory/rules/new']:
            self.assertEqual(client.get(url).status_code,200)
        data=dict(action='create',request_id=str(uuid.uuid4()),match_field='code',match_value='HTTP',
                  status_id=self.statuses['CAM_NHAP'],note='<script>alert(1)</script>')
        self.assertEqual(self.post(client,data,False).status_code,400)
        response=self.post(client,data)
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        rule=response.json['rule_id']
        html=client.get(response.json['url']).get_data(as_text=True)
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertIn('Phiên bản 1',html)
        self.assertTrue(self.post(client,data).json['replayed'])
        changed=dict(data,action='edit',rule_id=rule,expected_revision=1,request_id=str(uuid.uuid4()),note='new')
        self.assertEqual(self.post(client,changed).status_code,200)
        self.assertEqual(self.post(client,dict(changed,request_id=str(uuid.uuid4()))).status_code,409)
        self.assertEqual(len(self.rows('SELECT * FROM regulatory_rule_manual_events')),2)
        self.assertEqual(client.post('/admin/regulatory/rules/check',json=dict(match_field='cas',match_value='123-45-6',status_id=1),headers={'X-CSRF-Token':'regulatory-test-csrf'}).status_code,400)

    def test_revoked_actor_direct_urls_and_replay_denied(self):
        rule,data=self.create()
        client=self.client()
        self.rows("DELETE FROM admin_menu_grants WHERE user_id=%s",(self.actor,))
        self.assertIn(client.get('/admin/regulatory/rules').status_code,(302,403))
        self.assertIn(self.post(client,data).status_code,(302,403))
        self.assertEqual(len(self.rows('SELECT * FROM regulatory_rule_manual_events')),1)

    def test_concurrent_same_request_and_same_key_are_single_write(self):
        payload=dict(action='create',request_id=str(uuid.uuid4()),match_field='code',match_value='RACE',
                     status_id=self.statuses['CAM_NHAP'],note='one')
        barrier=threading.Barrier(2)
        def run(data):
            with psycopg2.connect(self.dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                barrier.wait(timeout=5)
                try:
                    return manual.mutate(cur,data,self.actor,self.version)
                except manual.ManualProblem as exc:
                    return exc.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(run,[payload,payload]))
        self.assertEqual(sum(bool(r.get('replayed')) for r in results),1)
        self.assertEqual(len(self.rows('SELECT * FROM regulatory_rules')),1)
        barrier=threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(run,[dict(payload,request_id=str(uuid.uuid4()),match_value='SECOND'),
                                       dict(payload,request_id=str(uuid.uuid4()),match_value='SECOND')]))
        self.assertEqual(results.count(409),1)
        self.assertEqual(len(self.rows('SELECT * FROM regulatory_rule_manual_events')),2)

    def test_revocation_while_waiting_for_domain_lock(self):
        import admin_permissions
        rule,_=self.create()
        blocker=psycopg2.connect(self.dsn)
        started=threading.Event(); outcome=[]
        with blocker.cursor() as cur: cur.execute('SELECT pg_advisory_xact_lock(62402601)')
        def writer():
            with psycopg2.connect(self.dsn) as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET application_name='manual-wait-test'")
                started.set()
                try:
                    manual.mutate(cur,dict(action='deactivate',rule_id=rule,expected_revision=1,
                                          request_id=str(uuid.uuid4()),reason='stop'),self.actor,self.version)
                    outcome.append('wrote')
                except admin_permissions.PermissionDenied:
                    outcome.append('denied')
        thread=threading.Thread(target=writer); thread.start()
        try:
            self.assertTrue(started.wait(5))
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                if self.rows("SELECT 1 FROM pg_stat_activity WHERE application_name='manual-wait-test' AND wait_event='advisory'"):
                    break
                time.sleep(.01)
            else: self.fail('writer did not reach advisory-lock barrier')
            self.rows('UPDATE app_users SET auth_version=auth_version+1 WHERE id=%s',(self.actor,))
        finally:
            blocker.rollback(); blocker.close(); thread.join(5)
        self.assertEqual(outcome,['denied'])
        self.assertTrue(self.rows('SELECT is_active FROM regulatory_rules')[0]['is_active'])

    def test_schema_missing_fails_closed(self):
        self.rows('ALTER TABLE regulatory_rule_manual_keys RENAME TO unavailable_keys')
        self.assertEqual(self.client().get('/admin/regulatory/rules').status_code,503)
        response=self.post(self.client(),dict(action='create'))
        self.assertEqual(response.status_code,503)
