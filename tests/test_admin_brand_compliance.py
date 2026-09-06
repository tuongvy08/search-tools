"""Admin brand manual compliance toggle tests (local DB only).

Phase 6A -- Local Release Gate: this class used to `psycopg2.connect()`
directly to whatever `DATABASE_URL` pointed at (in practice the real local
app database, `products_local`, per `.env`) and write a `CURSOR_*`-prefixed
fixture there, cleaning it up in tearDown. That was an acceptable pattern
before Phase 6A's always-on IP/team-policy middleware existed; now a real
Flask request through an authenticated session also triggers a real policy
read against whatever `teams`/`office_ip_allowlist` rows happen to be in
the target database, which `products_local` has no business influencing or
being influenced by. Runs against its own throwaway, uniquely-named
database instead -- see `tests/pg_temp_db.py` for the schema/isolation
contract; never touches `products_local`.
"""

import re
import unittest
from unittest import mock

import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

import search  # noqa: E402
from auth_test_helpers import start_auth_db_patch  # noqa: E402
from pg_temp_db import create_full_schema_temp_db, drop_temp_db, probe_postgres_reachable  # noqa: E402
from search import _BRAND_COMPLIANCE_LIST_SQL  # noqa: E402


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class AdminBrandComplianceTests(unittest.TestCase):
    PREFIX = "CURSOR_ADMIN_BRAND"
    BRAND_DISPLAY = " Cursor Admin Brand "
    BRAND_NORM = "CURSOR ADMIN BRAND"
    CODE = "CURSOR-ADMIN-BRAND-CODE"
    CAS_LEGACY = "CURSOR-ADMIN-BRAND-CAS"

    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        # unittest does NOT call tearDownClass if setUpClass raises --
        # anything below that fails would otherwise leak this temp DB
        # forever, so clean up ourselves on any exception past this point.
        try:
            # The Flask routes under test (via `self._client(...)`) call
            # `search.get_connection()` == `db.get_connection()`, which
            # reads `DATABASE_URL` fresh from the environment on every
            # call -- patch it for the whole class so the app's OWN
            # queries during a test request land in the same temp DB as
            # this class's own fixture connection, never `products_local`.
            cls._env_patch = mock.patch.dict("os.environ", {"DATABASE_URL": cls.dsn})
            cls._env_patch.start()
            cls.conn = psycopg2.connect(cls.dsn)
            cls.conn.autocommit = True
            cls._reset_fixture()
            # Phase 6A team model: a non-admin session MUST carry a real
            # `team_id` (staff without one is a data/session inconsistency
            # that middleware_access.py now correctly fail-closes on --
            # see `_load_team_ip_policy`'s docstring). This class predates
            # the team model and only needs SOME valid team to exercise
            # its own admin-only route guard, not team-scoped brand
            # visibility.
            with cls.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO teams (name, ip_policy) VALUES ('Brand Compliance Test Team', 'INHERIT') RETURNING id"
                )
                cls.team_id = cur.fetchone()[0]
        except Exception:
            drop_temp_db(cls.db_name)
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.close()
        finally:
            try:
                drop_temp_db(cls.db_name)
            finally:
                cls._env_patch.stop()

    @classmethod
    def _cleanup_fixture(cls):
        with cls.conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM regulatory_rules
                WHERE UPPER(TRIM(match_value)) = %s
                """,
                (cls.CAS_LEGACY,),
            )
            cur.execute(
                """
                DELETE FROM products
                WHERE UPPER(TRIM(code)) = UPPER(TRIM(%s))
                   OR UPPER(TRIM(COALESCE(brand, ''))) = %s
                """,
                (cls.CODE, cls.BRAND_NORM),
            )
            cur.execute(
                "DELETE FROM brand_compliance_settings WHERE brand_norm = %s",
                (cls.BRAND_NORM,),
            )

    @classmethod
    def _reset_fixture(cls):
        cls._cleanup_fixture()
        with cls.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO products
                    (name, code, cas, brand, size, ship, price, note, manual_compliance, manual_compliance_note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    "Admin brand toggle",
                    cls.CODE,
                    cls.CAS_LEGACY,
                    cls.BRAND_DISPLAY,
                    "1g",
                    "1",
                    "100",
                    "product note",
                    "Được bán",
                    "manual note",
                ),
            )
            cur.execute(
                """
                INSERT INTO regulatory_rules
                    (rule_type, rule_label, match_field, match_value, priority, is_active, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                ("CAM_NHAP", "CẤM NHẬP", "cas", cls.CAS_LEGACY, 10, True, "legacy note"),
            )

    def setUp(self):
        self._cleanup_fixture()
        self._reset_fixture()
        # Phase 5D2A: stub the per-request session-liveness DB check with an
        # in-memory fake (no real Postgres touched) for every test here.
        start_auth_db_patch(self)

    def tearDown(self):
        self._cleanup_fixture()

    def _client(self, *, authenticated=False, is_admin=False):
        search.app.testing = True
        client = search.app.test_client()
        if authenticated:
            with client.session_transaction() as sess:
                sess["authenticated"] = True
                sess["user_id"] = 1
                sess["auth_version"] = 1
                sess["is_admin"] = is_admin
                sess["csrf_token"] = "the-real-token"
                if not is_admin:
                    # Real middleware now requires a real team_id for any
                    # authenticated non-admin session -- see setUpClass.
                    sess["team_id"] = self.team_id
        return client

    def _post_toggle(self, client, *, brand_norm, action):
        return client.post(
            "/admin/brand-compliance",
            data={"brand_norm": brand_norm, "action": action, "csrf_token": "the-real-token"},
            follow_redirects=False,
        )

    def _search_row(self, client):
        response = client.get("/search", query_string={"query": self.CODE})
        self.assertEqual(response.status_code, 200)
        rows = response.get_json()["results"]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def _setting_enabled(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT manual_compliance_priority FROM brand_compliance_settings WHERE brand_norm = %s",
                (self.BRAND_NORM,),
            )
            row = cur.fetchone()
        return bool(row and row[0])

    def test_anonymous_and_non_admin_denied(self):
        response = self._client().get("/admin/brand-compliance")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

        response = self._client(authenticated=True, is_admin=False).get("/admin/brand-compliance")
        self.assertEqual(response.status_code, 403)

        response = self._client(authenticated=True, is_admin=False).post(
            "/admin/brand-compliance",
            data={"brand_norm": self.BRAND_NORM, "action": "enable"},
        )
        self.assertEqual(response.status_code, 403)

    def test_brand_list_and_normalization(self):
        client = self._client(authenticated=True, is_admin=True)
        response = client.get("/admin/brand-compliance")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(self.BRAND_DISPLAY.strip(), html)
        self.assertIn(self.BRAND_NORM, html)
        self.assertIn("Đang tắt", html)

        with self.conn.cursor() as cur:
            rows = search._fetch_brand_compliance_rows(cur)
        match = next(row for row in rows if row["brand_norm"] == self.BRAND_NORM)
        self.assertEqual(match["brand"], self.BRAND_DISPLAY.strip())
        self.assertFalse(match["manual_enabled"])

    def test_enable_disable_and_idempotent_enable(self):
        client = self._client(authenticated=True, is_admin=True)

        response = self._post_toggle(client, brand_norm=self.BRAND_NORM, action="enable")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._setting_enabled())

        response = self._post_toggle(client, brand_norm=self.BRAND_NORM, action="enable")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._setting_enabled())

        response = self._post_toggle(client, brand_norm=self.BRAND_NORM, action="disable")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self._setting_enabled())

    def test_missing_csrf_rejected_without_mutation(self):
        client = self._client(authenticated=True, is_admin=True)
        response = client.post(
            "/admin/brand-compliance",
            data={"brand_norm": self.BRAND_NORM, "action": "enable"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self._setting_enabled())

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM brand_compliance_settings WHERE brand_norm = %s",
                (self.BRAND_NORM,),
            )
            self.assertIsNone(cur.fetchone())

    def test_invalid_brand_rejected_safely(self):
        client = self._client(authenticated=True, is_admin=True)
        response = self._post_toggle(client, brand_norm="NO_SUCH_BRAND_NORM", action="enable")
        self.assertEqual(response.status_code, 200)
        self.assertIn("không tồn tại", response.get_data(as_text=True).lower())
        self.assertFalse(self._setting_enabled())

        response = client.post(
            "/admin/brand-compliance",
            data={"brand_norm": self.BRAND_NORM, "action": "toggle", "csrf_token": "the-real-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("không hợp lệ", response.get_data(as_text=True).lower())

    def test_toggle_changes_search_manual_vs_legacy(self):
        client = self._client(authenticated=True, is_admin=True)

        row = self._search_row(client)
        self.assertEqual(row["compliance"], "CẤM NHẬP")
        self.assertEqual(row["compliance_source"], "legacy")

        self._post_toggle(client, brand_norm=self.BRAND_NORM, action="enable")
        row = self._search_row(client)
        self.assertEqual(row["compliance"], "Được bán")
        self.assertEqual(row["compliance_note"], "manual note")
        self.assertEqual(row["compliance_source"], "manual")

        self._post_toggle(client, brand_norm=self.BRAND_NORM, action="disable")
        row = self._search_row(client)
        self.assertEqual(row["compliance"], "CẤM NHẬP")
        self.assertEqual(row["compliance_source"], "legacy")

    def test_brand_list_query_is_single_scan(self):
        with self.conn.cursor() as cur:
            cur.execute("BEGIN")
            try:
                cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + _BRAND_COMPLIANCE_LIST_SQL)
                plan = "\n".join(row[0] for row in cur.fetchall())
            finally:
                cur.execute("ROLLBACK")
        execution = re.search(r"Execution Time: ([0-9.]+ ms)", plan)
        self.assertIsNotNone(execution)
        self.assertNotIn("SubPlan", plan)
        self.assertRegex(plan, re.compile(r"Hash Left Join|Merge Left Join|Nested Loop Left Join"))
        self._brand_list_execution_ms = float(execution.group(1).replace(" ms", ""))

    def test_admin_page_render_structure(self):
        client = self._client(authenticated=True, is_admin=True)
        response = client.get("/admin/brand-compliance")
        html = response.get_data(as_text=True)
        self.assertIn('meta name="viewport"', html)
        self.assertIn("brandFilter", html)
        self.assertIn("table-wrap", html)
        self.assertIn("word-break", html)
        self.assertIn("overflow-x: auto", html)


if __name__ == "__main__":
    unittest.main()
