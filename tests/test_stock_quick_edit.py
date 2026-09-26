"""Phase 6D6: all DB writes target a disposable pgtest database."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest import mock
import uuid

import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.datastructures import FileStorage

import admin_permissions
from brand_gateway import PRODUCTS_IMPORT_LOCK_KEY
from import_engine import ImportProblem
import pg_temp_db
import search
from stock import active_snapshot, STOCK_LOCK_KEY
import stock_import_jobs as jobs
import stock_manual as manual
from tests.test_phase6d2_stock import workbook_bytes, HEADERS


ROOT = Path(__file__).resolve().parents[1]
BASE_ROW = dict(name="Hóa chất mẫu", code="C1", cas="50-00-0", brand="Brand A", size="1g",
                stock_price_vnd="0", quantity="7", expiry_date="", stock_note="Kho A")


@unittest.skipUnless(pg_temp_db.probe_postgres_reachable(), "isolated PostgreSQL required")
class StockQuickEditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = pg_temp_db.create_full_schema_temp_db()
        cls.conn = psycopg2.connect(cls.dsn)
        cls.conn.autocommit = True
        with cls.conn.cursor() as cur:
            pg_temp_db.apply_brand_master_and_currency_migrations(cur)
            pg_temp_db.apply_dynamic_brand_currency_migration(cur)
            cur.execute("INSERT INTO app_users(username,password_hash,is_admin) VALUES ('manual_admin','x',true) RETURNING id")
            cls.actor_id = cur.fetchone()[0]
        cls.upload_dir = tempfile.TemporaryDirectory(prefix="stock-quick-edit-upload-")
        cls.env = mock.patch.dict(os.environ, {"DATABASE_URL": cls.dsn, "DISABLE_IP_ALLOWLIST": "1",
                                             "IMPORT_UPLOAD_DIR": cls.upload_dir.name})
        cls.env.start()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.env.stop()
        pg_temp_db.drop_temp_db(cls.db_name)
        cls.upload_dir.cleanup()

    def setUp(self):
        search.app.testing = True
        with self.conn.cursor() as cur:
            cur.execute("TRUNCATE stock_manual_requests,stock_snapshot_events,stock_import_events,stock_import_jobs,stock_items,stock_state,stock_snapshots CASCADE")
            cur.execute("INSERT INTO stock_state(singleton) VALUES (true)")
            cur.execute("DELETE FROM products; DELETE FROM team_brands; DELETE FROM brand_aliases; DELETE FROM brand_master")
            cur.execute("UPDATE app_users SET is_admin=true,account_status='ACTIVE',auth_version=1 WHERE id=%s", (self.actor_id,))
            cur.execute("INSERT INTO admin_menu_grants(user_id,permission_key) VALUES (%s,'stock') ON CONFLICT DO NOTHING", (self.actor_id,))
            cur.execute("UPDATE app_users SET auth_version=1 WHERE id=%s", (self.actor_id,))

    def state(self):
        with self.conn.cursor() as cur:
            return manual.state_wire(active_snapshot(cur))

    def preview(self, values=None, item_id=None, **kw):
        return manual.review(values or BASE_ROW, item_id, kw.get("expected", self.state()),
                             kw.get("request_id", str(uuid.uuid4())), self.actor_id, kw.get("version", 1))

    def save(self, preview):
        return manual.save(preview, "manual_admin", self.actor_id, 1)

    def add(self, **values):
        return self.save(self.preview(dict(BASE_ROW, **values)))

    def client(self, *, admin=True):
        client = search.app.test_client()
        with client.session_transaction() as sess:
            sess.update(authenticated=True,user_id=self.actor_id,auth_version=1,is_admin=admin,
                        username="manual_admin",csrf_token="manual-csrf",auth_provider="LOCAL")
        return client

    def rows(self, snapshot_id=None):
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM stock_items WHERE snapshot_id=%s ORDER BY id", (snapshot_id or self.state()["snapshot_id"],))
            return [dict(r) for r in cur.fetchall()]

    def test_add_empty_and_existing_clones_unchanged_rows_and_real_digest(self):
        first = self.add()
        old_rows = self.rows()
        second = self.add(code="C2", stock_note="Kho B")
        self.assertEqual(self.rows(first["snapshot_id"]), old_rows)
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(second["revision"], 2)
        with self.conn.cursor() as cur:
            cur.execute("SELECT source_kind,row_count,content_sha256 FROM stock_snapshots WHERE id=%s", (second["snapshot_id"],))
            self.assertEqual(cur.fetchone(), ("MANUAL", 2, jobs.content_digest(self.rows())))
            cur.execute("SELECT detail FROM stock_snapshot_events WHERE snapshot_id=%s", (first["snapshot_id"],))
            event = cur.fetchone()[0]
            self.assertIsNone(event["before"])
            self.assertEqual(set(event["after"]), set(manual.KEYS))
            self.assertEqual(event["actor_user_id"], self.actor_id)
            self.assertEqual(event["target_item_id"], first["item_id"])

    def test_edit_all_nine_fields_before_after_and_restore(self):
        first = self.add()
        old = self.rows()
        values = dict(name="Tên mới", code="C2", cas="64-17-5", brand="Brand B", size="2g",
                      stock_price_vnd="123.45", quantity="0", expiry_date="31/12/2027", stock_note="Kho B\nHàng mẫu")
        reviewed = self.preview(values, first["item_id"])
        self.assertEqual(reviewed["new_brands"], ["Brand B"])
        saved = self.save(reviewed)
        self.assertEqual(self.rows(first["snapshot_id"]), old)
        self.assertEqual(manual.wire(self.rows()[0]), reviewed["after"])
        with self.conn.cursor() as cur:
            cur.execute("SELECT event,detail FROM stock_snapshot_events WHERE snapshot_id=%s", (saved["snapshot_id"],))
            event, detail = cur.fetchone()
            self.assertEqual(event, "manual_edited")
            self.assertEqual(detail["before"], reviewed["before"])
            self.assertEqual(detail["after"], reviewed["after"])
            self.assertEqual(detail["source_item_id"], first["item_id"])
        state = self.state()
        restored = jobs.restore_snapshot(first["snapshot_id"], "manual_admin", self.actor_id, 1, state["revision"], state["fingerprint"])
        self.assertEqual(manual.wire(self.rows(restored)[0]), manual.wire(old[0]))

    def test_noop_and_idempotency_receipt_no_extra_snapshot_brand_or_audit(self):
        first = self.add()
        preview = self.preview(dict(BASE_ROW, stock_price_vnd="0.00"), first["item_id"])
        self.assertFalse(preview["changed"])
        saved = self.save(preview)
        replay = self.save(preview)
        self.assertFalse(saved["changed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(self.state()["revision"], 1)
        with self.conn.cursor() as cur:
            for table in ("stock_snapshots", "stock_snapshot_events", "brand_master"):
                cur.execute("SELECT count(*) FROM " + table)
                self.assertEqual(cur.fetchone()[0], 1)

    def test_double_submit_concurrent_and_reused_key_other_payload(self):
        preview = self.preview()
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.save(preview), range(2)))
        self.assertEqual({r["snapshot_id"] for r in results}, {results[0]["snapshot_id"]})
        self.assertEqual(sum(r["replayed"] for r in results), 1)
        modified = dict(preview, after=dict(preview["after"], quantity="8"))
        with self.assertRaises(manual.StockConflict):
            self.save(modified)
        self.assertEqual(self.state()["revision"], 1)

    def test_duplicate_identities_null_and_dated_and_edit_collision(self):
        first = self.add()
        with self.assertRaises(ImportProblem):
            self.preview(dict(BASE_ROW, brand=" brand a ", code="c1", size="1G"))
        second = self.add(expiry_date="2027-12-31")
        with self.assertRaises(ImportProblem):
            self.preview(dict(BASE_ROW, expiry_date="31/12/2027"))
        with self.assertRaises(ImportProblem):
            self.preview(dict(BASE_ROW), second["item_id"])
        with self.assertRaises(manual.StockConflict):
            self.preview(dict(BASE_ROW), first["item_id"])

    def test_invalid_fields_and_numeric_db_bounds(self):
        cases = {"name": ["", "a"*501, "a\x00b"], "code": [""], "brand": [""], "size": [""],
                 "quantity": ["", "-1", "1.1", "2147483648", "1e999999", "NaN"],
                 "stock_price_vnd": ["-1", "1.001", "10000000000000000", "Infinity"],
                 "cas": ["50-00-1"], "expiry_date": ["2027-02-30"], "stock_note": ["x"*2001]}
        for field, values in cases.items():
            for value in values:
                with self.subTest(field=field, value=value[:15]), self.assertRaises(ImportProblem):
                    self.preview(dict(BASE_ROW, **{field: value}))
        maximum = self.add(quantity="2147483647", stock_price_vnd="9999999999999999.99", stock_note="đ"*2000)
        self.assertTrue(maximum["changed"])

    def test_brand_preview_read_only_alias_and_no_catalog_currency_team_writes(self):
        p = self.preview()
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM brand_master")
            self.assertEqual(cur.fetchone()[0], 0)
        self.save(p)
        with self.conn.cursor() as cur:
            cur.execute("SELECT id,currency_code FROM brand_master")
            brand_id, currency = cur.fetchone()
            self.assertIsNone(currency)
            cur.execute("INSERT INTO brand_aliases(brand_id,alias,normalized_alias) VALUES (%s,'Alias A','ALIAS A')", (brand_id,))
            for table in ("products", "team_brands"):
                cur.execute("SELECT count(*) FROM " + table)
                self.assertEqual(cur.fetchone()[0], 0)
        p = self.preview(dict(BASE_ROW, brand="Alias A", code="C2"))
        self.assertEqual(p["after"]["brand"], "Brand A")
        self.assertEqual(p["new_brands"], [])
        self.save(p)

    def test_rollback_failure_after_clone_leaves_no_brand_snapshot_receipt(self):
        self.add()
        before = self.state()
        preview = self.preview(dict(BASE_ROW, brand="New Brand", code="C2"))
        original = manual._clone
        def fail(*args):
            original(*args)
            raise RuntimeError("injected after clone")
        with mock.patch.object(manual, "_clone", side_effect=fail), self.assertRaises(RuntimeError):
            self.save(preview)
        self.assertEqual(self.state(), before)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM brand_master WHERE name='New Brand'")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT count(*) FROM stock_snapshots")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("SELECT count(*) FROM stock_manual_requests")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_stale_edit_edit_and_restore_fences(self):
        first = self.add()
        edit = self.preview(dict(BASE_ROW, quantity="8"), first["item_id"])
        other = self.preview(dict(BASE_ROW, quantity="9"), first["item_id"])
        self.save(edit)
        with self.assertRaises(manual.StockConflict):
            self.save(other)
        with self.assertRaises(ImportProblem):
            jobs.restore_snapshot(first["snapshot_id"], "manual_admin", self.actor_id, 1, edit["revision"], edit["fingerprint"])
        current = self.rows()[0]
        stale = self.preview(dict(BASE_ROW, quantity="10"), current["id"])
        state = self.state()
        jobs.restore_snapshot(first["snapshot_id"], "manual_admin", self.actor_id, 1, state["revision"], state["fingerprint"])
        with self.assertRaises(manual.StockConflict):
            self.save(stale)

    def import_preview(self):
        data = workbook_bytes([[BASE_ROW[k] for k in manual.KEYS]], headers=HEADERS+["Ghi chú"])
        job_id = jobs.submit(FileStorage(stream=data, filename="stock.xlsx"), "manual_admin", self.actor_id, 1, str(uuid.uuid4()))
        jobs.run_once(job_id)
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            return job_id, jobs.fetch_job(cur, job_id)

    def apply_import(self, job_id, job):
        jobs.control(job_id, "apply", "manual_admin", job["preview"]["fingerprint"], str(job["preview"]["current_rows"]))
        jobs.run_once(job_id)
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            return jobs.fetch_job(cur, job_id)

    def test_import_manual_stale_both_directions(self):
        job_id, job = self.import_preview()
        self.add(code="MANUAL")
        self.assertEqual(self.apply_import(job_id, job)["status"], "failed")
        stale = self.preview(dict(BASE_ROW, code="STALE"))
        job_id, job = self.import_preview()
        self.assertEqual(self.apply_import(job_id, job)["status"], "completed")
        with self.assertRaises(manual.StockConflict):
            self.save(stale)

    def wait_advisory(self):
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            with self.conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid WHERE a.datname=current_database() AND l.locktype='advisory' AND NOT l.granted")
                if cur.fetchone():
                    return
            time.sleep(.01)
        self.fail("writer never reached blocked advisory lock")

    def test_domain_lock_timeout_rolls_back_without_any_write(self):
        preview = self.preview(dict(BASE_ROW, brand="Busy New Brand"))
        for key in (PRODUCTS_IMPORT_LOCK_KEY, STOCK_LOCK_KEY):
            blocker = psycopg2.connect(self.dsn)
            try:
                with blocker.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
                started = time.monotonic()
                with mock.patch.object(manual, "LOCK_TIMEOUT_MS", 100):
                    with self.assertRaises(psycopg2.errors.LockNotAvailable):
                        self.save(preview)
                self.assertLess(time.monotonic()-started, 2)
                self.assertEqual(self.state()["revision"], 0)
                with self.conn.cursor() as cur:
                    for table in ("stock_snapshots", "stock_items", "stock_manual_requests", "brand_master", "stock_snapshot_events"):
                        cur.execute("SELECT count(*) FROM " + table)
                        self.assertEqual(cur.fetchone()[0], 0, table)
            finally:
                blocker.close()

    def test_review_actor_lock_timeout_and_http_busy_feedback(self):
        client = self.client()
        _, token = self.post_review(client)
        blocker = psycopg2.connect(self.dsn)
        try:
            with blocker.cursor() as cur:
                cur.execute("UPDATE app_users SET username=username WHERE id=%s", (self.actor_id,))
            with mock.patch.object(manual, "LOCK_TIMEOUT_MS", 100):
                started = time.monotonic()
                response, _ = self.post_review(client)
                self.assertEqual(response.status_code, 503)
                self.assertIn("Chưa lưu dữ liệu", response.get_data(as_text=True))
                self.assertLess(time.monotonic()-started, 2)
        finally:
            blocker.close()
        with mock.patch.object(manual, "save", side_effect=psycopg2.errors.LockNotAvailable()):
            response = client.post("/admin/stock/items/save", data={"csrf_token":"manual-csrf","review_token":token})
            self.assertEqual(response.status_code, 503)
            self.assertIn("rollback", response.get_data(as_text=True))
        self.assertEqual(self.state()["revision"], 0)

    def test_grant_revoked_while_waiting_domain_lock_and_replay_revalidated(self):
        p = self.preview()
        blocker = psycopg2.connect(self.dsn)
        with ThreadPoolExecutor(1) as pool:
            try:
                with blocker.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock(%s)", (PRODUCTS_IMPORT_LOCK_KEY,))
                future = pool.submit(self.save, p)
                self.wait_advisory()
                with self.conn.cursor() as cur:
                    cur.execute("DELETE FROM admin_menu_grants WHERE user_id=%s", (self.actor_id,))
                blocker.rollback()
                with self.assertRaises(admin_permissions.PermissionDenied):
                    future.result(timeout=5)
            finally:
                blocker.close()
        self.assertEqual(self.state()["revision"], 0)

    def test_replay_rechecks_active_actor_auth_version_and_grant(self):
        p = self.preview()
        self.save(p)
        with self.conn.cursor() as cur:
            cur.execute("UPDATE app_users SET account_status='SUSPENDED',auth_version=auth_version+1 WHERE id=%s", (self.actor_id,))
        with self.assertRaises(admin_permissions.PermissionDenied):
            self.save(p)

    def test_replay_rechecks_missing_grant_even_same_auth_version(self):
        p = self.preview()
        self.save(p)
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM admin_menu_grants WHERE user_id=%s", (self.actor_id,))
            cur.execute("UPDATE app_users SET auth_version=1 WHERE id=%s", (self.actor_id,))
        with self.assertRaises(admin_permissions.PermissionDenied):
            self.save(p)

    def test_actor_suspended_while_save_waits_on_actor_lock(self):
        p = self.preview()
        blocker = psycopg2.connect(self.dsn)
        with ThreadPoolExecutor(1) as pool:
            try:
                with blocker.cursor() as cur:
                    cur.execute("UPDATE app_users SET account_status='SUSPENDED' WHERE id=%s", (self.actor_id,))
                future = pool.submit(self.save, p)
                deadline = time.monotonic()+5
                waiting = False
                while time.monotonic() < deadline:
                    with self.conn.cursor() as cur:
                        cur.execute("SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE 'SELECT id, account_status%'")
                        waiting = cur.fetchone() is not None
                    if waiting:
                        break
                    time.sleep(.01)
                self.assertTrue(waiting, "save must wait for actor row, not use an earlier auth read")
                blocker.commit()
                with self.assertRaises(admin_permissions.PermissionDenied):
                    future.result(timeout=5)
            finally:
                blocker.rollback(); blocker.close()
        self.assertEqual(self.state()["revision"], 0)

    def test_concurrent_edit_import_only_one_activation(self):
        first = self.add()
        edit = self.preview(dict(BASE_ROW, quantity="8"), first["item_id"])
        job_id, job = self.import_preview()
        jobs.control(job_id, "apply", "manual_admin", job["preview"]["fingerprint"], str(job["preview"]["current_rows"]))
        def edit_attempt():
            try:
                self.save(edit)
                return True
            except manual.StockConflict:
                return False
        with ThreadPoolExecutor(2) as pool:
            editing = pool.submit(edit_attempt)
            importing = pool.submit(jobs.run_once, job_id)
            edited = editing.result(timeout=10)
            importing.result(timeout=10)
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            imported = jobs.fetch_job(cur, job_id)["status"] == "completed"
        self.assertNotEqual(edited, imported)
        self.assertEqual(self.state()["revision"], 2)

    def test_concurrent_edit_restore_only_one_activation(self):
        first = self.add()
        second = self.add(code="C2")
        state = self.state()
        edit = self.preview(dict(BASE_ROW, code="C2", quantity="8"), second["item_id"])
        def edit_attempt():
            try:
                self.save(edit)
                return True
            except manual.StockConflict:
                return False
        def restore_attempt():
            try:
                jobs.restore_snapshot(first["snapshot_id"], "manual_admin", self.actor_id, 1, state["revision"], state["fingerprint"])
                return True
            except ImportProblem:
                return False
        with ThreadPoolExecutor(2) as pool:
            editing = pool.submit(edit_attempt)
            restoring = pool.submit(restore_attempt)
            results = [editing.result(timeout=10), restoring.result(timeout=10)]
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.state()["revision"], 3)

    def test_concurrent_edit_edit_only_one_activation(self):
        first = self.add()
        previews = [self.preview(dict(BASE_ROW, quantity=str(n)), first["item_id"]) for n in (8, 9)]
        def attempt(p):
            try:
                return self.save(p)
            except manual.StockConflict:
                return None
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(attempt, previews))
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertEqual(self.state()["revision"], 2)

    def test_search_contains_unicode_wildcards_brand_pagination_and_zero_price(self):
        self.add(name="Hóa chất %_! mẫu", code="AC_10", stock_price_vnd="0")
        self.add(code="AC210", brand="Brand B", stock_price_vnd="")
        for query, count in (("hóa", 2), ("%_!", 1), ("ac_", 1), ("50-00", 2)):
            result = manual.browse(manual.filters({"q": query}))
            self.assertEqual(result["total"], count)
        result = manual.browse(manual.filters({"q": "Hóa", "brand": "Brand B", "page_size": "1"}))
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["price_label"], "")
        result = manual.browse(manual.filters({"q": "%"}))
        self.assertEqual(result["rows"][0]["price_label"], "0 ₫")
        pages = [manual.browse(manual.filters({"page_size": "1", "page": str(n)})) for n in (1, 2)]
        self.assertNotEqual(pages[0]["rows"][0]["id"], pages[1]["rows"][0]["id"])
        self.assertEqual(manual.filters({"page_size": "99999"})["page_size"], 100)

    def post_review(self, client, values=None, item_id=None, selected=None):
        form = manual.form_context(item_id)
        data = dict(values or BASE_ROW, csrf_token="manual-csrf", item_id=item_id or "",
                    request_id=form["request_id"], snapshot_id=form["snapshot_id"] or "",
                    revision=form["revision"], fingerprint=form["fingerprint"])
        data.update({"filter_"+key:value for key,value in (selected or {}).items()})
        response = client.post("/admin/stock/items/review", data=data)
        match = re.search(r'name="review_token" value="([^"]+)"', response.get_data(as_text=True))
        return response, match.group(1) if match else None

    def test_http_signed_review_ignores_forged_hidden_fields_filter_feedback_xss(self):
        client = self.client()
        response, token = self.post_review(client, dict(BASE_ROW, stock_note='<script>alert(1)</script>'), selected={"q":"not-found"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", response.get_data(as_text=True))
        response = client.post("/admin/stock/items/save", data={"csrf_token":"manual-csrf", "review_token":token,
                               "quantity":"999", "actor_id":"999", "request_id":str(uuid.uuid4()), "revision":"999"})
        self.assertEqual(response.status_code, 303)
        html = client.get(response.location).get_data(as_text=True)
        self.assertIn("không còn khớp bộ lọc", html)
        self.assertEqual(self.rows()[0]["quantity"], 7)
        html = client.get("/admin/stock").get_data(as_text=True)
        self.assertIn("Thủ công", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertEqual(client.post("/admin/stock/items/save", data={"csrf_token":"manual-csrf","review_token":token+"x"}).status_code, 400)

    def test_http_csrf_menu_grant_and_stale_token(self):
        client = self.client()
        _, token = self.post_review(client)
        self.assertEqual(client.post("/admin/stock/items/save", data={"review_token":token}).status_code, 400)
        with mock.patch("itsdangerous.timed.TimestampSigner.get_timestamp", return_value=int(time.time())+1000):
            self.assertEqual(client.post("/admin/stock/items/save", data={"csrf_token":"manual-csrf","review_token":token}).status_code, 400)
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM admin_menu_grants WHERE user_id=%s", (self.actor_id,))
        for path in ("/admin/stock", "/admin/stock/items/new", "/admin/stock/items/1/edit"):
            self.assertNotEqual(client.get(path).status_code, 200)
        self.assertNotEqual(client.post("/admin/stock/items/save", data={"csrf_token":"manual-csrf","review_token":token}).status_code, 303)

    def test_http_invalid_filters_are_controlled_bad_requests(self):
        client = self.client()
        for query in ({"page":"bad"}, {"page":"0"}, {"page_size":"-1"}, {"q":"x"*501}):
            self.assertEqual(client.get("/admin/stock", query_string=query).status_code, 400)

    def test_http_cross_actor_token_and_staff_are_denied(self):
        client = self.client()
        _, token = self.post_review(client)
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO app_users(username,password_hash,is_admin) VALUES (%s,'x',true) RETURNING id", ("other_"+uuid.uuid4().hex,))
            other = cur.fetchone()[0]
            cur.execute("INSERT INTO admin_menu_grants(user_id,permission_key) VALUES (%s,'stock')", (other,))
            cur.execute("UPDATE app_users SET auth_version=1 WHERE id=%s", (other,))
        with client.session_transaction() as sess:
            sess["user_id"] = other
        response = client.post("/admin/stock/items/save", data={"csrf_token":"manual-csrf","review_token":token})
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(self.client(admin=False).get("/admin/stock/items/new").status_code, 200)
        self.assertEqual(self.state()["revision"], 0)

    def test_http_error_and_back_edit_preserve_values_and_filters(self):
        client = self.client()
        first = self.add()
        response, token = self.post_review(client, dict(BASE_ROW, quantity="8"), first["item_id"], {"brand":"Brand A", "q":"C1"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="filter_brand" value="Brand A"', response.get_data(as_text=True))
        state = self.state()
        data = dict(BASE_ROW, csrf_token="manual-csrf", item_id=first["item_id"], revision=state["revision"],
                    snapshot_id=state["snapshot_id"], fingerprint=state["fingerprint"], request_id=str(uuid.uuid4()),
                    quantity="8", edit_again="1", filter_q="C1")
        self.assertIn('value="8"', client.post("/admin/stock/items/review", data=data).get_data(as_text=True))
        data.pop("edit_again"); data["quantity"] = "-1"
        self.assertEqual(client.post("/admin/stock/items/review", data=data).status_code, 400)
        self.add(code="C2")
        self.assertEqual(client.post("/admin/stock/items/save", data={"csrf_token":"manual-csrf","review_token":token}).status_code, 409)

    @unittest.skipUnless(pg_temp_db.psql_runner_available(), "psql runner required")
    def test_migration_real_psql_reapply_and_conflicting_schema_rollback(self):
        self.add()
        before = self.state()
        path = ROOT / "sql/migration_032_stock_manual.sql"
        for _ in range(2):
            code, output = pg_temp_db.run_migration_via_psql(self.dsn, path)
            self.assertEqual(code, 0, output)
        with self.conn.cursor() as cur:
            cur.execute("ALTER TABLE stock_manual_requests ALTER COLUMN payload_sha256 DROP NOT NULL")
        code, output = pg_temp_db.run_migration_via_psql(self.dsn, path)
        self.assertNotEqual(code, 0)
        self.assertIn("Migration 032", output)
        self.assertEqual(self.state(), before)
        with self.conn.cursor() as cur:
            cur.execute("ALTER TABLE stock_manual_requests ALTER COLUMN payload_sha256 SET NOT NULL")

    def test_migration_032_reapply_and_partial_schema_fail_closed(self):
        self.add()
        before = self.state()
        sql = (ROOT / "sql/migration_032_stock_manual.sql").read_text()
        with self.conn.cursor() as cur:
            cur.execute(sql); cur.execute(sql)
            cur.execute("ALTER TABLE stock_manual_requests DROP CONSTRAINT stock_manual_requests_pkey")
            with self.assertRaises(psycopg2.Error):
                cur.execute(sql)
            cur.execute("ROLLBACK")
            cur.execute("ALTER TABLE stock_manual_requests ADD PRIMARY KEY(actor_user_id,request_id)")
            cur.execute(sql)
        self.assertEqual(self.state(), before)


if __name__ == "__main__":
    unittest.main()
