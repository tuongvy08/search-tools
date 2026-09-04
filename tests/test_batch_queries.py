"""Regression checks for batch endpoint SQL (local DB only).

Phase 6A -- Local Release Gate: `setUpClass` used to connect straight to
whatever `DATABASE_URL` pointed at (in practice `products_local`, per
`.env`) and write `CURSOR_*`-prefixed fixture rows there. It now creates
its own throwaway, uniquely-named database (see `tests/pg_temp_db.py`) and
patches `DATABASE_URL` to point at it for the whole class, so every DB
entrypoint the app itself uses during a test request -- not just this
file's own fixture connection and `RecordingConnection` -- resolves to the
temp DB: `search.get_connection`/`middleware_access.get_connection`/
`db.get_connection` all read `DATABASE_URL` fresh on every call, with no
separate patch needed per module. `_local_dsn()` below is unchanged code;
it simply now reads back the patched (temp DB) value instead of whatever
the real environment/`.env` set.
"""

import os
import re
import unittest
from unittest import mock
from unittest.mock import patch
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

import search  # noqa: E402
from auth_test_helpers import start_auth_db_patch  # noqa: E402
from pg_temp_db import (  # noqa: E402
    apply_sql_file_statement_by_statement,
    create_full_schema_temp_db,
    drop_temp_db,
    probe_postgres_reachable,
)


def _local_dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    host = urlparse(dsn).hostname or ""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    return dsn


def _sample_values(cur, column, count, direction="ASC"):
    cur.execute(
        f"""
        SELECT UPPER(TRIM({column}))
        FROM products
        WHERE NULLIF(TRIM({column}), '') IS NOT NULL
        ORDER BY id {direction}
        LIMIT %s
        """,
        (count,),
    )
    values = [row[0] for row in cur.fetchall()]
    while values and len(values) < count:
        values.extend(values[: min(len(values), count - len(values))])
    return values[:count]


class RecordingCursor:
    def __init__(self, cursor, recorder):
        self._cursor = cursor
        self._recorder = recorder

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._cursor.close()

    def execute(self, query, params=None):
        if "WITH input AS" in query and "FROM products p" in query:
            self._recorder.append((query, params or ()))
        return self._cursor.execute(query, params)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()


class RecordingConnection:
    def __init__(self, dsn, recorder):
        self._conn = psycopg2.connect(dsn)
        self._recorder = recorder

    def cursor(self, *args, **kwargs):
        return RecordingCursor(self._conn.cursor(*args, **kwargs), self._recorder)

    def close(self):
        self._conn.close()


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class BatchQueryRegressionTests(unittest.TestCase):
    CODE_MAIN = "CURSOR_BATCH_CODE"
    CODE_NOCAS = "CURSOR_BATCH_NOCAS"
    CAS_MAIN = "CURSOR-BATCH-CAS"
    CODE_TEAM = "CURSOR_TEAM_CODE"
    CAS_TEAM = "CURSOR-TEAM-CAS"
    CAS_DENY = "CURSOR-DENY-CAS"
    BRAND_ALLOW = "CURSOR_BATCH_ALLOW"
    BRAND_DENY = "CURSOR_BATCH_DENY"
    TEAM_NAME = "Cursor batch test team"

    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        # unittest does NOT call tearDownClass if setUpClass raises --
        # anything below that fails would otherwise leak this temp DB
        # forever, so clean up ourselves on any exception past this point.
        try:
            cls._env_patch = mock.patch.dict("os.environ", {"DATABASE_URL": cls.dsn})
            cls._env_patch.start()
            cls.conn = psycopg2.connect(_local_dsn())
            cls.conn.autocommit = True
            cls._reset_fixture()
            cls._seed_perf_index_prerequisites()
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

    def setUp(self):
        # Phase 5D2A: `enforce_session_validity` needs `user_id` + a
        # matching `auth_version`; stub the DB it checks with an in-memory
        # fake (never touches `products_local`/real Postgres) so this real
        # DB test class's business-logic queries -- recorded via
        # `RecordingConnection` -- stay isolated from the auth check.
        start_auth_db_patch(self)

    @classmethod
    def _seed_perf_index_prerequisites(cls):
        """`test_advanced_search_plan_uses_normalized_cas_index` and
        `test_batch_endpoint_plans_use_normalized_indexes_for_late_matches`
        assert the query PLANNER actually chooses
        `idx_products_code_upper_trim`/`idx_products_cas_upper_trim`
        (migrations 007/008) over a sequential scan. That's a genuine
        cost-based decision Postgres makes from real table statistics --
        on a table with only the handful of fixture rows this class
        inserts, a seq scan is always cheaper regardless of whether the
        index exists, so a temp DB needs BOTH the indexes AND enough rows
        for the index to actually win, or these two plan-shape assertions
        would be testing the temp DB's smallness rather than the app's
        indexing. Neither of `migration_007`/`migration_008` write CAS/code
        values that could collide with this class's own `CURSOR_*`-prefixed
        fixtures. `CREATE INDEX CONCURRENTLY` requires autocommit (already
        set above) and no surrounding explicit transaction.
        """
        with cls.conn.cursor() as cur:
            for name in (
                "migration_007_products_code_upper_trim_index.sql",
                "migration_008_check_cas_perf_indexes.sql",
            ):
                path = os.path.join(os.path.dirname(__file__), "..", "sql", name)
                apply_sql_file_statement_by_statement(cur, path)
            cur.execute(
                """
                INSERT INTO products (name, code, cas, brand, size, ship, price, note)
                SELECT 'Perf decoy ' || g, 'PERF-DECOY-CODE-' || g, 'PERF-DECOY-CAS-' || g,
                       'PERF_DECOY_BRAND', '1g', '1', '1000', ''
                FROM generate_series(1, 8000) AS g
                """
            )
            cur.execute("ANALYZE products")
            cur.execute("ANALYZE regulatory_rules")

    @classmethod
    def _cleanup_fixture(cls):
        with cls.conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM regulatory_rules
                WHERE UPPER(TRIM(match_value)) = ANY(%s)
                """,
                ([cls.CODE_MAIN, cls.CODE_NOCAS, cls.CAS_MAIN, cls.CODE_TEAM, cls.CAS_TEAM, cls.CAS_DENY],),
            )
            cur.execute(
                """
                DELETE FROM products
                WHERE UPPER(TRIM(code)) = ANY(%s)
                   OR UPPER(TRIM(cas)) = ANY(%s)
                   OR brand IN (%s, %s)
                """,
                (
                    [cls.CODE_MAIN, cls.CODE_NOCAS, cls.CODE_TEAM],
                    [cls.CAS_MAIN, cls.CAS_TEAM, cls.CAS_DENY],
                    cls.BRAND_ALLOW,
                    cls.BRAND_DENY,
                ),
            )
            cur.execute(
                "DELETE FROM brand_compliance_settings WHERE brand_norm = ANY(%s)",
                ([cls.BRAND_ALLOW.upper(), cls.BRAND_DENY.upper()],),
            )
            cur.execute("DELETE FROM teams WHERE name = %s", (cls.TEAM_NAME,))

    @classmethod
    def _reset_fixture(cls):
        cls._cleanup_fixture()
        with cls.conn.cursor() as cur:
            cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", (cls.TEAM_NAME,))
            cls.team_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO team_brands (team_id, brand) VALUES (%s, %s)",
                (cls.team_id, cls.BRAND_ALLOW),
            )
            cur.executemany(
                """
                INSERT INTO brand_compliance_settings (brand_norm, manual_compliance_priority)
                VALUES (%s, %s)
                ON CONFLICT (brand_norm)
                DO UPDATE SET manual_compliance_priority = EXCLUDED.manual_compliance_priority
                """,
                [
                    (cls.BRAND_ALLOW.upper(), True),
                    (cls.BRAND_DENY.upper(), False),
                ],
            )
            cur.executemany(
                """
                INSERT INTO products
                    (name, code, cas, brand, size, ship, price, note, manual_compliance, manual_compliance_note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        "Batch Early",
                        cls.CODE_MAIN,
                        cls.CAS_MAIN,
                        cls.BRAND_ALLOW,
                        "1g",
                        "2",
                        "1000",
                        "early product note",
                        "Được bán",
                        "manual note only",
                    ),
                    (
                        "Batch Later",
                        f" {cls.CODE_MAIN.lower()} ",
                        f" {cls.CAS_MAIN.lower()} ",
                        cls.BRAND_ALLOW,
                        "2g",
                        "3",
                        "1000",
                        "later",
                        "",
                        "ignored blank manual note",
                    ),
                    (
                        "No CAS Product",
                        cls.CODE_NOCAS,
                        "",
                        cls.BRAND_ALLOW,
                        "1g",
                        "1",
                        "1000",
                        "no cas product note",
                        "",
                        "",
                    ),
                    (
                        "Team Denied",
                        cls.CODE_TEAM,
                        cls.CAS_TEAM,
                        cls.BRAND_DENY,
                        "1g",
                        "1",
                        "1000",
                        "denied",
                        "Được bán",
                        "disabled manual note",
                    ),
                    ("Team Allowed", cls.CODE_TEAM, cls.CAS_TEAM, cls.BRAND_ALLOW, "1g", "1", "1000", "allowed", "", ""),
                    ("CAS Denied", "", cls.CAS_DENY, cls.BRAND_DENY, "1g", "1", "1000", "denied cas", "", ""),
                ],
            )
            cur.executemany(
                """
                INSERT INTO regulatory_rules
                    (rule_type, rule_label, match_field, match_value, priority, is_active, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    ("CAM_NHAP", "CẤM NHẬP", "code", cls.CODE_MAIN, 10, True, "code wins"),
                    ("PHU_LUC_II", "Phụ lục II", "cas", cls.CAS_MAIN, 20, True, "cas note"),
                    ("TON_KHO", "TỒN KHO", "cas", cls.CAS_TEAM, 30, True, "team cas"),
                    ("PHU_LUC_III", "Phụ lục III", "cas", cls.CAS_DENY, 40, True, "deny cas"),
                ],
            )

    def _call_endpoint(self, path, data, *, is_admin=True, team_id=None):
        recorder = []

        def _recording_connection():
            return RecordingConnection(_local_dsn(), recorder)

        with patch("search.get_connection", _recording_connection):
            search.app.testing = True
            with search.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["authenticated"] = True
                    sess["user_id"] = 1
                    sess["auth_version"] = 1
                    sess["is_admin"] = is_admin
                    if team_id is not None:
                        sess["team_id"] = team_id
                response = client.post(path, data=data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(recorder)
        return response.get_json(), recorder[-1]

    def _plan_for_endpoint(self, path, data, **session_kwargs):
        _json, (query, params) = self._call_endpoint(path, data, **session_kwargs)
        with self.conn.cursor() as cur:
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + query, params)
            return "\n".join(row[0] for row in cur.fetchall())

    def _assert_no_position_dependent_product_scan(self, plan, index_name):
        self.assertIn(index_name, plan)
        self.assertNotIn("Seq Scan on products", plan)
        self.assertIsNone(
            re.search(r"Index Scan using products_pkey on products.*Rows Removed by Filter: [1-9]", plan, re.S),
        )

    def test_batch_endpoints_preserve_duplicates_missing_lowest_and_compliance(self):
        payload = f" {self.CODE_MAIN.lower()} \nNO_SUCH_CURSOR_BATCH\n{self.CODE_MAIN}"
        data, find_query = self._call_endpoint("/find_code_batch", {"codes": payload})
        rows = data["results"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["Name"], "Batch Early")
        self.assertEqual(rows[0]["Brand"], self.BRAND_ALLOW)
        self.assertEqual(rows[0]["Cas"], self.CAS_MAIN)
        self.assertEqual(rows[0]["Unit_Price"], "2,000")
        self.assertEqual(rows[0]["Compliance_Status"], "Được bán")
        self.assertEqual(rows[0]["Compliance_Note"], "manual note only")
        self.assertEqual(rows[0]["Compliance_Css"], "warning-duoc-ban")
        self.assertEqual(rows[0]["Compliance_Source"], "manual")
        self.assertEqual(rows[0]["note"], "early product note")
        self.assertEqual(rows[0]["compliance"], "Được bán")
        self.assertEqual(rows[0]["compliance_note"], "manual note only")
        self.assertEqual(rows[0]["compliance_css"], "warning-duoc-ban")
        self.assertEqual(rows[0]["compliance_source"], "manual")
        self.assertEqual(rows[1]["Code"], "NO_SUCH_CURSOR_BATCH")
        self.assertEqual(rows[1]["Name"], "")
        self.assertEqual(rows[1]["Compliance_Status"], "Chưa xác định")
        self.assertEqual(rows[1]["Compliance_Source"], "unresolved")
        self.assertEqual(rows[2]["Name"], "Batch Early")
        self.assertIn("brand_compliance_settings", find_query[0])
        self.assertIn("NULLIF(TRIM(COALESCE(p.manual_compliance, '')), '') IS NOT NULL", find_query[0])

        payload = f" {self.CAS_MAIN.lower()} \nNO-SUCH-CURSOR-CAS\n{self.CAS_MAIN}"
        data, _query = self._call_endpoint("/check_cas_batch", {"cas": payload})
        rows = data["results"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["Cas"], self.CAS_MAIN.lower())
        self.assertEqual(rows[0]["Compliance_Status"], "Phụ lục II")
        self.assertEqual(rows[0]["Compliance_Note"], "cas note")
        self.assertEqual(rows[1]["Compliance_Status"], "")
        self.assertEqual(rows[2]["Compliance_Status"], "Phụ lục II")

    def test_find_code_missing_cas_uses_unresolved_precedence(self):
        data, _query = self._call_endpoint("/find_code_batch", {"codes": self.CODE_NOCAS})
        row = data["results"][0]
        self.assertEqual(row["Name"], "No CAS Product")
        self.assertEqual(row["Cas"], "")
        self.assertEqual(row["Compliance_Status"], "Chưa xác định")
        self.assertEqual(row["Compliance_Css"], "warning-chua-xac-dinh")
        self.assertEqual(row["Compliance_Source"], "unresolved")
        self.assertEqual(row["Note"], "no cas product note")
        self.assertEqual(row["Compliance_Note"], "")

    def test_batch_endpoints_preserve_team_visibility(self):
        data, _query = self._call_endpoint("/find_code_batch", {"codes": self.CODE_TEAM})
        self.assertEqual(data["results"][0]["Name"], "Team Denied")
        self.assertEqual(data["results"][0]["Brand"], self.BRAND_DENY)
        self.assertEqual(data["results"][0]["Compliance_Status"], "TỒN KHO")
        self.assertEqual(data["results"][0]["Compliance_Source"], "legacy")

        data, _query = self._call_endpoint(
            "/find_code_batch",
            {"codes": self.CODE_TEAM},
            is_admin=False,
            team_id=self.team_id,
        )
        self.assertEqual(data["results"][0]["Name"], "Team Allowed")
        self.assertEqual(data["results"][0]["Brand"], self.BRAND_ALLOW)

        data, _query = self._call_endpoint(
            "/check_cas_batch",
            {"cas": f"{self.CAS_TEAM}\n{self.CAS_DENY}"},
            is_admin=False,
            team_id=self.team_id,
        )
        self.assertEqual(data["results"][0]["Compliance_Status"], "TỒN KHO")
        self.assertEqual(data["results"][1]["Compliance_Status"], "")

    def test_batch_endpoint_plans_use_normalized_indexes_for_late_matches(self):
        with self.conn.cursor() as cur:
            codes = _sample_values(cur, "code", 100, direction="DESC")
            cas_values = _sample_values(cur, "cas", 100, direction="DESC")

        code_plan = self._plan_for_endpoint("/find_code_batch", {"codes": "\n".join(codes)})
        self._assert_no_position_dependent_product_scan(code_plan, "idx_products_code_upper_trim")

        cas_plan = self._plan_for_endpoint("/check_cas_batch", {"cas": "\n".join(cas_values)})
        self._assert_no_position_dependent_product_scan(cas_plan, "idx_products_cas_upper_trim")

    def test_advanced_search_preserves_duplicates_missing_multimatch_and_compliance(self):
        payload = f" {self.CAS_MAIN.lower()} \nNO-SUCH-ADVANCED-CAS\n{self.CAS_MAIN}"
        data, adv_query = self._call_endpoint("/advanced_search", {"cas": payload})
        rows = data["results"]

        self.assertEqual(data["total_cas"], 3)
        self.assertEqual(data["matched_cas"], 2)
        self.assertEqual(len(rows), 5)
        self.assertEqual([row["Name"] for row in rows], ["Batch Early", "Batch Later", "", "Batch Early", "Batch Later"])
        self.assertEqual(rows[0]["Compliance_Status"], "Được bán")
        self.assertEqual(rows[0]["Compliance_Note"], "manual note only")
        self.assertEqual(rows[0]["Compliance_Css"], "warning-duoc-ban")
        self.assertEqual(rows[0]["Compliance_Source"], "manual")
        self.assertEqual(rows[0]["Note"], "early product note")
        self.assertEqual(rows[0]["compliance"], "Được bán")
        self.assertEqual(rows[0]["compliance_note"], "manual note only")
        self.assertEqual(rows[1]["Compliance_Status"], "CẤM NHẬP")
        self.assertEqual(rows[1]["Compliance_Note"], "code wins")
        self.assertEqual(rows[1]["Compliance_Source"], "legacy")
        self.assertEqual(rows[2]["Cas"], "NO-SUCH-ADVANCED-CAS")
        self.assertEqual(rows[2]["Compliance_Status"], "Không phát hiện hạn chế")
        self.assertEqual(rows[2]["Compliance_Source"], "unresolved")
        self.assertIn("brand_compliance_settings", adv_query[0])
        self.assertIn("NULLIF(TRIM(COALESCE(p.manual_compliance, '')), '') IS NOT NULL", adv_query[0])

    def test_advanced_search_filters_and_placeholder_behavior(self):
        data, _query = self._call_endpoint(
            "/advanced_search",
            {"cas": self.CAS_MAIN, "brand": self.BRAND_ALLOW.lower()},
        )
        self.assertEqual([row["Name"] for row in data["results"]], ["Batch Early", "Batch Later"])

        data, _query = self._call_endpoint(
            "/advanced_search",
            {"cas": self.CAS_MAIN, "brand": "NO_SUCH_ADVANCED_BRAND"},
        )
        self.assertEqual(data["matched_cas"], 0)
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["Cas"], self.CAS_MAIN)
        self.assertEqual(data["results"][0]["Name"], "")
        self.assertEqual(data["results"][0]["Compliance_Status"], "Không phát hiện hạn chế")

        data, _query = self._call_endpoint("/advanced_search", {"cas": self.CAS_MAIN, "size": "2g"})
        self.assertEqual([row["Name"] for row in data["results"]], ["Batch Later"])
        self.assertEqual(data["results"][0]["Unit_Price"], "3,000")

        data, _query = self._call_endpoint("/advanced_search", {"cas": self.CAS_MAIN, "size": "999g"})
        self.assertEqual(data["matched_cas"], 0)
        self.assertEqual(data["results"][0]["Cas"], self.CAS_MAIN)
        self.assertEqual(data["results"][0]["Name"], "")
        self.assertEqual(data["results"][0]["Compliance_Status"], "Không phát hiện hạn chế")

        data, _query = self._call_endpoint(
            "/advanced_search",
            {"cas": self.CAS_MAIN, "size": "1.05g", "size_fuzzy": "1"},
        )
        self.assertEqual([row["Name"] for row in data["results"]], ["Batch Early"])

    def test_advanced_search_preserves_team_visibility(self):
        data, _query = self._call_endpoint(
            "/advanced_search",
            {"cas": self.CAS_TEAM},
            is_admin=False,
            team_id=self.team_id,
        )
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["Name"], "Team Allowed")
        self.assertEqual(data["results"][0]["Brand"], self.BRAND_ALLOW)
        self.assertEqual(data["results"][0]["Compliance_Status"], "TỒN KHO")

    def test_advanced_search_plan_uses_normalized_cas_index(self):
        with self.conn.cursor() as cur:
            cas_values = _sample_values(cur, "cas", 100, direction="DESC")

        plan = self._plan_for_endpoint("/advanced_search", {"cas": "\n".join(cas_values)})
        self._assert_no_position_dependent_product_scan(plan, "idx_products_cas_upper_trim")


if __name__ == "__main__":
    unittest.main()
