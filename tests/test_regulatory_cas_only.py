"""CAS-only automatic policy, with brand-enabled explicit product overrides."""
import uuid
from pathlib import Path
from unittest import mock

from openpyxl import Workbook
from psycopg2.extras import RealDictCursor

import regulatory_import_jobs as jobs
import regulatory_manual as manual
from import_engine import ImportProblem
from regulatory import product_resolver_lateral
from regulatory_manual_support import RegulatoryCase


class CasOnlyTests(RegulatoryCase):
    def test_unicode_cas_rejected_without_writing_or_normalizing(self):
        client = self.client()
        headers = {'X-CSRF-Token': 'regulatory-test-csrf'}
        existing, _ = self.create('64-17-5')
        for cas in ('６４-１７-５', '٦٤-١٧-٥', '64-１７-5', '64–17–5'):
            with self.subTest(cas=cas):
                before = self.rows('SELECT * FROM regulatory_rules ORDER BY id')
                data = dict(match_field='cas', match_value=cas, status_id=self.statuses['CAM_NHAP'])
                checked = client.post('/admin/regulatory/rules/check', json=data, headers=headers)
                self.assertEqual(checked.status_code, 400)
                self.assertIn('0-9', checked.get_json()['error'])
                for action in ('create', 'edit'):
                    payload = dict(data, action=action, request_id=str(uuid.uuid4()))
                    if action == 'edit':
                        payload.update(rule_id=existing, expected_revision=1)
                    response = client.post('/admin/regulatory/rules/save', json=payload, headers=headers)
                    self.assertEqual(response.status_code, 400)
                self.assertEqual(self.rows('SELECT * FROM regulatory_rules ORDER BY id'), before)
                path = Path(self.uploads.name) / 'unicode.xlsx'
                wb = Workbook(); wb.active.append(['CAS', 'Tình trạng quản lý', 'Ghi chú quản lý'])
                wb.active.append(['50-00-0', 'CẤM NHẬP', 'valid first row'])
                wb.active.append([cas, 'CẤM NHẬP', 'invalid second row']); wb.save(path); wb.close()
                with self.assertRaisesRegex(ImportProblem, 'Dòng 3:.*0-9'):
                    jobs._parse_workbook(path)
                rows = [dict(row_number=2, match_field='cas', match_value=cas, status_label='CẤM NHẬP', note='')]
                with self.conn.cursor() as cur, self.assertRaisesRegex(ImportProblem, '0-9'):
                    jobs.build_plan(cur, rows, 'upsert')
        self.assertEqual(client.post('/admin/regulatory/rules/check', json=dict(
            match_field='cas', match_value='64-17-5', status_id=self.statuses['CAM_NHAP']),
            headers=headers).status_code, 409)  # exact existing key remains a collision

    def test_existing_unicode_cas_cannot_be_restored_and_is_not_rewritten(self):
        rule = self.rule('cas', '６４-１７-５')
        self.rows('UPDATE regulatory_rules SET is_active=false WHERE id=%s', (rule,))
        before = self.rows('SELECT * FROM regulatory_rules WHERE id=%s', (rule,))[0]
        response = self.client().post('/admin/regulatory/rules/save', json=dict(
            action='restore', request_id=str(uuid.uuid4()), rule_id=rule,
            expected_revision=before['revision']), headers={'X-CSRF-Token':'regulatory-test-csrf'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('0-9', response.get_json()['error'])
        self.assertEqual(self.rows('SELECT * FROM regulatory_rules WHERE id=%s', (rule,))[0], before)

    def rule(self, field, value, status='CAM_NHAP', note='rule note'):
        return self.rows("""INSERT INTO regulatory_rules(status_id,rule_type,rule_label,
            match_field,match_value,note) VALUES (%s,'','',%s,%s,%s) RETURNING id""",
            (self.statuses[status], field, value, note))[0]['id']

    def product(self, name, code, cas, brand='CAS TEST', override=None):
        return self.rows("""INSERT INTO products(name,code,cas,brand,size,price,ship,manual_compliance)
            VALUES (%s,%s,%s,%s,'1g','100','1',%s) RETURNING id""",
            (name, code, cas, brand, override))[0]['id']

    def resolve(self, product_id):
        return self.rows("""SELECT rr.* FROM products p LEFT JOIN brand_compliance_settings bcs
            ON bcs.brand_norm=upper(btrim(p.brand)) """ + product_resolver_lateral() +
            ' WHERE p.id=%s', (product_id,))[0]

    def test_names_codes_and_partial_cas_cannot_classify(self):
        self.rule('name', 'Diethyl ether', note='wrong name rule')
        self.rule('code', 'SAME-CODE', note='wrong code rule')
        self.rule('cas', '60-29-7', 'PHU_LUC_III', 'actual CAS')
        for cas in ('629-14-1', '', None, '60-29', '160-29-7'):
            with self.subTest(cas=cas):
                row = self.resolve(self.product('Ethylene Glycol Diethyl Ether', 'SAME-CODE', cas))
                self.assertIsNone(row['status_id'])
        product = self.product('Unrelated display name', 'OTHER', ' 60-29-7 ')
        row = self.resolve(product)
        self.assertEqual((row['status_id'], row['note'], row['source']),
                         (self.statuses['PHU_LUC_III'], 'actual CAS', 'automatic'))

    def test_brand_switch_controls_override_and_no_name_leak(self):
        self.rule('cas', '50-00-0', note='CAS BLOCK')
        self.rule('name', 'Override product', note='name ignored')
        self.rows("INSERT INTO brand_compliance_settings VALUES ('PRIORITY',true,now()),('NORMAL',false,now())")
        enabled = self.product('Override product', 'ONE', '50-00-0', 'PRIORITY', 'Được bán')
        disabled = self.product('Override product', 'TWO', '50-00-0', 'NORMAL', 'Được bán')
        blank = self.product('Override product', 'THREE', '50-00-0', 'PRIORITY')
        self.assertEqual((self.resolve(enabled)['export_policy'], self.resolve(enabled)['source']), ('ALLOW', 'manual'))
        for product in (disabled, blank):
            row = self.resolve(product)
            self.assertEqual((row['export_policy'], row['source'], row['note']), ('BLOCK', 'automatic', 'CAS BLOCK'))
        self.rows("UPDATE brand_compliance_settings SET manual_compliance_priority=false WHERE brand_norm='PRIORITY'")
        self.assertEqual(self.resolve(enabled)['export_policy'], 'BLOCK')
        self.rows("UPDATE brand_compliance_settings SET manual_compliance_priority=true WHERE brand_norm='PRIORITY'")
        with self.conn, self.conn.cursor() as cur:
            cur.execute('ALTER TABLE products DISABLE TRIGGER trg_products_manual_regulatory_status')
            cur.execute('UPDATE products SET manual_compliance_status_id=NULL WHERE id=%s', (enabled,))
            cur.execute('ALTER TABLE products ENABLE TRIGGER trg_products_manual_regulatory_status')
        row = self.resolve(enabled)
        self.assertEqual((row['source'], row['export_policy']), ('error', 'BLOCK'))

    def test_search_quote_export_and_check_cas_agree(self):
        import search
        self.rule('name', 'Diethyl ether', note='ignored')
        self.rule('code', 'FALSE-MATCH', note='ignored')
        self.rule('cas', '60-29-7', 'PHU_LUC_III', 'actual CAS')
        false_id = self.product('Ethylene Glycol Diethyl Ether', 'FALSE-MATCH', '629-14-1')
        true_id = self.product('Diethyl ether', 'TRUE-MATCH', '60-29-7')
        client = self.client()
        results = client.get('/search', query_string={'query': '-MATCH'}).get_json()['results']
        by_code = {row['Code']: row for row in results}
        self.assertEqual(by_code['FALSE-MATCH']['Compliance_Status'], '')
        self.assertEqual(by_code['TRUE-MATCH']['Compliance_Note'], 'actual CAS')
        quote = client.post('/api/quote-assistant/match', json={
            'rows': [{'code': 'FALSE-MATCH'}, {'code': 'TRUE-MATCH'}],
            'selection_strategy': 'LOWEST_UNIT_PRICE'}).get_json()['results']
        self.assertEqual(quote[0]['candidates'][0]['Compliance'], '')
        self.assertEqual(quote[1]['candidates'][0]['Compliance_Note'], 'actual CAS')
        with search.app.test_request_context('/'):
            export_rows = search._quote_export_products(self.conn,
                [{'ord': 1, 'product_id': false_id}, {'ord': 2, 'product_id': true_id}],
                {'is_admin': True, 'team_id': None, 'grants': frozenset({'VIEW_COMPLIANCE', 'VIEW_COMPLIANCE_NOTE'})})
        self.assertEqual(export_rows[0]['Compliance'], '')
        self.assertEqual(export_rows[1]['Compliance_Note'], 'actual CAS')
        checked = client.post('/check_cas_batch', data={'cas': '629-14-1\n60-29-7'}).get_json()['results']
        self.assertEqual(checked[0]['Compliance_Status'], '')
        self.assertEqual(checked[1]['Compliance_Note'], 'actual CAS')

    def test_http_and_parser_reject_non_cas_while_legacy_can_only_be_stopped(self):
        client = self.client()
        headers = {'X-CSRF-Token': 'regulatory-test-csrf'}
        for field in ('name', 'code'):
            original = self.rule(field, 'LEGACY-'+field)
            before = self.rows('SELECT * FROM regulatory_rules ORDER BY id')
            data = dict(match_field=field, match_value='LEGACY-'+field, status_id=self.statuses['CAM_NHAP'])
            self.assertEqual(client.post('/admin/regulatory/rules/check', json=data, headers=headers).status_code, 400)
            data.update(action='create', request_id=str(uuid.uuid4()))
            self.assertEqual(client.post('/admin/regulatory/rules/save', json=data, headers=headers).status_code, 400)
            data.update(action='edit', rule_id=original, expected_revision=1)
            self.assertEqual(client.post('/admin/regulatory/rules/save', json=data, headers=headers).status_code, 400)
            restore = dict(action='restore', rule_id=original, expected_revision=1, request_id=str(uuid.uuid4()))
            self.assertEqual(client.post('/admin/regulatory/rules/save', json=restore, headers=headers).status_code, 400)
            html = client.get('/admin/regulatory/rules/'+str(original)).get_data(as_text=True)
            self.assertIn('Không dùng đối chiếu', html)
            self.assertNotIn('value="restore"', html)
            self.assertEqual(self.rows('SELECT * FROM regulatory_rules ORDER BY id'), before)
            restore.update(action='deactivate', reason='stop old rule')
            self.assertEqual(client.post('/admin/regulatory/rules/save', json=restore, headers=headers).status_code, 200)
            self.assertEqual(client.get('/admin/regulatory/templates/'+field).status_code, 404)
            path = Path(self.uploads.name) / (field+'.xlsx')
            wb = Workbook(); wb.active.append(['Tên' if field=='name' else 'Code','Tình trạng quản lý','Ghi chú quản lý'])
            wb.active.append(['LEGACY', 'CẤM NHẬP', 'test']); wb.save(path); wb.close()
            with self.assertRaisesRegex(ImportProblem, 'chỉ đối chiếu CAS'):
                jobs._parse_workbook(path)
            with self.conn.cursor() as cur, self.assertRaisesRegex(ImportProblem, 'chỉ đối chiếu CAS'):
                jobs.build_plan(cur, [dict(row_number=2, match_field=field, match_value='LEGACY', status_label='CẤM NHẬP', note='')], 'upsert')

    def test_malformed_status_is_visible_for_review_but_not_selectable_for_new_rule(self):
        bad = self.rows("INSERT INTO regulatory_statuses(stable_key,label,priority,export_policy) VALUES ('BAD_FIXTURE','1563-66-2',99,'ALLOW') RETURNING id")[0]['id']
        client = self.client()
        html = client.get('/admin/regulatory/rules/new').get_data(as_text=True)
        self.assertNotIn('1563-66-2', html)
        self.assertIn('1563-66-2', client.get('/admin/regulatory').get_data(as_text=True))
        payload = dict(action='create',request_id=str(uuid.uuid4()),match_field='cas',match_value='50-00-0',status_id=bad)
        self.assertEqual(client.post('/admin/regulatory/rules/save',json=payload,
            headers={'X-CSRF-Token':'regulatory-test-csrf'}).status_code,400)
        self.assertEqual(self.rows('SELECT * FROM regulatory_rules'), [])
        rule = self.rows("""INSERT INTO regulatory_rules(status_id,rule_type,rule_label,
            match_field,match_value,is_active) VALUES (%s,'','','cas','50-00-0',false) RETURNING id""", (bad,))[0]['id']
        payload = dict(action='restore',request_id=str(uuid.uuid4()),rule_id=rule,expected_revision=1)
        self.assertEqual(client.post('/admin/regulatory/rules/save',json=payload,
            headers={'X-CSRF-Token':'regulatory-test-csrf'}).status_code,400)
        self.assertFalse(self.rows('SELECT is_active FROM regulatory_rules WHERE id=%s', (rule,))[0]['is_active'])
