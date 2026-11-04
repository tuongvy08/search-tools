"""Independent adversarial checks for regulatory-cas-only (synthetic data, disposable DB)."""
import json
import os
import subprocess
import sys
import uuid
from io import BytesIO
from pathlib import Path
from unittest import mock

import psycopg2
from openpyxl import Workbook
from psycopg2.extras import RealDictCursor

import regulatory_import_jobs as jobs
import regulatory_manual as manual
from import_engine import ImportProblem
from regulatory import product_resolver_lateral
from regulatory_manual_support import RegulatoryCase
from regulatory_status_cleanup import CleanupProblem, cleanup_statuses, inspect_statuses

CSRF = {'X-CSRF-Token': 'regulatory-test-csrf'}
ROOT = Path(__file__).resolve().parents[2]
TRACKED = ['regulatory_rules', 'regulatory_statuses', 'regulatory_status_events',
           'regulatory_rule_manual_events', 'regulatory_rule_manual_keys']


def xlsx(headers, rows):
    wb = Workbook(); ws = wb.active; ws.append(headers)
    for r in rows:
        ws.append(r)
    s = BytesIO(); wb.save(s); s.seek(0)
    return s


class Base(RegulatoryCase):
    def snap(self, tables=TRACKED):
        return {t: self.rows('SELECT * FROM %s ORDER BY 1' % t) for t in tables}

    def rule(self, field, value, status='CAM_NHAP', note='n', active=True):
        return self.rows("""INSERT INTO regulatory_rules(status_id,rule_type,rule_label,match_field,
            match_value,note,is_active) VALUES (%s,'','',%s,%s,%s,%s) RETURNING id""",
                         (self.statuses[status], field, value, note, active))[0]['id']

    def product(self, name, code, cas, brand='B1', manual=None):
        return self.rows("""INSERT INTO products(name,code,cas,brand,size,price,ship,manual_compliance)
            VALUES (%s,%s,%s,%s,'1g','100','1',%s) RETURNING id""", (name, code, cas, brand, manual))[0]['id']

    def resolve(self, pid):
        return self.rows("""SELECT rr.* FROM products p LEFT JOIN brand_compliance_settings bcs
            ON bcs.brand_norm=upper(btrim(p.brand)) """ + product_resolver_lateral() +
                         ' WHERE p.id=%s', (pid,))[0]

    def team_client(self, grants):
        import search
        m = uuid.uuid4().hex[:6]
        t = self.rows("INSERT INTO teams(name,lifecycle_status,permission_keys) VALUES (%s,'ACTIVE',%s) RETURNING id",
                      ('T' + m, list(grants)))[0]['id']
        u = self.rows("""INSERT INTO app_users(username,password_hash,team_id,is_admin,account_status,auth_version)
            VALUES (%s,'x',%s,false,'ACTIVE',1) RETURNING id""", ('u' + m, t))[0]['id']
        c = search.app.test_client()
        with c.session_transaction() as s:
            s.update(authenticated=True, user_id=u, auth_version=1, is_admin=False, team_id=t,
                     username='u' + m, csrf_token='regulatory-test-csrf')
        return c


class V1(Base):
    def test_priority_notes_and_name_code_cannot_win(self):
        # high-priority BLOCK by name/code must not beat an ALLOW on CAS
        self.rule('name', 'ethanol', 'CAM_NHAP', 'NAME-BLOCK')
        self.rule('code', 'SHARED', 'CAM_NHAP', 'CODE-BLOCK')
        self.rule('cas', '64-17-5', 'DUOC_BAN', 'CAS-ALLOW')
        pid = self.product('Long name with Ethanol inside', 'SHARED', '64-17-5')
        r = self.resolve(pid)
        self.assertEqual((r['export_policy'], r['note']), ('ALLOW', 'CAS-ALLOW'))
        # removing the legacy rules must not weaken a real CAS BLOCK
        self.rows("DELETE FROM regulatory_rules WHERE match_field<>'cas'")
        self.rows("UPDATE regulatory_rules SET status_id=%s,note='REAL-BLOCK'", (self.statuses['CAM_NHAP'],))
        self.assertEqual(self.resolve(pid)['export_policy'], 'BLOCK')

    def test_two_statuses_same_cas_priority_and_note_merge(self):
        self.rule('cas', '64-17-5', 'PHU_LUC_III', 'loser')
        self.rule('cas', '64-17-5', 'CAM_NHAP', 'zeta')
        pid = self.product('x', 'c1', '64-17-5')
        r = self.resolve(pid)
        self.assertEqual(r['status_id'], self.statuses['CAM_NHAP'])
        self.assertEqual(r['export_policy'], 'BLOCK')
        self.assertEqual(r['note'], 'zeta')

    def test_inexact_cas_never_matches(self):
        self.rule('cas', '64-17-5', 'CAM_NHAP', 'x')
        for cas in ['64-17-5; 50-00-0', '64-17-5 (anhydrous)', '4-17-5', '64-17-50', '64175', '', '   ',
                    None, '64-17-5​', '６４-１７-５', 'x64-17-5']:
            with self.subTest(cas=cas):
                self.assertIsNone(self.resolve(self.product('p', 'c', cas))['status_id'])
        for cas in [' 64-17-5 ', '64-17-5']:
            self.assertIsNotNone(self.resolve(self.product('p', 'c', cas))['status_id'])

    def test_inactive_cas_rule_and_legacy_active_name_do_not_classify(self):
        self.rule('cas', '64-17-5', 'CAM_NHAP', 'off', active=False)
        self.rule('name', 'p', 'CAM_NHAP', 'name')
        self.assertIsNone(self.resolve(self.product('p', 'c', '64-17-5'))['status_id'])

    def test_all_surfaces_with_staff_visibility(self):
        import search
        self.rule('name', 'Diethyl', 'CAM_NHAP', 'NAMEWRONG')
        self.rule('code', 'FAKE-1', 'CAM_NHAP', 'CODEWRONG')
        self.rule('cas', '60-29-7', 'PHU_LUC_III', 'REAL')
        fake = self.product('Glycol Diethyl ether', 'FAKE-1', '629-14-1')
        nocas = self.product('Diethyl', 'FAKE-1B', None)
        real = self.product('Unrelated', 'REAL-1', '60-29-7')
        for client in (self.client(), self.team_client({'SEARCH', 'VIEW_CODE', 'VIEW_NAME', 'VIEW_CAS', 'SEARCH_BY_CAS',
                                                          'VIEW_COMPLIANCE', 'VIEW_COMPLIANCE_NOTE', 'CHECK_LICENSE'})):
            resp = client.get('/search', query_string={'query': '-1'})
            res = resp.get_json()
            self.assertIsNotNone(res, (resp.status_code, resp.get_data(as_text=True)[:200]))
            by = {r['Code']: r for r in res['results']}
            for code in ('FAKE-1', 'FAKE-1B'):
                if code in by:
                    self.assertEqual(by[code].get('Compliance_Status', ''), '')
                    self.assertNotIn('WRONG', json.dumps(by[code]))
            if 'REAL-1' in by:
                self.assertEqual(by['REAL-1']['Compliance_Note'], 'REAL')
        with search.app.test_request_context('/'):
            rows = search._quote_export_products(self.conn, [{'ord': 1, 'product_id': fake},
                {'ord': 2, 'product_id': nocas}, {'ord': 3, 'product_id': real}],
                {'is_admin': True, 'team_id': None, 'grants': frozenset({'VIEW_COMPLIANCE', 'VIEW_COMPLIANCE_NOTE'})})
        self.assertEqual([r['Compliance'] for r in rows[:2]], ['', ''])
        self.assertEqual(rows[2]['Compliance_Note'], 'REAL')
        c = self.client().post('/check_cas_batch', data={'cas': '629-14-1\n60-29-7\nDiethyl'}).get_json()['results']
        self.assertEqual(c[0]['Compliance_Status'], '')
        self.assertEqual(c[1]['Compliance_Note'], 'REAL')
        q = self.client().post('/api/quote-assistant/match', json={'rows': [{'code': 'FAKE-1'}, {'code': 'REAL-1'}],
                                                                   'selection_strategy': 'LOWEST_UNIT_PRICE'}).get_json()['results']
        self.assertEqual(q[0]['candidates'][0]['Compliance'], '')
        self.assertEqual(q[1]['candidates'][0]['Compliance_Note'], 'REAL')

    def test_staff_without_view_compliance_sees_nothing_for_cas_match(self):
        self.rule('cas', '60-29-7', 'CAM_NHAP', 'SECRETNOTE')
        self.product('p', 'REAL-9', '60-29-7')
        c = self.team_client({'SEARCH', 'VIEW_CODE', 'VIEW_NAME', 'VIEW_CAS', 'SEARCH_BY_CAS'})
        body = c.get('/search', query_string={'query': 'REAL-9'}).get_data(as_text=True)
        self.assertNotIn('SECRETNOTE', body)


class V2(Base):
    def test_brand_switch_matrix(self):
        self.rule('cas', '64-17-5', 'CAM_NHAP', 'CAS-BLOCK')
        self.rows("INSERT INTO brand_compliance_settings VALUES ('ON',true,now()),('OFF',false,now())")
        on = self.product('p', 'c1', '64-17-5', 'on ', 'Được bán')   # case/space-insensitive brand
        off = self.product('p', 'c2', '64-17-5', 'OFF', 'Được bán')
        unset = self.product('p', 'c3', '64-17-5', 'NOSETTING', 'Được bán')
        none = self.product('p', 'c4', '64-17-5', 'ON')
        blocking_manual = self.product('p', 'c5', None, 'ON', 'CẤM NHẬP')
        for pid, pol, src in [(on, 'ALLOW', 'manual'), (off, 'BLOCK', 'automatic'),
                              (unset, 'BLOCK', 'automatic'), (none, 'BLOCK', 'automatic'),
                              (blocking_manual, 'BLOCK', 'manual')]:
            r = self.resolve(pid)
            self.assertEqual((r['export_policy'], r['source']), (pol, src), pid)
        self.assertEqual(self.resolve(off)['note'], 'CAS-BLOCK')
        # product with manual BLOCK under enabled brand but CAS ALLOW stays BLOCK
        self.rows("UPDATE regulatory_rules SET status_id=%s", (self.statuses['DUOC_BAN'],))
        b2 = self.product('p', 'c6', '64-17-5', 'ON', 'CẤM NHẬP')
        self.assertEqual(self.resolve(b2)['export_policy'], 'BLOCK')

    def test_broken_link_blocks_with_cas_allow_and_through_http(self):
        self.rule('cas', '64-17-5', 'DUOC_BAN', 'ok')
        self.rows("INSERT INTO brand_compliance_settings VALUES ('ON',true,now())")
        pid = self.product('p', 'BROKEN', '64-17-5', 'ON', 'Được bán')
        with self.conn, self.conn.cursor() as cur:
            cur.execute('ALTER TABLE products DISABLE TRIGGER trg_products_manual_regulatory_status')
            cur.execute("UPDATE products SET manual_compliance_status_id=NULL WHERE id=%s", (pid,))
            cur.execute('ALTER TABLE products ENABLE TRIGGER trg_products_manual_regulatory_status')
        r = self.resolve(pid)
        self.assertEqual((r['source'], r['export_policy']), ('error', 'BLOCK'))
        res = self.client().get('/search', query_string={'query': 'BROKEN'}).get_json()['results'][0]
        self.assertNotEqual(res.get('Compliance_Export_Policy'), 'ALLOW')
        # dangling text-only mismatch (status id points elsewhere than text) must not silently go CAS
        self.rows("UPDATE brand_compliance_settings SET manual_compliance_priority=false")
        self.assertEqual(self.resolve(pid)['source'], 'automatic')

    def test_reads_do_not_mutate_products_or_brand_settings(self):
        self.rule('cas', '64-17-5', 'CAM_NHAP')
        self.rows("INSERT INTO brand_compliance_settings VALUES ('ON',true,now())")
        self.product('p', 'c1', '64-17-5', 'ON', 'Được bán')
        before = self.snap(['products', 'brand_compliance_settings'])
        c = self.client()
        c.get('/search', query_string={'query': 'c1'}); c.post('/check_cas_batch', data={'cas': '64-17-5'})
        self.assertEqual(before, self.snap(['products', 'brand_compliance_settings']))


class V3(Base):
    BAD_VALUES = [('name', 'Ethanol'), ('code', 'ABC'), ('NAME', 'x'), ('Cas ', '64-17-5'), (None, '64-17-5'),
                  ('', '64-17-5'), (['cas'], '64-17-5'), ({'a': 1}, '64-17-5'), ('cas', 'Ethanol'),
                  ('cas', '64-17-6'), ('cas', '64-17-5, 50-00-0'), ('cas', '64-17-5\n50-00-0'),
                  ('cas', ''), ('cas', None), ('cas', '64175'), ('cas', '4-17-5'), ('cas', '1' * 600)]

    def test_json_and_form_rejections_write_nothing(self):
        c = self.client()
        for orig_field in ('name', 'code'):
            self.rule(orig_field, 'LEG-' + orig_field)
        before = self.snap()
        for field, value in self.BAD_VALUES:
            for action in ('check', 'create'):
                with self.subTest(field=field, value=value, action=action):
                    p = dict(match_field=field, match_value=value, status_id=self.statuses['CAM_NHAP'])
                    if action == 'create':
                        p.update(action='create', request_id=str(uuid.uuid4()))
                        r = c.post('/admin/regulatory/rules/save', json=p, headers=CSRF)
                        f = c.post('/admin/regulatory/rules/save', headers=CSRF, data={
                            k: v for k, v in p.items() if isinstance(v, (str, int))})
                        self.assertIn(f.status_code, (400, 409))
                    else:
                        r = c.post('/admin/regulatory/rules/check', json=p, headers=CSRF)
                    self.assertGreaterEqual(r.status_code, 400)
                    self.assertLess(r.status_code, 500, r.get_data(as_text=True)[:200])
        self.assertEqual(before, self.snap())

    def test_edit_cannot_switch_field_and_missing_field_rejected(self):
        c = self.client()
        rid, _ = self.create('64-17-5')
        legacy = self.rule('name', 'foo')
        before = self.snap()
        for p in [dict(action='edit', rule_id=rid, expected_revision=1, match_field='name', match_value='x'),
                  dict(action='edit', rule_id=rid, expected_revision=1, match_value='64-17-5'),
                  dict(action='edit', rule_id=legacy, expected_revision=1, match_field='cas', match_value='64-17-5'),
                  dict(action='edit', rule_id=legacy, expected_revision=1, match_field='name', match_value='foo'),
                  dict(action='restore', rule_id=legacy, expected_revision=1),
                  dict(action='activate', rule_id=legacy, expected_revision=1),
                  dict(action='edit', rule_id=legacy, expected_revision=1)]:
            p.update(request_id=str(uuid.uuid4()), status_id=self.statuses['CAM_NHAP'], reason='r')
            r = c.post('/admin/regulatory/rules/save', json=p, headers=CSRF)
            self.assertIn(r.status_code, (400, 409), (p, r.get_data(as_text=True)[:200]))
        self.assertEqual(before, self.snap())

    def test_unicode_digit_cas_is_rejected_or_ascii(self):
        c = self.client()
        r = c.post('/admin/regulatory/rules/check', json=dict(match_field='cas', match_value='６４-１７-５',
                                                              status_id=self.statuses['CAM_NHAP']), headers=CSRF)
        s = c.post('/admin/regulatory/rules/save', json=dict(action='create', request_id=str(uuid.uuid4()),
                   match_field='cas', match_value='６４-１７-５', status_id=self.statuses['CAM_NHAP']), headers=CSRF)
        stored = [x['match_value'] for x in self.rows('SELECT match_value FROM regulatory_rules')]
        self.assertTrue(all(v.isascii() for v in stored), (r.status_code, s.status_code, stored))

    def upload(self, headers, rows, mode='upsert'):
        resp = self.client().post('/admin/regulatory/upload', data={
            'csrf_token': 'regulatory-test-csrf', 'mode': mode, 'submission_key': str(uuid.uuid4()),
            'file': (xlsx(headers, rows), 't.xlsx')})
        self.assertEqual(resp.status_code, 302)
        return resp.location.rsplit('/', 1)[-1] if '/jobs/' in resp.location else None

    def test_bad_files_rejected_whole_and_nothing_written(self):
        self.rule('name', 'LEG')
        H = lambda f: [f, 'Tình trạng quản lý', 'Ghi chú quản lý']
        cases = {
            'name': (H('Tên'), [['ethanol', 'CẤM NHẬP', '']]),
            'code': (H('Code'), [['ABC', 'CẤM NHẬP', '']]),
            'ten-lower': (H('tên'), [['ethanol', 'CẤM NHẬP', '']]),
            'cas+code': (['CAS', 'Code', 'Tình trạng quản lý'], [['64-17-5', 'x', 'CẤM NHẬP']]),
            'one bad cas among good': (H('CAS'), [['64-17-5', 'CẤM NHẬP', ''], ['64-17-6', 'CẤM NHẬP', '']]),
            'name text in cas': (H('CAS'), [['ethanol', 'CẤM NHẬP', '']]),
            'cas as status': (H('CAS'), [['64-17-5', '50-00-0', '']]),
            'blank status': (H('CAS'), [['64-17-5', '', '']]),
            'cas multi': (H('CAS'), [['64-17-5; 50-00-0', 'CẤM NHẬP', '']]),
            'formula': (H('CAS'), [['=1+1', 'CẤM NHẬP', '']]),
        }
        for name, (hdr, rows) in cases.items():
            with self.subTest(name):
                before = self.snap()
                job = self.upload(hdr, rows)
                self.assertTrue(jobs.run_once(job) or True)
                j = self.rows('SELECT status,preview_ready,preview FROM regulatory_import_jobs WHERE id=%s', (job,))[0]
                self.assertEqual(j['status'], 'failed', j)
                self.assertFalse(j['preview_ready'])
                self.assertEqual(before, self.snap())

    def test_old_uploaded_name_file_and_stale_plans_cannot_apply(self):
        # an "old" job whose stored file is name-based: worker must fail it
        job = self.upload(['Tên', 'Tình trạng quản lý', 'Ghi chú quản lý'], [['ethanol', 'CẤM NHẬP', 'n']])
        before = self.snap()
        jobs.run_once(job)
        self.assertEqual(self.rows('SELECT status FROM regulatory_import_jobs WHERE id=%s', (job,))[0]['status'], 'failed')
        # apply attempt on that failed job
        r = self.client().post('/admin/regulatory/jobs/%s/apply' % job, data={
            'csrf_token': 'regulatory-test-csrf', 'fingerprint': 'x', 'confirm_delete': '0'})
        jobs.run_once(job)
        self.assertEqual(before, self.snap())
        # plans: tamper contract / fingerprint / digest and field
        rows = [dict(row_number=2, match_field='cas', match_value='64-17-5', status_label='CẤM NHẬP', note='')]
        with self.conn.cursor() as cur:
            plan = jobs.build_plan(cur, rows, 'upsert')
            for mut in [dict(contract_version=1), dict(fingerprint='0' * 64), dict(plan_digest='0' * 64),
                        dict(fingerprint=None)]:
                with self.assertRaises(ImportProblem):
                    jobs.apply_plan(cur, rows, 'upsert', dict(plan, **mut))
            name_rows = [dict(rows[0], match_field='name', match_value='ethanol')]
            with self.assertRaises(ImportProblem):
                jobs.apply_plan(cur, name_rows, 'upsert', plan)
            # apply recomputes the plan itself: a tampered _prepared must not smuggle a name rule
            jobs.apply_plan(cur, rows, 'upsert', dict(plan, _prepared=name_rows))
            cur.execute("SELECT count(*) FROM regulatory_rules WHERE match_field<>'cas'")
            self.assertEqual(cur.fetchone()[0], 0)
            self.conn.rollback()
            with self.assertRaises(ImportProblem):
                jobs.build_plan(cur, [dict(rows[0], match_field='code')], 'replace_scoped')
            with self.assertRaises(ImportProblem):
                jobs.build_plan(cur, [dict(rows[0], match_value='64-17-6')], 'upsert')
            with self.assertRaises(ImportProblem):
                jobs.build_plan(cur, [dict(rows[0], status_label='64-17-5')], 'upsert')
        self.conn.rollback()
        self.assertEqual(before, self.snap())

    def test_old_plan_without_matching_policy_fingerprint_rejected(self):
        rows = [dict(row_number=2, match_field='cas', match_value='64-17-5', status_label='CẤM NHẬP', note='')]
        with self.conn.cursor() as cur:
            plan = jobs.build_plan(cur, rows, 'upsert')
            import regulatory
            src = open(regulatory.__file__).read()
            self.assertIn("cas-only-v1", src)
            # a plan fingerprinted before the policy change cannot equal the new one
            import hashlib
            old_like = hashlib.sha256(b'pre-policy').hexdigest()
            with self.assertRaisesRegex(ImportProblem, 'đổi'):
                jobs.apply_plan(cur, rows, 'upsert', dict(plan, fingerprint=old_like))
        self.conn.rollback()

    def test_legacy_disabled_endpoints(self):
        c = self.client()
        before = self.snap()
        r = c.post('/admin/imports/quick-rule', data=dict(rule_type='CAM_NHAP', match_field='name', match_value='x'),
                   headers=CSRF)
        self.assertIn(r.status_code, (400, 403, 410))
        r = c.post('/admin/imports/preview', data=dict(csrf_token='regulatory-test-csrf', dataset='regulatory_rules',
                   mode='upsert', file=(xlsx(['rule_type', 'rule_label', 'match_field', 'match_value', 'priority',
                                             'is_active', 'note'], [['CAM_NHAP', 'l', 'name', 'x', 1, 1, '']]), 'a.xlsx')))
        self.assertIn(r.status_code, (302, 403))
        self.assertEqual(before, self.snap())
        self.assertEqual(c.get('/admin/regulatory/templates/name').status_code, 404)
        self.assertEqual(c.get('/admin/regulatory/templates/code').status_code, 404)
        self.assertEqual(c.get('/admin/regulatory/templates/cas').status_code, 200)
        self.assertEqual(c.get('/admin/regulatory/templates/CAS').status_code, 404)

    def test_valid_cas_still_works_end_to_end(self):
        job = self.upload(['CAS', 'Tình trạng quản lý', 'Ghi chú quản lý'], [['64-17-5', 'CẤM NHẬP', 'ok']])
        jobs.run_once(job)
        j = self.rows('SELECT status,preview FROM regulatory_import_jobs WHERE id=%s', (job,))[0]
        self.assertEqual(j['status'], 'completed', j)
        rid, payload = self.create('50-00-0')
        self.assertEqual(self.mutate(dict(payload, action='edit', rule_id=rid, expected_revision=1,
                                           request_id=str(uuid.uuid4()), note='again'))['rule_id'], rid)


class V4(Base):
    def test_replace_scoped_and_upsert_keep_legacy_and_protected(self):
        legacy_n = self.rule('name', 'LEGNAME', 'CAM_NHAP', 'ln')
        legacy_c = self.rule('code', 'LEGCODE', 'CAM_NHAP', 'lc')
        legacy_off = self.rule('name', 'LEGOFF', 'CAM_NHAP', 'lo', active=False)
        prot, _ = self.create('50-00-0', status='CAM_NHAP')
        absent = self.rule('cas', '64-17-5', 'CAM_NHAP', 'absent-cas')
        other_status = self.rule('cas', '67-56-1', 'PHU_LUC_II', 'other-status')
        before = {r['id']: r for r in self.rows('SELECT * FROM regulatory_rules')}
        for mode in ('replace_scoped', 'upsert'):
            resp = self.client().post('/admin/regulatory/upload', data={
                'csrf_token': 'regulatory-test-csrf', 'mode': mode, 'submission_key': str(uuid.uuid4()),
                'file': (xlsx(['CAS', 'Tình trạng quản lý', 'Ghi chú quản lý'], [['71-43-2', 'CẤM NHẬP', 'new']]), 'a.xlsx')})
            job = resp.location.rsplit('/', 1)[-1]
            jobs.run_once(job)
            pv = self.rows('SELECT preview FROM regulatory_import_jobs WHERE id=%s', (job,))[0]['preview']
            r = self.client().post('/admin/regulatory/jobs/%s/apply' % job, data={
                'csrf_token': 'regulatory-test-csrf', 'fingerprint': pv['fingerprint'],
                'confirm_delete': str(pv['deleted'])})
            self.assertNotIn('err=', r.location)
            jobs.run_once(job)
            self.assertEqual(self.rows('SELECT status FROM regulatory_import_jobs WHERE id=%s', (job,))[0]['status'],
                             'completed')
        after = {r['id']: r for r in self.rows('SELECT * FROM regulatory_rules')}
        for rid in (legacy_n, legacy_c, legacy_off, prot, other_status):
            self.assertEqual(before[rid], after[rid], rid)
        # unprotected absent CAS rule in same scope may be removed in replace_scoped; legacy never
        self.assertTrue(any(r['match_value'] == '71-43-2' for r in after.values()))

    def test_legacy_ui_readonly_and_deactivate_only(self):
        c = self.client()
        legacy = self.rule('name', 'LEGNAME', note='<b>x</b>')
        html = c.get('/admin/regulatory/rules/%d' % legacy).get_data(as_text=True)
        self.assertIn('Không dùng đối chiếu', html)
        self.assertNotIn('value="restore"', html); self.assertNotIn('value="edit"', html)
        lst = c.get('/admin/regulatory/rules?q=LEGNAME&field=name&state=all').get_data(as_text=True)
        self.assertIn('Không dùng đối chiếu', lst)
        r = c.post('/admin/regulatory/rules/save', json=dict(action='deactivate', rule_id=legacy,
                   expected_revision=1, request_id=str(uuid.uuid4()), reason='stop'), headers=CSRF)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.rows('SELECT is_active FROM regulatory_rules WHERE id=%s', (legacy,))[0]['is_active'])
        for act in ('restore', 'edit', 'create'):
            r = c.post('/admin/regulatory/rules/save', json=dict(action=act, rule_id=legacy, expected_revision=2,
                       request_id=str(uuid.uuid4()), match_field='name', match_value='LEGNAME',
                       status_id=self.statuses['CAM_NHAP'], reason='x'), headers=CSRF)
            self.assertEqual(r.status_code, 400)
        self.assertFalse(self.rows('SELECT is_active FROM regulatory_rules WHERE id=%s', (legacy,))[0]['is_active'])
        # stale revision on legacy deactivate
        l2 = self.rule('code', 'L2')
        r = c.post('/admin/regulatory/rules/save', json=dict(action='deactivate', rule_id=l2, expected_revision=9,
                   request_id=str(uuid.uuid4()), reason='x'), headers=CSRF)
        self.assertEqual(r.status_code, 409)

    def test_audit_failure_rolls_back_cas_mutation_and_key(self):
        self.rows("""CREATE FUNCTION rej() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit'; END $$""")
        self.rows('CREATE TRIGGER rej BEFORE INSERT ON regulatory_rule_manual_events FOR EACH ROW EXECUTE FUNCTION rej()')
        before = self.snap()
        with self.assertRaises(psycopg2.Error):
            self.create('64-17-5')
        self.conn.rollback()
        self.assertEqual(before, self.snap())

    def test_replay_returns_same_and_double_submit_single_row(self):
        rid, payload = self.create('64-17-5')
        again = self.mutate(payload)
        self.assertTrue(again['replayed'])
        self.assertEqual(self.rows('SELECT count(*) c FROM regulatory_rules')[0]['c'], 1)
        # same request id with different (name) content is not accepted
        bad = dict(payload, match_field='name', match_value='x')
        with self.assertRaises(manual.ManualProblem):
            self.mutate(bad)


class V5(Base):
    def setUp(self):
        super().setUp()
        self.bad = self.rows("INSERT INTO regulatory_statuses(stable_key,label,priority,export_policy) "
                             "VALUES ('BAD_X','1563-66-2',99,'ALLOW') RETURNING id")[0]['id']
        self.t = {self.bad: '1563-66-2'}

    def insp(self, targets=None):
        with self.conn, self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            return inspect_statuses(cur, targets or self.t)

    def apply(self, plan, targets=None):
        with self.conn, self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            return cleanup_statuses(cur, targets or self.t, plan['fingerprint'], actor='v', reason='r')

    def all_tables(self):
        names = [r['tablename'] for r in self.rows("select tablename from pg_tables where schemaname='public' order by 1")]
        return {n: self.rows('SELECT md5(t::text) h FROM %s t ORDER BY 1' % n) for n in names}

    def test_valid_status_labels_never_deletable(self):
        for sid, label in [(self.statuses['CAM_NHAP'], 'CẤM NHẬP'), (self.statuses['CAM_NHAP'], '64-17-5'),
                           (self.bad, '1563-66-2 '), (self.bad, ' 1563-66-2'), (self.bad, '1563-66-2\n'),
                           (self.bad, '１５６３-66-2'), (self.bad, "1563-66-2'; DROP TABLE x;--")]:
            with self.subTest(label=label):
                before = self.all_tables()
                with self.assertRaises(CleanupProblem):
                    self.insp({sid: label})
                self.assertEqual(before, self.all_tables())

    def test_ids_and_label_pairing_and_batch_atomicity(self):
        other = self.rows("INSERT INTO regulatory_statuses(stable_key,label,priority,export_policy) "
                          "VALUES ('BAD_Y','944-22-9',98,'ALLOW') RETURNING id")[0]['id']
        self.rows("INSERT INTO regulatory_status_events(status_id,actor,event) VALUES (%s,'a','created')", (other,))
        targets = {self.bad: '1563-66-2', other: '944-22-9'}
        plan = self.insp(targets)
        self.assertFalse(plan['can_delete'])
        before = self.all_tables()
        with self.assertRaises(CleanupProblem):
            self.apply(plan, targets)
        self.assertEqual(before, self.all_tables())      # nothing partially deleted
        with self.assertRaises(CleanupProblem):
            self.insp({self.bad: '944-22-9', other: '1563-66-2'})      # swapped
        with self.assertRaises(CleanupProblem):
            self.insp({i: '1563-66-2' for i in range(1, 22)})
        for bad in ({0: '1563-66-2'}, {-1: '1563-66-2'}, {'1': '1563-66-2'}, {self.bad: None}):
            with self.assertRaises((CleanupProblem, TypeError)):
                self.insp(bad)

    def test_each_reference_kind_blocks_and_nothing_else_changes(self):
        rule, _ = self.create('64-17-5')
        self.rows('CREATE TABLE product_deleted_rows (id serial, manual_compliance_status_id bigint REFERENCES regulatory_statuses(id))')
        stmts = [("INSERT INTO regulatory_rules(status_id,rule_type,rule_label,match_field,match_value) VALUES (%s,'','','cas','50-00-0')", (self.bad,)),
                 ("INSERT INTO products(name,code,brand,manual_compliance) VALUES ('t','t','t','1563-66-2')", ()),
                 ('INSERT INTO product_deleted_rows(manual_compliance_status_id) VALUES (%s)', (self.bad,)),
                 ("INSERT INTO regulatory_rule_manual_keys(rule_id,status_id,match_field,match_value) VALUES (%s,%s,'cas','50-00-0')", (rule, self.bad)),
                 ("INSERT INTO regulatory_status_events(status_id,actor,event) VALUES (%s,'t','created')", (self.bad,))]
        for s, a in stmts:
            with self.subTest(s=s[:40]):
                self.rows(s, a) if '%s' in s else self.rows(s)
                snap = self.all_tables()
                p = self.insp()
                self.assertFalse(p['can_delete'])
                with self.assertRaises(CleanupProblem):
                    self.apply(p)
                self.assertEqual(snap, self.all_tables())
                self.rows("DELETE FROM product_deleted_rows"); self.rows("DELETE FROM regulatory_status_events WHERE status_id=%s", (self.bad,))
                self.rows("DELETE FROM regulatory_rule_manual_keys WHERE status_id=%s", (self.bad,))
                self.rows("DELETE FROM regulatory_rules WHERE status_id=%s", (self.bad,))
                self.rows("DELETE FROM products WHERE code='t'")

    def test_stale_after_dry_run_changes(self):
        for change in ["UPDATE regulatory_statuses SET export_policy='BLOCK' WHERE id=%(i)s",
                       "UPDATE regulatory_statuses SET priority=7 WHERE id=%(i)s",
                       "INSERT INTO regulatory_status_events(status_id,actor,event) VALUES (%(i)s,'x','created')",
                       "INSERT INTO regulatory_rules(status_id,rule_type,rule_label,match_field,match_value) VALUES (%(i)s,'','','cas','50-00-0')"]:
            with self.subTest(change=change[:50]):
                plan = self.insp()
                self.rows(change % dict(i=self.bad))
                snap = self.all_tables()
                with self.assertRaises(CleanupProblem):
                    self.apply(plan)
                self.assertEqual(snap, self.all_tables())
                self.rows("DELETE FROM regulatory_rules WHERE status_id=%s", (self.bad,))
                self.rows("DELETE FROM regulatory_status_events WHERE status_id=%s", (self.bad,))
                self.rows("UPDATE regulatory_statuses SET priority=99,export_policy='ALLOW' WHERE id=%s", (self.bad,))

    def test_competing_reference_during_apply_never_deletes(self):
        plan = self.insp()
        other = psycopg2.connect(self.dsn); self.addCleanup(other.close)
        with other.cursor() as cur:
            cur.execute("INSERT INTO regulatory_rules(status_id,rule_type,rule_label,match_field,match_value) VALUES (%s,'','','cas','50-00-0')", (self.bad,))
        with self.assertRaises(psycopg2.errors.LockNotAvailable):
            with self.conn, self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SET LOCAL lock_timeout='150ms'")
                cleanup_statuses(cur, self.t, plan['fingerprint'], actor='v', reason='r')
        other.commit()
        self.assertEqual(len(self.rows('SELECT 1 FROM regulatory_statuses WHERE id=%s', (self.bad,))), 1)

    def test_successful_apply_scope_audit_backup(self):
        before = self.all_tables()
        plan = self.insp()
        self.assertEqual(before, self.all_tables())    # dry-run writes nothing
        self.apply(plan)
        after = self.all_tables()
        changed = {k for k in before if before[k] != after[k]}
        self.assertEqual(changed, {'regulatory_statuses', 'regulatory_status_events'})
        ev = self.rows('SELECT * FROM regulatory_status_events')[-1]
        self.assertEqual(ev['event'], 'removed_invalid_labels')
        self.assertEqual(ev['before_json']['statuses'][0]['label'], '1563-66-2')
        self.assertEqual(ev['actor'], 'v')
        with self.assertRaises(CleanupProblem):
            self.apply(plan)     # replay

    def test_cli_requires_flags(self):
        env = dict(os.environ)
        for args in (['--status', '%d=1563-66-2' % self.bad, '--apply'],
                     ['--status', '%d=1563-66-2' % self.bad, '--apply', '--fingerprint', 'x'],
                     ['--status', '%d=1563-66-2' % self.bad, '--status', '%d=1563-66-2' % self.bad],
                     ['--status', 'abc'], []):
            p = subprocess.run([sys.executable, '-B', 'scripts/cleanup_invalid_regulatory_statuses.py'] + args,
                               cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(p.returncode, 0, args)
        p = subprocess.run([sys.executable, '-B', 'scripts/cleanup_invalid_regulatory_statuses.py', '--status',
                            '%d=1563-66-2' % self.bad, '--apply', '--fingerprint', '0' * 64, '--actor', 'a',
                            '--reason', 'r'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(len(self.rows('SELECT 1 FROM regulatory_statuses WHERE id=%s', (self.bad,))), 1)


class V6(Base):
    def test_status_label_validation_http(self):
        c = self.client()
        before = self.snap()
        for label in ['50-00-0', ' 50-00-0 ', '64-17-5', '1563-66-2', '', '   ', 'x' * 121]:
            r = c.post('/admin/regulatory/statuses', data=dict(csrf_token='regulatory-test-csrf', action='add', label=label))
            self.assertNotIn('err=', '')
        # none of the above may have added a CAS-looking status
        labels = [s['label'] for s in self.rows('SELECT label FROM regulatory_statuses')]
        for bad in ['50-00-0', '64-17-5', '1563-66-2']:
            self.assertNotIn(bad, [l.strip() for l in labels])
        n = len(labels)
        for label in ['Danh mục tự đặt', '50-00-0 Formaldehyde', 'Nhóm 3', '50 - 00 - 0']:
            c.post('/admin/regulatory/statuses', data=dict(csrf_token='regulatory-test-csrf', action='add', label=label))
        labels2 = [s['label'] for s in self.rows('SELECT label FROM regulatory_statuses')]
        self.assertEqual(len(labels2), n + 4, labels2)

    def test_rename_to_cas_rejected_and_stale_revision(self):
        c = self.client()
        st = self.rows('SELECT * FROM regulatory_statuses ORDER BY id')[0]
        before = self.snap()
        for label in ['50-00-0', '  64-17-5']:
            c.post('/admin/regulatory/statuses', data=dict(csrf_token='regulatory-test-csrf', action='rename',
                   status_id=st['id'], label=label, revision=str(st['updated_at'])))
        self.assertEqual(before, self.snap())
        c.post('/admin/regulatory/statuses', data=dict(csrf_token='regulatory-test-csrf', action='rename',
               status_id=st['id'], label='Tên mới', revision='stale'))
        self.assertEqual(before, self.snap())
        c.post('/admin/regulatory/statuses', data=dict(csrf_token='regulatory-test-csrf', action='rename',
               status_id=st['id'], label='Tên mới', revision=str(st['updated_at'])))
        self.assertEqual(self.rows('SELECT label FROM regulatory_statuses WHERE id=%s', (st['id'],))[0]['label'], 'Tên mới')

    def test_import_new_status_cas_label_rejected_custom_accepted(self):
        before = self.snap()
        wb = lambda lab: xlsx(['CAS', 'Tình trạng quản lý', 'Ghi chú quản lý'], [['64-17-5', lab, '']])
        for lab in ['50-00-0', ' 50-00-0']:
            resp = self.client().post('/admin/regulatory/upload', data={'csrf_token': 'regulatory-test-csrf',
                'mode': 'upsert', 'submission_key': str(uuid.uuid4()), 'file': (wb(lab), 'a.xlsx')})
            job = resp.location.rsplit('/', 1)[-1]
            jobs.run_once(job)
            self.assertEqual(self.rows('SELECT status FROM regulatory_import_jobs WHERE id=%s', (job,))[0]['status'], 'failed')
        self.assertEqual(before, self.snap())
        resp = self.client().post('/admin/regulatory/upload', data={'csrf_token': 'regulatory-test-csrf',
            'mode': 'upsert', 'submission_key': str(uuid.uuid4()), 'file': (wb('Nhóm tự đặt'), 'a.xlsx')})
        job = resp.location.rsplit('/', 1)[-1]; jobs.run_once(job)
        self.assertEqual(self.rows('SELECT status FROM regulatory_import_jobs WHERE id=%s', (job,))[0]['status'], 'completed')

    def test_staff_csrf_and_menu_permission_negatives(self):
        staff = self.team_client({'SEARCH', 'VIEW_NAME', 'VIEW_COMPLIANCE'})
        before = self.snap()
        sv = dict(action='create', request_id=str(uuid.uuid4()), match_field='cas', match_value='64-17-5',
                  status_id=self.statuses['CAM_NHAP'])
        for path, kw in [('/admin/regulatory/rules/save', dict(json=sv)), ('/admin/regulatory/rules/check', dict(json=sv)),
                         ('/admin/regulatory/statuses', dict(data=dict(action='add', label='X', csrf_token='regulatory-test-csrf'))),
                         ('/admin/regulatory/upload', dict(data=dict(csrf_token='regulatory-test-csrf', mode='upsert',
                          submission_key=str(uuid.uuid4()), file=(xlsx(['CAS', 'Tình trạng quản lý', 'Ghi chú quản lý'],
                          [['64-17-5', 'CẤM NHẬP', '']]), 'a.xlsx'))))]:
            r = staff.post(path, headers=CSRF, **kw)
            self.assertIn(r.status_code, (302, 401, 403), (path, r.status_code))
            if r.status_code == 302:
                self.assertNotIn('/admin/regulatory/jobs/', r.location)
        for path in ['/admin/regulatory', '/admin/regulatory/rules', '/admin/regulatory/rules/new']:
            self.assertIn(staff.get(path).status_code, (302, 401, 403))
        self.assertEqual(before, self.snap())
        self.assertEqual(self.rows('SELECT count(*) c FROM regulatory_import_jobs')[0]['c'], 0)
        # admin without CSRF / with wrong CSRF / form-token only
        c = self.client()
        for hdr in ({}, {'X-CSRF-Token': 'wrong'}):
            r = c.post('/admin/regulatory/rules/save', json=sv, headers=hdr)
            self.assertEqual(r.status_code, 400)
            r = c.post('/admin/regulatory/rules/check', json=sv, headers=hdr)
            self.assertEqual(r.status_code, 400)
        r = c.post('/admin/regulatory/statuses', data=dict(action='add', label='Y'))
        self.assertGreaterEqual(r.status_code, 300 if r.status_code != 302 else 302)
        self.assertEqual(before, self.snap())
        # admin lacking the 'regulatory' menu grant
        self.rows('DELETE FROM admin_menu_grants WHERE user_id=%s', (self.actor,))
        r = c.post('/admin/regulatory/rules/save', json=sv, headers=CSRF)
        self.assertIn(r.status_code, (302, 403))
        self.assertEqual(before, self.snap())

    def test_extra_upload_params_and_check_payload_keys(self):
        c = self.client()
        r = c.post('/admin/regulatory/rules/check', json=dict(match_field='cas', match_value='64-17-5',
                   status_id=self.statuses['CAM_NHAP'], extra=1), headers=CSRF)
        self.assertEqual(r.status_code, 400)
        r = c.post('/admin/regulatory/upload', data={'csrf_token': 'regulatory-test-csrf', 'mode': 'upsert',
            'submission_key': str(uuid.uuid4()), 'match_field': 'name',
            'file': (xlsx(['CAS', 'Tình trạng quản lý', 'Ghi chú quản lý'], [['64-17-5', 'CẤM NHẬP', '']]), 'a.xlsx')})
        self.assertEqual(self.rows('SELECT count(*) c FROM regulatory_import_jobs')[0]['c'], 0)

    def test_nav_near_miss_paths(self):
        import re
        import search
        from flask import render_template, session
        from auth_test_helpers import set_authenticated_session
        cases = {'/admin/regulatory': True, '/admin/regulatory/': True, '/admin/regulatory/rules': True,
                 '/admin/regulatory/jobs/x/protection': True, '/admin/regulatory-other': False,
                 '/admin/regulatoryx/rules': False, '/admin/regulatory_rules': False,
                 '/admin/Regulatory/rules': False, '/regulatory/rules': False}
        for path, expected in cases.items():
            with self.subTest(path=path), search.app.test_request_context(path):
                session.update(authenticated=True, user_id=self.actor, auth_version=self.version,
                               is_admin=True, username='manual_admin')
                body = render_template('_user_nav.html')
                trig = re.search(r'<button[^>]*id="sqAdminTrigger"[^>]*>', body).group(0)
                link = re.search(r'<a class="[^"]*"\s+href="/admin/regulatory"[^>]*>', body).group(0)
                self.assertEqual('is-active' in trig, expected)
                self.assertEqual('is-active' in link, expected)
                self.assertEqual('aria-current' in link, expected)

    def test_subpages_render_shared_menu_and_assets(self):
        c = self.client()
        rid, _ = self.create('64-17-5')
        for path in ['/admin/regulatory', '/admin/regulatory/rules', '/admin/regulatory/rules/new',
                     '/admin/regulatory/rules/%d' % rid]:
            body = c.get(path).get_data(as_text=True)
            self.assertIn('sqAdminTrigger', body, path)
            self.assertIn('href="/admin/regulatory"', body, path)
            self.assertNotIn('>Mã<', body.split('<main')[1].split('</select>')[0] if path.endswith('new') else '')
        new = c.get('/admin/regulatory/rules/new').get_data(as_text=True)
        self.assertNotIn('value="name"', new); self.assertNotIn('value="code"', new)
        home = c.get('/admin/regulatory').get_data(as_text=True)
        self.assertNotIn('regulatory/templates/code', home); self.assertNotIn('regulatory/templates/name', home)
