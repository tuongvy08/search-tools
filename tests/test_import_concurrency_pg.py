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
