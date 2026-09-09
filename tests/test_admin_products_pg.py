"""Phase 6C3 product management against a uniquely named throwaway PostgreSQL DB.

The application DATABASE_URL is patched to that database before every request.
No test opens, reads, or writes products_local/staging/production.
"""
from html import unescape
import os
from pathlib import Path
import re
import threading
import unittest
from unittest import mock

import psycopg2

import admin_products
from brand_gateway import acquire_products_import_lock
import search
from tests.pg_temp_db import (
    apply_brand_master_and_currency_migrations,
    apply_dynamic_brand_currency_migration,
    create_full_schema_temp_db,
    drop_temp_db,
    probe_postgres_reachable,
    psql_runner_available,
    run_migration_via_psql,
)


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(probe_postgres_reachable(), "local PostgreSQL required")
class AdminProductsPgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        try:
            cls.conn = psycopg2.connect(cls.dsn)
            cls.conn.autocommit = True
            with cls.conn.cursor() as cur:
                apply_brand_master_and_currency_migrations(cur)
                apply_dynamic_brand_currency_migration(cur)
                cur.execute((ROOT / "sql/migration_004_import_jobs.sql").read_text())
                migration = (ROOT / "sql/migration_025_admin_product_management.sql").read_text()
                transactional, concurrent = migration.split("CREATE INDEX CONCURRENTLY", 1)
                for _ in range(2):  # additive/idempotent rehearsal
                    cur.execute(transactional)
                    cur.execute("CREATE INDEX CONCURRENTLY" + concurrent)
                cur.execute("INSERT INTO teams(name,lifecycle_status) VALUES ('Products staff','ACTIVE') RETURNING id")
                cls.team_id = cur.fetchone()[0]
                cur.execute(
                    """
                    INSERT INTO app_users(username,password_hash,is_admin,account_status)
                    VALUES ('products-admin','x',true,'ACTIVE'),('products-staff','x',false,'ACTIVE')
                    RETURNING id
                    """
                )
                cls.admin_id, cls.staff_id = [row[0] for row in cur.fetchall()]
                cur.execute("UPDATE app_users SET team_id=%s WHERE id=%s", (cls.team_id, cls.staff_id))
        except Exception:
            drop_temp_db(cls.db_name)
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.close()
        finally:
            drop_temp_db(cls.db_name)

    def setUp(self):
        env = mock.patch.dict(
            os.environ,
            {"DATABASE_URL": self.dsn, "DISABLE_IP_ALLOWLIST": "1"},
        )
        env.start()
        self.addCleanup(env.stop)
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM product_admin_events")
            cur.execute("DELETE FROM product_deleted_rows")
            cur.execute("DELETE FROM product_delete_batches")
            cur.execute("DELETE FROM product_delete_previews")
            cur.execute("DELETE FROM products")
            cur.execute("DELETE FROM import_jobs")
            cur.execute(
                "UPDATE app_users SET is_admin=(id=%s),account_status='ACTIVE',auth_version=1 WHERE id IN (%s,%s)",
                (self.admin_id, self.admin_id, self.staff_id),
            )
        search.app.testing = True
        self.client = self._client(self.admin_id, True)

    @staticmethod
    def _session(client, user_id, is_admin, auth_version=1, team_id=None):
        with client.session_transaction() as sess:
            sess.update(
                authenticated=True, user_id=user_id, auth_version=auth_version,
                is_admin=is_admin, username="qa-user", csrf_token="qa-csrf", team_id=team_id,
            )

    def _client(self, user_id, is_admin=True, auth_version=1):
        client = search.app.test_client()
        self._session(client, user_id, is_admin, auth_version, None if is_admin else self.team_id)
        return client

    def _seed(self, *, brand="TRC", source="TRC", code="A", name="Alpha", size="1 g", note="note"):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO products
                    (name,code,cas,brand,size,ship,price,note,manual_compliance,
                     manual_compliance_note,preparation_type,source_brand)
                VALUES (%s,%s,'50-00-0',%s,%s,'1.2','25.00',%s,'Được bán','reviewed','NEAT',%s)
                RETURNING id
                """,
                (name, code, brand, size, note, source),
            )
            return cur.fetchone()[0]

    def _revision(self, product_id):
        with self.conn.cursor() as cur:
            cur.execute("SELECT xmin::text FROM products WHERE id=%s", (product_id,))
            return cur.fetchone()[0]

    def _product_form(self, product_id, **changes):
        values = {
            "csrf_token": "qa-csrf", "revision": self._revision(product_id),
            "name": "Alpha", "code": "A", "cas": "50-00-0", "brand": "TRC",
            "source_brand": "TRC", "size": "1 g", "ship": "1.2", "price": "25.00",
            "note": "note", "manual_compliance": "Được bán",
            "manual_compliance_note": "reviewed", "preparation_type": "NEAT",
        }
        values.update(changes)
        return values

    def test_keyset_pagination_search_and_canonical_brand_filter(self):
        for index in range(55):
            self._seed(code=f"TRC-{index:03d}", name=f"TRC item {index:03d}")
        self._seed(brand="SPEX", source="SPEX", code="S-ONLY", name="SPEX needle")

        first = self.client.get("/admin/products?page_size=25")
        self.assertEqual(first.status_code, 200)
        body = first.get_data(as_text=True)
        self.assertEqual(body.count('class="product-link"'), 25)
        match = re.search(r'href="([^\"]*cursor=[^\"]*)"[^>]*>Trang sau', body)
        self.assertIsNotNone(match)
        second = self.client.get(unescape(match.group(1)))
        self.assertEqual(second.status_code, 200)
        self.assertIn("Trang trước", second.get_data(as_text=True))

        filtered = self.client.get("/admin/products?q=needle&brand=SPEX")
        text = filtered.get_data(as_text=True)
        self.assertIn("SPEX needle", text)
        self.assertNotIn("TRC item", text)
        source = (ROOT / "admin_products.py").read_text()
        self.assertNotRegex(source.upper(), r"\bOFFSET\b")

    def test_create_update_validation_canonical_identity_and_stale_revision(self):
        create = self.client.post(
            "/admin/products/create",
            data={
                "csrf_token": "qa-csrf", "name": "Created", "code": "NEW-1",
                "cas": "64-17-5", "brand": "TRC", "source_brand": "TRC source",
                "size": "10 mL", "ship": "1", "price": "12", "note": "ok",
                "manual_compliance": "Phụ lục II", "manual_compliance_note": "manual",
                "preparation_type": "solution",
            },
        )
        self.assertEqual(create.status_code, 302)
        product_id = int(re.search(r"/admin/products/(\d+)", create.location).group(1))
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT brand,source_brand,manual_compliance,preparation_type FROM products WHERE id=%s",
                (product_id,),
            )
            self.assertEqual(cur.fetchone(), ("TRC", "TRC source", "Phụ lục II", "SOLUTION"))

        old_revision = self._revision(product_id)
        updated = self.client.post(
            f"/admin/products/{product_id}/update",
            data=self._product_form(product_id, code="NEW-1", name="Edited", manual_compliance="", manual_compliance_note=""),
        )
        self.assertEqual(updated.status_code, 302)
        stale = self._product_form(product_id, code="NEW-1", name="Lost update")
        stale["revision"] = old_revision
        rejected = self.client.post(f"/admin/products/{product_id}/update", data=stale)
        self.assertEqual(rejected.status_code, 302)
        self.assertIn("error=", rejected.location)
        with self.conn.cursor() as cur:
            cur.execute("SELECT name,manual_compliance FROM products WHERE id=%s", (product_id,))
            self.assertEqual(cur.fetchone(), ("Edited", None))

        bad = self.client.post(
            "/admin/products/create",
            data={"csrf_token": "qa-csrf", "code": "BAD", "brand": "Unknown 6C3"},
        )
        self.assertIn("error=", bad.location)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM brand_master WHERE normalized_name='UNKNOWN 6C3'")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_admin_csrf_positive_and_negative_boundaries(self):
        product_id = self._seed()
        self.assertEqual(self.client.get("/admin/products").status_code, 200)
        self.assertEqual(
            self.client.post(
                f"/admin/products/{product_id}/update",
                data={k: v for k, v in self._product_form(product_id).items() if k != "csrf_token"},
            ).status_code,
            400,
        )
        staff = self._client(self.staff_id, False)
        self.assertEqual(staff.get("/admin/products").status_code, 403)
        self.assertEqual(
            staff.post("/admin/products/delete-preview", data={"csrf_token": "qa-csrf", "scope_type": "product", "product_id": product_id}).status_code,
            403,
        )

    def test_brand_delete_is_durable_one_time_audited_and_fully_restorable(self):
        expected = []
        for index in range(3):
            pid = self._seed(code=f"D-{index}", name=f"Delete {index}", note=f"note {index}")
            expected.append(pid)
        survivor = self._seed(brand="SPEX", source="SPEX", code="KEEP", name="Keep")
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM brand_master"); master_before = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM brand_aliases"); aliases_before = cur.fetchone()[0]
            cur.execute("INSERT INTO regulatory_rules(rule_type,rule_label,match_field,match_value) VALUES ('TON_KHO','Keep','code','D-0')")
            cur.execute("INSERT INTO import_jobs(dataset,mode,status,row_count,created_by) VALUES ('products','test','success',3,'qa')")

        preview = self.client.post(
            "/admin/products/delete-preview",
            data={"csrf_token": "qa-csrf", "scope_type": "brand", "brand": "TRC"},
        )
        self.assertEqual(preview.status_code, 200, preview.get_data(as_text=True))
        claim = preview.get_json()
        self.assertEqual(claim["count"], 3)

        # A second Flask client can consume the DB-backed preview, proving the
        # flow does not depend on the Gunicorn worker that created it.
        other_worker = self._client(self.admin_id, True)
        applied = other_worker.post(
            "/admin/products/delete-apply",
            data={"csrf_token": "qa-csrf", "token": claim["token"], "confirmation": "XOA 3"},
        )
        self.assertEqual(applied.status_code, 200, applied.get_data(as_text=True))
        batch_id = applied.get_json()["batch_id"]
        self.assertEqual(
            other_worker.post(
                "/admin/products/delete-apply",
                data={"csrf_token": "qa-csrf", "token": claim["token"], "confirmation": "XOA 3"},
            ).status_code,
            400,
        )
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM products ORDER BY id")
            self.assertEqual([row[0] for row in cur.fetchall()], [survivor])
            cur.execute("SELECT count(*) FROM product_deleted_rows WHERE batch_id=%s", (batch_id,))
            self.assertEqual(cur.fetchone()[0], 3)
            cur.execute("SELECT count(*) FROM brand_master"); self.assertEqual(cur.fetchone()[0], master_before)
            cur.execute("SELECT count(*) FROM brand_aliases"); self.assertEqual(cur.fetchone()[0], aliases_before)
            cur.execute("SELECT count(*) FROM regulatory_rules WHERE match_value='D-0'"); self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("SELECT count(*) FROM import_jobs WHERE created_by='qa'"); self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("SELECT action,row_count FROM product_admin_events ORDER BY id")
            self.assertIn(("delete", 3), cur.fetchall())

        restored = other_worker.post(
            f"/admin/products/delete-batches/{batch_id}/restore",
            data={"csrf_token": "qa-csrf"},
        )
        self.assertEqual(restored.status_code, 302)
        with self.conn.cursor() as cur:
            cur.execute("SELECT id,note,manual_compliance,preparation_type,source_brand FROM products WHERE brand='TRC' ORDER BY id")
            rows = cur.fetchall()
            self.assertEqual([row[0] for row in rows], expected)
            self.assertEqual(rows[2][1:], ("note 2", "Được bán", "NEAT", "TRC"))

    def test_stale_delete_preview_fails_without_partial_delete_or_backup(self):
        first = self._seed(code="S-1")
        second = self._seed(code="S-2")
        preview = self.client.post(
            "/admin/products/delete-preview",
            data={"csrf_token": "qa-csrf", "scope_type": "brand", "brand": "TRC"},
        ).get_json()
        with self.conn.cursor() as cur:
            cur.execute("UPDATE products SET note='changed outside preview' WHERE id=%s", (second,))
        response = self.client.post(
            "/admin/products/delete-apply",
            data={"csrf_token": "qa-csrf", "token": preview["token"], "confirmation": "XOA 2"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("thay đổi", response.get_json()["message"])
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM products ORDER BY id")
            self.assertEqual([row[0] for row in cur.fetchall()], [first, second])
            cur.execute("SELECT count(*) FROM product_delete_batches")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_actor_is_rechecked_after_waiting_for_products_lock(self):
        product_id = self._seed()
        locker = psycopg2.connect(self.dsn)
        with locker.cursor() as cur:
            acquire_products_import_lock(cur)
        reached_lock = threading.Event()
        real_acquire = admin_products.acquire_products_import_lock
        result = {}

        def marked_acquire(cur):
            reached_lock.set()
            return real_acquire(cur)

        def request_update():
            client = self._client(self.admin_id, True)
            result["response"] = client.post(
                f"/admin/products/{product_id}/update",
                data=self._product_form(product_id, name="must not write"),
            )

        with mock.patch.object(admin_products, "acquire_products_import_lock", side_effect=marked_acquire):
            thread = threading.Thread(target=request_update)
            thread.start()
            self.assertTrue(reached_lock.wait(5))
            with self.conn.cursor() as cur:
                cur.execute("UPDATE app_users SET auth_version=2 WHERE id=%s", (self.admin_id,))
            locker.rollback()
            thread.join(10)
        locker.close()
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["response"].status_code, 403)
        with self.conn.cursor() as cur:
            cur.execute("SELECT name FROM products WHERE id=%s", (product_id,))
            self.assertEqual(cur.fetchone()[0], "Alpha")

    def test_single_delete_expiry_and_restore_overlap_fail_closed(self):
        product_id = self._seed(code="ONE")
        expired = self.client.post(
            "/admin/products/delete-preview",
            data={"csrf_token": "qa-csrf", "scope_type": "product", "product_id": product_id},
        ).get_json()
        with self.conn.cursor() as cur:
            cur.execute("UPDATE product_delete_previews SET expires_at=now()-interval '1 second' WHERE token=%s", (expired["token"],))
        self.assertEqual(
            self.client.post(
                "/admin/products/delete-apply",
                data={"csrf_token": "qa-csrf", "token": expired["token"], "confirmation": "XOA 1"},
            ).status_code,
            400,
        )

        fresh = self.client.post(
            "/admin/products/delete-preview",
            data={"csrf_token": "qa-csrf", "scope_type": "brand", "brand": "TRC"},
        ).get_json()
        deleted = self.client.post(
            "/admin/products/delete-apply",
            data={"csrf_token": "qa-csrf", "token": fresh["token"], "confirmation": "XOA 1"},
        ).get_json()
        replacement = self._seed(code="REPLACEMENT")
        refused = self.client.post(
            f"/admin/products/delete-batches/{deleted['batch_id']}/restore",
            data={"csrf_token": "qa-csrf"},
        )
        self.assertIn("error=", refused.location)
        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM products WHERE brand='TRC'")
            self.assertEqual(cur.fetchall(), [(replacement,)])
            cur.execute("SELECT count(*) FROM product_deleted_rows WHERE batch_id=%s", (deleted["batch_id"],))
            self.assertEqual(cur.fetchone()[0], 1)

    @unittest.skipUnless(psql_runner_available(), "psql or local docker PostgreSQL required")
    def test_migration_runs_via_production_style_psql_and_is_rerunnable(self):
        path = ROOT / "sql/migration_025_admin_product_management.sql"
        for _ in range(2):
            code, output = run_migration_via_psql(self.dsn, path)
            self.assertEqual(code, 0, output)


if __name__ == "__main__":
    unittest.main()
