"""Phase 6C2 real queue/engine security, concurrency and atomicity gates.

Only temporary databases from pg_temp_db; no application DSN is written.
"""
import hashlib
from io import BytesIO
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from unittest import mock
import zipfile

import psycopg2
from psycopg2.extras import RealDictCursor
from openpyxl import Workbook
from werkzeug.datastructures import FileStorage

import search
import import_jobs as jobs
from import_engine import ImportProblem, inspect_workbook, workbook_rows
from brand_gateway import acquire_products_import_lock
from tests.pg_temp_db import create_full_schema_temp_db, drop_temp_db, probe_postgres_reachable, apply_brand_master_and_currency_migrations, apply_dynamic_brand_currency_migration

ROOT=Path(__file__).resolve().parents[1]


def xlsx(rows, headers=('brand','code','name','size','source_brand','price')):
    wb=Workbook(); ws=wb.active; ws.append(list(headers))
    for r in rows:
        ws.append([r.get(h,'') for h in headers])
    out=BytesIO(); wb.save(out); wb.close(); out.seek(0)
    return out


class WorkbookSecurityTests(unittest.TestCase):
    def test_formula_and_partial_compliance_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'file.xlsx'
            for rows, headers, expected in [([{'brand':'TRC','code':'=1+1'}],('brand','code'),'công thức'),
                ([{'brand':'TRC'}],('brand','Compliance'),'Compliance_Note')]:
                path.write_bytes(xlsx(rows,headers).getvalue())
                with self.assertRaisesRegex(ImportProblem,expected):
                    list(workbook_rows(path))

    def test_renamed_metadata_part_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'renamed.xlsx'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('[Content_Types].xml','<Types><Override PartName="/xl/strings.dat" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
                z.writestr('xl/workbook.xml','<workbook/>')
                z.writestr('xl/strings.dat','<!DOCTYPE x>')
            with self.assertRaises(ImportProblem):inspect_workbook(path)

    def test_styles_cannot_claim_worksheet_content_type(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'mislabeled.xlsx'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('[Content_Types].xml','<Types><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
                z.writestr('xl/workbook.xml','<workbook/>')
                z.writestr('xl/styles.xml','<styles>'+(' '*2000)+'</styles>')
            with mock.patch.dict(os.environ,{'IMPORT_METADATA_BYTES':'1000'}),self.assertRaises(ImportProblem):inspect_workbook(path)

    def test_workbook_metadata_cannot_masquerade_as_worksheet(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'renamed.xlsx'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('[Content_Types].xml','<Types><Override PartName="/xl/worksheets/sheetMetadata.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>')
                z.writestr('xl/workbook.xml','<workbook/>')
                z.writestr('xl/worksheets/sheetMetadata.xml','<workbook/>')
            with self.assertRaises(ImportProblem):inspect_workbook(path)

    def test_zip_traversal_entity_and_expansion_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'file.xlsx'
            for name, content in [('../x','x'),('xl/sharedStrings.xml','<!DOCTYPE x>'),('xl/vbaProject.bin','x')]:
                with zipfile.ZipFile(path,'w') as z:
                    z.writestr('[Content_Types].xml','x');z.writestr('xl/workbook.xml','x');z.writestr(name,content)
                with self.assertRaises(ImportProblem):inspect_workbook(path)
            path.write_bytes(xlsx([{'brand':'TRC'}]).getvalue())
            with mock.patch.dict(os.environ,{'IMPORT_ZIP_BYTES':'100'}),self.assertRaises(ImportProblem):inspect_workbook(path)


@unittest.skipUnless(probe_postgres_reachable(),'local Postgres required')
class ImportCenterPgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name,cls.dsn=create_full_schema_temp_db()
        try:
            cls.conn=psycopg2.connect(cls.dsn);cls.conn.autocommit=True
            with cls.conn.cursor() as cur:
                apply_brand_master_and_currency_migrations(cur);apply_dynamic_brand_currency_migration(cur)
                cur.execute((ROOT/'sql/migration_004_import_jobs.sql').read_text())
                for _ in range(2):cur.execute((ROOT/'sql/migration_024_admin_import_center.sql').read_text())
                cur.execute("INSERT INTO app_users(username,password_hash,is_admin,account_status) VALUES ('import-admin','x',true,'ACTIVE') RETURNING id")
                cls.uid=cur.fetchone()[0]
        except Exception:
            drop_temp_db(cls.name);raise

    @classmethod
    def tearDownClass(cls):
        cls.conn.close();drop_temp_db(cls.name)

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        patch=mock.patch.dict(os.environ,{'DATABASE_URL':self.dsn,'IMPORT_UPLOAD_DIR':self.directory.name,'DISABLE_IP_ALLOWLIST':'1','IMPORT_CHUNK_ROWS':'2'})
        patch.start();self.addCleanup(patch.stop);self.addCleanup(self.directory.cleanup)
        with self.conn.cursor() as cur:
            cur.execute('DELETE FROM product_import_jobs');cur.execute('DELETE FROM products')
            cur.execute('UPDATE app_users SET is_admin=true,auth_version=1 WHERE id=%s',(self.uid,))
        self.client=search.app.test_client()
        with self.client.session_transaction() as s:
            s.update(authenticated=True,is_admin=True,user_id=self.uid,auth_version=1,csrf_token='qa-csrf')

    def submit(self,rows,mode='upsert',key=None):
        return jobs.submit(FileStorage(xlsx(rows),filename='catalog.xlsx',content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),mode,'user:'+str(self.uid),self.uid,1,key or str(uuid.uuid4()))

    def state(self,jid):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:return jobs.fetch_job(cur,jid)

    def preview(self,rows,mode='upsert'):
        jid=self.submit(rows,mode);self.assertTrue(jobs.run_once(jid));return jid

    def apply(self,jid):
        p=self.state(jid)['preview']
        jobs.control(jid,'apply','qa',p['fingerprint'],str(p['deleted']))
        jobs.run_once(jid)
        return self.state(jid)

    def seed(self,brand='TRC',source='TRC',code='A',name='old',size='1g'):
        with self.conn.cursor() as cur:
            cur.execute('INSERT INTO products(brand,source_brand,code,name,size) VALUES (%s,%s,%s,%s,%s)',(brand,source,code,name,size))

    def test_durable_preview_readonly_new_brand_currency_null_and_apply_once(self):
        rows=[{'brand':'New Discovery 6c2','code':'X'},{'brand':'new discovery 6c2','code':'Y'}]
        jid=self.preview(rows)
        self.assertEqual(self.state(jid)['status'],'completed',self.state(jid)['errors'])
        with self.conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM products');self.assertEqual(cur.fetchone()[0],0)
        done=self.apply(jid);self.assertEqual(done['status'],'completed',done['errors']);self.assertEqual(done['inserted_count'],2)
        with self.conn.cursor() as cur:
            cur.execute("SELECT currency_code FROM brand_master WHERE normalized_name='NEW DISCOVERY 6C2'");self.assertIsNone(cur.fetchone()[0])
            cur.execute("SELECT count(*) FROM team_brands WHERE brand='New Discovery 6c2'");self.assertEqual(cur.fetchone()[0],0)
        with self.assertRaises(ImportProblem):self.apply(jid)
        self.assertFalse(jobs.run_once(jid))

    def test_upload_dedup_path_and_readonly_get(self):
        key=str(uuid.uuid4());rows=[{'brand':'TRC','code':'X'}]
        jid=self.submit(rows,key=key);self.assertEqual(self.submit(rows,key=key),jid)
        self.assertEqual(len(list(Path(self.directory.name).iterdir())),1)
        r=self.client.get('/admin/imports/jobs/'+jid);self.assertEqual(r.status_code,200)
        self.assertEqual(self.state(jid)['status'],'queued')
        with self.assertRaises(ValueError):jobs.upload_path('../../bad')

    def test_admin_csrf_recheck_and_staff_boundaries(self):
        jid=self.submit([{'brand':'TRC'}])
        self.assertEqual(self.client.post('/admin/imports/jobs/'+jid+'/cancel').status_code,400)
        with self.conn.cursor() as cur:cur.execute('UPDATE app_users SET is_admin=false WHERE id=%s',(self.uid,))
        self.assertEqual(self.client.get('/admin/imports').status_code,403)
        self.assertEqual(self.client.get('/admin/imports/jobs/'+jid+'/status').status_code,403)
        self.assertEqual(self.client.post('/admin/imports/upload',data={'csrf_token':'qa-csrf'}).status_code,403)

    def test_separate_delete_confirmation_and_same_count_stale_preview(self):
        self.seed()
        jid=self.preview([{'brand':'TRC','code':'A','name':'new','size':'1g'}],'replace_by_brand')
        p=self.state(jid)['preview'];self.assertEqual(p['deleted'],1)
        with self.assertRaises(ImportProblem):jobs.control(jid,'apply','qa',p['fingerprint'],'')
        with self.conn.cursor() as cur:cur.execute("UPDATE products SET name='changed outside preview'")
        failed=self.apply(jid);self.assertEqual(failed['status'],'failed');self.assertIn('thay đổi',failed['errors'][0])
        with self.conn.cursor() as cur:cur.execute('SELECT name FROM products');self.assertEqual(cur.fetchone()[0],'changed outside preview')

    def test_replace_alias_scope_and_optional_fields_preserved(self):
        self.seed('LGC','LGC (Mikromol)');self.seed('LGC','LGC (XRF)',code='B')
        with self.conn.cursor() as cur:cur.execute("UPDATE products SET manual_compliance='Được bán',manual_compliance_note='reviewed',preparation_type='NEAT' WHERE code='A'")
        jid=self.preview([{'brand':'LGC (Mikromol)','code':'A','name':'new','size':'1g'}],'replace_by_brand')
        self.assertEqual(self.state(jid)['preview']['deleted'],1)
        self.assertEqual(self.apply(jid)['deleted_count'],1)
        with self.conn.cursor() as cur:
            cur.execute("SELECT name,manual_compliance,preparation_type FROM products WHERE code='A'");self.assertEqual(cur.fetchone(),('new','Được bán','NEAT'))
            cur.execute("SELECT count(*) FROM products WHERE source_brand='LGC (XRF)'");self.assertEqual(cur.fetchone()[0],1)
        bad=self.preview([{'brand':'LGC','code':'X'}],'replace_by_brand');self.assertEqual(self.state(bad)['status'],'failed')

    def test_upsert_counts_and_ambiguous_or_duplicate_rejected_atomically(self):
        self.seed()
        jid=self.preview([{'brand':'TRC','code':'A','name':'new','size':'1g'},{'brand':'TRC','code':'B','name':'b'}])
        done=self.apply(jid);self.assertEqual((done['inserted_count'],done['updated_count']),(1,1),done['errors'])
        jid=self.preview([{'brand':'TRC','code':'X'},{'brand':'TRC','code':'X'}]);self.assertEqual(self.state(jid)['status'],'failed')
        self.seed(code='A',size='2g')
        jid=self.preview([{'brand':'TRC','code':'A'}]);self.assertEqual(self.state(jid)['status'],'failed')

    def test_cancel_queued_and_running_atomic_rollback(self):
        jid=self.submit([{'brand':'TRC'}]);jobs.control(jid,'cancel','qa');self.assertFalse(jobs.run_once(jid));self.assertEqual(self.state(jid)['status'],'cancelled')
        jid=self.preview([{'brand':'TRC','code':'X'}]);p=self.state(jid)['preview'];jobs.control(jid,'apply','qa',p['fingerprint'])
        real=jobs.apply_plan
        def cancel(cur,plan,expected,progress):
            jobs.control(jid,'cancel','qa')
            return real(cur,plan,expected,progress)
        with mock.patch.object(jobs,'apply_plan',side_effect=cancel):jobs.run_once(jid)
        self.assertEqual(self.state(jid)['status'],'cancelled')
        with self.conn.cursor() as cur:cur.execute('SELECT count(*) FROM products');self.assertEqual(cur.fetchone()[0],0)

    def test_worker_claim_fencing_and_crash_recovery_require_new_preview(self):
        jid=self.submit([{'brand':'TRC'}])
        worker=psycopg2.connect(self.dsn)
        try:
            self.assertEqual(jobs.claim(worker,jid),jid)
            with jobs.connection() as other:self.assertIsNone(jobs.claim(other,jid))
        finally:worker.close()
        jobs.run_once(jid);self.assertEqual(self.state(jid)['status'],'failed')
        jobs.control(jid,'retry','qa');jobs.run_once(jid);self.assertTrue(self.state(jid)['preview_ready'])

    def test_two_workers_same_job_do_not_duplicate_and_products_lock_blocks(self):
        jid=self.preview([{'brand':'TRC','code':'X'}]);p=self.state(jid)['preview'];jobs.control(jid,'apply','qa',p['fingerprint'])
        lock=psycopg2.connect(self.dsn)
        with lock.cursor() as cur:acquire_products_import_lock(cur)
        thread=threading.Thread(target=lambda:jobs.run_once(jid));thread.start()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if self.state(jid)['status']=='running':break
            time.sleep(.02)
        self.assertEqual(self.state(jid)['status'],'running');self.assertFalse(jobs.run_once(jid))
        thread.join(.1);self.assertTrue(thread.is_alive())
        lock.rollback();lock.close();thread.join(10);self.assertFalse(thread.is_alive())
        self.assertEqual(self.state(jid)['status'],'completed')
        with self.conn.cursor() as cur:cur.execute('SELECT count(*) FROM products');self.assertEqual(cur.fetchone()[0],1)

    def test_failure_rolls_back_new_brand_and_products(self):
        jid=self.preview([{'brand':'Rollback New 6c2','code':'X'}]);p=self.state(jid)['preview'];jobs.control(jid,'apply','qa',p['fingerprint'])
        def fail(*args):raise RuntimeError('secret-dsn-marker')
        with mock.patch.object(jobs,'apply_plan',side_effect=fail):jobs.run_once(jid)
        state=self.state(jid);self.assertEqual(state['status'],'failed');self.assertNotIn('secret',str(state['errors']))
        with self.conn.cursor() as cur:cur.execute("SELECT count(*) FROM brand_master WHERE name='Rollback New 6c2'");self.assertEqual(cur.fetchone()[0],0)

    def test_same_count_competing_previews_only_first_apply_succeeds(self):
        self.seed()
        a=self.preview([{'brand':'TRC','code':'A','name':'first','size':'1g'}],'replace_by_brand')
        b=self.preview([{'brand':'TRC','code':'A','name':'second','size':'1g'}],'replace_by_brand')
        self.assertEqual(self.apply(a)['status'],'completed')
        self.assertEqual(self.apply(b)['status'],'failed')
        with self.conn.cursor() as cur:cur.execute('SELECT name FROM products');self.assertEqual(cur.fetchone()[0],'first')

    def test_quick_delete_requires_fresh_exact_count_confirmation(self):
        self.seed()
        data={'csrf_token':'qa-csrf','brand':'TRC','code':'A'}
        response=self.client.post('/admin/imports/quick-product/delete',data=data)
        self.assertEqual(response.status_code,400)
        response=self.client.post('/admin/imports/quick-product/delete-preview',data=data)
        self.assertEqual(response.status_code,200);p=response.get_json();self.assertEqual(p['count'],1)
        with self.conn.cursor() as cur:cur.execute("UPDATE products SET name='edited'")
        data['confirmation']=p['confirmation'];self.assertEqual(self.client.post('/admin/imports/quick-product/delete',data=data).status_code,400)
        data['confirmation']=self.client.post('/admin/imports/quick-product/delete-preview',data=data).get_json()['confirmation']
        self.assertEqual(self.client.post('/admin/imports/quick-product/delete',data=data).status_code,200)
        with self.conn.cursor() as cur:cur.execute('SELECT count(*) FROM products');self.assertEqual(cur.fetchone()[0],0)

    def test_rules_preview_remains_durable_and_csrf_protected(self):
        headers=('rule_type','rule_label','match_field','match_value','priority','is_active','note')
        response=self.client.post('/admin/imports/preview',data={'csrf_token':'qa-csrf','dataset':'regulatory_rules','mode':'upsert',
            'file':(xlsx([dict(zip(headers,('TON_KHO','Stock','code','X',100,True,'')))],headers),'rules.xlsx')})
        self.assertEqual(response.status_code,302)
        self.assertIn('preview=',response.location)
        page=self.client.get(response.location);self.assertEqual(page.status_code,200)
        self.assertIn('Stock',page.get_data(as_text=True))
        from urllib.parse import parse_qs,urlparse
        token=parse_qs(urlparse(response.location).query)['preview'][0]
        applied=self.client.post('/admin/imports/apply',data={'csrf_token':'qa-csrf','preview_token':token})
        self.assertEqual(applied.status_code,302);self.assertIn('msg=',applied.location)
        with self.conn.cursor() as cur:
            cur.execute("SELECT priority,is_active FROM regulatory_rules WHERE match_value='X'")
            self.assertEqual(cur.fetchone(),(100,True))

    def test_expiry_cleanup_payload_and_upload_removed_audit_retained(self):
        jid=self.preview([{'brand':'TRC'}]);path=jobs.upload_path(jid)
        with self.conn.cursor() as cur:cur.execute("UPDATE product_import_jobs SET expires_at=now()-interval '1 second' WHERE id=%s",(jid,))
        jobs.cleanup();self.assertFalse(path.exists());self.assertIsNotNone(self.state(jid)['purged_at']);self.assertIsNone(self.state(jid)['preview'])
        with self.conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM product_import_rows WHERE job_id=%s',(jid,));self.assertEqual(cur.fetchone()[0],0)
            cur.execute('SELECT count(*) FROM product_import_events WHERE job_id=%s',(jid,));self.assertGreater(cur.fetchone()[0],0)
        with self.assertRaises(ImportProblem):jobs.control(jid,'retry','qa')
