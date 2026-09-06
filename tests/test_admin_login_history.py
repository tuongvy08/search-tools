"""Phase 5D3 tests: admin login-history screen (read-only).

Two groups:

1. `AccessControlTests` -- no real DB touched at all. The admin/staff/
   anonymous guard in `admin_login_history._require_admin` runs BEFORE any
   query, so these prove access control with plain Flask test-client
   sessions only.

2. `_RealPgTestBase` subclasses -- REAL PostgreSQL, same pattern as
   `tests/test_admin_pg_integration.py`: one temporary, uniquely-prefixed
   database (`sd3_pgtest_<random>`) created in `setUpClass` and dropped in
   `tearDownClass`, with the minimal pre-014 schema + migration_014 applied
   once. NEVER touches `products_local` or any pre-existing database, and
   NEVER writes real user data. Every row here is a synthetic fixture. If a
   local Postgres server is not reachable via `DATABASE_URL`'s host/port/
   user, every real-DB test is SKIPPED with an explicit reason.

As of Phase 5D3, migration_014 has NOT been applied to the real local app
database (`login_audit_events` does not exist there yet) -- that is why
every functional test below runs against its own temporary, migrated
database rather than the app's actual dev database, and why this file
never asserts anything about what's currently in `products_local`.
"""
import os
import secrets
import unittest
from unittest import mock
from urllib.parse import urlparse, urlunparse

import psycopg2

import admin_login_history
import search
import session_security

MIGRATION_014_PATH = os.path.join(os.path.dirname(__file__), "..", "sql", "migration_014_google_oidc.sql")
MIGRATION_006_PATH = os.path.join(os.path.dirname(__file__), "..", "sql", "migration_006_office_ip_allowlist.sql")

_REAL_DATABASE_URL = os.environ.get("DATABASE_URL", "")


# session_security.py's before_request hook runs on every request that
# carries a `user_id` in session, regardless of endpoint -- including `/`
# and `/admin/login-history`. It uses its own `get_connection` (separate
# from `admin_login_history.get_connection`). This tiny stand-in (same
# pattern as `tests/test_admin_google_users.py`) always reports the
# session's account as ACTIVE with auth_version=1 so that hook passes
# through without touching a real DB for the pure access-control tests
# below.
class _PassthroughSessionCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        pass

    def fetchone(self):
        return ("ACTIVE", 1)


class _PassthroughSessionConn:
    def cursor(self):
        return _PassthroughSessionCursor()

    def close(self):
        pass


def _passthrough_session_connection():
    return _PassthroughSessionConn()


def _dsn_for(dbname: str) -> str:
    parsed = urlparse(_REAL_DATABASE_URL)
    new_path = "/" + dbname
    return urlunparse(parsed._replace(path=new_path))


def _maintenance_dsn() -> str:
    return _dsn_for("postgres")


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
    "(maintenance DB 'postgres'). BLOCKER for Phase 5D3 -- see report."
)

_TEST_DB_PREFIX = "sd3_pgtest_"

with open(MIGRATION_014_PATH, "r", encoding="utf-8") as _f:
    _MIGRATION_014_SQL = _f.read()

# Phase 6A-Fix1: middleware_access.py's before_request hook also runs on
# every `search.app.test_client()` request below (every session here is
# an admin session -> INHERIT -> reads `office_ip_allowlist`). Fix1 made a
# failed/missing-table read of that table a hard 503 instead of silently
# swallowing to "no rules" -- so this temp-DB fixture must actually create
# the table for these read-only login-history tests to keep exercising
# real admin-route behaviour instead of being short-circuited by IP
# middleware.
with open(MIGRATION_006_PATH, "r", encoding="utf-8") as _f:
    _MIGRATION_006_SQL = _f.read()

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
"""


# --------------------------------------------------------------------------
# 1. Access control -- no real DB needed.
# --------------------------------------------------------------------------

class AccessControlTests(unittest.TestCase):
    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()
        patcher = mock.patch.object(session_security, "get_connection", _passthrough_session_connection)
        patcher.start()
        self.addCleanup(patcher.stop)
        # This class's own contract is "no real DB touched at all" (see
        # module docstring). middleware_access.py's before_request hook
        # would otherwise try to resolve the staff session's real team IP
        # policy against `products_local` (not migrated for Phase 6A) --
        # disable it via its documented escape hatch to keep that contract
        # true; this class only tests the admin/staff/anonymous role guard.
        env_patcher = mock.patch.dict(os.environ, {"DISABLE_IP_ALLOWLIST": "1"})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get("/admin/login-history")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers.get("Location", ""))

    def test_staff_forbidden(self):
        with self.client.session_transaction() as sess:
            sess.update(authenticated=True, user_id=42, auth_version=1, is_admin=False,
                        role="user", username="staff1", team_id=1)
        resp = self.client.get("/admin/login-history")
        self.assertEqual(resp.status_code, 403)

    def test_staff_does_not_see_nav_link(self):
        with self.client.session_transaction() as sess:
            sess.update(authenticated=True, user_id=42, auth_version=1, is_admin=False,
                        role="user", username="staff1", team_id=1)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"L\xe1\xbb\x8bch s\xe1\xbb\xad \xc4\x91\xc4\x83ng nh\xe1\xbb\xadp", resp.data)
        self.assertNotIn(b"/admin/login-history", resp.data)

    def test_admin_sees_nav_link(self):
        with self.client.session_transaction() as sess:
            sess.update(authenticated=True, user_id=1, auth_version=1, is_admin=True,
                        role="admin", username="admin1")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"/admin/login-history", resp.data)


# --------------------------------------------------------------------------
# 2. Real Postgres functional tests.
# --------------------------------------------------------------------------

@unittest.skipUnless(_POSTGRES_REACHABLE, _SKIP_REASON)
class _RealPgTestBase(unittest.TestCase):
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
                    cur.execute(_MIGRATION_006_SQL)
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):
        assert cls.test_db_name.startswith(_TEST_DB_PREFIX)
        parsed = urlparse(cls.test_dsn)
        assert parsed.hostname in ("127.0.0.1", "localhost")
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
        search.app.testing = True
        self.client = search.app.test_client()

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
                        "TRUNCATE login_audit_events, app_users, teams RESTART IDENTITY CASCADE"
                    )
        finally:
            conn.close()

    def _insert_user(self, **kwargs):
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO app_users
                            (username, password_hash, is_admin, auth_provider, google_sub, email,
                             display_name, account_status)
                        VALUES (%(username)s, 'x', %(is_admin)s, %(auth_provider)s, %(google_sub)s,
                                %(email)s, %(display_name)s, %(account_status)s)
                        RETURNING id
                        """,
                        {
                            "username": kwargs["username"],
                            "is_admin": kwargs.get("is_admin", False),
                            "auth_provider": kwargs.get("auth_provider", "LOCAL"),
                            "google_sub": kwargs.get("google_sub"),
                            "email": kwargs.get("email"),
                            "display_name": kwargs.get("display_name"),
                            "account_status": kwargs.get("account_status", "ACTIVE"),
                        },
                    )
                    (uid,) = cur.fetchone()
        finally:
            conn.close()
        return uid

    def _insert_event(self, **kwargs):
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO login_audit_events
                            (user_id, actor_user_id, provider, outcome, reason_code,
                             source_ip, created_at)
                        VALUES (%(user_id)s, %(actor_user_id)s, %(provider)s, %(outcome)s,
                                %(reason_code)s, %(source_ip)s,
                                COALESCE(%(created_at)s, NOW()))
                        RETURNING id
                        """,
                        {
                            "user_id": kwargs.get("user_id"),
                            "actor_user_id": kwargs.get("actor_user_id"),
                            "provider": kwargs.get("provider", "GOOGLE"),
                            "outcome": kwargs.get("outcome", "SUCCESS"),
                            "reason_code": kwargs.get("reason_code"),
                            "source_ip": kwargs.get("source_ip"),
                            "created_at": kwargs.get("created_at"),
                        },
                    )
                    (eid,) = cur.fetchone()
        finally:
            conn.close()
        return eid

    def _admin_client(self, admin_id):
        client = search.app.test_client()
        with client.session_transaction() as sess:
            sess.update(authenticated=True, user_id=admin_id, auth_version=1, is_admin=True,
                        role="admin", username=f"admin{admin_id}")
        return client

    def _count_all_rows(self, table):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                (n,) = cur.fetchone()
        finally:
            conn.close()
        return n


class AdminCanViewTests(_RealPgTestBase):
    def test_admin_sees_page_with_events(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        target = self._insert_user(username="user1", auth_provider="GOOGLE",
                                    email="user1@standards.vn", account_status="ACTIVE")
        self._insert_event(user_id=target, provider="GOOGLE", outcome="SUCCESS", reason_code="LOGIN")

        resp = self._admin_client(admin_id).get("/admin/login-history")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"user1@standards.vn", resp.data)
        self.assertIn(b"Th\xc3\xa0nh c\xc3\xb4ng", resp.data)  # "Thành công"


class NoMutationTests(_RealPgTestBase):
    def test_viewing_history_never_mutates_any_table(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        target = self._insert_user(username="user1")
        self._insert_event(user_id=target, outcome="SUCCESS", reason_code="LOGIN")

        before_users = self._count_all_rows("app_users")
        before_events = self._count_all_rows("login_audit_events")

        resp = self._admin_client(admin_id).get(
            "/admin/login-history?date_from=2020-01-01&outcome=SUCCESS&event_type=AUTH&account=user1"
        )
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(self._count_all_rows("app_users"), before_users)
        self.assertEqual(self._count_all_rows("login_audit_events"), before_events)


class UnknownUserRenderingTests(_RealPgTestBase):
    def test_null_user_id_renders_unknown_account(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        self._insert_event(user_id=None, provider="GOOGLE", outcome="FAILURE", reason_code="TOKEN_INVALID")

        resp = self._admin_client(admin_id).get("/admin/login-history")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Không xác định".encode("utf-8"), resp.data)

    def test_deleted_user_sets_user_id_null_via_fk_and_still_renders(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        target = self._insert_user(username="soon_deleted", auth_provider="GOOGLE",
                                    email="soon_deleted@standards.vn")
        self._insert_event(user_id=target, provider="GOOGLE", outcome="SUCCESS", reason_code="LOGIN")

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM app_users WHERE id = %s", (target,))
        finally:
            conn.close()

        resp = self._admin_client(admin_id).get("/admin/login-history")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Không xác định".encode("utf-8"), resp.data)
        self.assertNotIn(b"soon_deleted", resp.data)


class UnknownReasonCodeTests(_RealPgTestBase):
    def test_unrecognized_reason_code_gets_safe_fallback_label_not_error(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        target = self._insert_user(username="user1")
        self._insert_event(user_id=target, outcome="DENIED", reason_code="SOME_FUTURE_CODE")

        resp = self._admin_client(admin_id).get("/admin/login-history")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"SOME_FUTURE_CODE", resp.data)
        self.assertIn("Không xác định".encode("utf-8"), resp.data)


class EventClassificationTests(_RealPgTestBase):
    def test_admin_action_never_labeled_as_login_success(self):
        actor = self._insert_user(username="admin1", is_admin=True)
        target = self._insert_user(username="target1", auth_provider="GOOGLE",
                                    email="target1@standards.vn", account_status="SUSPENDED")
        self._insert_event(user_id=target, actor_user_id=actor, provider="GOOGLE",
                            outcome="SUCCESS", reason_code="USER_SUSPENDED")

        resp = self._admin_client(actor).get("/admin/login-history")
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode("utf-8")
        self.assertIn("Hành động quản trị", body)
        self.assertIn("Quản trị: tạm khoá tài khoản", body)
        # Never rendered as a plain login success line for this row.
        self.assertNotIn("Đăng nhập thành công", body)

    def test_event_type_filter_admin_only(self):
        actor = self._insert_user(username="admin1", is_admin=True)
        target = self._insert_user(username="target1")
        self._insert_event(user_id=target, actor_user_id=actor, outcome="SUCCESS",
                            reason_code="USER_APPROVED")
        self._insert_event(user_id=target, outcome="SUCCESS", reason_code="LOGIN")

        resp = self._admin_client(actor).get("/admin/login-history?event_type=ADMIN")
        body = resp.data.decode("utf-8")
        self.assertIn("Quản trị: phê duyệt tài khoản", body)
        self.assertNotIn("Đăng nhập thành công", body)

    def test_event_type_filter_auth_only(self):
        actor = self._insert_user(username="admin1", is_admin=True)
        target = self._insert_user(username="target1")
        self._insert_event(user_id=target, actor_user_id=actor, outcome="SUCCESS",
                            reason_code="USER_APPROVED")
        self._insert_event(user_id=target, outcome="SUCCESS", reason_code="LOGIN")

        resp = self._admin_client(actor).get("/admin/login-history?event_type=AUTH")
        body = resp.data.decode("utf-8")
        self.assertIn("Đăng nhập thành công", body)
        self.assertNotIn("Quản trị: phê duyệt tài khoản", body)

    # --- Phase 5D3 Final regressions: classification must use reason_code,
    # never `actor_user_id IS NOT NULL` alone (that column is ON DELETE SET
    # NULL and can legitimately be NULL on a real admin-action row). ---

    def test_admin_action_with_null_actor_user_id_still_classified_as_admin(self):
        """Regression: an admin-action row with `actor_user_id = NULL` (either
        because the actor was since deleted, or -- as tested here -- never
        resolvable) must still classify as ADMIN, purely from reason_code.
        """
        target = self._insert_user(username="target_null_actor")
        self._insert_event(user_id=target, actor_user_id=None, provider="GOOGLE",
                            outcome="SUCCESS", reason_code="USER_REACTIVATED")

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                result = admin_login_history.fetch_login_history(cur, {})
        finally:
            conn.close()

        self.assertEqual(len(result["events"]), 1)
        ev = result["events"][0]
        self.assertEqual(ev["event_type"], "ADMIN")
        self.assertEqual(ev["event_type_label"], "Hành động quản trị")
        self.assertEqual(ev["actor"], "Không xác định")
        # Phương thức đăng nhập must show "—" for an admin action, never the
        # stored (artifact) provider value.
        self.assertEqual(ev["provider_label"], "—")

    def test_admin_action_with_deleted_actor_still_shown_as_admin_action_on_page(self):
        """Same regression, end-to-end: delete the real actor row (triggers
        the FK's ON DELETE SET NULL) and confirm the page still classifies
        the row as an admin action, not a login.
        """
        actor = self._insert_user(username="admin_to_be_deleted", is_admin=True)
        target = self._insert_user(username="target_deleted_actor", auth_provider="GOOGLE",
                                    email="target_deleted_actor@standards.vn",
                                    account_status="SUSPENDED")
        self._insert_event(user_id=target, actor_user_id=actor, provider="GOOGLE",
                            outcome="SUCCESS", reason_code="USER_SUSPENDED")

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM app_users WHERE id = %s", (actor,))
        finally:
            conn.close()

        viewer = self._insert_user(username="admin_viewer", is_admin=True)
        resp = self._admin_client(viewer).get("/admin/login-history")
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode("utf-8")
        self.assertIn("Hành động quản trị", body)
        self.assertIn("Quản trị: tạm khoá tài khoản", body)
        self.assertNotIn("Đăng nhập thành công", body)

    def test_filter_and_count_consistent_for_null_actor_admin_event(self):
        """Filter/count/page must all agree on the same classification --
        never counted under one bucket and displayed under another.
        """
        target = self._insert_user(username="target_consistency")
        self._insert_event(user_id=target, actor_user_id=None, outcome="SUCCESS",
                            reason_code="USER_APPROVED")
        self._insert_event(user_id=target, outcome="SUCCESS", reason_code="LOGIN")

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                admin_only = admin_login_history.fetch_login_history(cur, {"event_type": "ADMIN"})
                auth_only = admin_login_history.fetch_login_history(cur, {"event_type": "AUTH"})
                all_events = admin_login_history.fetch_login_history(cur, {"event_type": "ALL"})
        finally:
            conn.close()

        self.assertEqual(admin_only["total_count"], 1)
        self.assertEqual(len(admin_only["events"]), 1)
        self.assertEqual(admin_only["events"][0]["event_type"], "ADMIN")

        self.assertEqual(auth_only["total_count"], 1)
        self.assertEqual(len(auth_only["events"]), 1)
        self.assertEqual(auth_only["events"][0]["event_type"], "AUTH")

        self.assertEqual(all_events["total_count"], 2)

    def test_admin_action_page_never_shows_google_as_login_method(self):
        actor = self._insert_user(username="admin_method_check", is_admin=True)
        target = self._insert_user(username="target_method_check")
        self._insert_event(user_id=target, actor_user_id=actor, provider="GOOGLE",
                            outcome="SUCCESS", reason_code="USER_SUSPENDED")

        resp = self._admin_client(actor).get("/admin/login-history")
        body = resp.data.decode("utf-8")
        self.assertIn("Hành động quản trị", body)
        self.assertNotIn("Google Workspace", body)

    def test_unknown_reason_code_classified_as_other_not_login_or_admin(self):
        target = self._insert_user(username="target_unknown")
        self._insert_event(user_id=target, actor_user_id=None, outcome="DENIED",
                            reason_code="SOME_FUTURE_CODE_NOT_YET_MAPPED")

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                result = admin_login_history.fetch_login_history(cur, {})
                other_only = admin_login_history.fetch_login_history(cur, {"event_type": "OTHER"})
        finally:
            conn.close()

        self.assertEqual(len(result["events"]), 1)
        ev = result["events"][0]
        self.assertEqual(ev["event_type"], "OTHER")
        self.assertEqual(ev["event_type_label"], "Sự kiện khác")
        self.assertNotEqual(ev["event_type"], "AUTH")
        self.assertNotEqual(ev["event_type"], "ADMIN")
        self.assertEqual(other_only["total_count"], 1)

    def test_event_type_other_filter_works_end_to_end_over_http(self):
        actor = self._insert_user(username="admin_other_http", is_admin=True)
        target = self._insert_user(username="target_other_http")
        self._insert_event(user_id=target, outcome="DENIED", reason_code="SOME_UNMAPPED_CODE")
        self._insert_event(user_id=target, outcome="SUCCESS", reason_code="LOGIN")

        resp = self._admin_client(actor).get("/admin/login-history?event_type=OTHER")
        self.assertEqual(resp.status_code, 200)
        body = resp.data.decode("utf-8")
        self.assertIn("Sự kiện khác", body)
        self.assertNotIn("Đăng nhập thành công", body)

    def test_null_reason_code_classified_as_other_not_defaulted_to_login(self):
        target = self._insert_user(username="target_null_reason")
        self._insert_event(user_id=target, outcome="DENIED", reason_code=None)

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                result = admin_login_history.fetch_login_history(cur, {})
        finally:
            conn.close()

        ev = result["events"][0]
        self.assertEqual(ev["event_type"], "OTHER")
        self.assertEqual(ev["event_type_label"], "Sự kiện khác")


class FilterAndOrderingTests(_RealPgTestBase):
    def test_outcome_filter(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        u1 = self._insert_user(username="u1")
        self._insert_event(user_id=u1, outcome="SUCCESS", reason_code="LOGIN")
        self._insert_event(user_id=u1, outcome="FAILURE", reason_code="TOKEN_INVALID")

        resp = self._admin_client(admin_id).get("/admin/login-history?outcome=FAILURE")
        body = resp.data.decode("utf-8")
        self.assertIn("Thất bại", body)
        self.assertNotIn("Đăng nhập thành công", body)

    def test_account_filter_matches_username(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        alice = self._insert_user(username="alice_local")
        bob = self._insert_user(username="bob_local")
        self._insert_event(user_id=alice, provider="LOCAL", outcome="SUCCESS", reason_code="LOGIN")
        self._insert_event(user_id=bob, provider="LOCAL", outcome="SUCCESS", reason_code="LOGIN")

        resp = self._admin_client(admin_id).get("/admin/login-history?account=alice")
        body = resp.data
        self.assertIn(b"alice_local", body)
        self.assertNotIn(b"bob_local", body)

    def test_date_range_filter_excludes_out_of_range_rows(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        u1 = self._insert_user(username="u1")
        self._insert_event(user_id=u1, outcome="SUCCESS", reason_code="LOGIN",
                            created_at="2020-01-15 10:00:00+07")
        self._insert_event(user_id=u1, outcome="SUCCESS", reason_code="LOGOUT",
                            created_at="2024-06-01 10:00:00+07")

        resp = self._admin_client(admin_id).get(
            "/admin/login-history?date_from=2024-01-01&date_to=2024-12-31"
        )
        body = resp.data.decode("utf-8")
        self.assertIn("Đăng xuất", body)
        self.assertNotIn("2020-01-15", body)

    def test_ordering_is_newest_first_and_stable_for_same_timestamp(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        u1 = self._insert_user(username="u1")
        same_ts = "2024-05-05 08:00:00+07"
        first_id = self._insert_event(user_id=u1, outcome="SUCCESS", reason_code="LOGIN",
                                       created_at=same_ts)
        second_id = self._insert_event(user_id=u1, outcome="SUCCESS", reason_code="LOGOUT",
                                        created_at=same_ts)

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                result = admin_login_history.fetch_login_history(cur, {})
        finally:
            conn.close()

        ids_in_order = [e["id"] for e in result["events"]]
        # Same created_at for both -> secondary key `id DESC` must break the
        # tie deterministically (newest/highest id first).
        self.assertEqual(ids_in_order, sorted(ids_in_order, reverse=True))
        self.assertEqual(ids_in_order[0], second_id)
        self.assertEqual(ids_in_order[1], first_id)

    def test_pagination_default_page_size_50_and_max_100(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        u1 = self._insert_user(username="u1")
        for _ in range(120):
            self._insert_event(user_id=u1, outcome="SUCCESS", reason_code="LOGIN")

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                default_result = admin_login_history.fetch_login_history(cur, {})
                self.assertEqual(len(default_result["events"]), 50)
                self.assertEqual(default_result["page_size"], 50)

                capped_result = admin_login_history.fetch_login_history(cur, {"page_size": "9999"})
                self.assertEqual(capped_result["page_size"], 100)
                self.assertEqual(len(capped_result["events"]), 100)
        finally:
            conn.close()

    def test_page_2_returns_next_slice_no_overlap_with_page_1(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        u1 = self._insert_user(username="u1")
        for _ in range(60):
            self._insert_event(user_id=u1, outcome="SUCCESS", reason_code="LOGIN")

        conn = self._connect()
        try:
            with conn.cursor() as cur:
                page1 = admin_login_history.fetch_login_history(cur, {"page": "1", "page_size": "50"})
                page2 = admin_login_history.fetch_login_history(cur, {"page": "2", "page_size": "50"})
        finally:
            conn.close()

        ids_page1 = {e["id"] for e in page1["events"]}
        ids_page2 = {e["id"] for e in page2["events"]}
        self.assertEqual(len(ids_page1), 50)
        self.assertEqual(len(ids_page2), 10)
        self.assertEqual(ids_page1 & ids_page2, set())

    def test_invalid_date_filter_is_ignored_not_an_error(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        u1 = self._insert_user(username="u1")
        self._insert_event(user_id=u1, outcome="SUCCESS", reason_code="LOGIN")

        resp = self._admin_client(admin_id).get("/admin/login-history?date_from=not-a-date")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Th\xc3\xa0nh c\xc3\xb4ng", resp.data)


class XssAndSensitiveFieldTests(_RealPgTestBase):
    def test_display_name_is_html_escaped(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        target = self._insert_user(username="xss_user", auth_provider="GOOGLE",
                                    email="xss@standards.vn",
                                    display_name="<script>alert(1)</script>")
        self._insert_event(user_id=target, provider="GOOGLE", outcome="SUCCESS", reason_code="LOGIN")

        resp = self._admin_client(admin_id).get("/admin/login-history")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"<script>alert(1)</script>", resp.data)
        self.assertIn(b"&lt;script&gt;", resp.data)

    def test_no_google_sub_password_hash_or_token_field_in_page(self):
        admin_id = self._insert_user(username="admin1", is_admin=True)
        target = self._insert_user(username="guser", auth_provider="GOOGLE",
                                    google_sub="super-secret-sub-12345",
                                    email="guser@standards.vn")
        self._insert_event(user_id=target, provider="GOOGLE", outcome="SUCCESS", reason_code="LOGIN")

        resp = self._admin_client(admin_id).get("/admin/login-history")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"super-secret-sub-12345", resp.data)
        self.assertNotIn(b"google_sub", resp.data)
        self.assertNotIn(b"password_hash", resp.data)


if __name__ == "__main__":
    unittest.main()
