"""Presentation regression; reads must not rewrite saved rule/audit/job data."""
from datetime import datetime, timezone, timedelta
import unittest
import uuid
from psycopg2.extras import Json

import regulatory_import_jobs as jobs
from regulatory_manual_support import RegulatoryCase
from regulatory_presentation import vietnam_time
from test_regulatory_manual_import import workbook


class TimeDisplayTests(unittest.TestCase):
    def test_vietnam_time_and_boundaries(self):
        self.assertEqual(vietnam_time(datetime(2026,9,28,6,1,59,tzinfo=timezone.utc)), '28/09/2026 13:01')
        self.assertEqual(vietnam_time('2026-12-31T18:05:59Z'), '01/01/2027 01:05')
        self.assertEqual(vietnam_time('2024-02-28T18:00:00+00:00'), '29/02/2024 01:00')
        self.assertEqual(vietnam_time(datetime(2026,9,28,13,1,tzinfo=timezone(timedelta(hours=7)))), '28/09/2026 13:01')
        self.assertEqual(vietnam_time(None), '—')
        self.assertEqual(vietnam_time('not-a-timestamp'), '—')


class DisplayHttpTests(RegulatoryCase):
    def snapshot(self):
        return {table:self.rows('SELECT * FROM '+table+' ORDER BY id') for table in (
            'regulatory_rules','regulatory_statuses','regulatory_rule_manual_keys',
            'regulatory_rule_manual_events','regulatory_import_jobs','regulatory_import_events')}

    def test_manual_labels_boolean_missing_and_vietnam_history_are_read_only(self):
        client=self.client()
        for field,value,label in [('cas','50-00-0','CAS'),('code','DISPLAY','Mã sản phẩm'),('name','Tên mẫu','Tên sản phẩm')]:
            rule,_=self.create(value,field,note='<img src=x onerror=alert(1)>')
            self.mutate(dict(action='deactivate',rule_id=rule,expected_revision=1,
                             request_id=str(uuid.uuid4()),reason='Ngừng để kiểm tra'))
            self.rows("UPDATE regulatory_rule_manual_events SET created_at='2026-09-28T06:01:59+00:00' WHERE rule_id=%s",(rule,))
            before=self.snapshot()
            response=client.get('/admin/regulatory/rules/'+str(rule))
            self.assertEqual(response.status_code,200)
            html=response.get_data(as_text=True)
            self.assertIn('type="hidden" name="match_field" value="'+field+'"',html)
            self.assertIn('<input value="'+label+'" readonly>',html)
            self.assertNotIn('ID tình trạng',html)
            self.assertIn('<th>Áp dụng</th><td>Có</td><td>Không</td>',html)
            self.assertIn('<th>Áp dụng</th><td>—</td><td>Có</td>',html)
            self.assertIn('<th>Bảo vệ</th><td>Có</td><td>Có</td>',html)
            self.assertIn('>28/09/2026 13:01</time>',html)
            self.assertIn('UTC+7',html)
            self.assertNotIn('<img src=x',html)
            self.assertEqual(self.snapshot(),before)
            events=self.rows('SELECT before_json,after_json FROM regulatory_rule_manual_events WHERE rule_id=%s ORDER BY id',(rule,))
            self.assertIn('status_id',events[-1]['after_json'])
            self.assertIs(events[-1]['before_json']['is_active'],True)
            self.assertIs(events[-1]['after_json']['is_active'],False)

    def test_import_username_each_event_time_and_dynamic_delete_confirmation(self):
        client=self.client()
        other=self.rows("""INSERT INTO app_users(username,password_hash,is_admin,account_status)
            VALUES (%s,'x',false,'ACTIVE') RETURNING id""",('actor-<img src=x>',))[0]['id']
        for count,status in [(0,'CAM_NHAP'),(1,'PHU_LUC_II'),(3,'PHU_LUC_III')]:
            for i in range(count):
                self.rows("""INSERT INTO regulatory_rules(status_id,rule_type,rule_label,match_field,match_value)
                    VALUES (%s,'','','code',%s)""",(self.statuses[status],f'REMOVE-{count}-{i}'))
            label=self.rows('SELECT label FROM regulatory_statuses WHERE id=%s',(self.statuses[status],))[0]['label']
            response=client.post('/admin/regulatory/upload',data={'csrf_token':'regulatory-test-csrf',
                'mode':'replace_scoped','submission_key':str(uuid.uuid4()),
                'file':(workbook([[f'INSERT-{count}',label,'from file']]),'display.xlsx')})
            self.assertEqual(response.status_code,302)
            job_id=response.location.rsplit('/',1)[1]
            jobs.run_once(job_id)
            plan=self.rows('SELECT preview FROM regulatory_import_jobs WHERE id=%s',(job_id,))[0]['preview']
            self.assertEqual(plan['deleted'],count)
            for actor in [f'user:{other}','worker','user:999999999999999999999999999']:
                self.rows("""INSERT INTO regulatory_import_events(job_id,actor,event,created_at)
                    VALUES (%s,%s,'uploaded','2026-12-31T18:05:00+00:00')""",(job_id,actor))
            self.rows("UPDATE regulatory_import_jobs SET created_at='2026-09-28T06:01:00+00:00' WHERE id=%s",(job_id,))
            before=self.snapshot()
            html=client.get('/admin/regulatory/jobs/'+job_id).get_data(as_text=True)
            self.assertIn(f'Nhập <strong>{count}</strong> để xác nhận số quy tắc sẽ xóa',html)
            self.assertNotIn('để xác nhận số dòng sẽ xóa',html)
            self.assertIn('<td>manual_admin</td>',html)
            self.assertIn('<td>actor-&lt;img src=x&gt;</td>',html)
            self.assertIn('<td>Hệ thống</td>',html)
            self.assertIn('Tài khoản không còn tồn tại',html)
            self.assertIn('>01/01/2027 01:05</time>',html)
            self.assertNotIn('<img src=x>',html)
            index=client.get('/admin/regulatory').get_data(as_text=True)
            self.assertIn('>28/09/2026 13:01</time>',index)
            self.assertEqual(client.get('/admin/regulatory/jobs/'+job_id+'/protection').status_code,200)
            self.assertEqual(self.snapshot(),before)
            # A text change must not weaken the actual delete-count gate (including zero).
            denied=client.post('/admin/regulatory/jobs/'+job_id+'/apply',data={
                'csrf_token':'regulatory-test-csrf','fingerprint':plan['fingerprint'],'confirm_delete':str(count+1)})
            self.assertIn('err=',denied.location)
            self.assertEqual(self.snapshot(),before)
