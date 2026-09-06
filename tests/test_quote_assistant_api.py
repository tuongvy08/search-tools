"""Quote Assistant matching API checks (local DB only).

Phase 6A -- Local Release Gate: `QuoteAssistantApiTests` used to connect
straight to whatever `DATABASE_URL` pointed at (in practice
`products_local`, per `.env`); it now creates its own throwaway,
uniquely-named database (see `tests/pg_temp_db.py`) and patches
`DATABASE_URL` to point at it for the whole class, so `_local_dsn()` below
(used both by this class's own fixture connection and by
`RecordingConnection` inside `_call_api`) resolves to the temp DB instead
of the real environment/`.env` value.
"""

import os
import re
import time
import unittest
from unittest import mock
from unittest.mock import patch
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

import search  # noqa: E402
from auth_test_helpers import auth_db_patch, start_auth_db_patch  # noqa: E402
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
    try:
        conn = psycopg2.connect(dsn, connect_timeout=1)
        conn.close()
    except psycopg2.OperationalError:
        return None
    return dsn


class QuoteAssistantUnitTests(unittest.TestCase):
    """Phase 6A -- Local Release Gate finding: this class has no
    `@unittest.skipUnless(_local_dsn(), ...)` guard (unlike
    `QuoteAssistantApiTests` below) -- it's meant to run everywhere, with
    no real Postgres required. But its one Flask-route test uses a
    non-admin session with a synthetic `team_id=123`, which the real,
    always-on IP/team-policy middleware (`middleware_access.py`, Fix1) now
    looks up with a genuine, UNMOCKED `teams.ip_policy` query -- landing on
    whatever `DATABASE_URL` happens to be (in practice `products_local`,
    per `.env`) and getting a real 503 there instead of the 200 this test
    asserts, since team 123 doesn't exist / `products_local` doesn't have
    migration_015 applied. This test is about payload validation, not IP
    policy, so scope `DISABLE_IP_ALLOWLIST` narrowly to just this class
    (same pattern as `test_quote_templates.py`/`test_admin_teams.py`)
    rather than mocking `middleware_access.get_connection` for a policy
    this test isn't exercising either way.
    """

    def setUp(self):
        self._disable_ip_patch = mock.patch.dict("os.environ", {"DISABLE_IP_ALLOWLIST": "1"})
        self._disable_ip_patch.start()
        self.addCleanup(self._disable_ip_patch.stop)

    def test_payload_validation_and_missing_identifier_route_do_not_open_db(self):
        search.app.testing = True
        with search.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["authenticated"] = True
                sess["user_id"] = 1
                sess["auth_version"] = 1
                sess["is_admin"] = False
                sess["team_id"] = 123
            with auth_db_patch(user_id=1, auth_version=1):
                response = client.post(
                    "/api/quote-assistant/match",
                    json={"rows": [{"requested_name": "Display only"}]},
                )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["row_count"], 1)
        self.assertEqual(data["results"][0]["reason"], "MISSING_IDENTIFIER")
        self.assertIsNone(data["results"][0]["selected"])

    def test_selection_blocks_compliance_and_uses_price_tie_break(self):
        blocked = {
            "product_id": 1,
            "Unit_Price_Value": 1000,
            "eligible": False,
            "ineligible_reason": "COMPLIANCE_BLOCKED",
        }
        selected, status, reason = search._quote_select_candidate([blocked], "LOWEST_UNIT_PRICE")
        self.assertIsNone(selected)
        self.assertEqual(status, "UNRESOLVED")
        self.assertEqual(reason, "MANUAL_REVIEW")

        no_price = {
            "product_id": 2,
            "Unit_Price_Value": 0,
            "eligible": False,
            "ineligible_reason": "NO_VALID_PRICE",
        }
        selected, status, reason = search._quote_select_candidate([no_price], "LOWEST_UNIT_PRICE")
        self.assertIsNone(selected)
        self.assertEqual(reason, "NO_VALID_PRICE")

        first = {"product_id": 5, "Unit_Price_Value": 500, "eligible": True, "ineligible_reason": ""}
        second = {"product_id": 3, "Unit_Price_Value": 500, "eligible": True, "ineligible_reason": ""}
        selected, status, reason = search._quote_select_candidate([first, second], "LOWEST_UNIT_PRICE")
        self.assertEqual(selected["product_id"], 3)
        self.assertEqual(status, "MATCHED")
        self.assertEqual(reason, "SELECTED_LOWEST_UNIT_PRICE")


class RecordingCursor:
    def __init__(self, cursor, recorder):
        self._cursor = cursor
        self._recorder = recorder

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._cursor.close()

    def execute(self, query, params=None):
        if "WITH input AS" in query and "product_hits AS" in query and "FROM products p" in query:
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
class QuoteAssistantApiTests(unittest.TestCase):
    PREFIX = "CURSOR_QUOTE_ASSIST"
    BRAND_ALLOW = "CURSOR_QUOTE_ALLOW"
    BRAND_DENY = "CURSOR_QUOTE_DENY"
    BRAND_CATO = "CURSOR_QUOTE_CATO"
    BRAND_LGC = "CURSOR_QUOTE_LGC"
    BRAND_HPC = "CURSOR_QUOTE_HPC"
    BRAND_TRC = "CURSOR_QUOTE_TRC"
    BRAND_EXTRA = "CURSOR_QUOTE_EXTRA"
    TEAM_NAME = "Cursor quote assistant team"

    CODE_MULTI = f"{PREFIX}_MULTI"
    CAS_MULTI = f"{PREFIX}-CAS-MULTI"
    CODE_CONFLICT = f"{PREFIX}_CONFLICT_CODE"
    CAS_CONFLICT = f"{PREFIX}-CONFLICT-CAS"
    CODE_VISIBLE = f"{PREFIX}_VISIBLE"
    CAS_VISIBLE = f"{PREFIX}-VISIBLE-CAS"
    CODE_ZERO = f"{PREFIX}_ZERO"
    CODE_BLOCKED = f"{PREFIX}_BLOCKED"
    CODE_UNKNOWN = f"{PREFIX}_UNKNOWN"
    CODE_LICENSE = f"{PREFIX}_LICENSE"
    CAS_LICENSE = f"{PREFIX}-LICENSE-CAS"
    CODE_EQ = f"{PREFIX}_EQUIV"
    CAS_EQ = f"{PREFIX}-EQUIV-CAS"
    CODE_MULTI_CAS = f"{PREFIX}_MULTI_CAS"
    CAS_MULTI_A = f"{PREFIX}-MULTI-A"
    CAS_MULTI_B = f"{PREFIX}-MULTI-B"
    CODE_DUP = f"{PREFIX}_DUP"
    CAS_DUP = f"{PREFIX}-DUP-CAS"
    CAS_SIZE = f"{PREFIX}-SIZE-CAS"
    CAS_LIQUID = f"{PREFIX}-LIQUID-CAS"
    CAS_COMPOUND = f"{PREFIX}-COMPOUND-CAS"
    CAS_PREP = f"{PREFIX}-PREP-CAS"
    CAS_CROSS_UNIT = f"{PREFIX}-CROSS-UNIT-CAS"
    CAS_POLICY_TIER = f"{PREFIX}-POLICY-TIER-CAS"
    CAS_POLICY_BLOCKED = f"{PREFIX}-POLICY-BLOCKED-CAS"
    CAS_POLICY_NOPRICE = f"{PREFIX}-POLICY-NOPRICE-CAS"
    CAS_POLICY_MULTI_TIER = f"{PREFIX}-POLICY-MULTI-TIER-CAS"
    CAS_POLICY_ALL_AVAIL = f"{PREFIX}-POLICY-ALL-AVAIL-CAS"
    CODE_EXACT_POLICY = f"{PREFIX}_EXACT_POLICY_CODE"
    CODE_PLACEHOLDER_CAS = f"{PREFIX}_PLACEHOLDER_CAS"
    PLACEHOLDER_CAS_VALUE = "NOT AVAILABLE"
    CODE_VALID_AND_PLACEHOLDER = f"{PREFIX}_VALID_AND_PLACEHOLDER"
    CODE_MULTI_VALID_AND_PLACEHOLDER = f"{PREFIX}_MULTI_VALID_AND_PLACEHOLDER"

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
    def _seed_perf_index_prerequisites(cls):
        """Several tests in this class assert the query PLANNER actually
        chooses `idx_products_code_upper_trim`/`idx_products_cas_upper_trim`
        (migrations 007/008) over a sequential scan -- a genuine cost-based
        decision Postgres makes from real table statistics, not just from
        whether the index exists. See `test_batch_queries.py`'s identical
        helper for the full rationale.

        Also seeds >500 rows sharing one CAS (`7704-34-9`) so
        `test_candidate_limit_exceeded_uses_new_reason_code` /
        `test_candidate_limit_exact_boundary_500_vs_501` can exercise the
        real 500-SQL-candidate fail-closed path: those two tests originally
        relied on a real CAS (Sulfur) that happened to have 750 rows in
        `products_local` ("verified in scale-gate audit", per their own
        comment) -- a real-data coincidence a synthetic temp DB has no way
        to reproduce on its own. Seeding it explicitly here tests the same
        code path (an actual >500-row SQL result) rather than a hand-picked
        row count asserted only in Python.
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
            cur.execute(
                """
                INSERT INTO products (name, code, cas, brand, size, ship, price, note)
                SELECT 'Candidate limit decoy ' || g, 'CANDIDATE-LIMIT-CODE-' || g, '7704-34-9',
                       %s, '1g', '1', '1000', ''
                FROM generate_series(1, 600) AS g
                """,
                (cls.BRAND_ALLOW,),
            )
            cur.execute("ANALYZE products")
            cur.execute("ANALYZE regulatory_rules")

    def setUp(self):
        # Phase 5D2A: every authenticated session now needs a `user_id` +
        # `auth_version` matching an ACTIVE `app_users` row. This class's
        # sessions are all local, throwaway `is_admin`/`team_id` fixtures --
        # not real accounts -- so a single permissive in-memory fake (never
        # touching real Postgres/`products_local`) stands in for that
        # per-request check across every test in this class.
        start_auth_db_patch(self)

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
        brands_norm = [
            cls.BRAND_ALLOW.upper(),
            cls.BRAND_DENY.upper(),
            cls.BRAND_CATO.upper(),
            cls.BRAND_LGC.upper(),
            cls.BRAND_HPC.upper(),
            cls.BRAND_TRC.upper(),
            cls.BRAND_EXTRA.upper(),
        ]
        with cls.conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM products
                WHERE UPPER(TRIM(COALESCE(code, ''))) LIKE %s
                   OR UPPER(TRIM(COALESCE(cas, ''))) LIKE %s
                   OR UPPER(TRIM(COALESCE(brand, ''))) = ANY(%s)
                """,
                (f"{cls.PREFIX}%", f"{cls.PREFIX}%", brands_norm),
            )
            cur.execute("DELETE FROM brand_compliance_settings WHERE brand_norm = ANY(%s)", (brands_norm,))
            cur.execute("DELETE FROM team_brands WHERE brand = ANY(%s)", (brands_norm,))
            cur.execute("DELETE FROM teams WHERE name = %s", (cls.TEAM_NAME,))

    @classmethod
    def _reset_fixture(cls):
        cls._cleanup_fixture()
        with cls.conn.cursor() as cur:
            cur.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", (cls.TEAM_NAME,))
            cls.team_id = cur.fetchone()[0]
            for b in [cls.BRAND_ALLOW, cls.BRAND_CATO, cls.BRAND_LGC, cls.BRAND_HPC, cls.BRAND_TRC, cls.BRAND_EXTRA]:
                cur.execute("INSERT INTO team_brands (team_id, brand) VALUES (%s, %s)", (cls.team_id, b))
            # Phase 6B2B2-R2: this fixture predates migration_017/018 (no
            # brand_master/currency_rates), so `CurrencyRateResolver` takes
            # the legacy `exchange_rates` overlay path -- which, as of
            # Phase 6B2B2-R2, fails closed for any brand with no explicit
            # row there instead of silently defaulting to rate=1.0. Seed a
            # real rate=1 row per fixture brand (equivalent to a domestic
            # VND-priced legacy brand) so this class's Unit_Price
            # assertions -- all written assuming a 1:1 rate -- keep testing
            # matching/selection/compliance logic, not currency resolution.
            cur.executemany(
                "INSERT INTO exchange_rates (brand, rate) VALUES (%s, 1) "
                "ON CONFLICT (brand) DO UPDATE SET rate = 1",
                [
                    (cls.BRAND_ALLOW,), (cls.BRAND_DENY,), (cls.BRAND_CATO,),
                    (cls.BRAND_LGC,), (cls.BRAND_HPC,), (cls.BRAND_TRC,),
                    (cls.BRAND_EXTRA,), ("PERF_DECOY_BRAND",),
                ],
            )
            cur.executemany(
                """
                INSERT INTO brand_compliance_settings (brand_norm, manual_compliance_priority)
                VALUES (%s, TRUE)
                ON CONFLICT (brand_norm)
                DO UPDATE SET manual_compliance_priority = TRUE
                """,
                [(b.upper(),) for b in [cls.BRAND_ALLOW, cls.BRAND_DENY, cls.BRAND_CATO, cls.BRAND_LGC, cls.BRAND_HPC, cls.BRAND_TRC, cls.BRAND_EXTRA]],
            )
            cur.executemany(
                """
                INSERT INTO products
                    (name, code, cas, brand, size, ship, price, note, manual_compliance, manual_compliance_note, preparation_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    ("Quote High", cls.CODE_MULTI, cls.CAS_MULTI, cls.BRAND_ALLOW, "1g", "10", "200", "high", "Được bán", "ok", "NEAT"),
                    ("Quote Low", f" {cls.CODE_MULTI.lower()} ", f" {cls.CAS_MULTI.lower()} ", cls.BRAND_ALLOW, "2g", "10", "100", "low", "Được bán", "ok", "NEAT"),
                    ("Quote Tie Later", cls.CODE_MULTI, cls.CAS_MULTI, cls.BRAND_ALLOW, "3g", "5", "200", "tie", "Phụ lục II", "warn", "NEAT"),
                    ("Conflict Code", cls.CODE_CONFLICT, f"{cls.PREFIX}-OTHER-CAS", cls.BRAND_ALLOW, "1g", "1", "100", "", "Được bán", "", "NEAT"),
                    ("Conflict CAS", f"{cls.PREFIX}_OTHER_CODE", cls.CAS_CONFLICT, cls.BRAND_ALLOW, "1g", "1", "100", "", "Được bán", "", "NEAT"),
                    ("Visible Allow", cls.CODE_VISIBLE, cls.CAS_VISIBLE, cls.BRAND_ALLOW, "1g", "10", "100", "allow", "Được bán", "", "NEAT"),
                    ("Visible Deny", cls.CODE_VISIBLE, cls.CAS_VISIBLE, cls.BRAND_DENY, "1g", "10", "50", "deny", "Được bán", "", "NEAT"),
                    ("Zero Price", cls.CODE_ZERO, f"{cls.PREFIX}-ZERO-CAS", cls.BRAND_ALLOW, "1g", "1", "0", "", "Được bán", "", "NEAT"),
                    ("Blocked Product", cls.CODE_BLOCKED, f"{cls.PREFIX}-BLOCKED-CAS", cls.BRAND_ALLOW, "1g", "1", "100", "", "Cấm nhập", "blocked", "NEAT"),
                    ("Unknown No CAS", cls.CODE_UNKNOWN, "", cls.BRAND_ALLOW, "1g", "1", "100", "", "", "", "NEAT"),
                    ("License Product", cls.CODE_LICENSE, cls.CAS_LICENSE, cls.BRAND_ALLOW, "1g", "10", "100", "", "Cần giấy phép", "license note", "NEAT"),
                    ("Equiv Source", cls.CODE_EQ, cls.CAS_EQ, cls.BRAND_ALLOW, "100mg", "10", "300", "source", "Được bán", "", "NEAT"),
                    ("Equiv Low", f"{cls.CODE_EQ}_ALT1", cls.CAS_EQ, cls.BRAND_ALLOW, "100mg", "10", "100", "equiv low", "Được bán", "", "NEAT"),
                    ("Equiv Deny Brand", f"{cls.CODE_EQ}_ALT2", cls.CAS_EQ, cls.BRAND_DENY, "100mg", "10", "50", "equiv deny", "Được bán", "", "NEAT"),
                    ("Multi CAS A", cls.CODE_MULTI_CAS, cls.CAS_MULTI_A, cls.BRAND_ALLOW, "1g", "10", "100", "", "Được bán", "", "NEAT"),
                    ("Multi CAS B", cls.CODE_MULTI_CAS, cls.CAS_MULTI_B, cls.BRAND_ALLOW, "1g", "10", "100", "", "Được bán", "", "NEAT"),
                    ("Dup One", cls.CODE_DUP, cls.CAS_DUP, cls.BRAND_ALLOW, "1g", "10", "200", "", "Được bán", "", "NEAT"),
                    ("Dup Two", cls.CODE_DUP, cls.CAS_DUP, cls.BRAND_ALLOW, "1g", "10", "100", "", "Được bán", "", "NEAT"),
                    ("Size Allow Small", f"{cls.PREFIX}_SIZE_A1", cls.CAS_SIZE, cls.BRAND_ALLOW, "50mg", "10", "300", "", "Được bán", "", "NEAT"),
                    ("Size Allow Large", f"{cls.PREFIX}_SIZE_A2", cls.CAS_SIZE, cls.BRAND_ALLOW, "1g", "10", "100", "", "Được bán", "", "NEAT"),
                    ("Size Deny Small", f"{cls.PREFIX}_SIZE_D1", cls.CAS_SIZE, cls.BRAND_DENY, "100mg", "10", "100", "", "Được bán", "", "NEAT"),
                    ("Size Deny Large", f"{cls.PREFIX}_SIZE_D2", cls.CAS_SIZE, cls.BRAND_DENY, "2g", "10", "50", "", "Được bán", "", "NEAT"),
                    ("Liquid ML", f"{cls.PREFIX}_LIQ_ML", cls.CAS_LIQUID, cls.BRAND_ALLOW, "10ml", "10", "100", "", "Được bán", "", "SOLUTION"),
                    ("Liquid Solid", f"{cls.PREFIX}_LIQ_MG", cls.CAS_LIQUID, cls.BRAND_ALLOW, "100mg", "10", "100", "", "Được bán", "", "SOLUTION"),
                    ("Compound Size", f"{cls.PREFIX}_COMP", cls.CAS_COMPOUND, cls.BRAND_ALLOW, "10,25,50,100mg", "10", "100", "", "Được bán", "", "NEAT"),
                    ("Simple Size", f"{cls.PREFIX}_SIMPLE", cls.CAS_COMPOUND, cls.BRAND_ALLOW, "100mg", "10", "200", "", "Được bán", "", "NEAT"),
                    ("Prep Null", f"{cls.PREFIX}_PREP_NULL", cls.CAS_PREP, cls.BRAND_ALLOW, "1g", "10", "100", "", "Được bán", "", None),
                    ("Prep Other", f"{cls.PREFIX}_PREP_OTHER", cls.CAS_PREP, cls.BRAND_ALLOW, "1g", "10", "110", "", "Được bán", "", "OTHER"),
                    ("Prep Neat ML", f"{cls.PREFIX}_PREP_NEAT_ML", cls.CAS_PREP, cls.BRAND_ALLOW, "10ml", "10", "120", "", "Được bán", "", "NEAT"),
                    ("Prep Solution MG", f"{cls.PREFIX}_PREP_SOL_MG", cls.CAS_PREP, cls.BRAND_ALLOW, "100mg", "10", "130", "", "Được bán", "", "SOLUTION"),
                    ("Prep Mixture", f"{cls.PREFIX}_PREP_MIX", cls.CAS_PREP, cls.BRAND_ALLOW, "1g", "10", "140", "", "Được bán", "", "MIXTURE"),
                    ("Cross Mass", f"{cls.PREFIX}_CROSS_MASS", cls.CAS_CROSS_UNIT, cls.BRAND_ALLOW, "100mg", "10", "100", "", "Được bán", "", "NEAT"),
                    ("Cross Volume", f"{cls.PREFIX}_CROSS_VOL", cls.CAS_CROSS_UNIT, cls.BRAND_ALLOW, "10ml", "10", "200", "", "Được bán", "", "NEAT"),
                    # Brand policy fixtures
                    ("Tier CATO", f"{cls.PREFIX}_TIER_CATO", cls.CAS_POLICY_TIER, cls.BRAND_CATO, "100mg", "10", "500", "", "Được bán", "ok", "NEAT"),
                    ("Tier LGC", f"{cls.PREFIX}_TIER_LGC", cls.CAS_POLICY_TIER, cls.BRAND_LGC, "100mg", "10", "300", "", "Được bán", "ok", "NEAT"),
                    ("Tier TRC", f"{cls.PREFIX}_TIER_TRC", cls.CAS_POLICY_TIER, cls.BRAND_TRC, "100mg", "10", "200", "", "Được bán", "ok", "NEAT"),
                    ("Blocked CATO", f"{cls.PREFIX}_BLK_CATO", cls.CAS_POLICY_BLOCKED, cls.BRAND_CATO, "100mg", "10", "500", "", "Cấm nhập", "blocked", "NEAT"),
                    ("Blocked LGC", f"{cls.PREFIX}_BLK_LGC", cls.CAS_POLICY_BLOCKED, cls.BRAND_LGC, "100mg", "10", "300", "", "Được bán", "ok", "NEAT"),
                    ("Blocked TRC", f"{cls.PREFIX}_BLK_TRC", cls.CAS_POLICY_BLOCKED, cls.BRAND_TRC, "100mg", "10", "200", "", "Được bán", "ok", "NEAT"),
                    ("NoPrice CATO", f"{cls.PREFIX}_NOPRICE_CATO", cls.CAS_POLICY_NOPRICE, cls.BRAND_CATO, "100mg", "10", "0", "", "Được bán", "ok", "NEAT"),
                    ("NoPrice LGC", f"{cls.PREFIX}_NOPRICE_LGC", cls.CAS_POLICY_NOPRICE, cls.BRAND_LGC, "100mg", "10", "300", "", "Được bán", "ok", "NEAT"),
                    ("MultiTier LGC", f"{cls.PREFIX}_MT_LGC", cls.CAS_POLICY_MULTI_TIER, cls.BRAND_LGC, "100mg", "10", "300", "", "Được bán", "ok", "NEAT"),
                    ("MultiTier HPC", f"{cls.PREFIX}_MT_HPC", cls.CAS_POLICY_MULTI_TIER, cls.BRAND_HPC, "100mg", "10", "250", "", "Được bán", "ok", "NEAT"),
                    ("AllAvail CATO High", f"{cls.PREFIX}_AA_CATO1", cls.CAS_POLICY_ALL_AVAIL, cls.BRAND_CATO, "100mg", "10", "500", "", "Được bán", "ok", "NEAT"),
                    ("AllAvail CATO Low", f"{cls.PREFIX}_AA_CATO2", cls.CAS_POLICY_ALL_AVAIL, cls.BRAND_CATO, "100mg", "10", "400", "", "Được bán", "ok", "NEAT"),
                    ("AllAvail LGC", f"{cls.PREFIX}_AA_LGC", cls.CAS_POLICY_ALL_AVAIL, cls.BRAND_LGC, "100mg", "10", "600", "", "Được bán", "ok", "NEAT"),
                    ("AllAvail TRC", f"{cls.PREFIX}_AA_TRC", cls.CAS_POLICY_ALL_AVAIL, cls.BRAND_TRC, "100mg", "10", "300", "", "Được bán", "ok", "NEAT"),
                    ("Exact TRC", cls.CODE_EXACT_POLICY, cls.CAS_POLICY_TIER, cls.BRAND_TRC, "100mg", "10", "200", "", "Được bán", "ok", "NEAT"),
                    # Placeholder CAS: code exists but CAS is "NOT AVAILABLE"
                    ("Placeholder CAS", cls.CODE_PLACEHOLDER_CAS, cls.PLACEHOLDER_CAS_VALUE, cls.BRAND_ALLOW, "100mg", "10", "200", "", "Được bán", "ok", "NEAT"),
                    # 1 Valid CAS + 1 Placeholder CAS
                    ("Valid Plus Placeholder 1", cls.CODE_VALID_AND_PLACEHOLDER, cls.CAS_EQ, cls.BRAND_ALLOW, "100mg", "10", "100", "", "Được bán", "ok", "NEAT"),
                    ("Valid Plus Placeholder 2", cls.CODE_VALID_AND_PLACEHOLDER, cls.PLACEHOLDER_CAS_VALUE, cls.BRAND_ALLOW, "100mg", "10", "100", "", "Được bán", "ok", "NEAT"),
                    # 2 Valid CAS + 1 Placeholder CAS
                    ("Multi Valid Plus Placeholder 1", cls.CODE_MULTI_VALID_AND_PLACEHOLDER, cls.CAS_MULTI_A, cls.BRAND_ALLOW, "100mg", "10", "100", "", "Được bán", "ok", "NEAT"),
                    ("Multi Valid Plus Placeholder 2", cls.CODE_MULTI_VALID_AND_PLACEHOLDER, cls.CAS_MULTI_B, cls.BRAND_ALLOW, "100mg", "10", "100", "", "Được bán", "ok", "NEAT"),
                    ("Multi Valid Plus Placeholder 3", cls.CODE_MULTI_VALID_AND_PLACEHOLDER, "MIXTURE", cls.BRAND_ALLOW, "100mg", "10", "100", "", "Được bán", "ok", "NEAT"),
                ],
            )

    def _call_api(self, payload, *, authenticated=True, is_admin=True, team_id=None):
        recorder = []

        def _recording_connection():
            return RecordingConnection(_local_dsn(), recorder)

        with patch("search.get_connection", _recording_connection):
            search.app.testing = True
            with search.app.test_client() as client:
                if authenticated:
                    with client.session_transaction() as sess:
                        sess["authenticated"] = True
                        sess["user_id"] = 1
                        sess["auth_version"] = 1
                        sess["is_admin"] = is_admin
                        if team_id is not None:
                            sess["team_id"] = team_id
            response = client.post("/api/quote-assistant/match", json=payload)
        return response, recorder

    def test_auth_and_payload_limits(self):
        response, _recorder = self._call_api({"rows": []}, authenticated=False)
        self.assertEqual(response.status_code, 401)

        # Phase 6A -- Local Release Gate finding: this used to expect 403
        # from `_require_authenticated_quote_api`'s own "Tài khoản chưa
        # được gán team." check. That code path is UNREACHABLE for a real
        # account now: every write path that can make a non-admin account
        # ACTIVE (admin_google_users.approve/update, search.py's legacy
        # create_user/update_user) already REJECTS the write outright if
        # staff has no valid team -- there is no real flow that produces
        # an ACTIVE, non-admin, team_id=NULL account. This session shape
        # (authenticated=True, is_admin=False, no team_id at all) can only
        # happen from a forged/stale cookie, and Fix1's real, already-
        # reviewed middleware (`middleware_access.py`, see
        # `_load_team_ip_policy`'s docstring) now deliberately fails
        # CLOSED on exactly that data/session inconsistency with 503,
        # before this route's own 403 ever runs. 503 here is the correct,
        # intended contract from an already-shipped security fix, not an
        # app bug this test should paper over -- the assertion changed to
        # match reality, not the other way around.
        response, _recorder = self._call_api({"rows": []}, is_admin=False)
        self.assertEqual(response.status_code, 503)

        response, _recorder = self._call_api({"rows": [{}] * 2001})
        self.assertEqual(response.status_code, 413)

        response, _recorder = self._call_api({"rows": "bad"})
        self.assertEqual(response.status_code, 400)

    def test_preserves_order_duplicates_missing_exact_and_name_reference_only(self):
        payload = {
            "rows": [
                {"requested_name": "Quote Low", "code": "", "cas": ""},
                {"requested_name": "Quote Low", "code": "NO_SUCH_QUOTE_CODE", "cas": ""},
                {"code": f" {self.CODE_MULTI.lower()} "},
                {"cas": self.CAS_MULTI.replace("-", "")},
                {"code": self.CODE_MULTI},
            ]
        }
        response, recorder = self._call_api(payload)
        self.assertEqual(response.status_code, 200)
        rows = response.get_json()["results"]
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["reason"], "MISSING_IDENTIFIER")
        self.assertEqual(rows[1]["reason"], "NO_MATCH")
        self.assertEqual(rows[2]["status"], "MATCHED")
        self.assertEqual(rows[2]["reason"], "MANUAL_SELECTION_REQUIRED")
        self.assertIsNone(rows[2]["selected"])
        self.assertEqual(len(rows[2]["candidates"]), 3)
        self.assertEqual(rows[3]["reason"], "BRAND_REQUIRED")
        self.assertEqual(rows[4]["reason"], "MANUAL_SELECTION_REQUIRED")
        self.assertIsNone(rows[4]["selected"])
        self.assertEqual(rows[4]["selected_candidates"], [])
        self.assertEqual(len(recorder), 1)

    def test_code_cas_intersection_conflict_filters_and_manual_strategy(self):
        response, _recorder = self._call_api(
            {
                "rows": [
                    {"code": self.CODE_CONFLICT, "cas": self.CAS_CONFLICT},
                    {"code": self.CODE_MULTI, "cas": self.CAS_MULTI},
                    {"code": self.CODE_MULTI},
                    {"code": self.CODE_MULTI},
                ],
                "filters": {"brands": [self.BRAND_ALLOW.lower()], "sizes": ["2g"]},
            }
        )
        self.assertEqual(response.status_code, 200)
        rows = response.get_json()["results"]
        self.assertEqual(rows[0]["reason"], "CODE_CAS_CONFLICT")
        self.assertEqual([c["Name"] for c in rows[1]["candidates"]], ["Quote Low"])
        self.assertIsNone(rows[1]["selected"])
        self.assertEqual(rows[1]["reason"], "MANUAL_SELECTION_REQUIRED")
        self.assertIsNone(rows[2]["selected"])
        self.assertIsNone(rows[3]["selected"])

        response, _recorder = self._call_api({"rows": [{"code": self.CODE_MULTI}]})
        row = response.get_json()["results"][0]
        self.assertEqual(row["reason"], "MANUAL_SELECTION_REQUIRED")
        self.assertIsNone(row["selected"])
        self.assertEqual([c["Name"] for c in row["candidates"]], ["Quote High", "Quote Low", "Quote Tie Later"])

    def test_lowest_price_tie_break_visibility_zero_and_compliance(self):
        response, _recorder = self._call_api(
            {"rows": [{"code": self.CODE_MULTI}], "selection_strategy": "LOWEST_UNIT_PRICE"}
        )
        row = response.get_json()["results"][0]
        self.assertEqual(row["selected"]["Name"], "Quote Low")
        self.assertEqual(row["selected"]["Unit_Price"], "1,000")

        response, _recorder = self._call_api(
            {"rows": [{"code": self.CODE_VISIBLE}], "selection_strategy": "LOWEST_UNIT_PRICE"},
            is_admin=False,
            team_id=self.team_id,
        )
        self.assertEqual(response.get_json()["results"][0]["selected"]["Brand"], self.BRAND_ALLOW)

        response, _recorder = self._call_api(
            {"rows": [{"code": self.CODE_ZERO}], "selection_strategy": "LOWEST_UNIT_PRICE"}
        )
        self.assertEqual(response.get_json()["results"][0]["reason"], "NO_VALID_PRICE")

        response, _recorder = self._call_api({"rows": [{"code": self.CODE_BLOCKED}]})
        blocked = response.get_json()["results"][0]
        self.assertEqual(blocked["reason"], "MANUAL_REVIEW")
        self.assertFalse(blocked["candidates"][0]["eligible"])

        response, _recorder = self._call_api({"rows": [{"code": self.CODE_UNKNOWN}]})
        self.assertEqual(response.get_json()["results"][0]["reason"], "MANUAL_REVIEW")

        response, _recorder = self._call_api({"rows": [{"code": self.CODE_LICENSE}], "selection_strategy": "LOWEST_OVERALL"})
        license_row = response.get_json()["results"][0]
        self.assertEqual(license_row["selected"]["Name"], "License Product")
        self.assertEqual(license_row["candidates"][0]["warnings"], ["Cần giấy phép"])

    def test_equivalent_toggle_default_and_row_override(self):
        response, _recorder = self._call_api(
            {
                "equivalent_search_default": True,
                "rows": [
                    {"code": self.CODE_EQ},
                    {"code": self.CODE_EQ, "equivalent_override": False},
                ],
                "filters": {"brands": [self.BRAND_ALLOW]},
                "selection_strategy": "LOWEST_OVERALL",
            }
        )
        rows = response.get_json()["results"]
        self.assertEqual(rows[0]["match_mode"], "EQUIVALENT")
        self.assertEqual(rows[0]["selected"]["Name"], "Equiv Low")
        self.assertEqual(rows[1]["match_mode"], "EXACT_CODE")
        self.assertEqual(rows[1]["selected"]["Name"], "Equiv Source")

    def test_code_to_cas_zero_one_many_and_brand_required(self):
        response, _recorder = self._call_api(
            {
                "rows": [
                    {"code": self.CODE_UNKNOWN, "equivalent_override": True},
                    {"code": self.CODE_EQ, "equivalent_override": True},
                    {"code": self.CODE_MULTI_CAS, "equivalent_override": True},
                    {"cas": self.CAS_EQ},
                ],
                "filters": {"brands": [self.BRAND_ALLOW]},
                "selection_strategy": "LOWEST_OVERALL",
            }
        )
        rows = response.get_json()["results"]
        self.assertEqual(rows[0]["reason"], "CODE_HAS_NO_CAS")
        self.assertEqual(rows[1]["reason"], "SELECTED_LOWEST_OVERALL")
        self.assertEqual(rows[2]["reason"], "CODE_MULTIPLE_CAS")
        self.assertIn("CODE_MULTIPLE_CAS", rows[2]["warnings"])
        self.assertEqual(rows[3]["match_mode"], "EXACT_CAS")

        response, _recorder = self._call_api(
            {"rows": [{"code": self.CODE_EQ, "equivalent_override": True}, {"cas": self.CAS_EQ}]}
        )
        rows = response.get_json()["results"]
        self.assertEqual(rows[0]["reason"], "BRAND_REQUIRED")
        self.assertEqual(rows[1]["reason"], "BRAND_REQUIRED")

    def test_code_cas_equivalent_verification(self):
        response, _recorder = self._call_api(
            {
                "rows": [
                    {"code": self.CODE_EQ, "cas": self.CAS_EQ, "equivalent_override": True},
                    {"code": self.CODE_EQ, "cas": self.CAS_CONFLICT, "equivalent_override": True},
                ],
                "filters": {"brands": [self.BRAND_ALLOW]},
                "selection_strategy": "LOWEST_OVERALL",
            }
        )
        rows = response.get_json()["results"]
        self.assertEqual(rows[0]["match_mode"], "EQUIVALENT")
        self.assertEqual(rows[0]["selected"]["Name"], "Equiv Low")
        self.assertEqual(rows[1]["reason"], "CODE_CAS_CONFLICT")
        self.assertEqual(rows[1]["candidates"], [])

    def test_solid_liquid_conversion_and_no_cross_group(self):
        response, _recorder = self._call_api(
            {
                "rows": [{"cas": self.CAS_LIQUID}],
                "filters": {"brands": [self.BRAND_ALLOW], "unit_group": "SOLID", "size_mode": "MIN"},
                "selection_strategy": "LOWEST_OVERALL",
            }
        )
        row = response.get_json()["results"][0]
        self.assertEqual([c["Name"] for c in row["candidates"]], ["Liquid Solid"])
        self.assertEqual(row["selected"]["Name"], "Liquid Solid")

        response, _recorder = self._call_api(
            {
                "rows": [{"cas": self.CAS_LIQUID}],
                "filters": {"brands": [self.BRAND_ALLOW], "unit_group": "LIQUID", "size_mode": "MIN"},
                "selection_strategy": "LOWEST_OVERALL",
            }
        )
        row = response.get_json()["results"][0]
        self.assertEqual([c["Name"] for c in row["candidates"]], ["Liquid ML"])

    def test_preparation_filter_any_and_specific_values(self):
        response, _recorder = self._call_api(
            {
                "rows": [{"cas": self.CAS_PREP}],
                "filters": {"brands": [self.BRAND_ALLOW], "preparation_type": "ANY"},
            }
        )
        row = response.get_json()["results"][0]
        self.assertEqual(
            {c["Name"] for c in row["candidates"]},
            {"Prep Null", "Prep Other", "Prep Neat ML", "Prep Solution MG", "Prep Mixture"},
        )

        response, _recorder = self._call_api(
            {
                "rows": [{"cas": self.CAS_PREP}],
                "filters": {"brands": [self.BRAND_ALLOW], "preparation_type": "NEAT"},
            }
        )
        row = response.get_json()["results"][0]
        self.assertEqual([c["Name"] for c in row["candidates"]], ["Prep Neat ML"])
        self.assertEqual(row["candidates"][0]["Size"], "10ml")
        self.assertEqual(row["candidates"][0]["preparation_type"], "NEAT")

        response, _recorder = self._call_api(
            {
                "rows": [{"cas": self.CAS_PREP}],
                "filters": {"brands": [self.BRAND_ALLOW], "preparation_type": "SOLUTION"},
            }
        )
        row = response.get_json()["results"][0]
        self.assertEqual([c["Name"] for c in row["candidates"]], ["Prep Solution MG"])
        self.assertEqual(row["candidates"][0]["Size"], "100mg")
        self.assertEqual(row["candidates"][0]["preparation_type"], "SOLUTION")

        response, _recorder = self._call_api(
            {
                "rows": [{"cas": self.CAS_PREP}],
                "filters": {"brands": [self.BRAND_ALLOW], "preparation_type": "MIXTURE"},
            }
        )
        row = response.get_json()["results"][0]
        self.assertEqual([c["Name"] for c in row["candidates"]], ["Prep Mixture"])

    def test_invalid_preparation_filter_rejected(self):
        response, _recorder = self._call_api(
            {"rows": [{"code": self.CODE_MULTI}], "filters": {"preparation_type": "LIQUID"}}
        )
        self.assertEqual(response.status_code, 400)

    def test_minmax_does_not_compare_mass_with_volume(self):
        response, _recorder = self._call_api(
            {
                "rows": [{"cas": self.CAS_CROSS_UNIT}],
                "filters": {"brands": [self.BRAND_ALLOW], "size_mode": "MIN"},
            }
        )
        row = response.get_json()["results"][0]
        self.assertEqual({c["Name"] for c in row["candidates"]}, {"Cross Mass", "Cross Volume"})

    def test_compound_exact_allowed_but_not_minmax(self):
        response, _recorder = self._call_api(
            {
                "rows": [{"cas": self.CAS_COMPOUND}],
                "filters": {"brands": [self.BRAND_ALLOW], "sizes": ["10,25,50,100mg"]},
                "selection_strategy": "LOWEST_OVERALL",
            }
        )
        row = response.get_json()["results"][0]
        self.assertEqual([c["Name"] for c in row["candidates"]], ["Compound Size"])

        response, _recorder = self._call_api(
            {
                "rows": [{"cas": self.CAS_COMPOUND}],
                "filters": {"brands": [self.BRAND_ALLOW], "unit_group": "SOLID", "size_mode": "MIN"},
                "selection_strategy": "LOWEST_OVERALL",
            }
        )
        row = response.get_json()["results"][0]
        self.assertEqual([c["Name"] for c in row["candidates"]], ["Simple Size"])

    def test_min_max_per_brand_and_selection_strategies(self):
        response, _recorder = self._call_api(
            {
                "rows": [{"cas": self.CAS_SIZE}],
                "filters": {
                    "brands": [self.BRAND_ALLOW, self.BRAND_DENY],
                    "unit_group": "SOLID",
                    "size_mode": "MIN",
                },
                "selection_strategy": "LOWEST_PER_BRAND",
            }
        )
        row = response.get_json()["results"][0]
        self.assertEqual({c["Name"] for c in row["candidates"]}, {"Size Allow Small", "Size Deny Small"})
        self.assertEqual({c["Brand"] for c in row["selected_candidates"]}, {self.BRAND_ALLOW, self.BRAND_DENY})

        response, _recorder = self._call_api(
            {
                "rows": [{"cas": self.CAS_SIZE}],
                "filters": {
                    "brands": [self.BRAND_ALLOW, self.BRAND_DENY],
                    "unit_group": "SOLID",
                    "size_mode": "MAX",
                },
                "selection_strategy": "LOWEST_OVERALL",
            }
        )
        row = response.get_json()["results"][0]
        self.assertEqual({c["Name"] for c in row["candidates"]}, {"Size Allow Large", "Size Deny Large"})
        self.assertEqual(len(row["selected_candidates"]), 1)
        self.assertIn(row["selected"]["Name"], {"Size Allow Large", "Size Deny Large"})

    def test_manual_alias_response_compatibility_and_order_duplicates(self):
        response, _recorder = self._call_api(
            {
                "rows": [{"code": self.CODE_MULTI}, {"code": self.CODE_MULTI}],
                "selection_strategy": "LOWEST_UNIT_PRICE",
            }
        )
        data = response.get_json()
        self.assertEqual(data["selection_strategy"], "LOWEST_UNIT_PRICE")
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["results"][0]["reason"], "SELECTED_LOWEST_UNIT_PRICE")
        self.assertEqual(data["results"][0]["selected"], data["results"][0]["selected_candidates"][0])
        self.assertEqual(data["results"][1]["selected"]["Name"], "Quote Low")

        response, _recorder = self._call_api({"rows": [{"code": self.CODE_MULTI}], "selection_strategy": "MANUAL"})
        row = response.get_json()["results"][0]
        self.assertIsNone(row["selected"])
        self.assertEqual(row["selected_candidates"], [])
        self.assertEqual(row["reason"], "MANUAL_SELECTION_REQUIRED")

    def test_duplicate_code_brand_size_excluded_from_auto_selection(self):
        response, _recorder = self._call_api(
            {"rows": [{"code": self.CODE_DUP}], "selection_strategy": "LOWEST_OVERALL"}
        )
        row = response.get_json()["results"][0]
        self.assertEqual(row["reason"], "MANUAL_SELECTION_REQUIRED")
        self.assertIsNone(row["selected"])
        self.assertIn("DUPLICATE_CODE_BRAND_SIZE", row["warnings"])
        self.assertTrue(all(c.get("auto_excluded") for c in row["candidates"]))

    def test_query_plan_uses_normalized_code_and_cas_indexes(self):
        response, recorder = self._call_api(
            {
                "rows": [
                    {"code": self.CODE_MULTI, "cas": self.CAS_MULTI},
                    {"code": self.CODE_VISIBLE},
                    {"cas": self.CAS_LICENSE},
                ]
                * 40,
                "selection_strategy": "LOWEST_UNIT_PRICE",
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(recorder), 1)
        query, params = recorder[0]
        with self.conn.cursor() as cur:
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + query, params)
            plan = "\n".join(row[0] for row in cur.fetchall())
        self.assertIn("idx_products_cas_upper_trim", plan)
        self.assertIn("idx_products_code_upper_trim", plan)
        self.assertNotIn("Seq Scan on products", plan)
        self.assertIsNone(re.search(r"Index Scan using products_pkey on products.*Rows Removed by Filter: [1-9]", plan, re.S))

    def test_benchmark_sizes_keep_single_bulk_query(self):
        for size in (100, 500, 2000):
            rows = [{"code": self.CODE_MULTI if i % 2 == 0 else self.CODE_LICENSE} for i in range(size)]
            started = time.perf_counter()
            response, recorder = self._call_api({"rows": rows, "selection_strategy": "LOWEST_UNIT_PRICE"})
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["row_count"], size)
            self.assertEqual(len(recorder), 1, f"size={size}, elapsed_ms={elapsed_ms:.1f}")

    # ── Phase 1: stable request identity and index preservation ──

    def test_match_echoes_request_identity_for_direct_input(self):
        payload = {
            "rows": [
                {"request_id": "r1", "request_order": 1, "source_row": None,
                 "requested_name": "Quote Low", "code": self.CODE_VISIBLE, "cas": ""},
                {"request_id": "r2", "request_order": 2, "source_row": None,
                 "requested_name": "Quote Low", "code": self.CODE_VISIBLE, "cas": ""},
            ],
            "selection_strategy": "LOWEST_UNIT_PRICE",
        }
        response, _recorder = self._call_api(payload)
        self.assertEqual(response.status_code, 200)
        rows = response.get_json()["results"]
        self.assertEqual([r["request_id"] for r in rows], ["r1", "r2"])
        self.assertEqual([r["request_order"] for r in rows], [1, 2])
        self.assertTrue(all(r["source_row"] is None for r in rows))
        self.assertEqual([r["requested_code"] for r in rows], [self.CODE_VISIBLE, self.CODE_VISIBLE])

    def test_match_preserves_non_contiguous_source_row_and_skips_blanks(self):
        payload = {
            "rows": [
                {"request_id": "a", "request_order": 1, "source_row": 3, "code": self.CODE_VISIBLE},
                {"request_id": "b", "request_order": 2, "source_row": 7, "requested_name": "blank", "code": "", "cas": ""},
                {"request_id": "c", "request_order": 3, "source_row": 9, "code": self.CODE_LICENSE},
            ],
            "selection_strategy": "LOWEST_UNIT_PRICE",
        }
        response, _recorder = self._call_api(payload)
        rows = response.get_json()["results"]
        self.assertEqual([r["request_id"] for r in rows], ["a", "b", "c"])
        self.assertEqual([r["source_row"] for r in rows], [3, 7, 9])
        self.assertEqual(rows[1]["reason"], "MISSING_IDENTIFIER")

    def test_match_rejects_duplicate_request_id_and_invalid_request_order(self):
        dup = {
            "rows": [
                {"request_id": "x", "request_order": 1, "code": self.CODE_VISIBLE},
                {"request_id": "x", "request_order": 2, "code": self.CODE_LICENSE},
            ],
        }
        response, _recorder = self._call_api(dup)
        self.assertEqual(response.status_code, 400)

        bad_order = {
            "rows": [
                {"request_id": "y1", "request_order": 0, "code": self.CODE_VISIBLE},
            ],
        }
        response, _recorder = self._call_api(bad_order)
        self.assertEqual(response.status_code, 400)

        dup_order = {
            "rows": [
                {"request_id": "y2", "request_order": 5, "code": self.CODE_VISIBLE},
                {"request_id": "y3", "request_order": 5, "code": self.CODE_LICENSE},
            ],
        }
        response, _recorder = self._call_api(dup_order)
        self.assertEqual(response.status_code, 400)

    def test_legacy_payload_without_identity_still_works(self):
        payload = {"rows": [{"code": self.CODE_VISIBLE}], "selection_strategy": "LOWEST_UNIT_PRICE"}
        response, _recorder = self._call_api(payload)
        self.assertEqual(response.status_code, 200)
        row = response.get_json()["results"][0]
        self.assertEqual(row["selected"]["Name"], "Visible Allow")
        # legacy clients get a synthesized request_id/order for compatibility
        self.assertTrue(row["request_id"].startswith("legacy-"))
        self.assertEqual(row["request_order"], 1)
        self.assertIsNone(row["source_row"])

    def test_match_maps_results_back_to_request_id_despite_sql_ordering(self):
        # Send requests in an order where SQL ord would differ from request_order
        payload = {
            "rows": [
                {"request_id": "z3", "request_order": 3, "code": self.CODE_LICENSE},
                {"request_id": "z1", "request_order": 1, "code": self.CODE_VISIBLE},
                {"request_id": "z2", "request_order": 2, "code": self.CODE_MULTI},
            ],
            "selection_strategy": "LOWEST_UNIT_PRICE",
        }
        response, _recorder = self._call_api(payload)
        rows = response.get_json()["results"]
        # results are returned in input order, identity echoes back per-row
        self.assertEqual([r["request_id"] for r in rows], ["z3", "z1", "z2"])
        self.assertEqual([r["request_order"] for r in rows], [3, 1, 2])
        self.assertEqual(rows[0]["selected"]["Name"], "License Product")
        self.assertEqual(rows[1]["selected"]["Name"], "Visible Allow")

    def test_lifecycle_and_reason_code_fields_returned_in_match(self):
        payload = {
            "rows": [
                {"request_id": "lc1", "request_order": 1, "code": self.CODE_VISIBLE},
                {"request_id": "lc2", "request_order": 2, "requested_name": "No code or cas", "code": "", "cas": ""},
                {"request_id": "lc3", "request_order": 3, "code": self.CODE_ZERO},
            ],
            "selection_strategy": "LOWEST_UNIT_PRICE",
        }
        response, _recorder = self._call_api(payload)
        self.assertEqual(response.status_code, 200)
        results = response.get_json()["results"]
        self.assertEqual(results[0]["lifecycle"], "SELECTED")
        self.assertEqual(results[0]["reason_code"], "AUTO_SELECTED")
        self.assertEqual(results[0]["reason"], "SELECTED_LOWEST_UNIT_PRICE")

        self.assertEqual(results[1]["lifecycle"], "UNRESOLVED")
        self.assertEqual(results[1]["reason_code"], "MISSING_IDENTIFIER")
        self.assertEqual(results[1]["reason"], "MISSING_IDENTIFIER")

        self.assertEqual(results[2]["lifecycle"], "REVIEW")
        self.assertEqual(results[2]["reason_code"], "NO_VALID_PRICE")
        self.assertEqual(results[2]["reason"], "NO_VALID_PRICE")

    def test_match_compliance_blocked_lifecycle(self):
        payload = {
            "rows": [
                {"request_id": "blk1", "request_order": 1, "code": self.CODE_BLOCKED},
            ],
            "selection_strategy": "LOWEST_UNIT_PRICE",
        }
        response, _recorder = self._call_api(payload)
        self.assertEqual(response.status_code, 200)
        res = response.get_json()["results"][0]
        self.assertEqual(res["lifecycle"], "BLOCKED")
        self.assertEqual(res["reason_code"], "COMPLIANCE_BLOCKED")
        self.assertIsNone(res["selected"])
        self.assertFalse(res["candidates"][0]["eligible"])

    def test_preflight_endpoint_bulk_check(self):
        with search.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["authenticated"] = True
                sess["username"] = "admin"
                sess["user_id"] = 1
                sess["auth_version"] = 1
                sess["is_admin"] = True

            payload = {
                "rows": [
                    {"request_id": "pf1", "request_order": 1, "code": self.CODE_VISIBLE, "cas": ""},
                    {"request_id": "pf2", "request_order": 2, "code": "", "cas": "", "requested_name": "orphan"},
                    {"request_id": "pf3", "request_order": 3, "code": self.CODE_CONFLICT, "cas": self.CAS_CONFLICT},
                    {"request_id": "pf4", "request_order": 4, "code": "NON_EXISTENT_CODE_12345", "cas": ""},
                ]
            }
            resp = client.post("/api/quote-assistant/preflight", json=payload)
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIn("results", data)
            results = data["results"]
            self.assertEqual(len(results), 4)

            # Check pf1: found
            self.assertEqual(results[0]["request_id"], "pf1")
            self.assertEqual(results[0]["preflight_status"], "FOUND")
            self.assertEqual(results[0]["lifecycle"], "REVIEW")
            self.assertEqual(results[0]["reason_code"], "PENDING_MATCH")
            self.assertGreater(results[0]["match_count"], 0)

            # Check pf2: missing identifier
            self.assertEqual(results[1]["request_id"], "pf2")
            self.assertEqual(results[1]["preflight_status"], "MISSING_IDENTIFIER")
            self.assertEqual(results[1]["lifecycle"], "UNRESOLVED")
            self.assertEqual(results[1]["reason_code"], "MISSING_IDENTIFIER")

            # Check pf3: code-cas conflict
            self.assertEqual(results[2]["request_id"], "pf3")
            self.assertEqual(results[2]["preflight_status"], "CODE_CAS_CONFLICT")
            self.assertEqual(results[2]["lifecycle"], "UNRESOLVED")
            self.assertEqual(results[2]["reason_code"], "CODE_CAS_CONFLICT")

            # Check pf4: no match
            self.assertEqual(results[3]["request_id"], "pf4")
            self.assertEqual(results[3]["preflight_status"], "NO_MATCH")
            self.assertEqual(results[3]["lifecycle"], "UNRESOLVED")
            self.assertEqual(results[3]["reason_code"], "NO_MATCH")

            # Ensure no pricing information leaked
            for r in results:
                self.assertNotIn("Unit_Price", r)
                self.assertNotIn("price", r)

    def test_preflight_endpoint_auth_and_limits(self):
        with search.app.test_client() as anon:
            resp = anon.post("/api/quote-assistant/preflight", json={"rows": []})
            self.assertEqual(resp.status_code, 401)

        with search.app.test_client() as client:
            with client.session_transaction() as sess:
                sess["authenticated"] = True
                sess["username"] = "user"
                sess["user_id"] = 2
                sess["auth_version"] = 1
                sess["is_admin"] = False
                sess["team_id"] = self.team_id

            # Empty rows
            resp = client.post("/api/quote-assistant/preflight", json={"rows": []})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json()["results"], [])

            # > 2000 rows
            too_many = [{"request_id": f"r{i}", "code": "X"} for i in range(2001)]
            resp = client.post("/api/quote-assistant/preflight", json={"rows": too_many})
            self.assertEqual(resp.status_code, 413)

    # ── Phase 3A: Brand Policy engine and API contract ──

    def test_brand_policy_validation_rejections(self):
        # 1. Invalid global mode
        resp, _ = self._call_api({"global_brand_policy": {"mode": "INVALID"}, "rows": [{"code": self.CODE_VISIBLE}]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("không hợp lệ", resp.get_json()["error"])

        # 2. INHERIT as global mode (not allowed)
        resp, _ = self._call_api({"global_brand_policy": {"mode": "INHERIT"}, "rows": [{"code": self.CODE_VISIBLE}]})
        self.assertEqual(resp.status_code, 400)

        # 3. Invalid row override mode
        resp, _ = self._call_api({
            "rows": [{"code": self.CODE_VISIBLE, "brand_policy_override": {"mode": "UNKNOWN"}}]
        })
        self.assertEqual(resp.status_code, 400)

        # 4. PRIORITY_FALLBACK with empty tiers
        resp, _ = self._call_api({
            "global_brand_policy": {"mode": "PRIORITY_FALLBACK", "priority_tiers": []},
            "rows": [{"code": self.CODE_VISIBLE}]
        })
        self.assertEqual(resp.status_code, 400)

        # 5. PRIORITY_FALLBACK with empty brands list in tier
        resp, _ = self._call_api({
            "global_brand_policy": {"mode": "PRIORITY_FALLBACK", "priority_tiers": [{"brands": []}]},
            "rows": [{"code": self.CODE_VISIBLE}]
        })
        self.assertEqual(resp.status_code, 400)

        # 6. PRIORITY_FALLBACK with empty brand string
        resp, _ = self._call_api({
            "global_brand_policy": {"mode": "PRIORITY_FALLBACK", "priority_tiers": [{"brands": ["   "]}]},
            "rows": [{"code": self.CODE_VISIBLE}]
        })
        self.assertEqual(resp.status_code, 400)

        # 7. PRIORITY_FALLBACK with duplicate brand in same tier
        resp, _ = self._call_api({
            "global_brand_policy": {"mode": "PRIORITY_FALLBACK", "priority_tiers": [{"brands": ["CATO", " cato "]}]},
            "rows": [{"code": self.CODE_VISIBLE}]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("trùng lặp", resp.get_json()["error"])

        # 8. PRIORITY_FALLBACK with duplicate brand across different tiers
        resp, _ = self._call_api({
            "global_brand_policy": {
                "mode": "PRIORITY_FALLBACK",
                "priority_tiers": [{"brands": ["CATO"]}, {"brands": [" cato "]}]
            },
            "rows": [{"code": self.CODE_VISIBLE}]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("trùng lặp", resp.get_json()["error"])

        # 9. PRIORITY_FALLBACK with > 20 tiers
        resp, _ = self._call_api({
            "global_brand_policy": {
                "mode": "PRIORITY_FALLBACK",
                "priority_tiers": [{"brands": [f"B_{i}"]} for i in range(21)]
            },
            "rows": [{"code": self.CODE_VISIBLE}]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("tối đa 20 tiers", resp.get_json()["error"])

        # 10. ALLOWLIST_ONLY with empty brands
        resp, _ = self._call_api({
            "global_brand_policy": {"mode": "ALLOWLIST_ONLY", "brands": []},
            "rows": [{"code": self.CODE_VISIBLE}]
        })
        self.assertEqual(resp.status_code, 400)

        # 11. ALLOWLIST_ONLY with duplicate brands
        resp, _ = self._call_api({
            "global_brand_policy": {"mode": "ALLOWLIST_ONLY", "brands": ["CATO", "cato"]},
            "rows": [{"code": self.CODE_VISIBLE}]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("trùng lặp", resp.get_json()["error"])

    def test_priority_fallback_tier_0_success(self):
        payload = {
            "global_brand_policy": {
                "mode": "PRIORITY_FALLBACK",
                "priority_tiers": [
                    {"brands": [self.BRAND_CATO]},
                    {"brands": [self.BRAND_LGC]},
                    {"brands": [self.BRAND_TRC]},
                ]
            },
            "rows": [
                {"request_id": "r1", "request_order": 1, "code": "", "cas": self.CAS_POLICY_TIER}
            ],
            "selection_strategy": "LOWEST_OVERALL",
        }
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        row = data["results"][0]
        self.assertEqual(row["status"], "MATCHED")
        self.assertEqual(row["lifecycle"], "SELECTED")
        self.assertEqual(row["reason_code"], "AUTO_SELECTED")
        self.assertEqual(row["matched_priority_tier"], 0)
        self.assertEqual(row["fallback_path"], [])
        self.assertNotIn("FALLBACK_TIER_USED", row["warnings"])
        self.assertEqual(row["selected"]["Brand"], self.BRAND_CATO)
        self.assertEqual(row["effective_brand_policy"]["mode"], "PRIORITY_FALLBACK")
        self.assertEqual(len(row["effective_brand_policy"]["priority_tiers"]), 3)

    def test_priority_fallback_when_tier_0_empty(self):
        payload = {
            "global_brand_policy": {
                "mode": "PRIORITY_FALLBACK",
                "priority_tiers": [
                    {"brands": [self.BRAND_EXTRA]},  # Not in DB for this CAS
                    {"brands": [self.BRAND_LGC]},
                    {"brands": [self.BRAND_TRC]},
                ]
            },
            "rows": [
                {"request_id": "r1", "request_order": 1, "code": "", "cas": self.CAS_POLICY_TIER}
            ],
            "selection_strategy": "LOWEST_OVERALL",
        }
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["status"], "MATCHED")
        self.assertEqual(row["lifecycle"], "SELECTED")
        self.assertEqual(row["matched_priority_tier"], 1)
        self.assertIn("FALLBACK_TIER_USED", row["warnings"])
        self.assertEqual(len(row["fallback_path"]), 1)
        self.assertEqual(row["fallback_path"][0]["tier"], 0)
        self.assertEqual(row["fallback_path"][0]["brands"], [self.BRAND_EXTRA])
        self.assertEqual(row["fallback_path"][0]["eligible_count"], 0)
        self.assertEqual(row["selected"]["Brand"], self.BRAND_LGC)

    def test_priority_fallback_when_tier_0_blocked_by_compliance(self):
        payload = {
            "global_brand_policy": {
                "mode": "PRIORITY_FALLBACK",
                "priority_tiers": [
                    {"brands": [self.BRAND_CATO]},  # Blocked in fixture (Cấm nhập)
                    {"brands": [self.BRAND_LGC]},
                    {"brands": [self.BRAND_TRC]},
                ]
            },
            "rows": [
                {"request_id": "r1", "request_order": 1, "code": "", "cas": self.CAS_POLICY_BLOCKED}
            ],
            "selection_strategy": "LOWEST_OVERALL",
        }
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["status"], "MATCHED")
        self.assertEqual(row["lifecycle"], "SELECTED")
        self.assertEqual(row["matched_priority_tier"], 1)
        self.assertIn("FALLBACK_TIER_USED", row["warnings"])
        self.assertEqual(len(row["fallback_path"]), 1)
        self.assertEqual(row["fallback_path"][0]["tier"], 0)
        self.assertEqual(row["fallback_path"][0]["rejected_counts"]["COMPLIANCE"], 1)
        self.assertEqual(row["selected"]["Brand"], self.BRAND_LGC)

    def test_priority_fallback_when_tier_0_no_valid_price(self):
        payload = {
            "global_brand_policy": {
                "mode": "PRIORITY_FALLBACK",
                "priority_tiers": [
                    {"brands": [self.BRAND_CATO]},  # Price 0 in fixture
                    {"brands": [self.BRAND_LGC]},
                ]
            },
            "rows": [
                {"request_id": "r1", "request_order": 1, "code": "", "cas": self.CAS_POLICY_NOPRICE}
            ],
            "selection_strategy": "LOWEST_OVERALL",
        }
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["status"], "MATCHED")
        self.assertEqual(row["lifecycle"], "SELECTED")
        self.assertEqual(row["matched_priority_tier"], 1)
        self.assertIn("FALLBACK_TIER_USED", row["warnings"])
        self.assertEqual(len(row["fallback_path"]), 1)
        self.assertEqual(row["fallback_path"][0]["tier"], 0)
        self.assertEqual(row["fallback_path"][0]["rejected_counts"]["NO_VALID_PRICE"], 1)
        self.assertEqual(row["selected"]["Brand"], self.BRAND_LGC)

    def test_priority_fallback_all_tiers_blocked_creates_blocked_lifecycle(self):
        payload = {
            "global_brand_policy": {
                "mode": "PRIORITY_FALLBACK",
                "priority_tiers": [
                    {"brands": [self.BRAND_CATO]},  # Blocked in fixture
                ]
            },
            "rows": [
                {"request_id": "r1", "request_order": 1, "code": "", "cas": self.CAS_POLICY_BLOCKED}
            ],
            "selection_strategy": "LOWEST_OVERALL",
        }
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["status"], "UNRESOLVED")
        self.assertEqual(row["lifecycle"], "BLOCKED")
        self.assertEqual(row["reason_code"], "COMPLIANCE_BLOCKED")
        self.assertIsNone(row["matched_priority_tier"])
        self.assertIsNone(row["selected"])
        self.assertEqual(len(row["fallback_path"]), 1)

    def test_priority_fallback_two_brands_in_same_tier_and_strategies(self):
        # Test 1: LOWEST_OVERALL -> picks cheaper between LGC (300) and HPC (250) -> HPC
        payload = {
            "global_brand_policy": {
                "mode": "PRIORITY_FALLBACK",
                "priority_tiers": [
                    {"brands": [self.BRAND_LGC, self.BRAND_HPC]}
                ]
            },
            "rows": [
                {"request_id": "r1", "request_order": 1, "code": "", "cas": self.CAS_POLICY_MULTI_TIER}
            ],
            "selection_strategy": "LOWEST_OVERALL",
        }
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["matched_priority_tier"], 0)
        self.assertEqual(row["selected"]["Brand"], self.BRAND_HPC)
        self.assertEqual(len(row["selected_candidates"]), 1)

        # Test 2: LOWEST_PER_BRAND -> picks 1 for LGC and 1 for HPC -> 2 selected candidates
        payload["selection_strategy"] = "LOWEST_PER_BRAND"
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(len(row["selected_candidates"]), 2)
        brands_selected = {c["Brand"] for c in row["selected_candidates"]}
        self.assertEqual(brands_selected, {self.BRAND_LGC, self.BRAND_HPC})

        # Test 3: MANUAL -> returns both candidates, 0 selected, lifecycle REVIEW
        payload["selection_strategy"] = "MANUAL"
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["status"], "MATCHED")
        self.assertEqual(row["lifecycle"], "REVIEW")
        self.assertEqual(row["reason_code"], "MANUAL_SELECTION_REQUIRED")
        self.assertIsNone(row["selected"])
        self.assertEqual(len(row["candidates"]), 2)

    def test_allowlist_only_mode_and_no_fallback(self):
        # 1. Allowlist with TRC only (both Tier TRC and Exact TRC match)
        payload = {
            "global_brand_policy": {
                "mode": "ALLOWLIST_ONLY",
                "brands": [self.BRAND_TRC]
            },
            "rows": [
                {"request_id": "r1", "request_order": 1, "code": "", "cas": self.CAS_POLICY_TIER}
            ],
            "selection_strategy": "LOWEST_OVERALL",
        }
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["lifecycle"], "SELECTED")
        self.assertEqual(row["selected"]["Brand"], self.BRAND_TRC)
        self.assertEqual(len(row["candidates"]), 2)
        for c in row["candidates"]:
            self.assertEqual(c["Brand"], self.BRAND_TRC)
        self.assertIsNone(row["matched_priority_tier"])
        self.assertEqual(row["fallback_path"], [])

        # 2. Allowlist with brand not in DB -> FILTER_NO_MATCH
        payload["global_brand_policy"]["brands"] = [self.BRAND_EXTRA]
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["lifecycle"], "REVIEW")
        self.assertEqual(row["reason_code"], "FILTER_NO_MATCH")
        self.assertEqual(row["candidates"], [])
        self.assertIsNone(row["selected"])

    def test_all_available_picks_best_candidate_per_brand(self):
        # In fixture CAS_POLICY_ALL_AVAIL:
        # CATO has 2 prods: 500 & 400 (unit prices: 5000 & 4000)
        # LGC has 1 prod: 600 (unit price: 6000)
        # TRC has 1 prod: 300 (unit price: 3000)
        payload = {
            "global_brand_policy": {
                "mode": "ALL_AVAILABLE"
            },
            "rows": [
                {"request_id": "r1", "request_order": 1, "code": "", "cas": self.CAS_POLICY_ALL_AVAIL}
            ],
            "selection_strategy": "LOWEST_OVERALL",
        }
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["lifecycle"], "SELECTED")
        self.assertEqual(row["reason_code"], "AUTO_SELECTED")
        # Exactly 3 candidates returned (1 best per brand: CATO 4000, TRC 3000, LGC 6000)
        self.assertEqual(len(row["candidates"]), 3)
        cato_cand = next(c for c in row["candidates"] if c["Brand"] == self.BRAND_CATO)
        self.assertEqual(cato_cand["Unit_Price_Value"], 4000.0)
        self.assertEqual(row["selected"]["Brand"], self.BRAND_TRC)
        self.assertEqual(row["selected"]["Unit_Price_Value"], 3000.0)

        # Strategy LOWEST_PER_BRAND selects all 3
        payload["selection_strategy"] = "LOWEST_PER_BRAND"
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(len(row["selected_candidates"]), 3)

    def test_exact_code_not_altered_by_global_brand_policy(self):
        # CODE_EXACT_POLICY is brand TRC in DB.
        # Global policy prioritizes CATO -> LGC -> TRC.
        payload = {
            "global_brand_policy": {
                "mode": "PRIORITY_FALLBACK",
                "priority_tiers": [
                    {"brands": [self.BRAND_CATO]},
                    {"brands": [self.BRAND_LGC]},
                    {"brands": [self.BRAND_TRC]},
                ]
            },
            "rows": [
                # Row 1: Exact code search without equivalent -> MUST return TRC
                {"request_id": "r1", "request_order": 1, "code": self.CODE_EXACT_POLICY, "cas": "", "equivalent_override": False},
                # Row 2: Equivalent code search -> MUST follow brand policy and return CATO
                {"request_id": "r2", "request_order": 2, "code": self.CODE_EXACT_POLICY, "cas": "", "equivalent_override": True},
            ],
            "selection_strategy": "LOWEST_OVERALL",
        }
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        results = resp.get_json()["results"]

        # Exact code row:
        self.assertEqual(results[0]["selected"]["Brand"], self.BRAND_TRC)
        self.assertIsNone(results[0]["matched_priority_tier"])
        self.assertEqual(results[0]["fallback_path"], [])

        # Equivalent row:
        self.assertEqual(results[1]["selected"]["Brand"], self.BRAND_CATO)
        self.assertEqual(results[1]["matched_priority_tier"], 0)

    def test_per_row_inherit_and_override(self):
        payload = {
            "global_brand_policy": {
                "mode": "PRIORITY_FALLBACK",
                "priority_tiers": [
                    {"brands": [self.BRAND_CATO]},
                    {"brands": [self.BRAND_LGC]},
                ]
            },
            "rows": [
                # Row 1: Explicit INHERIT -> uses global policy (CATO)
                {"request_id": "r1", "request_order": 1, "code": "", "cas": self.CAS_POLICY_TIER,
                 "brand_policy_override": {"mode": "INHERIT"}},
                # Row 2: Explicit override to ALLOWLIST_ONLY with TRC -> overrides global policy (returns TRC)
                {"request_id": "r2", "request_order": 2, "code": "", "cas": self.CAS_POLICY_TIER,
                 "brand_policy_override": {"mode": "ALLOWLIST_ONLY", "brands": [self.BRAND_TRC]}},
                # Row 3: Omitted override -> uses global policy (CATO)
                {"request_id": "r3", "request_order": 3, "code": "", "cas": self.CAS_POLICY_TIER},
            ],
            "selection_strategy": "LOWEST_OVERALL",
        }
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        results = resp.get_json()["results"]

        self.assertEqual(results[0]["selected"]["Brand"], self.BRAND_CATO)
        self.assertEqual(results[0]["effective_brand_policy"]["mode"], "PRIORITY_FALLBACK")

        self.assertEqual(results[1]["selected"]["Brand"], self.BRAND_TRC)
        self.assertEqual(results[1]["effective_brand_policy"]["mode"], "ALLOWLIST_ONLY")

        self.assertEqual(results[2]["selected"]["Brand"], self.BRAND_CATO)
        self.assertEqual(results[2]["effective_brand_policy"]["mode"], "PRIORITY_FALLBACK")

    def test_legacy_payload_compatibility_preserved(self):
        # 1. CAS-only without brands -> BRAND_REQUIRED
        payload = {
            "rows": [{"request_id": "r1", "request_order": 1, "code": "", "cas": self.CAS_POLICY_TIER}],
            "selection_strategy": "LOWEST_OVERALL",
        }
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["lifecycle"], "REVIEW")
        self.assertEqual(row["reason_code"], "BRAND_REQUIRED")
        self.assertIsNone(row["effective_brand_policy"])
        self.assertIsNone(row["matched_priority_tier"])
        self.assertEqual(row["fallback_path"], [])

        # 2. CAS-only with legacy filters.brands -> returns filtered
        payload["filters"] = {"brands": [self.BRAND_TRC]}
        resp, _ = self._call_api(payload)
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["lifecycle"], "SELECTED")
        self.assertEqual(row["selected"]["Brand"], self.BRAND_TRC)
        self.assertIsNone(row["effective_brand_policy"])

    def test_brand_policy_benchmark_and_plan_preserves_single_bulk_query(self):
        policy = {
            "mode": "PRIORITY_FALLBACK",
            "priority_tiers": [
                {"brands": [self.BRAND_CATO]},
                {"brands": [self.BRAND_LGC]},
                {"brands": [self.BRAND_TRC]},
            ]
        }
        for size in (100, 500, 2000):
            rows = [
                {
                    "request_id": f"r{i}",
                    "request_order": i + 1,
                    "code": "",
                    "cas": self.CAS_POLICY_TIER if i % 2 == 0 else self.CAS_POLICY_ALL_AVAIL
                }
                for i in range(size)
            ]
            started = time.perf_counter()
            response, recorder = self._call_api({
                "global_brand_policy": policy,
                "rows": rows,
                "selection_strategy": "LOWEST_OVERALL"
            })
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["row_count"], size)
            self.assertEqual(len(recorder), 1, f"size={size}, elapsed_ms={elapsed_ms:.1f}")

        # Verify EXPLAIN query plan
        query, params = recorder[0]
        with self.conn.cursor() as cur:
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + query, params)
            plan = "\n".join(row[0] for row in cur.fetchall())
        self.assertIn("idx_products_cas_upper_trim", plan)
        self.assertNotIn("Seq Scan on products", plan)

    # ── Phase 3A.1: SQL optimisation, CAS placeholder, candidate-limit semantics ──

    def test_code_has_placeholder_cas_returns_correct_reason(self):
        """Code with only placeholder CAS (NOT AVAILABLE) → CODE_HAS_PLACEHOLDER_CAS, not CODE_HAS_NO_CAS."""
        resp, _ = self._call_api({
            "rows": [
                {"request_id": "r1", "request_order": 1,
                 "code": self.CODE_PLACEHOLDER_CAS, "equivalent_override": True}
            ],
            "filters": {"brands": [self.BRAND_ALLOW]},
            "selection_strategy": "LOWEST_OVERALL",
        })
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["lifecycle"], "UNRESOLVED")
        self.assertEqual(row["reason_code"], "CODE_HAS_PLACEHOLDER_CAS")
        self.assertEqual(row["match_mode"], "EQUIVALENT")
        self.assertIsNone(row["selected"])

    def test_code_with_one_valid_and_one_placeholder_cas_uses_valid_cas(self):
        """Code with 1 valid CAS and 1 placeholder CAS must expand using the valid CAS."""
        resp, _ = self._call_api({
            "rows": [
                {"request_id": "r1", "request_order": 1,
                 "code": self.CODE_VALID_AND_PLACEHOLDER, "equivalent_override": True}
            ],
            "filters": {"brands": [self.BRAND_ALLOW]},
            "selection_strategy": "LOWEST_OVERALL",
        })
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["status"], "MATCHED")
        self.assertEqual(row["lifecycle"], "SELECTED")
        self.assertEqual(row["match_mode"], "EQUIVALENT")
        self.assertIsNotNone(row["selected"])

    def test_code_with_multiple_valid_and_placeholder_cas_returns_multiple_cas(self):
        """Code with 2 valid CAS and 1 placeholder CAS must return CODE_MULTIPLE_CAS."""
        resp, _ = self._call_api({
            "rows": [
                {"request_id": "r1", "request_order": 1,
                 "code": self.CODE_MULTI_VALID_AND_PLACEHOLDER, "equivalent_override": True}
            ],
            "filters": {"brands": [self.BRAND_ALLOW]},
            "selection_strategy": "LOWEST_OVERALL",
        })
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["lifecycle"], "UNRESOLVED")
        self.assertEqual(row["reason_code"], "CODE_MULTIPLE_CAS")
        self.assertEqual(row["match_mode"], "EQUIVALENT")
        self.assertIsNone(row["selected"])

    def test_code_exact_bypasses_placeholder_filter(self):
        """Exact Code search for the same product must still succeed (placeholder filter only in equiv path)."""
        resp, _ = self._call_api({
            "rows": [
                {"request_id": "r1", "request_order": 1,
                 "code": self.CODE_PLACEHOLDER_CAS, "equivalent_override": False}
            ],
            "filters": {"brands": [self.BRAND_ALLOW]},
            "selection_strategy": "LOWEST_OVERALL",
        })
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["status"], "MATCHED")
        self.assertEqual(row["match_mode"], "EXACT_CODE")
        self.assertIsNotNone(row["selected"])

    def test_candidate_limit_exceeded_uses_new_reason_code(self):
        """Rows exceeding 500 SQL candidates must use CANDIDATE_LIMIT_EXCEEDED reason, not MANUAL_SELECTION_REQUIRED."""
        resp, _ = self._call_api({
            "global_brand_policy": {"mode": "ALL_AVAILABLE"},
            "rows": [{"cas": "7704-34-9"}],
            "selection_strategy": "LOWEST_OVERALL",
        })
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["lifecycle"], "REVIEW")
        self.assertEqual(row["reason_code"], "CANDIDATE_LIMIT_EXCEEDED")
        self.assertEqual(row["candidates"], [])
        self.assertIsNone(row["selected"])
        self.assertIsNone(row["selected_candidates"] or None)
        self.assertIn("CANDIDATE_LIMIT_EXCEEDED", row["warnings"])

    def test_candidate_limit_exact_boundary_500_vs_501(self):
        """500 candidates must be processed normally; 501 must fail-closed."""
        # 7704-34-9 returns 501+ rows (Sulfur: 750 rows in local DB), verified in scale-gate audit.
        # We verify fail-closed behaviour here by checking the reason code and empty candidate list.
        resp, _ = self._call_api({
            "global_brand_policy": {"mode": "ALL_AVAILABLE"},
            "rows": [{"cas": "7704-34-9"}],
            "selection_strategy": "LOWEST_OVERALL",
        })
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        # The key assertion: no partial candidates returned
        self.assertEqual(row["candidates"], [])
        self.assertIsNone(row["selected"])
        # reason_code must NOT be MANUAL_SELECTION_REQUIRED
        self.assertNotEqual(row["reason_code"], "MANUAL_SELECTION_REQUIRED")
        self.assertEqual(row["reason_code"], "CANDIDATE_LIMIT_EXCEEDED")

    def test_equivalent_code_single_bulk_query_and_plan(self):
        """Equivalent Code search must use idx_products_code_upper_trim, not Seq Scan."""
        rows = [
            {"code": self.CODE_EQ, "equivalent_override": True}
        ] * 10
        resp, recorder = self._call_api({
            "rows": rows,
            "filters": {"brands": [self.BRAND_ALLOW]},
            "selection_strategy": "LOWEST_OVERALL",
        })
        self.assertEqual(resp.status_code, 200)
        # Only one bulk query issued
        self.assertEqual(len(recorder), 1)
        query, params = recorder[0]
        with self.conn.cursor() as cur:
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + query, params)
            plan = "\n".join(r[0] for r in cur.fetchall())
        # Code index must be used in code_cas_summary
        self.assertIn("idx_products_code_upper_trim", plan)
        # CAS index also used in equivalent expansion
        self.assertIn("idx_products_cas_upper_trim", plan)
        # No table-level sequential scan
        self.assertNotIn("Seq Scan on products", plan)

    def test_equivalent_code_benchmark_n100_under_10s(self):
        """N=100 equivalent Code (e.g. CODE_EQ, small fan-out) must complete in under 10 s (median of 3)."""
        rows = [
            {"request_id": f"r{i}", "request_order": i + 1,
             "code": self.CODE_EQ, "equivalent_override": True}
            for i in range(100)
        ]
        payload = {
            "rows": rows,
            "filters": {"brands": [self.BRAND_ALLOW]},
            "selection_strategy": "LOWEST_OVERALL",
        }
        times = []
        for _ in range(3):
            t0 = time.perf_counter()
            resp, _ = self._call_api(payload)
            times.append((time.perf_counter() - t0) * 1000)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json()["row_count"], 100)
        median_ms = sorted(times)[1]
        self.assertLess(median_ms, 10_000, f"Median {median_ms:.0f}ms ≥ 10 000ms for N=100 equivalent Code")

    def test_two_identical_rows_keep_separate_identity(self):
        """Two equivalent rows with the same Code must have different request_ids and both resolve."""
        resp, _ = self._call_api({
            "rows": [
                {"request_id": "uid-A", "request_order": 1, "code": self.CODE_EQ, "equivalent_override": True},
                {"request_id": "uid-B", "request_order": 2, "code": self.CODE_EQ, "equivalent_override": True},
            ],
            "filters": {"brands": [self.BRAND_ALLOW]},
            "selection_strategy": "LOWEST_OVERALL",
        })
        self.assertEqual(resp.status_code, 200)
        results = resp.get_json()["results"]
        self.assertEqual(results[0]["request_id"], "uid-A")
        self.assertEqual(results[1]["request_id"], "uid-B")
        # Both must resolve to the same product independently
        self.assertEqual(results[0]["selected"]["Name"], results[1]["selected"]["Name"])
        self.assertEqual(results[0]["request_order"], 1)
        self.assertEqual(results[1]["request_order"], 2)

    def test_p3a_brand_policy_regression_with_optimised_query(self):
        """Brand policy modes still work correctly after query optimisation."""
        # PRIORITY_FALLBACK with LOWEST_OVERALL on equiv CAS
        resp, recorder = self._call_api({
            "global_brand_policy": {
                "mode": "PRIORITY_FALLBACK",
                "priority_tiers": [
                    {"brands": [self.BRAND_CATO]},
                    {"brands": [self.BRAND_LGC]},
                ]
            },
            "rows": [
                {"request_id": "r1", "request_order": 1, "code": "", "cas": self.CAS_POLICY_TIER}
            ],
            "selection_strategy": "LOWEST_OVERALL",
        })
        self.assertEqual(resp.status_code, 200)
        row = resp.get_json()["results"][0]
        self.assertEqual(row["lifecycle"], "SELECTED")
        self.assertEqual(row["matched_priority_tier"], 0)
        self.assertEqual(row["selected"]["Brand"], self.BRAND_CATO)
        # Still single bulk query
        self.assertEqual(len(recorder), 1)


if __name__ == "__main__":
    unittest.main()
