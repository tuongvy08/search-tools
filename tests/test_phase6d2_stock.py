"""Phase 6D2 inventory tests. All PostgreSQL writes use a throwaway DB."""

from datetime import date, timedelta
import hashlib
from io import BytesIO
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
import json
import uuid
from unittest import mock

from openpyxl import Workbook
import psycopg2
from psycopg2.extras import RealDictCursor
from werkzeug.datastructures import FileStorage

os.environ.setdefault("FLASK_SECRET_KEY", "phase6d2-test-only")

import pg_temp_db
import search
import stock_import_jobs
from brand_gateway import PRODUCTS_IMPORT_LOCK_KEY
from import_engine import ImportProblem
from stock import STOCK_LOCK_KEY, expiry_state


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_029 = (ROOT / "sql" / "migration_029_stock_management.sql").read_text(encoding="utf-8")
HEADERS = ["Name", "Code", "Cas", "Brand", "Size", "Giá tồn kho", "Số lượng tồn", "Hạn sử dụng"]
ENGLISH_ALIAS_HEADERS = ["Name", "Code", "Cas", "Brand", "Size", "stock price", "qty", "expiry"]


def workbook_bytes(rows, headers=HEADERS):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    stream = BytesIO()
    wb.save(stream); wb.close(); stream.seek(0)
    return stream


class CountingCursor:
    def __init__(self, cursor, statements):
        self.cursor, self.statements = cursor, statements
    def __enter__(self): return self
    def __exit__(self, *_): self.cursor.close()
    def execute(self, query, params=None):
        if "FROM stock_state state" in str(query):
            self.statements.append(str(query))
        return self.cursor.execute(query, params)
    def __getattr__(self, name): return getattr(self.cursor, name)


class CountingConnection:
    def __init__(self, dsn, statements):
        self.conn, self.statements = psycopg2.connect(dsn), statements
    def cursor(self, *args, **kwargs): return CountingCursor(self.conn.cursor(*args, **kwargs), self.statements)
    def close(self): self.conn.close()
    def __getattr__(self, name): return getattr(self.conn, name)


@unittest.skipUnless(pg_temp_db.probe_postgres_reachable(), "isolated local PostgreSQL required")
class Phase6D2StockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = pg_temp_db.create_full_schema_temp_db()
        try:
            cls.conn = psycopg2.connect(cls.dsn)
            cls.conn.autocommit = True
            with cls.conn.cursor() as cur:
                pg_temp_db.apply_brand_master_and_currency_migrations(cur)
                pg_temp_db.apply_dynamic_brand_currency_migration(cur)
                cur.execute(
                    """INSERT INTO app_users(username,password_hash,is_admin,account_status,auth_version)
                       VALUES ('stock_admin','x',true,'ACTIVE',1) RETURNING id"""
                )
                cls.admin_id = cur.fetchone()[0]
            cls.upload_dir = tempfile.mkdtemp(prefix="phase6d2-stock-")
            cls.env = mock.patch.dict(os.environ, {
                "DATABASE_URL": cls.dsn, "DISABLE_IP_ALLOWLIST": "1",
                "IMPORT_UPLOAD_DIR": cls.upload_dir,
            })
            cls.env.start()
        except Exception:
            pg_temp_db.drop_temp_db(cls.db_name)
            raise

    @classmethod
    def tearDownClass(cls):
        cls.conn.close(); cls.env.stop()
        try:
            pg_temp_db.drop_temp_db(cls.db_name)
        finally:
            shutil.rmtree(cls.upload_dir, ignore_errors=True)

    def setUp(self):
        search.app.testing = True
        with self.conn.cursor() as cur:
            cur.execute("UPDATE stock_state SET active_snapshot_id=NULL,revision=0,updated_at=now() WHERE singleton=TRUE")
            cur.execute("DELETE FROM stock_snapshot_events")
            cur.execute("DELETE FROM stock_items")
            cur.execute("DELETE FROM stock_snapshots")
            cur.execute("DELETE FROM stock_import_events")
            cur.execute("DELETE FROM stock_import_jobs")
            cur.execute("DELETE FROM products")
            cur.execute("DELETE FROM regulatory_rules")
            cur.execute("DELETE FROM team_brands")
            cur.execute("DELETE FROM app_users WHERE id<>%s", (self.admin_id,))
            cur.execute("DELETE FROM teams")
            cur.execute("DELETE FROM brand_aliases")
            cur.execute("DELETE FROM brand_master")
            cur.execute("UPDATE app_users SET is_admin=true,account_status='ACTIVE',auth_version=1 WHERE id=%s", (self.admin_id,))

    def _submit_preview(self, rows, filename="stock.xlsx"):
        job_id = stock_import_jobs.submit(
            FileStorage(stream=workbook_bytes(rows), filename=filename), "stock_admin",
            self.admin_id, 1, str(uuid.uuid4()),
        )
        self.assertTrue(stock_import_jobs.run_once(job_id))
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            job = stock_import_jobs.fetch_job(cur, job_id)
        if job["status"] == "completed":
            self.assertIsNotNone(job["preview"], job["errors"])
        return job_id, job

    def _apply(self, job_id, job):
        self.assertEqual(job["status"], "completed", job["errors"])
        self.assertIsNotNone(job["preview"], job["errors"])
        stock_import_jobs.control(
            job_id, "apply", "stock_admin", job["preview"]["fingerprint"],
            str(job["preview"]["current_rows"]),
        )
        self.assertTrue(stock_import_jobs.run_once(job_id))
        with self.conn.cursor(cursor_factory=RealDictCursor) as cur:
            return stock_import_jobs.fetch_job(cur, job_id)

    def _admin_client(self):
        client = search.app.test_client()
        with client.session_transaction() as sess:
            sess.update(authenticated=True,user_id=self.admin_id,auth_version=1,is_admin=True,
                        username="stock_admin",csrf_token="stock-csrf",auth_provider="LOCAL")
        return client

    def _state_signature(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT active_snapshot_id, revision FROM stock_state WHERE singleton=TRUE")
            snapshot_id, revision = cur.fetchone()
        payload = json.dumps({"id": str(snapshot_id), "revision": int(revision)}, sort_keys=True)
        return str(snapshot_id), int(revision), hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _team_client(self, grants, allowed_brands, provider="LOCAL"):
        marker = uuid.uuid4().hex[:8]
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO teams(name,lifecycle_status,permission_keys) VALUES (%s,'ACTIVE',%s) RETURNING id",
                ("Stock team " + marker, list(grants)),
            )
            team_id = cur.fetchone()[0]
            for brand in allowed_brands:
                cur.execute("INSERT INTO team_brands(team_id,brand) VALUES (%s,%s)", (team_id, brand))
            cur.execute(
                """INSERT INTO app_users(username,password_hash,team_id,is_admin,account_status,auth_version,auth_provider)
                   VALUES (%s,'x',%s,false,'ACTIVE',1,%s) RETURNING id""",
                ("stock_" + marker, team_id, provider),
            )
            user_id = cur.fetchone()[0]
        client = search.app.test_client()
        with client.session_transaction() as sess:
            sess.update(authenticated=True,user_id=user_id,auth_version=1,is_admin=False,
                        team_id=team_id,username="stock_" + marker,auth_provider=provider)
        return client

    def test_parser_multi_size_multi_expiry_optional_price_and_boundaries(self):
        today = date.today()
        path = Path(self.upload_dir) / "parser.xlsx"
        path.write_bytes(workbook_bytes([
            ["A", "C1", "50-00-0", "Brand A", "100 mL", "", 0, ""],
            ["A", "C1", "50-00-0", "Brand A", "500 mL", 100000, 2, today - timedelta(days=1)],
            ["A", "C1", "50-00-0", "Brand A", "500 mL", 100000, 3, today + timedelta(days=30)],
            ["A", "C1", "50-00-0", "Brand A", "500 mL", 100000, 4, today + timedelta(days=31)],
        ]).getvalue())
        rows = stock_import_jobs.parse_workbook(path)
        self.assertEqual([row["quantity"] for row in rows], [0, 2, 3, 4])
        self.assertIsNone(rows[0]["stock_price_vnd"])
        self.assertEqual(expiry_state(rows[0]["expiry_date"])[0], "missing")
        self.assertEqual(expiry_state(rows[1]["expiry_date"])[0], "expired")
        self.assertEqual(expiry_state(rows[2]["expiry_date"])[0], "near_expiry")
        self.assertEqual(expiry_state(rows[3]["expiry_date"])[0], "current")

    def test_parser_uses_vietnamese_headers_and_supports_english_aliases(self):
        today = date.today()
        path = Path(self.upload_dir) / "parser_alias.xlsx"
        path.write_bytes(workbook_bytes(
            [["A", "C1", "50-00-0", "Brand A", "100 mL", 100000, 1, today + timedelta(days=1)],
            ["B", "C2", "50-00-1", "Brand A", "100 mL", None, 0, None]],
            headers=ENGLISH_ALIAS_HEADERS,
        ))
        rows = stock_import_jobs.parse_workbook(path)
        self.assertEqual(len(rows), 2)
        rows = rows[:2]
        self.assertEqual([row["name"] for row in rows], ["A", "B"])
        self.assertEqual([row["quantity"] for row in rows], [1, 0])
        self.assertIsNotNone(rows[0]["stock_price_vnd"])
        self.assertIsNone(rows[1]["stock_price_vnd"])

    def test_quantity_cas_and_duplicate_errors_never_change_active_snapshot(self):
        good_id, good = self._submit_preview([["A", "C1", "50-00-0", "Brand A", "1g", None, 2, None]])
        self.assertEqual(self._apply(good_id, good)["status"], "completed")
        with self.conn.cursor() as cur:
            cur.execute("SELECT active_snapshot_id FROM stock_state WHERE singleton=TRUE")
            active_before = cur.fetchone()[0]
        invalid_sets = [
            [["A", "C2", None, "Brand A", "1g", None, None, None]],
            [["A", "C2", None, "Brand A", "1g", None, -1, None]],
            [["A", "C2", None, "Brand A", "1g", None, 1.5, None]],
            [["A", "C2", "50-00-1", "Brand A", "1g", None, 1, None]],
            [["A", "C2", None, "Brand A", "1g", None, 1, None],
             ["B", " c2 ", None, " brand a ", "1G", None, 2, None]],
        ]
        for rows in invalid_sets:
            with self.subTest(rows=rows):
                _job_id, job = self._submit_preview(rows, uuid.uuid4().hex + ".xlsx")
                self.assertEqual(job["status"], "failed")
                with self.conn.cursor() as cur:
                    cur.execute("SELECT active_snapshot_id FROM stock_state WHERE singleton=TRUE")
                    self.assertEqual(cur.fetchone()[0], active_before)

    def test_full_snapshot_replaces_a_b_with_a_and_restore_is_audited(self):
        first_id, first = self._submit_preview([
            ["A", "A-1", None, "Brand A", "1g", 100, 2, None],
            ["B", "B-1", None, "Brand B", "1g", 200, 3, None],
        ])
        self._apply(first_id, first)
        with self.conn.cursor() as cur:
            cur.execute("SELECT active_snapshot_id FROM stock_state WHERE singleton=TRUE")
            first_snapshot = cur.fetchone()[0]
        second_id, second = self._submit_preview([["A", "A-1", None, "Brand A", "1g", 110, 4, None]])
        self.assertEqual(second["preview"]["current_rows"], 2)
        self.assertEqual(second["preview"]["removed"], 1)
        self._apply(second_id, second)
        with self.conn.cursor() as cur:
            cur.execute("""SELECT i.brand,i.quantity FROM stock_items i JOIN stock_state s
                           ON i.snapshot_id=s.active_snapshot_id WHERE s.singleton=TRUE""")
            self.assertEqual(cur.fetchall(), [("Brand A", 4)])
        _active_snapshot, active_revision, active_fingerprint = self._state_signature()
        restored = stock_import_jobs.restore_snapshot(
            first_snapshot, "stock_admin", self.admin_id, 1, active_revision, active_fingerprint
        )
        self.assertNotEqual(str(first_snapshot), restored)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM stock_items WHERE snapshot_id=%s", (restored,))
            self.assertEqual(cur.fetchone()[0], 2)
            cur.execute("SELECT event FROM stock_snapshot_events WHERE snapshot_id=%s", (restored,))
            self.assertEqual(cur.fetchone()[0], "restored")

    def test_restore_rejects_when_active_snapshot_changes_before_submit(self):
        first_id, first = self._submit_preview([
            ["A", "A-1", None, "Brand A", "1g", 100, 2, None],
        ])
        self._apply(first_id, first)
        stale_snapshot, stale_revision, stale_fingerprint = self._state_signature()

        second_id, second = self._submit_preview([
            ["B", "B-1", None, "Brand B", "1g", 150, 3, None],
        ])
        self._apply(second_id, second)

        with self.assertRaises(ImportProblem):
            stock_import_jobs.restore_snapshot(
                stale_snapshot, "stock_admin", self.admin_id, 1, stale_revision, stale_fingerprint
            )
        with self.conn.cursor() as cur:
            cur.execute("SELECT active_snapshot_id FROM stock_state WHERE singleton=TRUE")
            self.assertNotEqual(cur.fetchone()[0], stale_snapshot)

    def test_restore_rejects_missing_state_guard(self):
        with self.assertRaises(ImportProblem):
            stock_import_jobs.restore_snapshot(uuid.uuid4(), "stock_admin", self.admin_id, 1, None, "")

    def test_restore_respects_admin_lock_before_actor_recheck(self):
        first_id, first = self._submit_preview([["A", "A-1", None, "Brand A", "1g", None, 1, None]])
        self._apply(first_id, first)
        stale_snapshot, _, stale_fingerprint = self._state_signature()
        second_id, second = self._submit_preview([["B", "B-1", None, "Brand B", "1g", None, 2, None]])
        self._apply(second_id, second)
        expected_snapshot, expected_revision, expected_fingerprint = self._state_signature()

        revoker = psycopg2.connect(self.dsn)
        revoker.autocommit = False
        with revoker.cursor() as cur:
            cur.execute(
                "UPDATE app_users SET account_status='SUSPENDED', auth_version = auth_version + 1 "
                "WHERE id=%s",
                (self.admin_id,),
            )
        outcome = {}

        def restore_job():
            try:
                stock_import_jobs.restore_snapshot(
                    stale_snapshot,
                    "stock_admin",
                    self.admin_id,
                    1,
                    expected_revision,
                    expected_fingerprint,
                )
                outcome["ok"] = True
            except ImportProblem as exc:
                outcome["error"] = str(exc)

        t = threading.Thread(target=restore_job)
        t.start()
        t.join(timeout=1.5)
        revoker.commit()
        revoker.close()
        t.join(timeout=5)
        self.assertFalse(t.is_alive())
        self.assertIn("Quyền quản trị", outcome.get("error", ""))
        with self.conn.cursor() as cur:
            cur.execute("SELECT active_snapshot_id FROM stock_state WHERE singleton=TRUE")
            self.assertEqual(cur.fetchone()[0], expected_snapshot)
            cur.execute("SELECT count(*) FROM stock_snapshots WHERE id=%s", (stale_snapshot,))
            self.assertEqual(cur.fetchone()[0], 1)

    def test_apply_respects_admin_lock_before_actor_recheck(self):
        first_id, first = self._submit_preview([["A", "A-1", None, "Brand A", "1g", None, 1, None]])
        self._apply(first_id, first)
        with self.conn.cursor() as cur:
            cur.execute("SELECT active_snapshot_id FROM stock_state WHERE singleton=TRUE")
            baseline_snapshot = cur.fetchone()[0]

        target_id, target = self._submit_preview([["B", "B-1", None, "Brand B", "1g", None, 2, None]])
        stock_import_jobs.control(
            target_id, "apply", "stock_admin", target["preview"]["fingerprint"], str(target["preview"]["current_rows"])
        )

        revoker = psycopg2.connect(self.dsn)
        revoker.autocommit = False
        with revoker.cursor() as cur:
            cur.execute(
                "UPDATE app_users SET account_status='SUSPENDED', auth_version = auth_version + 1 "
                "WHERE id=%s",
                (self.admin_id,),
            )

        outcome = {}
        worker = threading.Thread(target=lambda: outcome.setdefault("worked", stock_import_jobs.run_once(target_id)))
        worker.start()
        worker.join(timeout=1.5)
        revoker.commit()
        revoker.close()
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        with self.conn.cursor() as cur:
            cur.execute("SELECT status FROM stock_import_jobs WHERE id=%s", (target_id,))
            self.assertEqual(cur.fetchone()[0], "failed")
            cur.execute("SELECT active_snapshot_id FROM stock_state WHERE singleton=TRUE")
            self.assertEqual(cur.fetchone()[0], baseline_snapshot)

    def test_cancel_apply_during_apply_halts_before_snapshot_activation(self):
        first_id, first = self._submit_preview([["A", "A-1", None, "Brand A", "1g", None, 1, None]])
        self._apply(first_id, first)
        with self.conn.cursor() as cur:
            cur.execute("SELECT active_snapshot_id FROM stock_state WHERE singleton=TRUE")
            baseline_snapshot = cur.fetchone()[0]

        target_id, target = self._submit_preview([["B", "B-1", None, "Brand B", "1g", None, 2, None]])
        self.assertEqual(target["status"], "completed")
        stock_import_jobs.control(target_id, "apply", "stock_admin", target["preview"]["fingerprint"], str(target["preview"]["current_rows"]))
        lock_conn = psycopg2.connect(self.dsn)
        lock_conn.autocommit = False
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (PRODUCTS_IMPORT_LOCK_KEY,))

        outcome = {}
        worker = threading.Thread(target=lambda: outcome.setdefault("worked", stock_import_jobs.run_once(target_id)))
        worker.start()
        worker.join(timeout=.6)
        self.assertTrue(worker.is_alive())

        stock_import_jobs.control(target_id, "cancel", "stock_admin")
        lock_conn.commit()
        lock_conn.close()

        worker.join(timeout=8)
        self.assertFalse(worker.is_alive())

        with self.conn.cursor() as cur:
            cur.execute("SELECT status,cancel_requested FROM stock_import_jobs WHERE id=%s", (target_id,))
            status, cancel_requested = cur.fetchone()
            self.assertEqual(status, "cancelled")
            self.assertTrue(cancel_requested)
            cur.execute("SELECT active_snapshot_id FROM stock_state WHERE singleton=TRUE")
            self.assertEqual(cur.fetchone()[0], baseline_snapshot)

    def test_sql_timeout_prevents_apply_activation(self):
        first_id, first = self._submit_preview([["A", "A-1", None, "Brand A", "1g", None, 1, None]])
        self._apply(first_id, first)
        with self.conn.cursor() as cur:
            cur.execute("SELECT active_snapshot_id FROM stock_state WHERE singleton=TRUE")
            baseline_snapshot = cur.fetchone()[0]

        target_id, target = self._submit_preview([["B", "B-1", None, "Brand B", "1g", None, 2, None]])
        self.assertEqual(target["status"], "completed")
        stock_import_jobs.control(target_id, "apply", "stock_admin", target["preview"]["fingerprint"], str(target["preview"]["current_rows"]))

        with mock.patch.dict(os.environ, {"IMPORT_SQL_SECONDS": "1", "IMPORT_JOB_SECONDS": "2"}):
            lock_conn = psycopg2.connect(self.dsn)
            lock_conn.autocommit = False
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (PRODUCTS_IMPORT_LOCK_KEY,))

            outcome = {}
            worker = threading.Thread(target=lambda: outcome.setdefault("worked", stock_import_jobs.run_once(target_id)))
            worker.start()
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            lock_conn.commit()
            lock_conn.close()
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())

        with self.conn.cursor() as cur:
            cur.execute("SELECT status FROM stock_import_jobs WHERE id=%s", (target_id,))
            self.assertIn(cur.fetchone()[0], ("failed", "cancelled"))
            cur.execute("SELECT active_snapshot_id FROM stock_state WHERE singleton=TRUE")
            self.assertEqual(cur.fetchone()[0], baseline_snapshot)

    def test_preview_keeps_active_readable_then_stale_apply_fails(self):
        seed_id, seed = self._submit_preview([["A", "A-1", None, "Brand A", "1g", None, 1, None]])
        self._apply(seed_id, seed)
        stale_id, stale = self._submit_preview([["B", "B-1", None, "Brand B", "1g", None, 2, None]])
        fresh_id, fresh = self._submit_preview([["C", "C-1", None, "Brand C", "1g", None, 3, None]])
        with self.conn.cursor() as cur:
            cur.execute("""SELECT i.code FROM stock_items i JOIN stock_state s
                           ON i.snapshot_id=s.active_snapshot_id WHERE s.singleton=TRUE""")
            self.assertEqual(cur.fetchall(), [("A-1",)])
        self._apply(fresh_id, fresh)
        failed = self._apply(stale_id, stale)
        self.assertEqual(failed["status"], "failed")
        self.assertIn("Snapshot tồn kho đã thay đổi", failed["errors"][0])
        with self.conn.cursor() as cur:
            cur.execute("""SELECT i.code FROM stock_items i JOIN stock_state s
                           ON i.snapshot_id=s.active_snapshot_id WHERE s.singleton=TRUE""")
            self.assertEqual(cur.fetchall(), [("C-1",)])

    def test_active_snapshot_remains_readable_while_apply_waits_for_writer_lock(self):
        seed_id, seed = self._submit_preview([["A", "ACTIVE-1", None, "Brand A", "1g", None, 1, None]])
        self._apply(seed_id, seed)
        next_id, next_job = self._submit_preview([["B", "NEXT-1", None, "Brand B", "1g", None, 2, None]])
        stock_import_jobs.control(
            next_id, "apply", "stock_admin", next_job["preview"]["fingerprint"],
            str(next_job["preview"]["current_rows"]),
        )
        lock_conn = psycopg2.connect(self.dsn)
        lock_conn.autocommit = False
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (PRODUCTS_IMPORT_LOCK_KEY,))
        outcome = {}
        worker = threading.Thread(target=lambda: outcome.setdefault("worked", stock_import_jobs.run_once(next_id)))
        worker.start(); worker.join(timeout=.4)
        self.assertTrue(worker.is_alive())
        with self.conn.cursor() as cur:
            cur.execute("""SELECT i.code FROM stock_items i JOIN stock_state s
                           ON i.snapshot_id=s.active_snapshot_id WHERE s.singleton=TRUE""")
            self.assertEqual(cur.fetchall(), [("ACTIVE-1",)])
        lock_conn.commit(); lock_conn.close()
        worker.join(timeout=8)
        self.assertFalse(worker.is_alive())
        self.assertTrue(outcome.get("worked"))

    def test_concurrent_confirm_only_queues_once(self):
        job_id, job = self._submit_preview([["A", "A-1", None, "Brand A", "1g", None, 1, None]])
        barrier = threading.Barrier(2)
        outcomes = []
        def confirm():
            barrier.wait(timeout=3)
            try:
                stock_import_jobs.control(job_id, "apply", "stock_admin", job["preview"]["fingerprint"], "0")
                outcomes.append("ok")
            except ImportProblem:
                outcomes.append("rejected")
        threads = [threading.Thread(target=confirm) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=5)
        self.assertEqual(sorted(outcomes), ["ok", "rejected"])

    def test_revoked_admin_session_and_csrf_block_mutations(self):
        client = self._admin_client()
        response = client.post("/admin/stock/upload", data={"x": "1"})
        self.assertEqual(response.status_code, 400)
        with self.conn.cursor() as cur:
            cur.execute("UPDATE app_users SET is_admin=false,auth_version=2 WHERE id=%s", (self.admin_id,))
        response = client.post("/admin/stock/upload", data={"csrf_token": "stock-csrf"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_new_brand_gateway_creates_no_team_grant_or_currency(self):
        job_id, job = self._submit_preview([["A", "NEW-1", None, "New Brand", "1g", None, 1, None]])
        self.assertEqual(job["preview"]["new_brands"], ["New Brand"])
        self._apply(job_id, job)
        with self.conn.cursor() as cur:
            cur.execute("SELECT currency_code FROM brand_master WHERE normalized_name='NEW BRAND'")
            self.assertIsNone(cur.fetchone()[0])
            cur.execute("SELECT count(*) FROM team_brands WHERE brand='New Brand'")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_search_findcode_acl_hidden_fields_and_pricing_separation(self):
        job_id, job = self._submit_preview([
            ["Allowed", "CODE-X", "50-00-0", "Brand A", "1g", 125000, 2, None],
            ["Alt", "ALT-X", "50-00-0", "Brand A", "5g", 250000, 1, date.today()+timedelta(days=30)],
            ["CAS only", "CAS-ONLY", "64-17-5", "Brand A", "500mL", None, 5, None],
            ["Denied", "DENY-X", "50-00-0", "Brand B", "10g", 999999, 8, None],
        ])
        self._apply(job_id, job)
        with self.conn.cursor() as cur:
            cur.execute("UPDATE brand_master SET currency_code='VND' WHERE normalized_name='BRAND A'")
            cur.execute(
                """INSERT INTO regulatory_rules(rule_type,rule_label,match_field,match_value,priority,is_active,note,status_id)
                   SELECT stable_key,label,'cas','50-00-0',priority,true,'Không được tự bỏ chặn',id
                   FROM regulatory_statuses WHERE stable_key='CAM_NHAP'"""
            )
            cur.execute(
                """INSERT INTO products(name,code,cas,brand,size,ship,price,note,source_brand)
                   VALUES ('Catalog','CODE-X','50-00-0','Brand A','1g','2','1000','catalog','Brand A')"""
            )
        grants = ["SEARCH","FIND_CODE","SEARCH_BY_CAS","VIEW_NAME","VIEW_CODE","VIEW_SIZE"]
        local = self._team_client(grants, ["Brand A"], "LOCAL")
        google = self._team_client(grants, ["Brand A"], "GOOGLE")
        local_data = local.get("/search?query=CODE-X").get_json()["results"]
        google_data = google.get("/search?query=CODE-X").get_json()["results"]
        self.assertEqual(local_data, google_data)
        options = local_data[0]["Stock_Options"]
        self.assertEqual({item["Code"] for item in options}, {"CODE-X"})
        self.assertTrue(all("Brand" not in item and "Cas" not in item and "Stock_Price" not in item for item in options))
        cas_only = local.get("/search?query=64-17-5").get_json()["results"]
        self.assertTrue(cas_only and cas_only[0]["Result_Kind"] == "stock_only")
        self.assertNotIn("Compliance_Export_Policy", cas_only[0])
        self.assertNotIn("Cas", cas_only[0]["Stock_Options"][0])
        full = self._team_client(
            grants + ["VIEW_BRAND","VIEW_CAS","VIEW_PRICE","VIEW_COMPLIANCE","VIEW_COMPLIANCE_NOTE"],
            ["Brand A"],
        )
        row = full.get("/search?query=CODE-X").get_json()["results"][0]
        self.assertEqual(row["Unit_Price"], "2,000")
        self.assertEqual(row["Compliance_Export_Policy"], "BLOCK")
        full_cas_only = full.get("/search?query=64-17-5").get_json()["results"][0]
        self.assertEqual(full_cas_only["Compliance_Export_Policy"], "")
        self.assertEqual({item["Code"] for item in row["Stock_Options"]}, {"CODE-X", "ALT-X"})
        self.assertNotIn("DENY-X", repr(row))
        self.assertIn("125.000 ₫", repr(row["Stock_Options"]))
        found = full.post("/find_code_batch", data={"codes": "CODE-X"}).get_json()["results"][0]
        self.assertEqual(len(found["Stock_Options"]), 2)

    def test_large_catalog_search_and_findcode_each_use_one_bulk_stock_query(self):
        job_id, job = self._submit_preview([
            [f"Stock {index}", f"BULK-{index}", "50-00-0", "Brand A", "1g", None, index, None]
            for index in range(1, 301)
        ])
        self._apply(job_id, job)
        with self.conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO products(name,code,cas,brand,size,ship,price,note,source_brand)
                   VALUES (%s,%s,'50-00-0','Brand A','1g','1','1000','','Brand A')""",
                [("Bulk stock product", f"BULK-{index}") for index in range(1, 301)],
            )
        client = self._admin_client()
        statements = []
        with mock.patch.object(search, "get_connection", side_effect=lambda: CountingConnection(self.dsn, statements)):
            response = client.get("/search?query=Bulk%20stock%20product")
            self.assertEqual(response.status_code, 200)
        self.assertEqual(len(statements), 1)
        statements.clear()
        codes = "\n".join(f"BULK-{index}" for index in range(1, 301))
        with mock.patch.object(search, "get_connection", side_effect=lambda: CountingConnection(self.dsn, statements)):
            response = client.post("/find_code_batch", data={"codes": codes})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(len(statements), 1)

    def test_migration_reapply_preserves_snapshot_data(self):
        job_id, job = self._submit_preview([["A", "KEEP-1", None, "Brand A", "1g", None, 7, None]])
        self._apply(job_id, job)
        with self.conn.cursor() as cur:
            cur.execute("SELECT active_snapshot_id,revision FROM stock_state WHERE singleton=TRUE")
            before = cur.fetchone()
            cur.execute(MIGRATION_029)
            cur.execute(MIGRATION_029)
            cur.execute("SELECT active_snapshot_id,revision FROM stock_state WHERE singleton=TRUE")
            self.assertEqual(cur.fetchone(), before)
            cur.execute("SELECT quantity FROM stock_items WHERE snapshot_id=%s", (before[0],))
            self.assertEqual(cur.fetchone()[0], 7)


class Phase6D2StaticContracts(unittest.TestCase):
    def test_worker_and_routes_are_wired_without_stock_cleanup(self):
        worker = (ROOT / "scripts" / "import_worker.py").read_text(encoding="utf-8")
        self.assertIn("stock_import_jobs.run_once()", worker)
        self.assertNotIn("stock_import_jobs.cleanup", worker)
        self.assertIn("admin_stock.register", (ROOT / "search.py").read_text(encoding="utf-8"))

    def test_quote_and_copy_columns_do_not_include_stock(self):
        script = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
        export_block = script[script.index("const EXPORT_COLUMNS"):script.index("const REGULATORY_CLASS_RE")]
        self.assertNotIn("Stock_", export_block)
        self.assertNotIn("stock_price", (ROOT / "quote_workbook_export.py").read_text(encoding="utf-8"))

    def test_stock_query_is_bulk_and_indexed(self):
        source = (ROOT / "stock.py").read_text(encoding="utf-8")
        self.assertIn("i.code_norm=ANY(%s)", source)
        self.assertIn("i.cas_norm=ANY(%s)", source)
        migration = MIGRATION_029
        self.assertIn("idx_stock_items_snapshot_code", migration)
        self.assertIn("idx_stock_items_snapshot_cas", migration)


if __name__ == "__main__":
    unittest.main()
