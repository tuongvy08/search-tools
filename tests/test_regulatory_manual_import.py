from io import BytesIO
import uuid
from unittest import mock
from openpyxl import Workbook
from psycopg2.extras import RealDictCursor
import regulatory_import_jobs as jobs
from import_engine import ImportProblem
from regulatory_manual_support import RegulatoryCase


def workbook(rows):
    wb = Workbook(); ws = wb.active
    ws.append(['Code','Tình trạng quản lý','Ghi chú quản lý'])
    for row in rows:
        ws.append(row)
    stream = BytesIO(); wb.save(stream); stream.seek(0)
    return stream


class ImportTests(RegulatoryCase):
    def upload(self, mode, rows):
        response = self.client().post('/admin/regulatory/upload', data={
            'csrf_token':'regulatory-test-csrf', 'mode':mode, 'submission_key':str(uuid.uuid4()),
            'file':(workbook(rows),'test.xlsx')})
        self.assertEqual(response.status_code,302)
        self.assertIn('/jobs/', response.location)
        return response.location.rsplit('/',1)[1]

    def job(self, job_id):
        return self.rows('SELECT * FROM regulatory_import_jobs WHERE id=%s',(job_id,))[0]

    def apply(self, job_id):
        preview = self.job(job_id)['preview']
        response = self.client().post('/admin/regulatory/jobs/' + job_id + '/apply', data={
            'csrf_token':'regulatory-test-csrf', 'fingerprint':preview['fingerprint'],
            'confirm_delete':str(preview['deleted'])})
        self.assertEqual(response.status_code,302)
        self.assertNotIn('err=',response.location)
        self.assertTrue(jobs.run_once(job_id))
        self.assertEqual(self.job(job_id)['status'],'completed',self.job(job_id)['errors'])

    def test_both_modes_protect_current_old_inactive_absent_and_cleanup(self):
        for mode in ['upsert','replace_scoped']:
            with self.subTest(mode=mode):
                prefix = mode + '-'
                a, original = self.create(prefix+'old')
                self.mutate(dict(original, action='edit', rule_id=a, expected_revision=1,
                                 request_id=str(uuid.uuid4()), match_value=prefix+'current', note='hand edit'))
                inactive, _ = self.create(prefix+'inactive')
                self.mutate(dict(action='deactivate', rule_id=inactive,expected_revision=1,
                                 request_id=str(uuid.uuid4()),reason='stop'))
                absent, _ = self.create(prefix+'absent')
                pure = self.rows("""INSERT INTO regulatory_rules(status_id,rule_type,rule_label,match_field,match_value,note)
                    VALUES (%s,'','','code',%s,'pure') RETURNING id""",(self.statuses['CAM_NHAP'],prefix+'pure'))[0]['id']
                before = self.rows('SELECT * FROM regulatory_rules WHERE id=ANY(%s) ORDER BY id',([a,inactive,absent],))
                job_id = self.upload(mode,[[prefix+'old','CẤM NHẬP','file'],
                    [prefix+'current','CẤM NHẬP',''],[prefix+'inactive','CẤM NHẬP','file'],[prefix+'new','CẤM NHẬP','file']])
                self.assertTrue(jobs.run_once(job_id))
                preview = self.job(job_id)['preview']
                self.assertEqual(preview['contract_version'],2)
                self.assertEqual(preview['inserted'],1)
                self.assertEqual(preview['updated'],0)
                self.assertEqual(preview['unchanged'],3)
                self.assertTrue(any(d['reason']=='Khóa trước khi sửa' for d in preview['protection_details']))
                self.apply(job_id)
                self.assertEqual(self.rows('SELECT * FROM regulatory_rules WHERE id=ANY(%s) ORDER BY id',([a,inactive,absent],)),before)
                self.assertEqual(bool(self.rows('SELECT id FROM regulatory_rules WHERE id=%s',(pure,))),mode=='upsert')
                self.assertFalse(self.rows('SELECT id FROM regulatory_rules WHERE match_value=%s',(prefix+'old',)))
                self.rows("UPDATE regulatory_import_jobs SET expires_at=now()-interval '1 second' WHERE id=%s",(job_id,))
                jobs.cleanup()
                self.assertIsNone(self.job(job_id)['preview'])
                with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                    details = jobs.protection_page(cur,job_id)
                self.assertEqual(details['total'],len(preview['protection_details']))
                self.assertEqual(self.rows('SELECT * FROM regulatory_rules WHERE id=ANY(%s) ORDER BY id',([a,inactive,absent],)),before)

    def test_stale_preview_old_contract_and_override_rejected(self):
        rule, original = self.create()
        job_id = self.upload('upsert',[['ABC','CẤM NHẬP','file']])
        jobs.run_once(job_id)
        plan = self.job(job_id)['preview']
        self.mutate(dict(original,action='edit',rule_id=rule,expected_revision=1,request_id=str(uuid.uuid4()),note='later'))
        with self.conn, self.conn.cursor() as cur:
            rows=[dict(row_number=2,match_field='code',match_value='ABC',status_label='CẤM NHẬP',note='file')]
            with self.assertRaisesRegex(ImportProblem,'đổi'):
                jobs.apply_plan(cur,rows,'upsert',plan)
            with self.assertRaisesRegex(ImportProblem,'cũ'):
                jobs.apply_plan(cur,rows,'upsert',dict(plan,contract_version=1))
        response=self.client().post('/admin/regulatory/jobs/'+job_id+'/apply',data={
            'csrf_token':'regulatory-test-csrf','force':'1','fingerprint':plan['fingerprint']})
        self.assertEqual(response.status_code,400)

    def test_full_snapshot_pages_and_byte_limit(self):
        for i in range(53):
            self.create('P'+str(i))
        job_id=self.upload('upsert', [['P'+str(i),'CẤM NHẬP','file'] for i in range(53)])
        jobs.run_once(job_id)
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            first=jobs.protection_page(cur,job_id)
            second=jobs.protection_page(cur,job_id,2)
        self.assertEqual((first['total'],len(first['details']),len(second['details'])),(53,50,3))
        with mock.patch.object(jobs,'PROTECTION_DETAILS_MAX_BYTES',10):
            failed=self.upload('replace_scoped',[['P0','CẤM NHẬP','long']])
            jobs.run_once(failed)
        self.assertEqual(self.job(failed)['status'],'failed')
        self.assertFalse(self.job(failed)['preview_ready'])
        self.assertIn('không giao nhau',self.job(failed)['errors'][0])
        self.assertEqual(len(self.rows('SELECT id FROM regulatory_rules')),53)

    def test_actual_eight_mib_boundary_without_truncation(self):
        self.rows("""INSERT INTO regulatory_rules(status_id,rule_type,rule_label,match_field,match_value,note,manual_protected)
            SELECT %s,'','','code','SIZE-'||lpad(n::text,4,'0'),repeat('M',4000),true
            FROM generate_series(1,1000) n""",(self.statuses['CAM_NHAP'],))
        rows=[dict(row_number=i+2,match_field='code',match_value='SIZE-'+str(i+1).zfill(4),
                   status_label='CẤM NHẬP',note='F'*4000) for i in range(1000)]
        with self.conn.cursor() as cur:
            with mock.patch.object(jobs,'PROTECTION_DETAILS_MAX_BYTES',32*1024*1024):
                large=jobs.build_plan(cur,rows,'upsert')
            excess=jobs.detail_bytes(large['protection_details'])-8*1024*1024
            self.assertGreater(excess,0)
            changed=0
            for i,row in enumerate(rows):
                cut=min(excess,len(row['note']))
                row['note']=row['note'][:len(row['note'])-cut]
                excess-=cut
                if not excess:
                    changed=i
                    break
            exact=jobs.build_plan(cur,rows,'upsert')
            self.assertEqual(jobs.detail_bytes(exact['protection_details']),8*1024*1024)
            self.assertEqual(len(exact['protection_details']),1000)
            rows[changed]['note']+='F'
            with self.assertRaisesRegex(ImportProblem,'8 MiB'):
                jobs.build_plan(cur,rows,'upsert')
            rows[changed]['note']=rows[changed]['note'][:-2]
            below=jobs.build_plan(cur,rows,'upsert')
            self.assertEqual(jobs.detail_bytes(below['protection_details']),8*1024*1024-1)

    def test_retry_has_no_default_snapshot_until_new_completion(self):
        for mode in ('upsert','replace_scoped'):
            with self.subTest(mode=mode):
                rule, original = self.create('SNAP-'+mode)
                job_id = self.upload(mode, [['SNAP-'+mode,'CẤM NHẬP','file']])
                jobs.run_once(job_id)
                with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                    old = jobs.protection_page(cur, job_id)
                client = self.client()
                response = client.post('/admin/regulatory/jobs/'+job_id+'/retry',
                                       data={'csrf_token':'regulatory-test-csrf'})
                self.assertEqual(response.status_code,302)
                self.assertNotIn('err=',response.location)
                current = self.job(job_id)
                self.assertEqual((current['status'],current['preview_ready'],current['preview']),('queued',False,None))
                url = '/admin/regulatory/jobs/'+job_id+'/protection'
                self.assertEqual(client.get(url).status_code,404)
                self.assertEqual(client.get(url+'?event_id='+str(old['id'])).status_code,200)
                self.mutate(dict(original,action='edit',rule_id=rule,expected_revision=1,
                                 request_id=str(uuid.uuid4()),note='after retry'))
                jobs.run_once(job_id)
                self.assertEqual(client.get(url).status_code,200)
                with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
                    new = jobs.protection_page(cur,job_id)
                    historical = jobs.protection_page(cur,job_id,event_id=old['id'])
                self.assertNotEqual(new['id'],old['id'])
                self.assertEqual(new['details'][0]['before']['note'],'after retry')
                self.assertEqual(historical['details'][0]['before']['note'],'manual')
