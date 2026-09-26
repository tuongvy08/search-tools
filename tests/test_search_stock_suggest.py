"""Phase6D7 tests only create disposable pgtest databases."""
import os
import time
import unittest
import uuid
from unittest import mock

import psycopg2
import pg_temp_db
import search
import search_suggestions as suggest
import team_permissions
from stock import normalized_text


@unittest.skipUnless(pg_temp_db.probe_postgres_reachable(), 'isolated PostgreSQL required')
class SearchStockSuggestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = pg_temp_db.create_full_schema_temp_db()
        cls.conn = psycopg2.connect(cls.dsn)
        cls.conn.autocommit = True
        cls.env = mock.patch.dict(os.environ, DATABASE_URL=cls.dsn, DISABLE_IP_ALLOWLIST='1')
        cls.env.start()
        with cls.conn.cursor() as cur:
            pg_temp_db.apply_brand_master_and_currency_migrations(cur)
            pg_temp_db.apply_dynamic_brand_currency_migration(cur)
            cur.execute("INSERT INTO brand_master(name,normalized_name,currency_code) VALUES ('Brand A','BRAND A','VND'),('Brand B','BRAND B','VND')")
            cur.execute("INSERT INTO app_users(username,password_hash,is_admin) VALUES ('suggest_admin','x',true) RETURNING id")
            cls.admin_id = cur.fetchone()[0]

    @classmethod
    def tearDownClass(cls):
        cls.conn.close(); cls.env.stop(); pg_temp_db.drop_temp_db(cls.db_name)

    def setUp(self):
        search.app.testing = True
        with self.conn.cursor() as cur:
            cur.execute('TRUNCATE stock_manual_requests,stock_snapshot_events,stock_items,stock_state,stock_snapshots CASCADE')
            cur.execute('DELETE FROM products')
            self.snapshot = str(uuid.uuid4())
            cur.execute("INSERT INTO stock_snapshots(id,source_kind,actor,row_count,content_sha256) VALUES (%s,'IMPORT','fixture',0,'')", (self.snapshot,))
            cur.execute('INSERT INTO stock_state(singleton,active_snapshot_id) VALUES(true,%s)', (self.snapshot,))

    def client(self, grants=None, brands=('Brand A',)):
        client = search.app.test_client()
        user_id, team_id, admin = self.admin_id, None, grants is None
        if not admin:
            with self.conn.cursor() as cur:
                cur.execute('INSERT INTO teams(name,permission_keys) VALUES(%s,%s) RETURNING id', (uuid.uuid4().hex, grants))
                team_id = cur.fetchone()[0]
                for brand in brands:
                    cur.execute('INSERT INTO team_brands(team_id,brand) VALUES(%s,%s)', (team_id, brand))
                cur.execute("INSERT INTO app_users(username,password_hash,is_admin,team_id) VALUES(%s,'x',false,%s) RETURNING id", (uuid.uuid4().hex,team_id))
                user_id = cur.fetchone()[0]
        with client.session_transaction() as sess:
            sess.update(authenticated=True,user_id=user_id,auth_version=1,is_admin=admin,team_id=team_id,
                        username='fixture',auth_provider='LOCAL',csrf_token='fixture')
        return client

    def stock(self, name='Stock name', code='HI70024P', cas='50-00-0', brand='Brand A', snapshot=None):
        with self.conn.cursor() as cur:
            cur.execute('''INSERT INTO stock_items(snapshot_id,name,code,cas,brand,size,quantity,stock_price_vnd,
                stock_note,brand_norm,code_norm,cas_norm,size_norm) VALUES(%s,%s,%s,%s,%s,'1g',7,999,'Kho A',%s,%s,%s,'1g') RETURNING id''',
                (snapshot or self.snapshot,name,code,cas,brand,normalized_text(brand),normalized_text(code),normalized_text(cas)))
            return cur.fetchone()[0]

    def product(self, name='Catalog name', code='CAT-1', cas='64-17-5', brand='Brand A'):
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO products(name,code,cas,brand,size,ship,price,source_brand) VALUES(%s,%s,%s,%s,'1g','1','100',%s)", (name,code,cas,brand,brand))

    def suggestions(self, client, query):
        return client.get('/search/suggestions', query_string={'query':query})

    def test_stock_name_mixed_catalog_dedup_ids_and_match_badges(self):
        attached = self.stock('Dung dịch chuẩn', 'CAT-1')
        separate = self.stock('Dung dịch độc lập', 'HI70024P')
        self.product('Dung dịch catalog')
        rows = self.client().get('/search',query_string={'query':'Dung dịch'}).get_json()['results']
        self.assertEqual(len(rows),2)
        self.assertEqual([o['Stock_Item_Id'] for r in rows for o in r['Stock_Options']], [attached,separate])
        self.assertEqual(rows[1]['Stock_Options'][0]['Stock_Match'], 'name')
        self.assertEqual(rows[1]['Unit_Price'], '')
        self.assertEqual(rows[1]['Stock_Options'][0]['Stock_Price'], '999 ₫')
        self.assertEqual(self.client().get('/search?query=HI70024P').get_json()['results'][0]['Stock_Options'][0]['Stock_Match'], 'exact_code')
        self.assertEqual(self.client().get('/search?query=50-00-0').get_json()['results'][0]['Stock_Options'][0]['Stock_Match'], 'same_cas')

    def test_stock_active_only_unicode_wildcard_and_hidden_name(self):
        self.stock('Dung Dịch 100%_chuẩn')
        old = str(uuid.uuid4())
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO stock_snapshots(id,source_kind,actor,row_count,content_sha256) VALUES(%s,'IMPORT','fixture',0,'')",(old,))
        self.stock('Historical name', 'OLD', snapshot=old)
        client = self.client()
        for query in ('dung dịch','100%_'):
            self.assertEqual(len(client.get('/search',query_string={'query':query}).get_json()['results']),1)
        self.assertEqual(client.get('/search?query=Historical').get_json()['results'],[])
        self.assertEqual(self.suggestions(client,'Historical').get_json()['suggestions'],[])
        self.assertEqual(client.get('/search?query=100XX').get_json()['results'],[])
        hidden = self.client(['SEARCH','VIEW_CODE'])
        self.assertEqual(hidden.get('/search?query=Dung').get_json()['results'],[])

    def test_direct_stock_cap_signalled_without_identity_redaction_dedup(self):
        self.stock('Match', 'A1'); self.stock('Match', 'A2')
        client = self.client(['SEARCH','VIEW_NAME'])
        self.assertEqual(len(client.get('/search?query=Match').get_json()['results']),2)
        with mock.patch('stock.DIRECT_STOCK_LIMIT',1):
            response = client.get('/search?query=Match').get_json()
            self.assertTrue(response['stock_truncated'])
            self.assertEqual(len(response['results']),1)

    def test_suggest_permissions_before_limit_no_hidden_value_or_match_leaks(self):
        for n in range(15): self.product('secretneedle', f'DENIED-{n}', brand='Brand B')
        self.product('secretneedle','VISIBLE-CODE', '50-00-0')
        self.stock('secretneedle','STOCK-VISIBLE','50-00-0')
        client = self.client(['SEARCH','VIEW_CODE'])
        self.assertEqual(self.suggestions(client,'secretneedle').get_json()['suggestions'],[])
        self.assertEqual(self.suggestions(client,'50-00-0').get_json()['suggestions'],[])
        values = self.suggestions(client,'VISIBLE').get_json()['suggestions']
        self.assertEqual({v['value'] for v in values},{'VISIBLE-CODE','STOCK-VISIBLE'})
        self.assertNotIn('secretneedle',repr(values)); self.assertNotIn('50-00-0',repr(values))
        names = self.client(['SEARCH','VIEW_NAME'])
        self.assertEqual(len(self.suggestions(names,'secretneedle').get_json()['suggestions']),1)
        for grants in (['SEARCH','VIEW_CAS'], ['SEARCH','SEARCH_BY_CAS']):
            self.assertEqual(self.suggestions(self.client(grants),'50-00-0').get_json()['suggestions'],[])
        cas = self.client(['SEARCH','SEARCH_BY_CAS','VIEW_CAS'])
        self.assertEqual(self.suggestions(cas,'50-00-0').get_json()['suggestions'][0]['value'],'50-00-0')

    def test_suggest_auth_bounds_max10_cache_and_no_heavy_resolver(self):
        self.assertEqual(self.suggestions(search.app.test_client(),'abc').status_code,401)
        self.assertEqual(self.suggestions(self.client(['VIEW_NAME']),'abc').status_code,403)
        client = self.client()
        for n in range(20): self.product(f'Needle {n}',f'CODE-{n}')
        for query in ('ab','   '):
            self.assertEqual(self.suggestions(client,query).get_json()['suggestions'],[])
        for query in ('a'*501,'a\x00b'):
            self.assertEqual(self.suggestions(client,query).status_code,400)
        with mock.patch.object(search,'_load_pricing_resolver',side_effect=AssertionError('heavy')):
            response = self.suggestions(client,'Needle')
        self.assertEqual(len(response.get_json()['suggestions']),10)
        self.assertIn('no-store',response.headers['Cache-Control'])

    def test_suggest_active_unicode_literals_xss_and_exact_first(self):
        self.product('XX Dung dịch', 'CODE1')
        self.stock('Dung dịch','CODE2')
        client=self.client()
        self.assertEqual(self.suggestions(client,'Dung dịch').get_json()['suggestions'][0]['value'],'Dung dịch')
        self.product('<img src=x onerror=alert(1)> 100%_', 'CODE3')
        result=self.suggestions(client,'100%_').get_json()['suggestions']
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['label'],'<img src=x onerror=alert(1)> 100%_')
        self.assertEqual(self.suggestions(client,'100XX').get_json()['suggestions'],[])

    def test_query_timeout_rolls_back_and_budget_does_not_repeat_scans(self):
        blocker=psycopg2.connect(self.dsn)
        conn=psycopg2.connect(self.dsn)
        try:
            with blocker.cursor() as cur: cur.execute('LOCK TABLE products IN ACCESS EXCLUSIVE MODE')
            start=time.monotonic()
            result=suggest.suggest(conn,'Needle',grants={'VIEW_NAME'},is_admin=True,team_id=None)
            self.assertTrue(result['degraded'])
            self.assertLess(time.monotonic()-start,1)
            with conn.cursor() as cur:
                cur.execute('SELECT 1'); self.assertEqual(cur.fetchone()[0],1)
        finally:
            blocker.close(); conn.close()

    def test_total_stage_budget_and_fresh_permissions_each_request(self):
        conn=psycopg2.connect(self.dsn)
        try:
            with mock.patch.object(suggest.time,'monotonic',side_effect=[0,.1,.49,.51]), mock.patch.object(suggest,'candidate_query',wraps=suggest.candidate_query) as builder:
                result=suggest.suggest(conn,'missing',grants={'VIEW_NAME','VIEW_CODE'},is_admin=True,team_id=None)
            self.assertTrue(result['degraded'])
            self.assertEqual(builder.call_count,2)
        finally: conn.close()
        self.product('Visible needle')
        client=self.client(['SEARCH','VIEW_NAME'])
        self.assertTrue(self.suggestions(client,'Visible').get_json()['suggestions'])
        with client.session_transaction() as sess: tid=sess['team_id']
        with self.conn.cursor() as cur:
            cur.execute("UPDATE teams SET permission_keys='{SEARCH,VIEW_CODE}' WHERE id=%s",(tid,))
        response=self.suggestions(client,'Visible')
        self.assertNotIn('Visible needle',response.get_data(as_text=True))

    def test_main_stock_only_team_and_name_visibility_no_row_value_dedup(self):
        self.stock('Secret stock name','ONE', brand='Brand B')
        self.stock('Public stock name','TWO')
        client=self.client(['SEARCH','VIEW_NAME'])
        rows=client.get('/search?query=stock').get_json()['results']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['Name'],'Public stock name')
        self.assertNotIn('Code',rows[0]); self.assertNotIn('Cas',rows[0])
        self.assertEqual(rows[0]['Stock_Options'][0]['Stock_Match'],'name')


if __name__ == '__main__': unittest.main()
