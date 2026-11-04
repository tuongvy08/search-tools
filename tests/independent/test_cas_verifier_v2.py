"""Independent v2 adversarial checks: non-ASCII digits in CAS (synthetic data, disposable DB)."""
import uuid

import regulatory_import_jobs as jobs
import regulatory_manual as manual
from import_engine import ImportProblem
from regulatory import normalize_cas, normalize_match_value, RegulatoryError
from independent.test_cas_verifier import Base, CSRF, xlsx

FULLWIDTH = '６４-１７-５'
ARABIC = '٦٤-١٧-٥'
MIXED = '６４-١٧-5'
MIXED2 = '64-١٧-5'
DEVANAGARI = '६४-१७-५'
SUPERSCRIPT = '6⁴-17-5'
BAD_CAS = [FULLWIDTH, ARABIC, MIXED, MIXED2, DEVANAGARI, SUPERSCRIPT,
           '64‐17‐5',        # U+2010 hyphen
           '64–17–5',        # en dash
           '64−17−5',        # minus sign
           '64－17－5',      # fullwidth hyphen
           '64-17-5​',  # zero width space
           '64​-17-5',
           '６４-１７-５\n64-17-5']
GUIDE = '0-9'


class V3Unicode(Base):
    def test_pure_validators(self):
        for v in BAD_CAS:
            with self.subTest(v=v):
                with self.assertRaises(RegulatoryError):
                    normalize_cas(v)
                with self.assertRaises(ValueError):
                    normalize_match_value('cas', v)
        for v in ('64-17-5', ' 64-17-5 ', '\t64-17-5\n'):
            self.assertEqual(normalize_cas(v), '64-17-5')

    def test_pure_message_guides_reentry_for_digits(self):
        for v in (FULLWIDTH, ARABIC, MIXED):
            with self.assertRaises(RegulatoryError) as cm:
                normalize_cas(v)
            self.assertIn(GUIDE, str(cm.exception))

    def test_api_check_and_save_reject_and_write_nothing(self):
        c = self.client()
        before = self.snap()
        for v in BAD_CAS:
            with self.subTest(v=v):
                r = c.post('/admin/regulatory/rules/check', json=dict(
                    match_field='cas', match_value=v, status_id=self.statuses['CAM_NHAP']), headers=CSRF)
                self.assertEqual(r.status_code, 400, r.get_data(as_text=True)[:200])
                s = c.post('/admin/regulatory/rules/save', json=dict(
                    action='create', request_id=str(uuid.uuid4()), match_field='cas', match_value=v,
                    status_id=self.statuses['CAM_NHAP']), headers=CSRF)
                self.assertEqual(s.status_code, 400, s.get_data(as_text=True)[:200])
                f = c.post('/admin/regulatory/rules/save', headers=CSRF, data=dict(
                    action='create', request_id=str(uuid.uuid4()), match_field='cas', match_value=v,
                    status_id=self.statuses['CAM_NHAP']))
                self.assertIn(f.status_code, (400, 415))
        self.assertEqual(before, self.snap())
        # message for a digit case carries re-entry guidance in the HTTP body
        r = c.post('/admin/regulatory/rules/check', json=dict(
            match_field='cas', match_value=FULLWIDTH, status_id=self.statuses['CAM_NHAP']), headers=CSRF)
        self.assertIn(GUIDE, r.get_data(as_text=True))

    def test_api_edit_to_unicode_rejected_original_untouched(self):
        c = self.client()
        rid, _ = self.create('64-17-5')
        before = self.snap()
        for v in (FULLWIDTH, ARABIC, MIXED):
            r = c.post('/admin/regulatory/rules/save', json=dict(
                action='edit', rule_id=rid, expected_revision=1, request_id=str(uuid.uuid4()),
                match_field='cas', match_value=v, status_id=self.statuses['CAM_NHAP'],
                reason='r'), headers=CSRF)
            self.assertEqual(r.status_code, 400)
        self.assertEqual(before, self.snap())

    def test_restore_and_edit_of_preexisting_unicode_rule_rejected_deactivate_allowed(self):
        c = self.client()
        # legacy bad data inserted directly (simulates old database content)
        rid = self.rule('cas', FULLWIDTH, 'CAM_NHAP', 'old-bad', active=False)
        before = self.snap()
        r = c.post('/admin/regulatory/rules/save', json=dict(
            action='restore', rule_id=rid, expected_revision=1, request_id=str(uuid.uuid4()),
            reason='r'), headers=CSRF)
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True)[:200])
        r = c.post('/admin/regulatory/rules/save', json=dict(
            action='edit', rule_id=rid, expected_revision=1, request_id=str(uuid.uuid4()),
            match_field='cas', match_value=FULLWIDTH, status_id=self.statuses['CAM_NHAP'],
            reason='r'), headers=CSRF)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(before, self.snap())
        # active legacy bad row: it must stay stored verbatim (no silent data rewrite)
        rid2 = self.rule('cas', ARABIC, 'CAM_NHAP', 'old-bad2', active=True)
        row = self.rows('SELECT match_value FROM regulatory_rules WHERE id=%s', (rid2,))[0]
        self.assertEqual(row['match_value'], ARABIC)
        # editing the legacy row to a correct ASCII CAS is a re-entry and must be allowed
        r = c.post('/admin/regulatory/rules/save', json=dict(
            action='edit', rule_id=rid2, expected_revision=1, request_id=str(uuid.uuid4()),
            match_field='cas', match_value='64-17-5', status_id=self.statuses['CAM_NHAP'],
            reason='re-enter'), headers=CSRF)
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])

    def test_service_level_restore_unicode(self):
        rid = self.rule('cas', MIXED, 'CAM_NHAP', 'x', active=False)
        before = self.snap()
        with self.assertRaises(manual.ManualProblem):
            self.mutate(dict(action='restore', rule_id=rid, expected_revision=1,
                             request_id=str(uuid.uuid4()), reason='r'))
        self.conn.rollback()
        self.assertEqual(before, self.snap())

    def test_workbook_rejects_whole_file_for_each_bad_form(self):
        H = ['CAS', 'Tình trạng quản lý', 'Ghi chú quản lý']
        for v in (FULLWIDTH, ARABIC, MIXED, DEVANAGARI, '64‐17‐5'):
            with self.subTest(v=v):
                before = self.snap()
                resp = self.client().post('/admin/regulatory/upload', data={
                    'csrf_token': 'regulatory-test-csrf', 'mode': 'upsert', 'submission_key': str(uuid.uuid4()),
                    'file': (xlsx(H, [['50-00-0', 'CẤM NHẬP', ''], [v, 'CẤM NHẬP', '']]), 't.xlsx')})
                job = resp.location.rsplit('/', 1)[-1]
                jobs.run_once(job)
                j = self.rows('SELECT status,preview_ready,error FROM regulatory_import_jobs WHERE id=%s', (job,))[0] \
                    if 'error' in [c['column_name'] for c in self.rows(
                        "select column_name from information_schema.columns where table_name='regulatory_import_jobs'")] \
                    else self.rows('SELECT status,preview_ready FROM regulatory_import_jobs WHERE id=%s', (job,))[0]
                self.assertEqual(j['status'], 'failed', j)
                self.assertFalse(j['preview_ready'])
                self.assertEqual(before, self.snap())

    def test_workbook_replace_scoped_with_bad_cas_deletes_nothing(self):
        absent = self.rule('cas', '64-17-5', 'CAM_NHAP', 'keep')
        before = self.snap()
        resp = self.client().post('/admin/regulatory/upload', data={
            'csrf_token': 'regulatory-test-csrf', 'mode': 'replace_scoped', 'submission_key': str(uuid.uuid4()),
            'file': (xlsx(['CAS', 'Tình trạng quản lý', 'Ghi chú quản lý'],
                          [[ARABIC, 'CẤM NHẬP', '']]), 't.xlsx')})
        job = resp.location.rsplit('/', 1)[-1]
        jobs.run_once(job)
        self.assertEqual(self.rows('SELECT status FROM regulatory_import_jobs WHERE id=%s', (job,))[0]['status'], 'failed')
        self.assertEqual(before, self.snap())
        self.assertEqual(len(self.rows('SELECT 1 FROM regulatory_rules WHERE id=%s', (absent,))), 1)

    def test_build_and_apply_plan_reject_unicode_even_with_valid_stale_plan(self):
        good = [dict(row_number=2, match_field='cas', match_value='64-17-5', status_label='CẤM NHẬP', note='')]
        with self.conn.cursor() as cur:
            plan = jobs.build_plan(cur, good, 'upsert')
            for v in (FULLWIDTH, ARABIC, MIXED):
                bad = [dict(good[0], match_value=v)]
                with self.assertRaises(ImportProblem):
                    jobs.build_plan(cur, bad, 'upsert')
                with self.assertRaises(ImportProblem):
                    jobs.build_plan(cur, bad, 'replace_scoped')
                # a plan valid for ASCII rows cannot be replayed with unicode rows
                with self.assertRaises(ImportProblem):
                    jobs.apply_plan(cur, bad, 'upsert', plan)
                # mixed good+bad rows
                with self.assertRaises(ImportProblem):
                    jobs.apply_plan(cur, good + [dict(good[0], row_number=3, match_value=v)], 'upsert', plan)
        self.conn.rollback()
        self.assertEqual([], self.rows('SELECT 1 FROM regulatory_rules'))

    def test_worker_applying_stored_bad_job_via_http_apply(self):
        # preview of a valid file, then the stored plan/rows can't be swapped by apply with tampered fingerprint
        resp = self.client().post('/admin/regulatory/upload', data={
            'csrf_token': 'regulatory-test-csrf', 'mode': 'upsert', 'submission_key': str(uuid.uuid4()),
            'file': (xlsx(['CAS', 'Tình trạng quản lý', 'Ghi chú quản lý'], [[FULLWIDTH, 'CẤM NHẬP', '']]), 't.xlsx')})
        job = resp.location.rsplit('/', 1)[-1]
        before = self.snap()
        jobs.run_once(job)
        r = self.client().post('/admin/regulatory/jobs/%s/apply' % job, data={
            'csrf_token': 'regulatory-test-csrf', 'fingerprint': '0' * 64, 'confirm_delete': '0'})
        jobs.run_once(job)
        self.assertEqual(before, self.snap())

    def test_ascii_cas_regression_paths_still_work(self):
        c = self.client()
        r = c.post('/admin/regulatory/rules/check', json=dict(
            match_field='cas', match_value=' 64-17-5 ', status_id=self.statuses['CAM_NHAP']), headers=CSRF)
        self.assertEqual(r.status_code, 200)
        s = c.post('/admin/regulatory/rules/save', json=dict(
            action='create', request_id=str(uuid.uuid4()), match_field='cas', match_value='7732-18-5',
            status_id=self.statuses['CAM_NHAP']), headers=CSRF)
        self.assertEqual(s.status_code, 200, s.get_data(as_text=True)[:200])
        stored = [x['match_value'] for x in self.rows('SELECT match_value FROM regulatory_rules')]
        self.assertEqual(stored, ['7732-18-5'])
        # unicode text in the status/note fields (not CAS) is still accepted
        s = c.post('/admin/regulatory/rules/save', json=dict(
            action='create', request_id=str(uuid.uuid4()), match_field='cas', match_value='50-00-0',
            status_id=self.statuses['CAM_NHAP'], note='Ghi chú tiếng Việt ６４'), headers=CSRF)
        self.assertEqual(s.status_code, 200, s.get_data(as_text=True)[:200])
        job_resp = self.client().post('/admin/regulatory/upload', data={
            'csrf_token': 'regulatory-test-csrf', 'mode': 'upsert', 'submission_key': str(uuid.uuid4()),
            'file': (xlsx(['CAS', 'Tình trạng quản lý', 'Ghi chú quản lý'], [['67-56-1', 'Nhóm tự đặt ٦', 'n']]), 't.xlsx')})
        job = job_resp.location.rsplit('/', 1)[-1]
        jobs.run_once(job)
        self.assertEqual(self.rows('SELECT status FROM regulatory_import_jobs WHERE id=%s', (job,))[0]['status'], 'completed')

    def test_unicode_product_cas_does_not_match_ascii_rule(self):
        self.rule('cas', '64-17-5', 'CAM_NHAP', 'x')
        for v in (FULLWIDTH, ARABIC, MIXED):
            self.assertIsNone(self.resolve(self.product('p', 'c', v))['status_id'])
