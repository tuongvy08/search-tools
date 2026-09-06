"""Phase 6A-Fix2: REAL PostgreSQL integration tests for Team CRUD, the
permission preview -> confirm workflow, `admin_google_users.update()`
(the Google-account team/role management route), and cross-cutting
LOCAL/GOOGLE permission equivalence.

Same isolation contract as `tests/test_admin_pg_integration.py` (read
first if reviewing this file): everything NOT explicitly labelled
"mock"/"fake" talks to a REAL local Postgres server (same host/port/user
as `DATABASE_URL`), but only ever inside ONE temporary, uniquely-prefixed
database (`p6afix2_pgtest_<random>`) created in `setUpClass` and dropped in
`tearDownClass` (even on failure). NEVER touches `products_local` or any
other pre-existing database; every row is a synthetic fixture created by
this file. `office_ip_allowlist`/`teams`/`app_users`/`team_brands`/
`login_audit_events`/`products`/`brand_compliance_settings`/
`regulatory_rules` are all built from the REAL migration/schema files in
`sql/`, not hand-approximated, so behaviour here matches a fully-migrated
app DB.

Section map (mock vs real Postgres called out explicitly per class):
  - TeamCrudPgTests: real Postgres. create/rename preserve id/members/
    brands; duplicate/invalid name; brand validation now REJECTS THE WHOLE
    request on any invalid brand (Fix2 change, see admin_teams.py).
  - PreviewConfirmPgTests: real Postgres, two independent connections for
the one genuine concurrency case (race between two confirms). Preview
    never writes; confirm applies exactly the previewed diff; a client
    cannot smuggle its own brand/policy values into confirm (only the
    server-stored preview record is ever used); staleness after a DIRECT
    team change and after a MEMBERSHIP change (a user's team_id changed)
    are both rejected; a preview confirmed by a DIFFERENT admin than the
    one who created it is rejected; two competing confirms for the same
    team can't both win.
  - GoogleUpdatePgTests: real Postgres, drives the actual
    `admin_google_users.update()` Flask view via `search.app.test_client()`
-- role/team change, invalid payloads, non-ACTIVE targets, self-demote/
    last-admin, auth_version bump + old-session rejection on next request,
    and that team-membership changes bump BOTH the old and new team's
    `updated_at` (closing the preview-staleness gap Fix2 also fixes).
  - CrossCuttingPermissionPgTests: real Postgres, REAL middleware stack
    (`session_security` + `middleware_access`) actually registered on
    `search.app` -- deliberately NOT using `DISABLE_IP_ALLOWLIST` (the
    task requires this) -- with `teams.ip_policy = 'INHERIT'` and an EMPTY
    `office_ip_allowlist` (the documented "zero rules configured => allow"
    INHERIT contract, see `middleware_access.py`), so the IP layer is
provably active-but-permissive while the TEAM/BRAND layer under test is
    exercised. Verifies LOCAL/GOOGLE same-team equivalence, an empty team
    seeing nothing, Match (`/api/quote-assistant/preflight`, real HTTP) and
    Export (`search._quote_export_products`, called directly against a
    real session+DB to avoid unrelated xlsx-template/file-upload plumbing
    that has nothing to do with permissions -- documented inline at that
    test) not leaking across teams, and a team's brand change taking
    effect on the very next request with no re-login.

Does not repeat the full "actor revoked while waiting for the shared
advisory lock" thread-blocking matrix already proven generic (across 7
mutations) by `test_admin_pg_integration.py`'s
`ActorRevokedWhileWaitingForLockParamTests` -- `admin_teams.py` and
`update()` reuse the exact same `acquire_last_admin_lock`/
`revalidate_actor` pair, so this file adds lighter, non-duplicative checks
that the SAME guards are actually wired into these newer routes, not a
second copy of the whole race matrix.
"""
import io
import json
import multiprocessing
import os
import secrets
import threading
import time
import unittest
from unittest import mock
from urllib.parse import urlparse, urlunparse

import psycopg2

import admin_google_users
import admin_teams
import middleware_access
import search
import session_security
from db import get_connection as db_get_connection
from test_quote_workbook_export import make_workbook, product as _wb_product

SQL_DIR = os.path.join(os.path.dirname(__file__), "..", "sql")


def _read(path):
    with open(os.path.join(SQL_DIR, path), "r", encoding="utf-8") as f:
        return f.read()


_MIGRATION_006_SQL = _read("migration_006_office_ip_allowlist.sql")
_MIGRATION_014_SQL = _read("migration_014_google_oidc.sql")
_MIGRATION_015_SQL = _read("migration_015_team_policy.sql")
_MIGRATION_016_SQL = _read("migration_016_team_permission_previews.sql")
_SCHEMA_PRODUCTS_SQL = _read("schema.sql")
_MIGRATION_003_REGULATORY_RULES_SQL = _read("migration_003_regulatory_rules.sql")
_MIGRATION_011_MANUAL_COMPLIANCE_SQL = _read("migration_011_manual_compliance.sql")
_MIGRATION_012_PREPARATION_TYPE_SQL = _read("migration_012_product_preparation_type.sql")

_REAL_DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _dsn_for(dbname: str) -> str:
    parsed = urlparse(_REAL_DATABASE_URL)
    return urlunparse(parsed._replace(path="/" + dbname))


def _maintenance_dsn() -> str:
    return _dsn_for("postgres")  # never products_local


def _probe_postgres_reachable() -> bool:
    if not _REAL_DATABASE_URL:
        return False
    try:
        conn = psycopg2.connect(_maintenance_dsn(), connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


_POSTGRES_REACHABLE = _probe_postgres_reachable()
_SKIP_REASON = (
    "Local PostgreSQL not reachable via DATABASE_URL's host/port/user "
    "(maintenance DB 'postgres'). BLOCKER for Phase 6A-Fix2 -- see report."
)

_TEST_DB_PREFIX = "p6afix2_pgtest_"

# Same minimal pre-014 base as test_admin_pg_integration.py -- teams /
# app_users / team_brands only get their Google-OIDC / team-policy columns
# via the real migration_014 / migration_015 files applied right after.
_MINIMAL_BASE_SCHEMA_SQL = """
CREATE TABLE teams (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE app_users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    team_id INTEGER NULL REFERENCES teams(id) ON DELETE SET NULL,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    ip_bypass_allowlist BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE team_brands (
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    brand TEXT NOT NULL,
    PRIMARY KEY (team_id, brand)
);
"""


@unittest.skipUnless(_POSTGRES_REACHABLE, _SKIP_REASON)
class _RealPgTestBase(unittest.TestCase):
    """One temp DB per class; full real schema (base + schema.sql +
    migrations 003/006/011/012/014/015); dropped in tearDownClass even on
    failure. `setUp`/`_truncate_all` reset all rows between tests.
    """

    @classmethod
    def setUpClass(cls):
        cls.test_db_name = _TEST_DB_PREFIX + secrets.token_hex(4)
        cls.test_dsn = _dsn_for(cls.test_db_name)

        maint = psycopg2.connect(_maintenance_dsn())
        maint.autocommit = True
        try:
            with maint.cursor() as cur:
                cur.execute(f'CREATE DATABASE "{cls.test_db_name}"')
        finally:
            maint.close()

        conn = psycopg2.connect(cls.test_dsn)
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(_MINIMAL_BASE_SCHEMA_SQL)
                    cur.execute(_MIGRATION_014_SQL)
                    cur.execute(_MIGRATION_015_SQL)
                    cur.execute(_MIGRATION_016_SQL)
                    cur.execute(_MIGRATION_006_SQL)
                    cur.execute(_SCHEMA_PRODUCTS_SQL)
                    cur.execute(_MIGRATION_003_REGULATORY_RULES_SQL)
                    cur.execute(_MIGRATION_011_MANUAL_COMPLIANCE_SQL)
                    cur.execute(_MIGRATION_012_PREPARATION_TYPE_SQL)
                    # `_exchange_rate_map()` SELECTs this table and only
                    # gracefully falls back to JSON defaults if the query
                    # itself raises (e.g. missing table) -- but a failed
                    # statement on a non-autocommit connection otherwise
                    # aborts the transaction for the NEXT statement too.
                    # Out of this fix's scope to touch that unrelated
                    # fallback; simplest is to give it a real (empty)
                    # table, exactly like `products_local` already has.
                    cur.execute("CREATE TABLE IF NOT EXISTS exchange_rates (brand TEXT PRIMARY KEY, rate NUMERIC NOT NULL)")
                    # Phase 6B2B2-R2: seed explicit rate=1 rows for the mock VND
                    # test brands (BrandA, BrandB) used by team visibility tests.
                    # This is explicit fixture data, not a runtime fallback.
                    cur.execute(
                        "INSERT INTO exchange_rates (brand, rate) VALUES ('BrandA', 1), ('BrandB', 1) "
                        "ON CONFLICT (brand) DO UPDATE SET rate = 1"
                    )
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):
        assert cls.test_db_name.startswith(_TEST_DB_PREFIX), (
            f"Refusing to drop DB with unexpected name: {cls.test_db_name!r}"
        )
        parsed = urlparse(cls.test_dsn)
        assert parsed.hostname in ("127.0.0.1", "localhost"), (
            f"Refusing to drop DB on unexpected host: {parsed.hostname!r}"
        )
        assert parsed.path.lstrip("/") == cls.test_db_name

        maint = psycopg2.connect(_maintenance_dsn())
        maint.autocommit = True
        try:
            with maint.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (cls.test_db_name,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{cls.test_db_name}"')
        finally:
            maint.close()

    def setUp(self):
        self._truncate_all()
        self._env_patch = mock.patch.dict(os.environ, {"DATABASE_URL": self.test_dsn})
        self._env_patch.start()
        # Belt-and-suspenders: this file's cross-cutting class explicitly
        # exercises the REAL IP middleware (no DISABLE_IP_ALLOWLIST), so
        # every class here must start from a clean slate regardless of
        # whatever the invoking shell happens to have set.
        for key in ("DISABLE_IP_ALLOWLIST", "OFFICE_IP_ALLOWLIST", "IP_ALLOWLIST_BYPASS_USERS"):
            os.environ.pop(key, None)
        search.app.testing = True

    def tearDown(self):
        self._env_patch.stop()

    def _connect(self):
        return psycopg2.connect(self.test_dsn)

    def _truncate_all(self):
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "TRUNCATE login_audit_events, team_permission_previews, team_brands, "
                        "app_users, teams, office_ip_allowlist, regulatory_rules, "
                        "brand_compliance_settings, products "
                        "RESTART IDENTITY CASCADE"
                    )
        finally:
            conn.close()

    # ---- fixtures ---------------------------------------------------

    def _insert_team(self, name="Team A", ip_policy="INHERIT", brands=None):
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO teams (name, ip_policy) VALUES (%s, %s) RETURNING id",
                        (name, ip_policy),
                    )
                    (tid,) = cur.fetchone()
                    for b in (brands or []):
                        cur.execute(
                            "INSERT INTO team_brands (team_id, brand) VALUES (%s, %s)",
                            (tid, b),
                        )
        finally:
            conn.close()
        return tid

    def _insert_user(self, **kwargs):
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO app_users
                            (username, password_hash, team_id, is_admin, ip_bypass_allowlist,
                             auth_provider, google_sub, email, account_status, auth_version)
                        VALUES (%(username)s, %(password_hash)s, %(team_id)s, %(is_admin)s,
                                FALSE, %(auth_provider)s, %(google_sub)s, %(email)s,
                                %(account_status)s, %(auth_version)s)
                        RETURNING id
                        """,
                        {
                            "username": kwargs["username"],
                            "password_hash": kwargs.get("password_hash", "x"),
                            "team_id": kwargs.get("team_id"),
                            "is_admin": kwargs.get("is_admin", False),
                            "auth_provider": kwargs.get("auth_provider", "LOCAL"),
                            "google_sub": kwargs.get("google_sub"),
                            "email": kwargs.get("email"),
                            "account_status": kwargs.get("account_status", "ACTIVE"),
                            "auth_version": kwargs.get("auth_version", 1),
                        },
                    )
                    (uid,) = cur.fetchone()
        finally:
            conn.close()
        return uid

    def _insert_product(self, *, brand, code=None, cas=None, price="1000", ship="1", name=None):
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO products (name, code, cas, brand, size, ship, price, note) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                        (name or f"Product {brand}", code, cas, brand, "1L", ship, price, None),
                    )
                    (pid,) = cur.fetchone()
        finally:
            conn.close()
        return pid

    def _fetch_team(self, team_id):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, ip_policy, updated_at FROM teams WHERE id = %s",
                    (team_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return row  # (id, name, ip_policy, updated_at) or None

    def _fetch_team_brands(self, team_id):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT brand FROM team_brands WHERE team_id = %s ORDER BY brand", (team_id,))
                rows = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
        return rows

    def _fetch_user(self, uid):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT account_status, is_admin, auth_version, auth_provider, team_id, "
                    "google_sub, ip_bypass_allowlist FROM app_users WHERE id = %s",
                    (uid,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return row

    def _count_audits(self):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM login_audit_events")
                (n,) = cur.fetchone()
        finally:
            conn.close()
        return n

    # ---- session helpers ---------------------------------------------

    def _client_for(self, user_id, *, auth_version=1, is_admin=False, team_id=None,
                     username="user1", csrf="the-real-token"):
        client = search.app.test_client()
        with client.session_transaction() as sess:
            sess.clear()
            sess.update(authenticated=True, user_id=user_id, auth_version=auth_version,
                        is_admin=is_admin, team_id=team_id, role="admin" if is_admin else "staff",
                        username=username)
            sess["csrf_token"] = csrf
        return client


# --------------------------------------------------------------------------
# 1. Team CRUD + brand validation -- real Postgres, real Flask views.
# --------------------------------------------------------------------------

class TeamCrudPgTests(_RealPgTestBase):
    def _admin(self):
        return self._insert_user(username="admin1", is_admin=True, account_status="ACTIVE")

    def test_create_team_persists_name_and_brands(self):
        self._insert_product(brand="BrandA", code="C1")
        self._insert_product(brand="BrandB", code="C2")
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        resp = client.post(
            "/admin/teams/create",
            data={"name": "Team Alpha", "ip_policy": "INHERIT", "brands": ["BrandA"],
                  "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("msg=", resp.headers["Location"])

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, ip_policy FROM teams WHERE name = %s", ("Team Alpha",))
                row = cur.fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        tid, name, ip_policy = row
        self.assertEqual(name, "Team Alpha")
        self.assertEqual(ip_policy, "INHERIT")
        self.assertEqual(self._fetch_team_brands(tid), ["BrandA"])

    def test_create_team_allows_zero_brands(self):
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)
        resp = client.post(
            "/admin/teams/create",
            data={"name": "Empty Team", "ip_policy": "INHERIT", "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp.status_code, 302)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM teams WHERE name = %s", ("Empty Team",))
                (tid,) = cur.fetchone()
        finally:
            conn.close()
        self.assertEqual(self._fetch_team_brands(tid), [])

    def test_create_team_rejects_duplicate_name_no_partial_write(self):
        self._insert_team("Existing Team")
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)
        resp = client.post(
            "/admin/teams/create",
            data={"name": "Existing Team", "ip_policy": "INHERIT", "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("err=", resp.headers["Location"])
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM teams WHERE name = %s", ("Existing Team",))
                (n,) = cur.fetchone()
        finally:
            conn.close()
        self.assertEqual(n, 1, "duplicate create must not add a second row")

    def test_create_team_missing_name_rejected(self):
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)
        resp = client.post(
            "/admin/teams/create",
            data={"name": "   ", "ip_policy": "INHERIT", "csrf_token": "the-real-token"},
        )
        self.assertIn("err=", resp.headers["Location"])
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM teams")
                (n,) = cur.fetchone()
        finally:
            conn.close()
        self.assertEqual(n, 0)

    def test_create_team_invalid_brand_rejects_whole_request(self):
        """Fix2: a request naming ONE valid + ONE invalid/forged brand must
        reject the ENTIRE request -- the team must not be created at all
        (not created-with-only-the-valid-brand, and not created-with-no-
        brands-silently). Previously `_sanitize_brands` would have
        silently dropped the invalid one and let "BrandA" through.
        """
        self._insert_product(brand="BrandA", code="C1")
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)
        resp = client.post(
            "/admin/teams/create",
            data={"name": "Should Not Exist", "ip_policy": "INHERIT",
                  "brands": ["BrandA", "Forged-Brand-Not-In-Products"],
                  "csrf_token": "the-real-token"},
        )
        self.assertIn("err=", resp.headers["Location"])
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM teams WHERE name = %s", ("Should Not Exist",))
                (n,) = cur.fetchone()
        finally:
            conn.close()
        self.assertEqual(n, 0, "invalid brand must reject the whole create, not partially apply it")

    def test_create_team_invalid_ip_policy_rejected(self):
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)
        resp = client.post(
            "/admin/teams/create",
            data={"name": "Bad Policy Team", "ip_policy": "SUPER_ADMIN_MODE",
                  "csrf_token": "the-real-token"},
        )
        self.assertIn("err=", resp.headers["Location"])
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM teams WHERE name = %s", ("Bad Policy Team",))
                (n,) = cur.fetchone()
        finally:
            conn.close()
        self.assertEqual(n, 0)

    def test_rename_preserves_id_members_and_brands(self):
        tid = self._insert_team("Old Name", brands=["BrandA"])
        member_id = self._insert_user(username="member1", team_id=tid)
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        resp = client.post(
            "/admin/teams/rename",
            data={"team_id": str(tid), "name": "New Name", "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("msg=", resp.headers["Location"])

        row = self._fetch_team(tid)
        self.assertIsNotNone(row, "team id must be unchanged/preserved")
        self.assertEqual(row[1], "New Name")
        self.assertEqual(self._fetch_team_brands(tid), ["BrandA"], "rename must not touch brands")

        _, _, _, _, member_team_id, _, _ = self._fetch_user(member_id)
        self.assertEqual(member_team_id, tid, "rename must not touch team membership")

    def test_rename_rejects_duplicate_name(self):
        self._insert_team("Team One")
        tid_two = self._insert_team("Team Two")
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)
        resp = client.post(
            "/admin/teams/rename",
            data={"team_id": str(tid_two), "name": "Team One", "csrf_token": "the-real-token"},
        )
        self.assertIn("err=", resp.headers["Location"])
        row = self._fetch_team(tid_two)
        self.assertEqual(row[1], "Team Two", "rejected rename must not have changed the name")

    def test_rename_nonexistent_team_rejected(self):
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)
        resp = client.post(
            "/admin/teams/rename",
            data={"team_id": "999999", "name": "Ghost Team", "csrf_token": "the-real-token"},
        )
        self.assertIn("err=", resp.headers["Location"])
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM teams WHERE name = %s", ("Ghost Team",))
                (n,) = cur.fetchone()
        finally:
            conn.close()
        self.assertEqual(n, 0)

    def test_staff_cannot_create_or_rename_team(self):
        tid = self._insert_team("Staff Team")
        staff_id = self._insert_user(username="staff1", team_id=tid, account_status="ACTIVE")
        client = self._client_for(staff_id, is_admin=False, team_id=tid)

        resp = client.post(
            "/admin/teams/create",
            data={"name": "Staff Made Team", "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp.status_code, 403)
        resp2 = client.post(
            "/admin/teams/rename",
            data={"team_id": str(tid), "name": "Renamed By Staff", "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp2.status_code, 403)
        row = self._fetch_team(tid)
        self.assertEqual(row[1], "Staff Team")


def _query_param(location: str, name: str):
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(location).query)
    values = qs.get(name)
    return values[0] if values else None


# --------------------------------------------------------------------------
# 1b. migration_016 idempotence -- real Postgres. `_RealPgTestBase.setUpClass`
# already applies migration_016 once as part of building the schema; this
# class re-applies the exact same SQL file a SECOND time against a live temp
# DB that already has real rows in `team_permission_previews`, and asserts
# nothing is lost/changed. Deliberately does not re-implement the SQL file's
# CREATE TABLE/INDEX logic -- reads and re-executes the actual file so this
# fails if the file itself is ever edited to something non-idempotent.
# --------------------------------------------------------------------------

class Migration016IdempotencePgTests(_RealPgTestBase):
    def test_running_migration_016_twice_does_not_lose_or_duplicate_data(self):
        tid = self._insert_team("Team X", ip_policy="INHERIT", brands=["BrandA"])
        admin_id = self._admin_row("admin1")

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO team_permission_previews "
                        "(token, team_id, new_brands, new_ip_policy, captured_updated_at, created_by) "
                        "VALUES (%s, %s, %s, %s, (SELECT updated_at FROM teams WHERE id = %s), %s)",
                        ("idempotence-check-token", tid, ["BrandA", "BrandB"], "INHERIT", tid, admin_id),
                    )

            # Re-running the migration file itself must be a pure no-op on
            # an already-migrated DB with real data present -- not just
            # "doesn't error", but the existing row must survive verbatim
            # and no duplicate/second table or index gets created.
            with conn:
                with conn.cursor() as cur:
                    cur.execute(_MIGRATION_016_SQL)

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT team_id, new_brands, new_ip_policy, created_by "
                    "FROM team_permission_previews WHERE token = %s",
                    ("idempotence-check-token",),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row, "existing preview row must survive re-running migration_016")
        self.assertEqual(row, (tid, ["BrandA", "BrandB"], "INHERIT", admin_id))

    def _admin_row(self, username):
        return self._insert_user(username=username, is_admin=True, account_status="ACTIVE")


# --------------------------------------------------------------------------
# 2. Preview -> Confirm -- real Postgres; the two-independent-connection
# concurrency case for the competing-confirms race.
# --------------------------------------------------------------------------

class PreviewConfirmPgTests(_RealPgTestBase):
    def _admin(self, username="admin1"):
        return self._insert_user(username=username, is_admin=True, account_status="ACTIVE")

    def _preview(self, client, *, team_id, brands, ip_policy):
        resp = client.post(
            "/admin/teams/preview",
            data={"team_id": str(team_id), "ip_policy": ip_policy, "brands": brands,
                  "csrf_token": "the-real-token"},
        )
        token = _query_param(resp.headers["Location"], "preview")
        self.assertIsNotNone(token, f"no preview token in {resp.headers['Location']!r}")
        return token

    def test_preview_does_not_write(self):
        self._insert_product(brand="BrandA", code="C1")
        self._insert_product(brand="BrandB", code="C2")
        tid = self._insert_team("Team X", ip_policy="INHERIT", brands=["BrandA"])
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        self._preview(client, team_id=tid, brands=["BrandB"], ip_policy="ALLOWLIST_ONLY")

        # Nothing about the team may have changed just from previewing.
        self.assertEqual(self._fetch_team_brands(tid), ["BrandA"])
        _, _, ip_policy, _ = self._fetch_team(tid)
        self.assertEqual(ip_policy, "INHERIT")
        self.assertEqual(self._count_audits(), 0, "preview must not write an audit row either")

    def test_confirm_applies_previewed_brand_and_policy_diff(self):
        self._insert_product(brand="BrandA", code="C1")
        self._insert_product(brand="BrandB", code="C2")
        tid = self._insert_team("Team X", ip_policy="INHERIT", brands=["BrandA"])
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        token = self._preview(client, team_id=tid, brands=["BrandB"], ip_policy="ALLOWLIST_ONLY")
        resp = client.post(
            "/admin/teams/confirm",
            data={"preview_token": token, "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("msg=", resp.headers["Location"])

        self.assertEqual(self._fetch_team_brands(tid), ["BrandB"])
        _, _, ip_policy, _ = self._fetch_team(tid)
        self.assertEqual(ip_policy, "ALLOWLIST_ONLY")
        self.assertEqual(self._count_audits(), 2, "one audit row for brands, one for ip_policy")

    def test_confirm_ignores_client_supplied_brand_and_policy_fields(self):
        """Confirm must apply ONLY the server-stored preview record -- extra
        form fields on the confirm POST claiming a different brand/policy
        must have zero effect (the route never even reads them).
        """
        self._insert_product(brand="BrandA", code="C1")
        self._insert_product(brand="BrandB", code="C2")
        self._insert_product(brand="BrandC", code="C3")
        tid = self._insert_team("Team X", ip_policy="INHERIT", brands=["BrandA"])
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        token = self._preview(client, team_id=tid, brands=["BrandB"], ip_policy="ALLOWLIST_ONLY")
        resp = client.post(
            "/admin/teams/confirm",
            data={"preview_token": token, "csrf_token": "the-real-token",
                  # Forged/stale extra fields a tampered client might send:
                  "brands": ["BrandC"], "ip_policy": "ANY_AUTHENTICATED",
                  "team_id": "999999"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("msg=", resp.headers["Location"])

        # Applied brand set is the PREVIEWED one (BrandB), never the
        # forged one (BrandC) or a mix.
        self.assertEqual(self._fetch_team_brands(tid), ["BrandB"])
        _, _, ip_policy, _ = self._fetch_team(tid)
        self.assertEqual(ip_policy, "ALLOWLIST_ONLY")

    def test_confirm_rejects_stale_after_direct_team_change(self):
        self._insert_product(brand="BrandA", code="C1")
        self._insert_product(brand="BrandB", code="C2")
        tid = self._insert_team("Team X", ip_policy="INHERIT", brands=["BrandA"])
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        token = self._preview(client, team_id=tid, brands=["BrandB"], ip_policy="INHERIT")

        # Someone else changes the team directly (a second, already-applied
        # confirm) BEFORE our preview is confirmed.
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE teams SET updated_at = NOW() WHERE id = %s", (tid,))
        finally:
            conn.close()

        resp = client.post(
            "/admin/teams/confirm",
            data={"preview_token": token, "csrf_token": "the-real-token"},
        )
        self.assertIn("err=", resp.headers["Location"])
        self.assertEqual(self._fetch_team_brands(tid), ["BrandA"], "stale confirm must not have applied")

    def test_confirm_rejects_stale_after_membership_change(self):
        """Fix2: a team's *membership* changing (a user moved into/out of
        it via the ordinary LOCAL user-assignment form) since the preview
        was captured must ALSO be treated as stale -- not just a direct
        brand/ip_policy edit. This exercises the
        `admin_google_users.touch_team_updated_at` fix wired into
        `search.py`'s legacy `update_user` action.
        """
        self._insert_product(brand="BrandA", code="C1")
        self._insert_product(brand="BrandB", code="C2")
        tid = self._insert_team("Team X", ip_policy="INHERIT", brands=["BrandA"])
        other_team = self._insert_team("Other Team", brands=["BrandB"])
        member_id = self._insert_user(username="member1", team_id=other_team, account_status="ACTIVE")
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        token = self._preview(client, team_id=tid, brands=["BrandB"], ip_policy="INHERIT")

        # A member is moved INTO `tid` via the ordinary LOCAL admin form --
        # no brand/ip_policy field touched directly, but membership of
        # `tid` did change.
        move_resp = client.post(
            "/admin/users",
            data={"action": "update_user", "user_id": str(member_id), "role": "staff",
                  "team_id": str(tid), "csrf_token": "the-real-token"},
        )
        self.assertEqual(move_resp.status_code, 302)

        resp = client.post(
            "/admin/teams/confirm",
            data={"preview_token": token, "csrf_token": "the-real-token"},
        )
        self.assertIn("err=", resp.headers["Location"], "membership change since preview must force a re-preview")
        self.assertEqual(self._fetch_team_brands(tid), ["BrandA"], "stale confirm must not have applied")

    def test_confirm_rejects_when_confirmed_by_a_different_admin(self):
        self._insert_product(brand="BrandA", code="C1")
        self._insert_product(brand="BrandB", code="C2")
        tid = self._insert_team("Team X", brands=["BrandA"])
        admin_a = self._admin("admin_a")
        admin_b = self._admin("admin_b")
        client_a = self._client_for(admin_a, is_admin=True, username="admin_a")
        client_b = self._client_for(admin_b, is_admin=True, username="admin_b")

        token = self._preview(client_a, team_id=tid, brands=["BrandB"], ip_policy="INHERIT")

        resp = client_b.post(
            "/admin/teams/confirm",
            data={"preview_token": token, "csrf_token": "the-real-token"},
        )
        self.assertIn("err=", resp.headers["Location"], "a DIFFERENT admin's session must not confirm this token")
        self.assertEqual(self._fetch_team_brands(tid), ["BrandA"])

        # The original admin (who actually created the preview) can still
        # confirm -- proves the rejection above was specifically about the
        # WRONG admin, not a broken/expired token.
        token2 = self._preview(client_a, team_id=tid, brands=["BrandB"], ip_policy="INHERIT")
        resp2 = client_a.post(
            "/admin/teams/confirm",
            data={"preview_token": token2, "csrf_token": "the-real-token"},
        )
        self.assertIn("msg=", resp2.headers["Location"])
        self.assertEqual(self._fetch_team_brands(tid), ["BrandB"])

    def test_wrong_admin_confirm_attempt_does_not_consume_the_real_owners_token(self):
        """Phase 6A -- Local Release Gate finding: a rejected confirm from a
        DIFFERENT admin (test above) must not itself burn the token. If it
        did, the "wrong admin" case above would be indistinguishable from a
        harmless information probe becoming a denial-of-service against the
        real owner's in-flight preview -- admin_b (or anyone who merely
        observes/guesses admin_a's token, e.g. over a shared screen or
        browser history) could force admin_a to redo the preview step with
        no legitimate access of their own. Proven here by having admin_b
        attempt (and get rejected) TWICE, then having admin_a successfully
        confirm the SAME original token afterwards.
        """
        self._insert_product(brand="BrandA", code="C1")
        self._insert_product(brand="BrandB", code="C2")
        tid = self._insert_team("Team X", brands=["BrandA"])
        admin_a = self._admin("admin_a2")
        admin_b = self._admin("admin_b2")
        client_a = self._client_for(admin_a, is_admin=True, username="admin_a2")
        client_b = self._client_for(admin_b, is_admin=True, username="admin_b2")

        token = self._preview(client_a, team_id=tid, brands=["BrandB"], ip_policy="INHERIT")

        for _ in range(2):
            wrong_resp = client_b.post(
                "/admin/teams/confirm",
                data={"preview_token": token, "csrf_token": "the-real-token"},
            )
            self.assertIn("err=", wrong_resp.headers["Location"])
            self.assertEqual(self._fetch_team_brands(tid), ["BrandA"],
                              "a wrong-admin confirm attempt must never apply the diff")

        # The SAME original token, presented by the actual owner, must
        # still work -- proves admin_b's attempts did not consume it.
        real_resp = client_a.post(
            "/admin/teams/confirm",
            data={"preview_token": token, "csrf_token": "the-real-token"},
        )
        self.assertIn("msg=", real_resp.headers["Location"],
                       "the real owner's original token must survive unrelated wrong-admin attempts")
        self.assertEqual(self._fetch_team_brands(tid), ["BrandB"])

    def test_confirm_rejects_unknown_or_reused_token(self):
        tid = self._insert_team("Team X", brands=["BrandA"])
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        resp = client.post(
            "/admin/teams/confirm",
            data={"preview_token": "not-a-real-token", "csrf_token": "the-real-token"},
        )
        self.assertIn("err=", resp.headers["Location"])

        # A token, once confirmed, is one-time-use -- confirming again must
        # also fail (already popped from the store).
        self._insert_product(brand="BrandB", code="C2")
        token = self._preview(client, team_id=tid, brands=["BrandB"], ip_policy="INHERIT")
        first = client.post("/admin/teams/confirm",
                             data={"preview_token": token, "csrf_token": "the-real-token"})
        self.assertIn("msg=", first.headers["Location"])
        second = client.post("/admin/teams/confirm",
                              data={"preview_token": token, "csrf_token": "the-real-token"})
        self.assertIn("err=", second.headers["Location"])

    def test_staff_cannot_preview_or_confirm(self):
        tid = self._insert_team("Team X", brands=["BrandA"])
        staff_id = self._insert_user(username="staff1", team_id=tid, account_status="ACTIVE")
        client = self._client_for(staff_id, is_admin=False, team_id=tid)

        resp = client.post(
            "/admin/teams/preview",
            data={"team_id": str(tid), "ip_policy": "INHERIT", "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp.status_code, 403)
        resp2 = client.post(
            "/admin/teams/confirm",
            data={"preview_token": "whatever", "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp2.status_code, 403)

    def test_confirm_rejected_when_actor_no_longer_an_active_admin(self):
        """Lighter proxy for "actor bị thu hồi" than the full lock-wait
        thread race already covered generically elsewhere (see module
        docstring): the acting admin's account is suspended BEFORE the
        confirm request is made. `session_security` rejects it at the
        outer layer before `admin_teams.confirm_permissions` even runs,
        which is itself a valid (simpler) proof that a revoked actor
        cannot mutate team permissions.
        """
        self._insert_product(brand="BrandA", code="C1")
        self._insert_product(brand="BrandB", code="C2")
        tid = self._insert_team("Team X", brands=["BrandA"])
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)
        token = self._preview(client, team_id=tid, brands=["BrandB"], ip_policy="INHERIT")

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE app_users SET account_status = 'SUSPENDED', "
                        "auth_version = auth_version + 1 WHERE id = %s",
                        (admin_id,),
                    )
        finally:
            conn.close()

        resp = client.post(
            "/admin/teams/confirm",
            data={"preview_token": token, "csrf_token": "the-real-token"},
        )
        self.assertNotEqual(resp.status_code, 200)
        self.assertEqual(self._fetch_team_brands(tid), ["BrandA"], "revoked actor's confirm must not apply")

    def test_two_competing_confirms_for_the_same_team_dont_both_win(self):
        """REAL Postgres, two independent connections/threads: two previews
        for the SAME team, both captured against the same initial
        `updated_at` (nobody has changed the team yet), confirmed
        concurrently. The `teams... FOR UPDATE` row lock must serialize
        them so exactly one wins; the loser must see the row it locked
        already stamped with a NEWER `updated_at` than what it captured,
        and reject as stale -- never both applying, never a mixed/corrupt
        final brand set.
        """
        self._insert_product(brand="BrandA", code="C1")
        self._insert_product(brand="BrandB", code="C2")
        self._insert_product(brand="BrandC", code="C3")
        tid = self._insert_team("Team X", ip_policy="INHERIT", brands=["BrandA"])
        admin_id = self._admin()
        client1 = self._client_for(admin_id, is_admin=True)
        client2 = self._client_for(admin_id, is_admin=True)

        token1 = self._preview(client1, team_id=tid, brands=["BrandA", "BrandB"], ip_policy="INHERIT")
        token2 = self._preview(client2, team_id=tid, brands=["BrandC"], ip_policy="INHERIT")

        barrier = threading.Barrier(2)
        results = {}

        def confirm1():
            barrier.wait(timeout=10)
            resp = client1.post("/admin/teams/confirm",
                                 data={"preview_token": token1, "csrf_token": "the-real-token"})
            results["c1"] = resp.headers["Location"]

        def confirm2():
            barrier.wait(timeout=10)
            resp = client2.post("/admin/teams/confirm",
                                 data={"preview_token": token2, "csrf_token": "the-real-token"})
            results["c2"] = resp.headers["Location"]

        t1 = threading.Thread(target=confirm1)
        t2 = threading.Thread(target=confirm2)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())

        outcomes = [("msg" if "msg=" in loc else "err") for loc in results.values()]
        self.assertEqual(sorted(outcomes), ["err", "msg"],
                          f"exactly one confirm must win, got {results}")

        final_brands = set(self._fetch_team_brands(tid))
        self.assertIn(final_brands, [{"BrandA", "BrandB"}, {"BrandC"}],
                      "final state must be exactly ONE full preview applied, never a mix")


# --------------------------------------------------------------------------
# 3. admin_google_users.update() -- real Postgres, real Flask view.
# --------------------------------------------------------------------------

class GoogleUpdatePgTests(_RealPgTestBase):
    def _admin(self, username="admin1"):
        return self._insert_user(username=username, is_admin=True, account_status="ACTIVE")

    def _google_staff(self, team_id, *, username="g_staff", account_status="ACTIVE", auth_version=1):
        return self._insert_user(username=username, email=f"{username}@x.vn", auth_provider="GOOGLE",
                                  google_sub=f"sub-{username}", team_id=team_id, is_admin=False,
                                  account_status=account_status, auth_version=auth_version)

    def test_update_changes_role_and_team_bumps_auth_version(self):
        team_a = self._insert_team("Team A")
        team_b = self._insert_team("Team B")
        target = self._google_staff(team_a)
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        resp = client.post(
            "/admin/users/google/update",
            data={"user_id": str(target), "role": "staff", "team_id": str(team_b),
                  "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("msg=", resp.headers["Location"])

        status, is_admin, auth_version, provider, team_id, google_sub, _ = self._fetch_user(target)
        self.assertEqual(status, "ACTIVE")
        self.assertFalse(is_admin)
        self.assertEqual(team_id, team_b)
        self.assertEqual(auth_version, 2, "auth_version must be bumped so old sessions are invalidated")
        self.assertEqual(provider, "GOOGLE")
        self.assertEqual(google_sub, f"sub-g_staff", "google_sub must be untouched")

    def test_old_session_rejected_on_next_request_after_update(self):
        team_a = self._insert_team("Team A")
        team_b = self._insert_team("Team B")
        target = self._google_staff(team_a)
        admin_id = self._admin()
        admin_client = self._client_for(admin_id, is_admin=True)
        target_client = self._client_for(target, is_admin=False, team_id=team_a, auth_version=1)

        # Prove the target's OWN session works before the change.
        pre_resp = target_client.get("/admin/teams")
        self.assertEqual(pre_resp.status_code, 403, "staff, not admin -- but NOT redirected as an invalid session")

        admin_client.post(
            "/admin/users/google/update",
            data={"user_id": str(target), "role": "staff", "team_id": str(team_b),
                  "csrf_token": "the-real-token"},
        )

        # Same OLD cookie (still carries auth_version=1) -- next request
        # must be rejected/redirected as an invalid session, not silently
        # keep working with stale team_id=team_a cached in the cookie.
        post_resp = target_client.get("/admin/teams", follow_redirects=False)
        self.assertNotEqual(post_resp.status_code, 403,
                             "must not reach the normal 403 path -- session must be invalidated first")
        self.assertIn(post_resp.status_code, (302, 401))

    def test_update_rejects_invalid_role(self):
        team_a = self._insert_team("Team A")
        target = self._google_staff(team_a)
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)
        resp = client.post(
            "/admin/users/google/update",
            data={"user_id": str(target), "role": "superuser", "team_id": str(team_a),
                  "csrf_token": "the-real-token"},
        )
        self.assertIn("err=", resp.headers["Location"])
        status, is_admin, auth_version, _, team_id, _, _ = self._fetch_user(target)
        self.assertEqual((is_admin, team_id, auth_version), (False, team_a, 1))

    def test_update_staff_requires_existing_team(self):
        team_a = self._insert_team("Team A")
        target = self._google_staff(team_a)
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        for bad_team in ("", "not-a-number", "999999"):
            resp = client.post(
                "/admin/users/google/update",
                data={"user_id": str(target), "role": "staff", "team_id": bad_team,
                      "csrf_token": "the-real-token"},
            )
            self.assertIn("err=", resp.headers["Location"], bad_team)
        _, _, auth_version, _, team_id, _, _ = self._fetch_user(target)
        self.assertEqual((team_id, auth_version), (team_a, 1), "no partial update on invalid team")

    def test_update_admin_role_forces_team_id_null_ignoring_payload(self):
        team_a = self._insert_team("Team A")
        target = self._google_staff(team_a)
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        resp = client.post(
            "/admin/users/google/update",
            data={"user_id": str(target), "role": "admin", "team_id": str(team_a),
                  "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("msg=", resp.headers["Location"])
        _, is_admin, _, _, team_id, _, _ = self._fetch_user(target)
        self.assertTrue(is_admin)
        self.assertIsNone(team_id, "promoting to admin must force team_id=NULL regardless of submitted team_id")

    def test_update_rejects_non_active_targets(self):
        team_a = self._insert_team("Team A")
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)
        for status in ("PENDING", "INVITED", "SUSPENDED"):
            target = self._google_staff(team_a, username=f"g_{status.lower()}", account_status=status)
            resp = client.post(
                "/admin/users/google/update",
                data={"user_id": str(target), "role": "staff", "team_id": str(team_a),
                      "csrf_token": "the-real-token"},
            )
            self.assertIn("err=", resp.headers["Location"], status)
            row = self._fetch_user(target)
            self.assertEqual(row[0], status, f"{status} target must be untouched")
            self.assertEqual(row[2], 1, f"{status} target's auth_version must not bump")

    def test_update_cannot_change_auth_provider_or_google_sub_via_payload(self):
        team_a = self._insert_team("Team A")
        target = self._google_staff(team_a)
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        resp = client.post(
            "/admin/users/google/update",
            data={"user_id": str(target), "role": "staff", "team_id": str(team_a),
                  # Forged fields the route never reads:
                  "auth_provider": "LOCAL", "google_sub": "attacker-controlled-sub",
                  "account_status": "SUSPENDED", "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp.status_code, 302)
        status, _, _, provider, _, google_sub, _ = self._fetch_user(target)
        self.assertEqual(provider, "GOOGLE", "auth_provider must be immutable via this payload")
        self.assertEqual(google_sub, "sub-g_staff", "google_sub must be immutable via this payload")
        self.assertEqual(status, "ACTIVE", "account_status must be immutable via this payload")

    def test_update_self_demote_blocked(self):
        team_a = self._insert_team("Team A")
        admin_id = self._insert_user(username="g_admin", email="g_admin@x.vn", auth_provider="GOOGLE",
                                      google_sub="sub-g_admin", is_admin=True, account_status="ACTIVE")
        client = self._client_for(admin_id, is_admin=True)
        resp = client.post(
            "/admin/users/google/update",
            data={"user_id": str(admin_id), "role": "staff", "team_id": str(team_a),
                  "csrf_token": "the-real-token"},
        )
        self.assertIn("err=", resp.headers["Location"])
        _, is_admin, auth_version, _, _, _, _ = self._fetch_user(admin_id)
        self.assertTrue(is_admin)
        self.assertEqual(auth_version, 1)

    # NOTE: a "last admin blocked" test with a DISTINCT actor is not
    # constructible as an ordinary single-request scenario here: `update()`
    # requires the acting session to itself be a currently-ACTIVE admin
    # (`revalidate_actor`), and the COUNT query excludes only the TARGET's
    # id -- so a legitimate, distinct actor is, by construction, always
    # counted as "another active admin" (count >= 1). Real Postgres already
    # proves `other_active_admins == 0` only actually arises from a genuine
    # RACE (two admins reducing the count at the same instant), which
    # `test_admin_pg_integration.py`'s `RealConcurrencyTests` already
    # covers end-to-end against the identical shared lock/count query --
    # not repeated here per "không lặp toàn bộ race matrix cũ nếu không
    # sửa cơ chế khóa chung" (this fix touched neither the lock nor the
    # count query). `test_update_self_demote_blocked` above covers the one
    # last-admin-adjacent case that IS reachable in a single request.

    def test_update_touches_old_and_new_team_updated_at(self):
        team_a = self._insert_team("Team A")
        team_b = self._insert_team("Team B")
        target = self._google_staff(team_a)
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        _, _, _, before_a = self._fetch_team(team_a)
        _, _, _, before_b = self._fetch_team(team_b)
        time.sleep(0.01)

        client.post(
            "/admin/users/google/update",
            data={"user_id": str(target), "role": "staff", "team_id": str(team_b),
                  "csrf_token": "the-real-token"},
        )

        _, _, _, after_a = self._fetch_team(team_a)
        _, _, _, after_b = self._fetch_team(team_b)
        self.assertGreater(after_a, before_a, "team being LEFT must be touched")
        self.assertGreater(after_b, before_b, "team being JOINED must be touched")

    def test_staff_cannot_call_update_directly(self):
        team_a = self._insert_team("Team A")
        target = self._google_staff(team_a)
        staff_id = self._insert_user(username="staff1", team_id=team_a, account_status="ACTIVE")
        client = self._client_for(staff_id, is_admin=False, team_id=team_a)
        resp = client.post(
            "/admin/users/google/update",
            data={"user_id": str(target), "role": "admin", "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp.status_code, 403)
        _, is_admin, auth_version, _, _, _, _ = self._fetch_user(target)
        self.assertFalse(is_admin)
        self.assertEqual(auth_version, 1)

    def test_wrong_csrf_token_no_mutation(self):
        team_a = self._insert_team("Team A")
        target = self._google_staff(team_a)
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)
        resp = client.post(
            "/admin/users/google/update",
            data={"user_id": str(target), "role": "admin", "csrf_token": "wrong-token"},
        )
        self.assertEqual(resp.status_code, 400)
        _, is_admin, auth_version, _, _, _, _ = self._fetch_user(target)
        self.assertFalse(is_admin)
        self.assertEqual(auth_version, 1)


# --------------------------------------------------------------------------
# 4. Cross-cutting LOCAL/GOOGLE permission equivalence -- real Postgres,
# REAL middleware stack (session_security + middleware_access), NOT using
# DISABLE_IP_ALLOWLIST. teams.ip_policy defaults to 'INHERIT' and
# office_ip_allowlist is empty -- the documented "zero rules configured =>
# allow" INHERIT contract (middleware_access.py) -- so the IP layer is
# genuinely active but permissive while the TEAM/BRAND layer is what's
# under test.
#
# Phase 6A-UAT: this class now hits BOTH real endpoints with their own
# helpers, named for what they actually call (Fix2 mislabeled its
# preflight-only helper/tests as "Match"):
#   - `_call_preflight_endpoint` -> real HTTP POST to
#     /api/quote-assistant/preflight ("Checks Code/CAS existence... using
#     team visibility" per its own docstring -- no candidates/pricing).
#   - `_call_match_endpoint` -> real HTTP POST to
#     /api/quote-assistant/match (the actual candidate-matching endpoint;
#     returns `candidates`/`status` per row).
# "Export" now has TWO layers, both real-HTTP where noted:
#   - `_direct_export_products` (existing, unchanged): a DIRECT call to
#     `search._quote_export_products` inside a real request context --
#     deliberately bypasses the xlsx-template/file-upload plumbing to keep
#     the brand-visibility SQL itself isolated from unrelated concerns.
#   - `ExportWorkbookHttpTests` below: real HTTP POST to
#     /api/quote-assistant/workbook/export with an uploaded, VALID minimal
#     BG-format .xlsx template (built by
#     `test_quote_workbook_export.make_workbook`, the same template-builder
#     the workbook-export unit tests already use) so the request actually
#     reaches `_quote_export_products`'s permission check instead of
#     failing earlier on a missing/invalid template or malformed payload.
# --------------------------------------------------------------------------

class CrossCuttingPermissionPgTests(_RealPgTestBase):
    def setUp(self):
        super().setUp()
        self.brand_a_product = self._insert_product(brand="BrandA", code="CODE-A1", cas="111-11-1", price="1000", ship="1")
        self.brand_b_product = self._insert_product(brand="BrandB", code="CODE-B1", cas="222-22-2", price="2000", ship="1")
        self.team_alpha = self._insert_team("Team Alpha", brands=["BrandA"])
        self.team_beta = self._insert_team("Team Beta", brands=["BrandB"])
        self.team_empty = self._insert_team("Team Empty", brands=[])

    def _call_preflight_endpoint(self, client, code):
        """Real HTTP to the PREFLIGHT endpoint -- existence/conflict check
        only, no candidates or pricing. NOT the same thing as Match; kept
        as its own helper (used by the preflight-specific tests below) so
        neither name is misleading about which endpoint it drives.
        """
        resp = client.post("/api/quote-assistant/preflight", json={"rows": [{"code": code}]})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()["results"][0]

    def _call_match_endpoint(self, client, code):
        """Real HTTP to the actual Match endpoint
        (`/api/quote-assistant/match`) -- returns `candidates`/`status` for
        the row, unlike preflight which only reports existence/conflict.
        """
        resp = client.post("/api/quote-assistant/match", json={"rows": [{"code": code}]})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()["results"][0]

    def test_ip_middleware_is_actually_active_not_bypassed(self):
        """Sanity check that this class is genuinely exercising the real IP
        middleware (not accidentally disabled) -- ALLOWLIST_ONLY with zero
        configured rules must deny, per its documented contract.
        """
        strict_team = self._insert_team("Strict Team", ip_policy="ALLOWLIST_ONLY", brands=["BrandA"])
        staff_id = self._insert_user(username="strict_staff", team_id=strict_team, account_status="ACTIVE")
        client = self._client_for(staff_id, is_admin=False, team_id=strict_team)
        resp = client.get("/admin/teams")  # any non-exempt endpoint
        self.assertEqual(resp.status_code, 403)

    # ---- PREFLIGHT endpoint (existence/conflict check only) --------------

    def test_local_and_google_same_team_get_equivalent_preflight_results(self):
        local_id = self._insert_user(username="local_staff", team_id=self.team_alpha, account_status="ACTIVE")
        google_id = self._insert_user(username="google_staff", email="google_staff@x.vn",
                                       auth_provider="GOOGLE", google_sub="sub-google-staff",
                                       team_id=self.team_alpha, account_status="ACTIVE")
        local_client = self._client_for(local_id, team_id=self.team_alpha)
        google_client = self._client_for(google_id, team_id=self.team_alpha)

        local_visible = self._call_preflight_endpoint(local_client, "CODE-A1")
        google_visible = self._call_preflight_endpoint(google_client, "CODE-A1")
        self.assertEqual(local_visible["preflight_status"], "FOUND")
        self.assertEqual(google_visible["preflight_status"], "FOUND")
        self.assertEqual(local_visible["match_count"], google_visible["match_count"])

        local_hidden = self._call_preflight_endpoint(local_client, "CODE-B1")
        google_hidden = self._call_preflight_endpoint(google_client, "CODE-B1")
        self.assertEqual(local_hidden["preflight_status"], "NO_MATCH")
        self.assertEqual(google_hidden["preflight_status"], "NO_MATCH")

    def test_preflight_does_not_leak_across_teams(self):
        alpha_staff = self._insert_user(username="alpha_staff", team_id=self.team_alpha, account_status="ACTIVE")
        beta_staff = self._insert_user(username="beta_staff", team_id=self.team_beta, account_status="ACTIVE")
        alpha_client = self._client_for(alpha_staff, team_id=self.team_alpha)
        beta_client = self._client_for(beta_staff, team_id=self.team_beta)

        self.assertEqual(self._call_preflight_endpoint(alpha_client, "CODE-A1")["preflight_status"], "FOUND")
        self.assertEqual(self._call_preflight_endpoint(alpha_client, "CODE-B1")["preflight_status"], "NO_MATCH")
        self.assertEqual(self._call_preflight_endpoint(beta_client, "CODE-B1")["preflight_status"], "FOUND")
        self.assertEqual(self._call_preflight_endpoint(beta_client, "CODE-A1")["preflight_status"], "NO_MATCH")

    def test_empty_team_sees_nothing_via_preflight(self):
        empty_staff = self._insert_user(username="empty_staff", team_id=self.team_empty, account_status="ACTIVE")
        client = self._client_for(empty_staff, team_id=self.team_empty)
        self.assertEqual(self._call_preflight_endpoint(client, "CODE-A1")["preflight_status"], "NO_MATCH")
        self.assertEqual(self._call_preflight_endpoint(client, "CODE-B1")["preflight_status"], "NO_MATCH")

    # ---- MATCH endpoint (real /api/quote-assistant/match, real candidates) --

    def test_local_and_google_same_team_get_equivalent_match_results(self):
        """Real HTTP to /api/quote-assistant/match (not preflight): LOCAL
        and GOOGLE staff on the SAME team must see the same candidate for
        an in-team code, and the same "no match" for an out-of-team code.
        """
        local_id = self._insert_user(username="local_staff", team_id=self.team_alpha, account_status="ACTIVE")
        google_id = self._insert_user(username="google_staff", email="google_staff@x.vn",
                                       auth_provider="GOOGLE", google_sub="sub-google-staff",
                                       team_id=self.team_alpha, account_status="ACTIVE")
        local_client = self._client_for(local_id, team_id=self.team_alpha)
        google_client = self._client_for(google_id, team_id=self.team_alpha)

        local_hit = self._call_match_endpoint(local_client, "CODE-A1")
        google_hit = self._call_match_endpoint(google_client, "CODE-A1")
        self.assertEqual(local_hit["status"], "MATCHED")
        self.assertEqual(google_hit["status"], "MATCHED")
        self.assertEqual(
            [c["product_id"] for c in local_hit["candidates"]],
            [c["product_id"] for c in google_hit["candidates"]],
        )
        self.assertEqual([c["product_id"] for c in local_hit["candidates"]], [self.brand_a_product])

        local_miss = self._call_match_endpoint(local_client, "CODE-B1")
        google_miss = self._call_match_endpoint(google_client, "CODE-B1")
        self.assertEqual(local_miss["status"], "UNRESOLVED")
        self.assertEqual(google_miss["status"], "UNRESOLVED")
        self.assertEqual(local_miss["candidates"], [])
        self.assertEqual(google_miss["candidates"], [])

    def test_match_does_not_return_out_of_team_candidates(self):
        """Real HTTP to /api/quote-assistant/match: a team's own in-team
        code returns its candidate; a DIFFERENT team's code returns none
        -- the out-of-team product must never appear in `candidates`.
        """
        alpha_staff = self._insert_user(username="alpha_staff", team_id=self.team_alpha, account_status="ACTIVE")
        beta_staff = self._insert_user(username="beta_staff", team_id=self.team_beta, account_status="ACTIVE")
        alpha_client = self._client_for(alpha_staff, team_id=self.team_alpha)
        beta_client = self._client_for(beta_staff, team_id=self.team_beta)

        alpha_own = self._call_match_endpoint(alpha_client, "CODE-A1")
        self.assertEqual([c["product_id"] for c in alpha_own["candidates"]], [self.brand_a_product])
        alpha_foreign = self._call_match_endpoint(alpha_client, "CODE-B1")
        self.assertEqual(alpha_foreign["candidates"], [])
        self.assertNotIn(self.brand_b_product, [c.get("product_id") for c in alpha_foreign["candidates"]])

        beta_own = self._call_match_endpoint(beta_client, "CODE-B1")
        self.assertEqual([c["product_id"] for c in beta_own["candidates"]], [self.brand_b_product])
        beta_foreign = self._call_match_endpoint(beta_client, "CODE-A1")
        self.assertEqual(beta_foreign["candidates"], [])

    def test_match_empty_team_sees_no_candidates(self):
        empty_staff = self._insert_user(username="empty_staff", team_id=self.team_empty, account_status="ACTIVE")
        client = self._client_for(empty_staff, team_id=self.team_empty)
        for code in ("CODE-A1", "CODE-B1"):
            result = self._call_match_endpoint(client, code)
            self.assertEqual(result["candidates"], [])
            self.assertEqual(result["status"], "UNRESOLVED")

    # ---- Export: direct function call (isolates the visibility SQL) ------

    def test_export_direct_call_does_not_leak_across_teams(self):
        from flask import session as flask_session

        with search.app.test_request_context():
            flask_session["authenticated"] = True
            flask_session["is_admin"] = False
            flask_session["team_id"] = self.team_alpha

            conn = self._connect()
            try:
                visible = search._quote_export_products(
                    conn, [{"ord": 1, "product_id": self.brand_a_product}]
                )
                self.assertEqual(len(visible), 1)
                self.assertEqual(visible[0]["Brand"], "BrandA")

                with self.assertRaises(ValueError):
                    search._quote_export_products(
                        conn, [{"ord": 1, "product_id": self.brand_b_product}]
                    )
            finally:
                conn.close()

    def test_export_direct_call_empty_team_sees_nothing(self):
        from flask import session as flask_session

        with search.app.test_request_context():
            flask_session["authenticated"] = True
            flask_session["is_admin"] = False
            flask_session["team_id"] = self.team_empty

            conn = self._connect()
            try:
                with self.assertRaises(ValueError):
                    search._quote_export_products(
                        conn, [{"ord": 1, "product_id": self.brand_a_product}]
                    )
            finally:
                conn.close()

    def test_team_brand_change_takes_effect_on_next_request_without_relogin(self):
        staff_id = self._insert_user(username="alpha_staff", team_id=self.team_alpha, account_status="ACTIVE")
        staff_client = self._client_for(staff_id, team_id=self.team_alpha)
        admin_id = self._insert_user(username="admin1", is_admin=True, account_status="ACTIVE")
        admin_client = self._client_for(admin_id, is_admin=True)

        # Before: BrandA visible, BrandB not.
        self.assertEqual(self._call_preflight_endpoint(staff_client, "CODE-A1")["preflight_status"], "FOUND")
        self.assertEqual(self._call_preflight_endpoint(staff_client, "CODE-B1")["preflight_status"], "NO_MATCH")

        preview_resp = admin_client.post(
            "/admin/teams/preview",
            data={"team_id": str(self.team_alpha), "ip_policy": "INHERIT", "brands": ["BrandB"],
                  "csrf_token": "the-real-token"},
        )
        token = _query_param(preview_resp.headers["Location"], "preview")
        confirm_resp = admin_client.post(
            "/admin/teams/confirm",
            data={"preview_token": token, "csrf_token": "the-real-token"},
        )
        self.assertIn("msg=", confirm_resp.headers["Location"])

        # After, SAME staff cookie/session (no re-login, no auth_version
        # change needed for a TEAM-level permission change): BrandB now
        # visible, BrandA no longer.
        self.assertEqual(self._call_preflight_endpoint(staff_client, "CODE-B1")["preflight_status"], "FOUND")
        self.assertEqual(self._call_preflight_endpoint(staff_client, "CODE-A1")["preflight_status"], "NO_MATCH")

    def test_team_ip_policy_change_takes_effect_on_next_request_without_relogin(self):
        """Same "no re-login needed" contract as the brand-change test
        above, but for `teams.ip_policy` -- proven at the IP-middleware
        layer (a 403 from `middleware_access`, not a brand/candidate
        difference from `search.py`). Team starts at INHERIT with an
        empty `office_ip_allowlist` (documented "no rules => allow"), so
        the staff member's very first request succeeds; admin then
        confirms a switch to ALLOWLIST_ONLY (still zero rows in
        `office_ip_allowlist` -- documented "no rules => deny"); the SAME
        staff session's very next request must now be denied, with no
        re-login and no `auth_version` bump (this is a team-level policy
        change, not a per-user role/team reassignment).
        """
        staff_id = self._insert_user(username="alpha_staff2", team_id=self.team_alpha, account_status="ACTIVE")
        staff_client = self._client_for(staff_id, team_id=self.team_alpha)
        admin_id = self._insert_user(username="admin2", is_admin=True, account_status="ACTIVE")
        admin_client = self._client_for(admin_id, is_admin=True)

        # Before: INHERIT + empty allowlist => allowed (any authenticated,
        # non-IP-gated business endpoint works; use preflight, same as the
        # brand-change test above, to avoid an unrelated dependency on
        # migration_013's quote_templates table).
        before = staff_client.post("/api/quote-assistant/preflight", json={"rows": [{"code": "CODE-A1"}]})
        self.assertEqual(before.status_code, 200, before.get_data(as_text=True))

        preview_resp = admin_client.post(
            "/admin/teams/preview",
            data={"team_id": str(self.team_alpha), "ip_policy": "ALLOWLIST_ONLY", "brands": ["BrandA"],
                  "csrf_token": "the-real-token"},
        )
        token = _query_param(preview_resp.headers["Location"], "preview")
        confirm_resp = admin_client.post(
            "/admin/teams/confirm",
            data={"preview_token": token, "csrf_token": "the-real-token"},
        )
        self.assertIn("msg=", confirm_resp.headers["Location"])

        # After, SAME staff cookie/session, no re-login: denied outright by
        # the IP middleware (403), before the route's own logic even runs.
        after = staff_client.post("/api/quote-assistant/preflight", json={"rows": [{"code": "CODE-A1"}]})
        self.assertEqual(after.status_code, 403, after.get_data(as_text=True))


# --------------------------------------------------------------------------
# 5. Every DB entrypoint really targets the temp test DB.
#
# Phase 6A-UAT explicitly calls out that "no rows changed" is NOT proof a
# test didn't touch `products_local` -- a test could, by construction,
# never generate a write and that would say nothing about which DATABASE
# it was pointed at. This class instead asks each entrypoint's own
# connection what database it's actually connected to
# (`conn.get_dsn_parameters()['dbname']`) and asserts it's THIS test's
# temp DB, not `products_local` or anything else -- direct proof, not an
# inference from row counts.
# --------------------------------------------------------------------------

class DbConnectionEntrypointPgTests(_RealPgTestBase):
    def _assert_targets_test_db(self, conn):
        try:
            dbname = conn.get_dsn_parameters().get("dbname")
        finally:
            conn.close()
        self.assertEqual(dbname, self.test_db_name)

    def test_db_module_get_connection_targets_test_db(self):
        self._assert_targets_test_db(db_get_connection())

    def test_admin_teams_get_connection_targets_test_db(self):
        self._assert_targets_test_db(admin_teams.get_connection())

    def test_admin_google_users_get_connection_targets_test_db(self):
        self._assert_targets_test_db(admin_google_users.get_connection())

    def test_session_security_get_connection_targets_test_db(self):
        self._assert_targets_test_db(session_security.get_connection())

    def test_middleware_access_get_connection_targets_test_db(self):
        self._assert_targets_test_db(middleware_access.get_connection())

    def test_search_get_connection_targets_test_db(self):
        self._assert_targets_test_db(search.get_connection())

    def test_real_middleware_request_actually_read_from_test_db_not_row_count(self):
        """Complements the above: drives one real HTTP request through the
        REAL `session_security` + `middleware_access` before_request hooks
        (not mocked/bypassed) and confirms it actually queried against
        THIS test DB by planting a row (an office_ip_allowlist CIDR) that
        only exists in the temp DB and observing its effect on the
        response -- if the request were silently hitting some other
        database, this CIDR would not exist there and the request would
        NOT be allowed (ALLOWLIST_ONLY + no matching rule => deny).
        """
        team_id = self._insert_team("Proof Team", ip_policy="ALLOWLIST_ONLY", brands=["BrandA"])
        self._insert_product(brand="BrandA", code="PROOF-1")
        staff_id = self._insert_user(username="proof_staff", team_id=team_id, account_status="ACTIVE")

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO office_ip_allowlist (cidr, label) VALUES (%s, %s)",
                        ("127.0.0.1/32", "proof-row-only-in-temp-db"),
                    )
        finally:
            conn.close()

        client = self._client_for(staff_id, team_id=team_id)
        resp = client.post("/api/quote-assistant/preflight", json={"rows": [{"code": "PROOF-1"}]})
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))


# --------------------------------------------------------------------------
# 6. Export via the REAL HTTP endpoint (/api/quote-assistant/workbook/
# export) with a VALID uploaded .xlsx template, so the request actually
# reaches `_quote_export_products`'s team-visibility check instead of
# failing earlier on a missing template or a malformed payload.
# `make_workbook()`/`product()` are the exact same template-builder/
# fixture helpers `tests/test_quote_workbook_export.py`'s own unit tests
# use, reused rather than re-invented.
# --------------------------------------------------------------------------

class ExportWorkbookHttpTests(_RealPgTestBase):
    def setUp(self):
        super().setUp()
        self.brand_a_product = self._insert_product(brand="BrandA", code="CODE-A1", cas="111-11-1", price="1000", ship="1")
        self.brand_b_product = self._insert_product(brand="BrandB", code="CODE-B1", cas="222-22-2", price="2000", ship="1")
        self.team_alpha = self._insert_team("Team Alpha", brands=["BrandA"])

    def _export(self, client, product_id):
        return client.post(
            "/api/quote-assistant/workbook/export",
            data={
                "workbook": (io.BytesIO(make_workbook()), "quote.xlsx"),
                "selections": json.dumps([{"product_id": product_id}]),
            },
            content_type="multipart/form-data",
        )

    def test_export_allows_in_team_product_and_returns_real_xlsx(self):
        """Positive control: proves the request genuinely reaches the
        exporter (not just failing early for an unrelated reason) by
        checking for a real, well-formed .xlsx response body.
        """
        staff_id = self._insert_user(username="alpha_staff", team_id=self.team_alpha, account_status="ACTIVE")
        client = self._client_for(staff_id, team_id=self.team_alpha)
        resp = self._export(client, self.brand_a_product)
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data()
        self.assertTrue(body.startswith(b"PK\x03\x04"), "response must be a real ZIP/OOXML .xlsx payload")

    def test_export_denies_out_of_team_product(self):
        """The actual gap Fix2 left open: a real HTTP POST (not a direct
        `_quote_export_products` call) exporting a product OUTSIDE the
        caller's team, through a template that is valid (so the request
        doesn't fail for template/payload reasons) -- must still be
        rejected by the permission check, not silently succeed.
        """
        staff_id = self._insert_user(username="alpha_staff2", team_id=self.team_alpha, account_status="ACTIVE")
        client = self._client_for(staff_id, team_id=self.team_alpha)
        resp = self._export(client, self.brand_b_product)
        self.assertEqual(resp.status_code, 400, resp.get_data(as_text=True))
        body = resp.get_json()
        self.assertNotIn("BrandB", json.dumps(body), "error must not leak the denied product's brand/data")

    def test_export_denies_out_of_team_product_for_admin_of_different_team_scope(self):
        """Sanity: an admin (full brand scope) CAN export the same product
        a restricted staff member cannot -- proves the 400 above is really
        about the STAFF member's team scope, not a template/payload defect
        that would reject the request for everyone.
        """
        admin_id = self._insert_user(username="admin_scope", is_admin=True, account_status="ACTIVE")
        admin_client = self._client_for(admin_id, is_admin=True)
        resp = self._export(admin_client, self.brand_b_product)
        self.assertEqual(resp.status_code, 200)


# --------------------------------------------------------------------------
# 7. Preview/confirm across SEPARATE OS PROCESSES -- the actual "multiple
# workers" scenario (each gunicorn worker is its own process; nothing in
# one worker's memory is visible to another). Confirms the Phase 6A-UAT
# fix (moving `admin_teams`'s preview store from a per-process Python dict
# into Postgres, migration_016) by creating the preview in THIS test
# process and consuming it from a genuinely separate `multiprocessing`
# child process that imports `search`/`admin_teams` fresh and only shares
# the same temp Postgres DB -- exactly the boundary a multi-worker gunicorn
# deployment has.
# --------------------------------------------------------------------------

def _confirm_preview_in_subprocess(dsn, secret_key, admin_id, token, out_queue):
    """Runs in a brand-new `multiprocessing` child process (spawn context
    -- no memory inherited from the parent beyond pickled arguments). If
    this can still find+consume a token created by the parent process,
    the preview store cannot be a parent-process-local Python dict.
    """
    import os as _os
    _os.environ["DATABASE_URL"] = dsn
    _os.environ["FLASK_SECRET_KEY"] = secret_key
    for key in ("DISABLE_IP_ALLOWLIST", "OFFICE_IP_ALLOWLIST", "IP_ALLOWLIST_BYPASS_USERS"):
        _os.environ.pop(key, None)
    import search as _search  # fresh import in this process
    _search.app.testing = True
    client = _search.app.test_client()
    with client.session_transaction() as sess:
        sess.clear()
        sess.update(authenticated=True, user_id=admin_id, auth_version=1, is_admin=True,
                    team_id=None, role="admin", username="admin1")
        sess["csrf_token"] = "the-real-token"
    resp = client.post(
        "/admin/teams/confirm",
        data={"preview_token": token, "csrf_token": "the-real-token"},
    )
    out_queue.put(resp.headers.get("Location", ""))


class MultiWorkerPreviewPgTests(_RealPgTestBase):
    def _admin(self):
        return self._insert_user(username="admin1", is_admin=True, account_status="ACTIVE")

    def _preview(self, client, *, team_id, brands, ip_policy="INHERIT"):
        resp = client.post(
            "/admin/teams/preview",
            data={"team_id": str(team_id), "ip_policy": ip_policy, "brands": brands,
                  "csrf_token": "the-real-token"},
        )
        token = _query_param(resp.headers["Location"], "preview")
        self.assertIsNotNone(token, f"no preview token in {resp.headers['Location']!r}")
        return token

    def test_preview_store_is_not_a_process_memory_dict(self):
        """Direct proof the old failure mode's root cause is gone: the
        module no longer has ANY process-local mutable preview store.
        """
        self.assertFalse(
            hasattr(admin_teams, "_TEAM_PERMISSION_PREVIEWS"),
            "preview store must not be an in-memory dict on the module",
        )

    def test_preview_created_in_one_process_is_confirmed_in_another(self):
        self._insert_product(brand="BrandA", code="C1")
        self._insert_product(brand="BrandB", code="C2")
        tid = self._insert_team("Team X", ip_policy="INHERIT", brands=["BrandA"])
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)

        token = self._preview(client, team_id=tid, brands=["BrandB"])

        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        secret_key = search.app.secret_key
        proc = ctx.Process(
            target=_confirm_preview_in_subprocess,
            args=(self.test_dsn, secret_key, admin_id, token, queue),
        )
        proc.start()
        try:
            location = queue.get(timeout=30)
        finally:
            proc.join(timeout=30)
        self.assertFalse(proc.is_alive(), "confirm subprocess must have finished")
        self.assertIn("msg=", location, f"cross-process confirm failed: {location!r}")
        self.assertEqual(self._fetch_team_brands(tid), ["BrandB"])

    def test_expired_token_rejected_not_partially_applied(self):
        """A token whose `created_at` is already past the TTL must be
        rejected outright (never partially apply brands/ip_policy)."""
        self._insert_product(brand="BrandA", code="C1")
        tid = self._insert_team("Team Y", ip_policy="INHERIT", brands=[])
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)
        token = self._preview(client, team_id=tid, brands=["BrandA"])

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE team_permission_previews SET created_at = NOW() - INTERVAL '3600 seconds' "
                        "WHERE token = %s",
                        (token,),
                    )
        finally:
            conn.close()

        resp = client.post(
            "/admin/teams/confirm",
            data={"preview_token": token, "csrf_token": "the-real-token"},
        )
        self.assertIn("err=", resp.headers["Location"])
        self.assertEqual(self._fetch_team_brands(tid), [], "expired token must not apply any part of the diff")

    def test_used_token_cannot_be_replayed_for_partial_reapplication(self):
        self._insert_product(brand="BrandA", code="C1")
        self._insert_product(brand="BrandB", code="C2")
        tid = self._insert_team("Team Z", ip_policy="INHERIT", brands=[])
        admin_id = self._admin()
        client = self._client_for(admin_id, is_admin=True)
        token = self._preview(client, team_id=tid, brands=["BrandA", "BrandB"])

        first = client.post("/admin/teams/confirm",
                             data={"preview_token": token, "csrf_token": "the-real-token"})
        self.assertIn("msg=", first.headers["Location"])
        self.assertEqual(self._fetch_team_brands(tid), ["BrandA", "BrandB"])

        # Replay with the SAME token -- must be rejected, not reapplied
        # (and definitely not partially reapplied).
        second = client.post("/admin/teams/confirm",
                              data={"preview_token": token, "csrf_token": "the-real-token"})
        self.assertIn("err=", second.headers["Location"])
        self.assertEqual(self._fetch_team_brands(tid), ["BrandA", "BrandB"], "replay must not change state")


if __name__ == "__main__":
    unittest.main()
