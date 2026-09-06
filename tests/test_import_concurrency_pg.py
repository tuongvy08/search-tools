"""Real PostgreSQL concurrency & atomicity tests for the import apply path
(Phase 6B2B1-E, Section 1: "Import concurrency thực sự").

Everything in this file talks to a REAL, isolated, uniquely-named temporary
Postgres database (never `products_local`), built the same way the other
real-Postgres suites in this repo build theirs (`tests/pg_temp_db.py`), plus
`migration_004_import_jobs.sql` (used by `_insert_import_job`, called at the
end of every `/admin/imports/apply` request) and
`migration_017_brand_master.sql` (creates `brand_master`/`brand_aliases`,
`products.source_brand`, the FK/NOT NULL constraints, and is what the
Brand Gateway / `acquire_products_import_lock` code in `brand_gateway.py`
and `search.py` actually run against).

Real two-connection/two-thread concurrency is used wherever this file
claims to prove serialization -- never a single-threaded simulation.
Waits are always bounded (`threading.Barrier(timeout=...)`,
`Thread.join(timeout=...)` + `is_alive()` polling), never a fixed
`time.sleep()` guess.
"""

from __future__ import annotations

import os
import threading
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

import psycopg2
from dotenv import load_dotenv
from openpyxl import Workbook

import search
from brand_gateway import PRODUCTS_IMPORT_LOCK_KEY
from tests.pg_temp_db import create_full_schema_temp_db, drop_temp_db, probe_postgres_reachable

load_dotenv()

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION_004_PATH = _ROOT / "sql" / "migration_004_import_jobs.sql"
_MIGRATION_017_PATH = _ROOT / "sql" / "migration_017_brand_master.sql"
_MIGRATION_018_PATH = _ROOT / "sql" / "migration_018_currency_rates.sql"
_MIGRATION_019_PATH = _ROOT / "sql" / "migration_019_dynamic_brand_currency.sql"


def _xlsx_bytes(headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class ImportConcurrencyPgTests(unittest.TestCase):
    HEADERS = ["brand", "code", "name", "size", "source_brand", "cas"]

    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        try:
            conn = psycopg2.connect(cls.dsn)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(_MIGRATION_004_PATH.read_text(encoding="utf-8"))
                cur.execute(_MIGRATION_017_PATH.read_text(encoding="utf-8"))
                cur.execute(_MIGRATION_018_PATH.read_text(encoding="utf-8"))
                cur.execute(_MIGRATION_019_PATH.read_text(encoding="utf-8"))
                # session_security's before_request hook re-validates every
                # authenticated session against a REAL app_users row
                # (account_status + auth_version) -- give it one real admin.
                cur.execute(
                    """
                    INSERT INTO app_users (username, password_hash, is_admin, account_status, auth_version)
                    VALUES ('admin1', 'x', TRUE, 'ACTIVE', 1)
                    ON CONFLICT (username) DO NOTHING
                    RETURNING id;
                    """
                )
                row = cur.fetchone()
                cur.execute("SELECT id FROM app_users WHERE username = 'admin1';")
                cls.admin_user_id = cur.fetchone()[0]
            cls.conn = conn
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
        self._env_patch = mock.patch.dict(
            os.environ, {"DATABASE_URL": self.dsn, "DISABLE_IP_ALLOWLIST": "1"}
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        search.app.testing = True

    def tearDown(self):
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE id > 0;")
            cur.execute("DELETE FROM import_jobs WHERE id > 0;")

    # -------------------------------------------------------------------
    # helpers
    # -------------------------------------------------------------------
    @staticmethod
    def _admin_client():
        client = search.app.test_client()
        with client.session_transaction() as sess:
            sess.clear()
            sess.update(authenticated=True, user_id=ImportConcurrencyPgTests.admin_user_id, is_admin=True,
                        auth_version=1, role="admin", username="admin1", csrf_token="import-csrf")
        return client

    def _preview(self, client, rows, mode="upsert"):
        bio = _xlsx_bytes(self.HEADERS, rows)
        resp = client.post(
            "/admin/imports/preview",
            data={"dataset": "products", "mode": mode, "file": (bio, "f.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 302)
        loc = resp.headers["Location"]
        self.assertIn("preview=", loc, f"preview did not succeed, got redirect: {loc}")
        return loc.split("preview=")[1].split("&")[0]

    @staticmethod
    def _apply(client, token):
        return client.post(
            "/admin/imports/apply",
            data={"preview_token": token, "csrf_token": "import-csrf"},
        )

    def _seed_products(self, rows):
        """rows: list of dict(name, code, cas, brand, source_brand, size)."""
        with self.conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    """
                    INSERT INTO products (name, code, cas, brand, source_brand, size)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (r.get("name"), r.get("code"), r.get("cas"), r["brand"],
                     r.get("source_brand") or r["brand"], r.get("size")),
                )

    def test_preview_reports_new_brand_without_write_then_apply_registers_it(self):
        client = self._admin_client()
        token = self._preview(
            client,
            [
                {"brand": "Dynamic Upload", "code": "DYN-UP-1", "name": "Upload One"},
                {"brand": " dynamic upload ", "code": "DYN-UP-2", "name": "Upload Two"},
            ],
        )
        preview = search.IMPORT_PREVIEWS[token]
        self.assertEqual(preview["new_brands"][0]["name"], "Dynamic Upload")
        self.assertEqual(preview["new_brands"][0]["row_count"], 2)
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM brand_master WHERE normalized_name='DYNAMIC UPLOAD'")
            self.assertEqual(cur.fetchone()[0], 0, "preview must be read-only")

        response = self._apply(client, token)
        self.assertEqual(response.status_code, 302)
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT currency_code FROM brand_master WHERE normalized_name='DYNAMIC UPLOAD'"
            )
            self.assertIsNone(cur.fetchone()[0])
            cur.execute(
                "SELECT COUNT(*), COUNT(DISTINCT brand) FROM products "
                "WHERE code IN ('DYN-UP-1','DYN-UP-2')"
            )
            self.assertEqual(cur.fetchone(), (2, 1))
            cur.execute("SELECT COUNT(*) FROM team_brands WHERE brand='Dynamic Upload'")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_apply_and_quick_product_reject_missing_csrf_without_mutation(self):
        client = self._admin_client()
        token = self._preview(
            client, [{"brand": "Dynamic Csrf", "code": "DYN-CSRF-1", "name": "No Write"}]
        )
        response = client.post("/admin/imports/apply", data={"preview_token": token})
        self.assertEqual(response.status_code, 400)
        response = client.post(
            "/admin/imports/quick-product",
            data={"brand": "Dynamic Csrf", "code": "DYN-CSRF-2", "name": "No Write"},
        )
        self.assertEqual(response.status_code, 400)
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM brand_master WHERE normalized_name='DYNAMIC CSRF'")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT COUNT(*) FROM products WHERE code LIKE 'DYN-CSRF-%'")
            self.assertEqual(cur.fetchone()[0], 0)

    def _fetch_names_by_brand(self, brand):
        with self.conn.cursor() as cur:
            cur.execute("SELECT name FROM products WHERE brand = %s ORDER BY code;", (brand,))
            return [row[0] for row in cur.fetchall()]

    # -------------------------------------------------------------------
    # 1. Two concurrent replace_by_brand applies must SERIALIZE (real
    #    threads/connections): the loser must never insert on top of a
    #    predicate/ID scope computed before the winner committed.
    # -------------------------------------------------------------------
    def test_two_concurrent_replace_by_brand_applies_serialize_no_duplication(self):
        self._seed_products([
            {"name": "Orig 1", "code": "RC-1", "brand": "PhytoLab"},
            {"name": "Orig 2", "code": "RC-2", "brand": "PhytoLab"},
            {"name": "Orig 3", "code": "RC-3", "brand": "PhytoLab"},
        ])

        client_a = self._admin_client()
        client_b = self._admin_client()
        rows_a = [{"brand": "PhytoLab", "code": f"RC-{i}", "name": f"FromFileA-{i}"} for i in range(1, 4)]
        rows_b = [{"brand": "PhytoLab", "code": f"RC-{i}", "name": f"FromFileB-{i}"} for i in range(1, 4)]

        token_a = self._preview(client_a, rows_a, mode="replace_by_brand")
        token_b = self._preview(client_b, rows_b, mode="replace_by_brand")

        barrier = threading.Barrier(2)
        results = {}

        def apply_a():
            barrier.wait(timeout=10)
            results["a"] = self._apply(client_a, token_a).headers.get("Location", "")

        def apply_b():
            barrier.wait(timeout=10)
            results["b"] = self._apply(client_b, token_b).headers.get("Location", "")

        t1 = threading.Thread(target=apply_a)
        t2 = threading.Thread(target=apply_b)
        t1.start()
        t2.start()
        t1.join(timeout=20)
        t2.join(timeout=20)
        self.assertFalse(t1.is_alive(), "apply A hung -- advisory lock leak/deadlock?")
        self.assertFalse(t2.is_alive(), "apply B hung -- advisory lock leak/deadlock?")

        # Both applies see a scope count that still matches their own
        # preview (3 rows in, 3 rows out each time), so both succeed --
        # but they must be fully serialized, never interleaved.
        self.assertIn("msg=", results.get("a", ""), results.get("a"))
        self.assertIn("msg=", results.get("b", ""), results.get("b"))

        names = self._fetch_names_by_brand("PhytoLab")
        self.assertEqual(len(names), 3,
                          f"expected exactly 3 rows after two serialized full replaces, got {names}")
        origins = {n.split("-")[0] for n in names}
        self.assertEqual(len(origins), 1,
                          f"rows came from BOTH concurrent applies at once -- lock did not serialize: {names}")

    # -------------------------------------------------------------------
    # 1b. Deterministic (non-flaky) proof of the above: without ANY
    #    injected sleep, a real natural race between two fast local
    #    requests can happen to finish sequentially anyway and would pass
    #    test 1 even with the lock removed (verified manually: two raw
    #    connections that both SELECT the target IDs before either
    #    DELETEs reproduce 6 rows instead of 3 when the lock is absent).
    #    To causally prove THIS code path is what prevents that, force
    #    genuine overlap: pause request A immediately after it resolves
    #    its exact-ID delete scope (still holding the products-import
    #    lock, before DELETE/INSERT), and prove request B is fully
    #    blocked -- it must not even reach its own scope resolution --
    #    for as long as A holds the lock. Only a real advisory lock
    #    taken BEFORE the scan (not just before the DELETE) can cause that.
    # -------------------------------------------------------------------
    def test_replace_by_brand_lock_blocks_second_request_before_its_scan_not_just_its_delete(self):
        from brand_gateway import resolve_replace_by_brand_target_ids as _real_resolve_ids

        self._seed_products([
            {"name": "Orig 1", "code": "FI-1", "brand": "PhytoLab"},
            {"name": "Orig 2", "code": "FI-2", "brand": "PhytoLab"},
            {"name": "Orig 3", "code": "FI-3", "brand": "PhytoLab"},
        ])
        client_a = self._admin_client()
        client_b = self._admin_client()
        rows_a = [{"brand": "PhytoLab", "code": f"FI-{i}", "name": f"FromFileA-{i}"} for i in range(1, 4)]
        rows_b = [{"brand": "PhytoLab", "code": f"FI-{i}", "name": f"FromFileB-{i}"} for i in range(1, 4)]
        token_a = self._preview(client_a, rows_a, mode="replace_by_brand")
        token_b = self._preview(client_b, rows_b, mode="replace_by_brand")

        call_order = []
        reached_pause = threading.Event()
        release_a = threading.Event()

        def wrapper(cur, brand_to_sources):
            call_order.append("enter")
            ids = _real_resolve_ids(cur, brand_to_sources)
            if len(call_order) == 1:
                # This is request A: pause here, still holding the
                # products-import advisory lock, BEFORE DELETE/INSERT.
                reached_pause.set()
                release_a.wait(timeout=10)
            return ids

        results = {}

        def apply_a():
            results["a"] = self._apply(client_a, token_a).headers.get("Location", "")

        def apply_b():
            results["b"] = self._apply(client_b, token_b).headers.get("Location", "")

        with mock.patch("search.resolve_replace_by_brand_target_ids", side_effect=wrapper):
            t_a = threading.Thread(target=apply_a)
            t_b = threading.Thread(target=apply_b)
            t_a.start()
            self.assertTrue(reached_pause.wait(timeout=10), "request A never reached its scope-resolution pause")

            t_b.start()
            # Bounded poll (no sleep-guessing): while A is paused holding
            # the lock, B must remain fully blocked BEFORE even calling
            # scope resolution -- not just blocked before its DELETE.
            t_b.join(timeout=1.5)
            self.assertTrue(t_b.is_alive(), "request B finished/errored instead of blocking on the lock")
            self.assertEqual(call_order, ["enter"],
                              "request B reached scope resolution while A still held the lock -- "
                              "lock is not actually taken before the scan")

            release_a.set()
            t_a.join(timeout=20)
            t_b.join(timeout=20)

        self.assertFalse(t_a.is_alive())
        self.assertFalse(t_b.is_alive())
        self.assertIn("msg=", results.get("a", ""), results.get("a"))
        self.assertIn("msg=", results.get("b", ""), results.get("b"))
        self.assertEqual(call_order, ["enter", "enter"])

        # B ran strictly after A committed -> deterministic final state:
        # exactly 3 rows, ALL from file B (never a 3+3=6 duplication).
        names = self._fetch_names_by_brand("PhytoLab")
        self.assertEqual(sorted(names), ["FromFileB-1", "FromFileB-2", "FromFileB-3"],
                          f"expected exactly B's 3 rows (serialized after A), got {names}")

    # -------------------------------------------------------------------
    # 2. Data changed between preview and apply (a second real
    #    connection commits a new row inside the previewed scope) must be
    #    DETECTED and rejected with ZERO mutation.
    # -------------------------------------------------------------------
    def test_scope_drift_between_preview_and_apply_is_detected_and_rejects_atomically(self):
        self._seed_products([
            {"name": "Orig D1", "code": "D-1", "brand": "PhytoLab"},
            {"name": "Orig D2", "code": "D-2", "brand": "PhytoLab"},
        ])
        client = self._admin_client()
        rows = [{"brand": "PhytoLab", "code": "D-1", "name": "New D1"},
                {"brand": "PhytoLab", "code": "D-2", "name": "New D2"}]
        token = self._preview(client, rows, mode="replace_by_brand")  # captures deletable_count=2

        # A SEPARATE, real second connection commits a new row into the
        # SAME previewed scope in between preview and apply.
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products (name, code, cas, brand, source_brand) "
                "VALUES ('Concurrently Added', 'D-3', NULL, 'PhytoLab', 'PhytoLab');"
            )

        resp = self._apply(client, token)
        loc = resp.headers.get("Location", "")
        self.assertIn("err=", loc, f"drift must be rejected, got: {loc}")
        self.assertIn("thay", loc + resp.get_data(as_text=True), "expected a drift/scope-changed message")

        # Zero mutation: all three rows (2 original + 1 concurrently
        # added) must be untouched.
        names = sorted(self._fetch_names_by_brand("PhytoLab"))
        self.assertEqual(names, ["Concurrently Added", "Orig D1", "Orig D2"])

    # -------------------------------------------------------------------
    # 3. A concurrent insert into a DIFFERENT source_brand scope of the
    #    SAME canonical (multi-source) brand must never be swept up by a
    #    replace_by_brand that only previewed one source_brand.
    # -------------------------------------------------------------------
    def test_concurrent_insert_outside_previewed_source_scope_is_not_deleted(self):
        self._seed_products([
            {"name": "Mikromol 1", "code": "MK-1", "brand": "LGC", "source_brand": "LGC (Mikromol)"},
            {"name": "Mikromol 2", "code": "MK-2", "brand": "LGC", "source_brand": "LGC (Mikromol)"},
            {"name": "XRF Original", "code": "XR-1", "brand": "LGC", "source_brand": "LGC (XRF)"},
        ])
        client = self._admin_client()
        rows = [
            {"brand": "LGC", "source_brand": "LGC (Mikromol)", "code": "MK-1", "name": "New Mikromol 1"},
            {"brand": "LGC", "source_brand": "LGC (Mikromol)", "code": "MK-2", "name": "New Mikromol 2"},
        ]
        token = self._preview(client, rows, mode="replace_by_brand")

        # Real second connection: concurrently insert into the LGC (XRF)
        # scope, which is explicitly OUTSIDE what was previewed.
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products (name, code, cas, brand, source_brand) "
                "VALUES ('XRF Concurrent', 'XR-2', NULL, 'LGC', 'LGC (XRF)');"
            )

        resp = self._apply(client, token)
        loc = resp.headers.get("Location", "")
        self.assertIn("msg=", loc, f"in-scope replace must succeed despite out-of-scope concurrent write: {loc}")

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT code, name FROM products WHERE brand = 'LGC' AND source_brand = 'LGC (XRF)' ORDER BY code;"
            )
            xrf_rows = cur.fetchall()
        self.assertEqual(xrf_rows, [("XR-1", "XRF Original"), ("XR-2", "XRF Concurrent")],
                          "concurrent insert in a DIFFERENT source_brand scope must survive untouched")

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM products WHERE brand = 'LGC' AND source_brand = 'LGC (Mikromol)' ORDER BY code;"
            )
            mikromol_names = [r[0] for r in cur.fetchall()]
        self.assertEqual(mikromol_names, ["New Mikromol 1", "New Mikromol 2"])

    # -------------------------------------------------------------------
    # 4. One ambiguous row anywhere in an upsert batch must abort the
    #    WHOLE file atomically via the real HTTP apply route -- earlier,
    #    unambiguous rows in the same file must NOT have been written.
    # -------------------------------------------------------------------
    def test_ambiguous_row_via_real_apply_route_causes_zero_mutation(self):
        self._seed_products([{"name": "OK Seed", "code": "OKX-1", "brand": "PhytoLab"}])
        with self.conn.cursor() as cur:
            # Two TLC Pharmaceutical rows sharing a code, with source_brand
            # values that are NEITHER the canonical name -- so the
            # (source_brand defaults to canonical) disambiguation used for
            # rows that omit source_brand in the file cannot narrow this
            # to a single row. Genuinely ambiguous.
            cur.execute(
                """
                INSERT INTO products (name, code, cas, brand, source_brand, size)
                VALUES
                    ('TLC A', 'TLCX-1', NULL, 'TLC Pharmaceutical', 'TLC (Mỹ)', '10mg'),
                    ('TLC B', 'TLCX-1', NULL, 'TLC Pharmaceutical', 'TLC (OtherCo)', '25mg');
                """
            )

        client = self._admin_client()
        rows = [
            {"brand": "PhytoLab", "code": "OKX-1", "name": "SHOULD_NOT_BE_WRITTEN"},
            {"brand": "TLC Pharmaceutical", "code": "TLCX-1", "name": "AMBIGUOUS_SHOULD_ABORT"},
        ]
        token = self._preview(client, rows, mode="upsert")
        resp = self._apply(client, token)
        loc = resp.headers.get("Location", "")
        self.assertIn("err=", loc, f"ambiguous row must fail the whole apply, got: {loc}")

        with self.conn.cursor() as cur:
            cur.execute("SELECT name FROM products WHERE code = 'OKX-1';")
            self.assertEqual(cur.fetchone()[0], "OK Seed",
                              "earlier unambiguous row in the SAME file must not have been mutated")
            cur.execute("SELECT name FROM products WHERE code = 'TLCX-1' ORDER BY source_brand;")
            self.assertEqual([r[0] for r in cur.fetchall()], ["TLC A", "TLC B"])

    # -------------------------------------------------------------------
    # 5. Bulk apply and the single-row quick-product endpoints must share
    #    the EXACT SAME advisory lock key -- proven by holding that key
    #    from a raw connection and observing the quick-product request
    #    genuinely block (bounded poll, no sleep-guessing) until released.
    # -------------------------------------------------------------------
    def test_quick_product_upsert_blocks_on_the_same_advisory_lock_as_bulk_apply(self):
        lock_conn = psycopg2.connect(self.dsn)
        lock_conn.autocommit = False
        try:
            with lock_conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s);", (PRODUCTS_IMPORT_LOCK_KEY,))

            client = self._admin_client()
            results = {}

            def quick_upsert():
                resp = client.post(
                    "/admin/imports/quick-product",
                    data={
                        "brand": "PhytoLab", "code": "QP-1", "name": "Quick",
                        "csrf_token": "import-csrf",
                    },
                )
                results["status"] = resp.status_code
                results["json"] = resp.get_json()

            t = threading.Thread(target=quick_upsert)
            t.start()

            # Bounded poll (not a sleep guess): the request must still be
            # blocked after a short deterministic wait, proving it is
            # actually contending for the SAME lock key held above.
            t.join(timeout=2)
            self.assertTrue(t.is_alive(),
                             "quick-product upsert did NOT block on the shared products-import lock")

            lock_conn.commit()  # releases pg_advisory_xact_lock
        finally:
            lock_conn.close()

        t.join(timeout=10)
        self.assertFalse(t.is_alive(), "quick-product upsert hung after lock release")
        self.assertEqual(results.get("status"), 200, results.get("json"))
        self.assertTrue(results["json"]["ok"])

        with self.conn.cursor() as cur:
            cur.execute("SELECT name FROM products WHERE code = 'QP-1' AND brand = 'PhytoLab';")
            self.assertEqual(cur.fetchone()[0], "Quick")


if __name__ == "__main__":
    unittest.main()
