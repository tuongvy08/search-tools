"""Phase 5D2B.2: REAL PostgreSQL integration tests for auth/admin.

Everything in this file that is NOT explicitly labelled "mock"/"fake" talks
to an ACTUAL local Postgres server -- the SAME host/port/user as local dev
(`DATABASE_URL`) -- but only ever inside one temporary, uniquely-prefixed
database (`sd2b2_pgtest_<random>`) that this file creates in `setUpClass`
and drops in `tearDownClass`, even on failure. It NEVER runs DDL/DML against
`products_local` or any other pre-existing database, and NEVER reads real
user data -- every row here is a synthetic fixture created by this file.

This is intentionally separate from `tests/test_admin_google_users.py` and
`tests/test_admin_users_legacy.py`, which use an in-memory fake DB cursor
and only prove that the right SQL statements are issued in the right order
-- NOT real transaction/lock/concurrency behaviour. Section below is
explicit about which parts are "real Postgres, two independent connections,
real app code" vs "raw-SQL setup/assertion helpers":

  - MigrationRehearsalTests: real Postgres, migration_014 SQL run twice
    against a minimal schema seeded with synthetic data.
  - RealConcurrencyTests: real Postgres, TWO independent psycopg2
    connections/threads, driving the actual Flask view functions
    (`admin_google_users.suspend`, `search.py`'s `update_user` branch) via
    `search.app.test_client()` -- i.e. the app's real transaction logic,
    not a reimplementation of the locking algorithm in the test.
  - ActorRevokedWhileWaitingForLockParamTests (Phase 5D2B Final): the same
    "actor revoked while the request waits for the shared advisory lock"
    scenario as RealConcurrencyTests.test_actor_suspended_while_request_waits_for_lock_is_rejected,
    but parameterized across EVERY admin user-management mutation --
    approve, invite, suspend, reactivate, revoke_sessions, legacy
    create_user, legacy update_user -- proving the unified
    lock-then-revalidate-actor sequence was actually applied to all of
    them, not just suspend().

If a local Postgres server is not reachable via `DATABASE_URL`'s
host/port/user, every test in this module is SKIPPED with an explicit
reason (reported as a blocker), never silently treated as a pass.
"""
import os
import secrets
import threading
import unittest
from unittest import mock
from urllib.parse import urlparse, urlunparse

import psycopg2
import psycopg2.errors

import admin_google_users
import search
import session_security

MIGRATION_014_PATH = os.path.join(os.path.dirname(__file__), "..", "sql", "migration_014_google_oidc.sql")

_REAL_DATABASE_URL = os.environ.get("DATABASE_URL", "")


def _dsn_for(dbname: str) -> str:
    """Build a DSN identical to DATABASE_URL except for the database name."""
    parsed = urlparse(_REAL_DATABASE_URL)
    new_path = "/" + dbname
    return urlunparse(parsed._replace(path=new_path))


def _maintenance_dsn() -> str:
    # `postgres` is Postgres's own always-present maintenance database --
    # deliberately NOT `products_local`, so this file never even opens a
    # connection to the application database.
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
    "(maintenance DB 'postgres'). BLOCKER for Phase 5D2B.2 -- see report."
)

_TEST_DB_PREFIX = "sd2b2_pgtest_"

with open(MIGRATION_014_PATH, "r", encoding="utf-8") as _f:
    _MIGRATION_014_SQL = _f.read()

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
    """Creates ONE temporary test database for the whole class, applies the
    minimal pre-014 schema + migration_014 (run once here; run-twice
    idempotency is separately asserted by MigrationRehearsalTests), and
    drops the database again in tearDownClass -- even if tests failed.
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
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):
        # Safety net before DROP DATABASE: re-verify name/host explicitly,
        # never rely solely on the attribute set in setUpClass.
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
                # Terminate any lingering backends (e.g. a test that failed
                # mid-transaction) so DROP DATABASE doesn't hang/error.
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (cls.test_db_name,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{cls.test_db_name}"')
        finally:
            maint.close()

    def _connect(self):
        return psycopg2.connect(self.test_dsn)

    def _truncate_all(self):
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "TRUNCATE login_audit_events, team_brands, app_users, teams "
                        "RESTART IDENTITY CASCADE"
                    )
        finally:
            conn.close()

    def setUp(self):
        self._truncate_all()

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

    def _fetch_user(self, uid):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT account_status, is_admin, auth_version, auth_provider "
                    "FROM app_users WHERE id = %s",
                    (uid,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return row  # (account_status, is_admin, auth_version, auth_provider) or None

    def _count_audits(self):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM login_audit_events")
                (n,) = cur.fetchone()
        finally:
            conn.close()
        return n


# --------------------------------------------------------------------------
# 1. Migration rehearsal -- REAL Postgres, run migration 014 TWICE
# --------------------------------------------------------------------------

class MigrationRehearsalTests(_RealPgTestBase):
    """setUpClass already ran the minimal schema + migration_014 ONCE. Each
    test here re-runs migration_014 a SECOND time (idempotency) and/or
    inspects the resulting real schema/constraints/backfill.
    """

    def test_rerunning_migration_014_is_a_no_op(self):
        uid = self._insert_user(username="preexisting_local")
        before = self._fetch_user(uid)

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(_MIGRATION_014_SQL)  # second run
        finally:
            conn.close()

        after = self._fetch_user(uid)
        self.assertEqual(before, after)

    def test_preexisting_row_backfilled_local_active_version_one(self):
        # Insert using ONLY the pre-014 columns (as if the row existed
        # before migration 014 ever ran), relying on column DEFAULTs.
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO app_users (username, password_hash) "
                        "VALUES (%s, %s) RETURNING id",
                        ("legacy_row", "h"),
                    )
                    (uid,) = cur.fetchone()
        finally:
            conn.close()
        account_status, is_admin, auth_version, auth_provider = self._fetch_user(uid)
        self.assertEqual(auth_provider, "LOCAL")
        self.assertEqual(account_status, "ACTIVE")
        self.assertEqual(auth_version, 1)
        self.assertFalse(is_admin)

    def test_google_sub_uniqueness_enforced(self):
        self._insert_user(username="g1", auth_provider="GOOGLE", google_sub="dup-sub",
                           account_status="ACTIVE")
        with self.assertRaises(psycopg2.IntegrityError):
            self._insert_user(username="g2", auth_provider="GOOGLE", google_sub="dup-sub",
                               account_status="ACTIVE")

    def test_email_uniqueness_case_insensitive_enforced(self):
        self._insert_user(username="e1", auth_provider="GOOGLE", email="Someone@Standards.vn",
                           account_status="INVITED")
        with self.assertRaises(psycopg2.IntegrityError):
            self._insert_user(username="e2", auth_provider="GOOGLE", email="someone@standards.vn",
                               account_status="INVITED")

    def test_account_status_check_constraint_rejects_invalid_value(self):
        conn = self._connect()
        try:
            with self.assertRaises(psycopg2.errors.CheckViolation):
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO app_users (username, password_hash, account_status) "
                            "VALUES (%s, %s, %s)",
                            ("bad_status", "h", "NOT_A_REAL_STATUS"),
                        )
        finally:
            conn.close()

    def test_auth_version_positive_check_constraint(self):
        conn = self._connect()
        try:
            with self.assertRaises(psycopg2.errors.CheckViolation):
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO app_users (username, password_hash, auth_version) "
                            "VALUES (%s, %s, %s)",
                            ("bad_version", "h", 0),
                        )
        finally:
            conn.close()

    def test_actor_user_id_column_and_fk_present_and_functional(self):
        actor_id = self._insert_user(username="actor1", is_admin=True)
        target_id = self._insert_user(username="target1")
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO login_audit_events (user_id, actor_user_id, provider, outcome, reason_code) "
                        "VALUES (%s, %s, 'GOOGLE', 'SUCCESS', 'USER_SUSPENDED')",
                        (target_id, actor_id),
                    )
                    # Deleting the actor must SET NULL on actor_user_id
                    # (ON DELETE SET NULL), not cascade-delete the audit row.
                    cur.execute("DELETE FROM app_users WHERE id = %s", (actor_id,))
                    cur.execute(
                        "SELECT actor_user_id FROM login_audit_events WHERE user_id = %s",
                        (target_id,),
                    )
                    (actor_user_id_after,) = cur.fetchone()
        finally:
            conn.close()
        self.assertIsNone(actor_user_id_after)


# --------------------------------------------------------------------------
# 2 & 3. Real concurrency -- TWO independent connections/threads driving the
# app's ACTUAL view functions against the real test DB.
# --------------------------------------------------------------------------

class RealConcurrencyTests(_RealPgTestBase):
    def setUp(self):
        super().setUp()
        # Point the WHOLE app at the temporary test DB for the duration of
        # this test only (db.get_connection() re-reads DATABASE_URL on every
        # call, so this affects search.py, admin_google_users.py, AND
        # session_security.py identically -- exactly like production).
        self._env_patch = mock.patch.dict(os.environ, {"DATABASE_URL": self.test_dsn})
        self._env_patch.start()
        search.app.testing = True

    def tearDown(self):
        self._env_patch.stop()
        super().tearDown()

    @staticmethod
    def _client_with_admin_session(user_id, auth_version):
        client = search.app.test_client()
        with client.session_transaction() as sess:
            sess.clear()
            sess.update(authenticated=True, user_id=user_id, auth_version=auth_version,
                        is_admin=True, role="admin", username=f"admin{user_id}")
            sess["csrf_token"] = "the-real-token"
        return client

    def test_mutual_suspend_demote_race_leaves_at_least_one_active_admin(self):
        """REAL Postgres, 2 threads, 2 independent connections, real Flask
        view functions: admin L1 (LOCAL) suspends admin G1 (GOOGLE) via
        admin_google_users.suspend() AT THE SAME TIME admin G1 tries to
        demote admin L1 (LOCAL) via search.py's legacy update_user path.
        Exactly one of the two admin-reducing mutations may win; the other
        MUST be rejected by actor-revalidation (since whichever loses will,
        by the time it gets the shared lock, find its OWN actor row already
        changed by the winner) -- either way at least one stays ACTIVE.
        """
        g1 = self._insert_user(username="g1@standards.vn", email="g1@standards.vn",
                                auth_provider="GOOGLE", google_sub="sub-g1",
                                is_admin=True, account_status="ACTIVE")
        l1 = self._insert_user(username="l1_admin", is_admin=True, account_status="ACTIVE")

        barrier = threading.Barrier(2)
        results = {}

        def suspend_g1_by_l1():
            client = self._client_with_admin_session(l1, 1)
            barrier.wait(timeout=10)
            resp = client.post(
                "/admin/users/google/suspend",
                data={"user_id": str(g1), "csrf_token": "the-real-token"},
            )
            results["suspend"] = resp.status_code

        def demote_l1_by_g1():
            client = self._client_with_admin_session(g1, 1)
            barrier.wait(timeout=10)
            resp = client.post(
                "/admin/users",
                data={"action": "update_user", "user_id": str(l1), "role": "staff",
                      "brands": "Sigma", "csrf_token": "the-real-token"},
            )
            results["demote"] = resp.status_code

        t1 = threading.Thread(target=suspend_g1_by_l1)
        t2 = threading.Thread(target=demote_l1_by_g1)
        t1.start()
        t2.start()
        t1.join(timeout=20)
        t2.join(timeout=20)
        self.assertFalse(t1.is_alive(), "suspend request hung (advisory lock leak / deadlock?)")
        self.assertFalse(t2.is_alive(), "update_user request hung (advisory lock leak / deadlock?)")
        self.assertEqual(results.get("suspend"), 302)
        self.assertEqual(results.get("demote"), 302)

        g1_status, g1_is_admin, _, _ = self._fetch_user(g1)
        l1_status, l1_is_admin, _, _ = self._fetch_user(l1)

        active_admins_remaining = sum([
            1 for (status, is_admin) in [(g1_status, g1_is_admin), (l1_status, l1_is_admin)]
            if status == "ACTIVE" and is_admin
        ])
        self.assertGreaterEqual(active_admins_remaining, 1,
                                 "Both admins ended up non-ACTIVE-admin -- last-admin invariant violated!")

        # Exactly one of the two mutations actually took effect (the loser
        # must have been rejected by actor-revalidation, not silently
        # no-op'd for some other reason).
        g1_suspended = (g1_status == "SUSPENDED")
        l1_demoted = (l1_status == "ACTIVE" and l1_is_admin is False)
        self.assertTrue(g1_suspended or l1_demoted, "neither mutation took effect")
        self.assertFalse(g1_suspended and l1_demoted,
                          "both mutations took effect -- shared lock did not serialize them")

    def test_rollback_releases_advisory_lock_not_held_for_next_request(self):
        """A request that acquires the shared advisory lock and then fails
        (self-suspend rejected) must roll back and release the lock -- a
        SEPARATE, unrelated request run immediately afterward must not be
        blocked by a stale held lock.
        """
        g1 = self._insert_user(username="g1@standards.vn", email="g1@standards.vn",
                                auth_provider="GOOGLE", google_sub="sub-g1",
                                is_admin=True, account_status="ACTIVE")
        other = self._insert_user(username="other_google@standards.vn", email="other_google@standards.vn",
                                   auth_provider="GOOGLE", google_sub="sub-other",
                                   is_admin=False, account_status="ACTIVE")

        client = self._client_with_admin_session(g1, 1)
        # Self-suspend: acquires the lock, then _ERR_SELF_SUSPEND raises
        # inside `with conn:`, so psycopg2 rolls back automatically.
        resp = client.post(
            "/admin/users/google/suspend",
            data={"user_id": str(g1), "csrf_token": "the-real-token"},
        )
        self.assertEqual(resp.status_code, 302)
        status_after_selfattempt, _, _, _ = self._fetch_user(g1)
        self.assertEqual(status_after_selfattempt, "ACTIVE")  # untouched

        # Immediately afterwards: a real unrelated suspend must complete
        # promptly (bounded wait), proving no advisory lock leaked across
        # requests/connections.
        result = {}

        def run_second_request():
            resp2 = client.post(
                "/admin/users/google/suspend",
                data={"user_id": str(other), "csrf_token": "the-real-token"},
            )
            result["status"] = resp2.status_code

        t = threading.Thread(target=run_second_request)
        t.start()
        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "second request hung -- advisory lock leaked from the failed first request")
        self.assertEqual(result.get("status"), 302)
        other_status, _, _, _ = self._fetch_user(other)
        self.assertEqual(other_status, "SUSPENDED")

    def test_actor_suspended_while_request_waits_for_lock_is_rejected(self):
        """Deterministic (no sleep-based) proof of actor-revalidation: a raw
        connection holds the shared advisory lock first. A real suspend()
        request by actor X is started concurrently and must BLOCK waiting
        for that lock (verified via a bounded poll, not a fixed sleep).
        While it is blocked, X's own account is suspended by a third,
        independent connection. Releasing the held lock must let the
        blocked request proceed straight into actor-revalidation, which
        must now reject it (X is no longer an ACTIVE admin).
        """
        x = self._insert_user(username="actor_x@standards.vn", email="actor_x@standards.vn",
                               auth_provider="GOOGLE", google_sub="sub-x",
                               is_admin=True, account_status="ACTIVE")
        y = self._insert_user(username="admin_y", is_admin=True, account_status="ACTIVE")
        victim = self._insert_user(username="victim@standards.vn", email="victim@standards.vn",
                                    auth_provider="GOOGLE", google_sub="sub-victim",
                                    is_admin=False, account_status="ACTIVE")

        holder_conn = psycopg2.connect(self.test_dsn)
        holder_conn.autocommit = False
        with holder_conn.cursor() as hcur:
            hcur.execute("SELECT pg_advisory_xact_lock(%s)", (admin_google_users._LAST_ADMIN_LOCK_KEY,))

        client = self._client_with_admin_session(x, 1)
        result = {}

        def blocked_request():
            resp = client.post(
                "/admin/users/google/suspend",
                data={"user_id": str(victim), "csrf_token": "the-real-token"},
            )
            result["status"] = resp.status_code

        t = threading.Thread(target=blocked_request)
        t.start()

        # Bounded poll (deterministic, finite): confirm the request is
        # genuinely blocked (still alive) before we proceed -- NOT a fixed
        # sleep used for timing/correctness, just an early-exit check.
        t.join(timeout=1.5)
        self.assertTrue(t.is_alive(), "request completed before lock was released -- lock not actually held/shared")

        # Simulate "someone else suspended X while X's request was
        # waiting" via a third, independent connection/transaction that
        # does NOT itself need the advisory lock (it's not reducing the
        # admin count below the invariant: y remains an ACTIVE admin).
        third_conn = psycopg2.connect(self.test_dsn)
        try:
            with third_conn:
                with third_conn.cursor() as tcur:
                    tcur.execute(
                        "UPDATE app_users SET account_status = 'SUSPENDED', "
                        "auth_version = auth_version + 1 WHERE id = %s",
                        (x,),
                    )
        finally:
            third_conn.close()

        # Release the held lock -> the blocked request can now proceed.
        holder_conn.commit()
        holder_conn.close()

        t.join(timeout=10)
        self.assertFalse(t.is_alive(), "request never completed after lock release")
        self.assertEqual(result.get("status"), 302)

        victim_status, _, _, _ = self._fetch_user(victim)
        self.assertEqual(victim_status, "ACTIVE", "victim must be untouched -- actor was invalid at revalidation time")
        self.assertEqual(self._count_audits(), 0, "no audit row should be written for a rejected mutation")

    def test_google_suspend_and_local_demote_share_the_same_lock_key(self):
        """Directly demonstrates the two code paths contend on the SAME
        advisory lock object: hold the key manually, start EACH real
        request (one at a time) against a different admin pair, and
        confirm both genuinely block on that exact key before release.
        """
        g1 = self._insert_user(username="g1@standards.vn", email="g1@standards.vn",
                                auth_provider="GOOGLE", google_sub="sub-g1",
                                is_admin=True, account_status="ACTIVE")
        l_actor = self._insert_user(username="l_actor", is_admin=True, account_status="ACTIVE")
        l_target = self._insert_user(username="l_target", is_admin=True, account_status="ACTIVE")

        holder_conn = psycopg2.connect(self.test_dsn)
        holder_conn.autocommit = False
        with holder_conn.cursor() as hcur:
            hcur.execute("SELECT pg_advisory_xact_lock(%s)", (admin_google_users._LAST_ADMIN_LOCK_KEY,))

        google_client = self._client_with_admin_session(l_actor, 1)
        local_client = self._client_with_admin_session(l_actor, 1)
        results = {}

        def google_suspend_request():
            resp = google_client.post(
                "/admin/users/google/suspend",
                data={"user_id": str(g1), "csrf_token": "the-real-token"},
            )
            results["google"] = resp.status_code

        def local_demote_request():
            resp = local_client.post(
                "/admin/users",
                data={"action": "update_user", "user_id": str(l_target), "role": "staff",
                      "brands": "Sigma", "csrf_token": "the-real-token"},
            )
            results["local"] = resp.status_code

        t1 = threading.Thread(target=google_suspend_request)
        t2 = threading.Thread(target=local_demote_request)
        t1.start()
        t2.start()
        t1.join(timeout=1.5)
        t2.join(timeout=1.5)
        self.assertTrue(t1.is_alive(), "GOOGLE suspend did not block on the held lock")
        self.assertTrue(t2.is_alive(), "LOCAL demote did not block on the held lock")

        holder_conn.commit()
        holder_conn.close()

        t1.join(timeout=10)
        t2.join(timeout=10)
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertEqual(results.get("google"), 302)
        self.assertEqual(results.get("local"), 302)


# --------------------------------------------------------------------------
# Phase 5D2B Final: parameterized "actor revoked while waiting for the
# shared lock" scenario, run against EVERY admin user-management mutation.
# --------------------------------------------------------------------------

class ActorRevokedWhileWaitingForLockParamTests(_RealPgTestBase):
    """One scenario, seven real actions. For each action: hold the shared
    advisory lock manually; start the REAL request (real Flask view, real
    transaction) in a thread; confirm (bounded poll, no fixed sleep) it is
    genuinely blocked; revoke the actor (suspend their account) via a
    THIRD independent connection while it waits; release the held lock;
    confirm the request is rejected and the target/DB is completely
    untouched. This is the same proof as
    RealConcurrencyTests.test_actor_suspended_while_request_waits_for_lock_is_rejected,
    generalized across every mutation instead of just suspend().
    """

    def setUp(self):
        super().setUp()
        self._env_patch = mock.patch.dict(os.environ, {"DATABASE_URL": self.test_dsn})
        self._env_patch.start()
        search.app.testing = True

    def tearDown(self):
        self._env_patch.stop()
        super().tearDown()

    def _client_for(self, user_id, auth_version=1):
        client = search.app.test_client()
        with client.session_transaction() as sess:
            sess.clear()
            sess.update(authenticated=True, user_id=user_id, auth_version=auth_version,
                         is_admin=True, role="admin", username=f"admin{user_id}")
            sess["csrf_token"] = "the-real-token"
        return client

    def _run_scenario(self, *, actor_provider, build_request):
        """`build_request(actor_id)` must insert whatever target row(s) it
        needs, and return `(client, do_post, verify_unchanged)` where
        `do_post()` issues the real request and `verify_unchanged()` asserts
        nothing was mutated and no audit was written.
        """
        if actor_provider == "GOOGLE":
            actor_id = self._insert_user(username="actor@standards.vn", email="actor@standards.vn",
                                          auth_provider="GOOGLE", google_sub="sub-actor",
                                          is_admin=True, account_status="ACTIVE")
        else:
            actor_id = self._insert_user(username="actor_local", is_admin=True, account_status="ACTIVE")

        client, do_post, verify_unchanged = build_request(actor_id)

        holder_conn = psycopg2.connect(self.test_dsn)
        holder_conn.autocommit = False
        with holder_conn.cursor() as hcur:
            hcur.execute("SELECT pg_advisory_xact_lock(%s)", (admin_google_users._LAST_ADMIN_LOCK_KEY,))

        result = {}

        def blocked_request():
            result["status"] = do_post(client)

        t = threading.Thread(target=blocked_request)
        t.start()
        t.join(timeout=1.5)
        self.assertTrue(t.is_alive(), "request completed before the lock was released -- not actually blocked")

        third_conn = psycopg2.connect(self.test_dsn)
        try:
            with third_conn:
                with third_conn.cursor() as tcur:
                    tcur.execute(
                        "UPDATE app_users SET account_status = 'SUSPENDED', "
                        "auth_version = auth_version + 1 WHERE id = %s",
                        (actor_id,),
                    )
        finally:
            third_conn.close()

        holder_conn.commit()
        holder_conn.close()

        t.join(timeout=10)
        self.assertFalse(t.is_alive(), "request never completed after lock release")
        self.assertEqual(result.get("status"), 302)
        verify_unchanged()
        self.assertEqual(self._count_audits(), 0, "no audit row should be written for a rejected mutation")

    def test_approve_rejected_when_actor_revoked_while_waiting(self):
        def build(actor_id):
            client = self._client_for(actor_id)
            target = self._insert_user(username="pending@standards.vn", email="pending@standards.vn",
                                        auth_provider="GOOGLE", google_sub=None, account_status="PENDING")

            def do_post(c):
                return c.post(
                    "/admin/users/google/approve",
                    data={"user_id": str(target), "role": "staff", "team_id": "",
                          "csrf_token": "the-real-token"},
                ).status_code

            def verify_unchanged():
                status, is_admin, _, _ = self._fetch_user(target)
                self.assertEqual(status, "PENDING")
                self.assertFalse(is_admin)

            return client, do_post, verify_unchanged

        self._run_scenario(actor_provider="GOOGLE", build_request=build)

    def test_invite_rejected_when_actor_revoked_while_waiting(self):
        def build(actor_id):
            client = self._client_for(actor_id)

            def do_post(c):
                return c.post(
                    "/admin/users/google/invite",
                    data={"email": "brandnew@standards.vn", "csrf_token": "the-real-token"},
                ).status_code

            def verify_unchanged():
                conn = self._connect()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT COUNT(*) FROM app_users WHERE email = %s", ("brandnew@standards.vn",))
                        (n,) = cur.fetchone()
                finally:
                    conn.close()
                self.assertEqual(n, 0, "no invited account should have been created")

            return client, do_post, verify_unchanged

        with mock.patch.dict(os.environ, {"GOOGLE_WORKSPACE_ALLOWED_DOMAINS": "standards.vn"}):
            self._run_scenario(actor_provider="GOOGLE", build_request=build)

    def test_suspend_rejected_when_actor_revoked_while_waiting(self):
        def build(actor_id):
            client = self._client_for(actor_id)
            target = self._insert_user(username="victim@standards.vn", email="victim@standards.vn",
                                        auth_provider="GOOGLE", google_sub="sub-victim",
                                        account_status="ACTIVE")

            def do_post(c):
                return c.post(
                    "/admin/users/google/suspend",
                    data={"user_id": str(target), "csrf_token": "the-real-token"},
                ).status_code

            def verify_unchanged():
                status, _, _, _ = self._fetch_user(target)
                self.assertEqual(status, "ACTIVE")

            return client, do_post, verify_unchanged

        self._run_scenario(actor_provider="GOOGLE", build_request=build)

    def test_reactivate_rejected_when_actor_revoked_while_waiting(self):
        def build(actor_id):
            client = self._client_for(actor_id)
            target = self._insert_user(username="suspended@standards.vn", email="suspended@standards.vn",
                                        auth_provider="GOOGLE", google_sub="sub-suspended",
                                        account_status="SUSPENDED")

            def do_post(c):
                return c.post(
                    "/admin/users/google/reactivate",
                    data={"user_id": str(target), "csrf_token": "the-real-token"},
                ).status_code

            def verify_unchanged():
                status, _, _, _ = self._fetch_user(target)
                self.assertEqual(status, "SUSPENDED")

            return client, do_post, verify_unchanged

        self._run_scenario(actor_provider="GOOGLE", build_request=build)

    def test_revoke_sessions_rejected_when_actor_revoked_while_waiting(self):
        def build(actor_id):
            client = self._client_for(actor_id)
            target = self._insert_user(username="target@standards.vn", email="target@standards.vn",
                                        auth_provider="GOOGLE", google_sub="sub-target",
                                        account_status="ACTIVE", auth_version=5)

            def do_post(c):
                return c.post(
                    "/admin/users/google/revoke-sessions",
                    data={"user_id": str(target), "csrf_token": "the-real-token"},
                ).status_code

            def verify_unchanged():
                _, _, auth_version, _ = self._fetch_user(target)
                self.assertEqual(auth_version, 5)

            return client, do_post, verify_unchanged

        self._run_scenario(actor_provider="GOOGLE", build_request=build)

    def test_legacy_update_user_rejected_when_actor_revoked_while_waiting(self):
        def build(actor_id):
            client = self._client_for(actor_id)
            target = self._insert_user(username="local_target", auth_provider="LOCAL",
                                        is_admin=False, account_status="ACTIVE")

            def do_post(c):
                return c.post(
                    "/admin/users",
                    data={"action": "update_user", "user_id": str(target), "role": "admin",
                          "csrf_token": "the-real-token"},
                ).status_code

            def verify_unchanged():
                _, is_admin, auth_version, _ = self._fetch_user(target)
                self.assertFalse(is_admin)
                self.assertEqual(auth_version, 1)

            return client, do_post, verify_unchanged

        self._run_scenario(actor_provider="LOCAL", build_request=build)

    def test_legacy_create_user_rejected_when_actor_revoked_while_waiting(self):
        def build(actor_id):
            client = self._client_for(actor_id)

            def do_post(c):
                return c.post(
                    "/admin/users",
                    data={"action": "create_user", "username": "brandnew_local", "password": "x",
                          "role": "admin", "csrf_token": "the-real-token"},
                ).status_code

            def verify_unchanged():
                conn = self._connect()
                try:
                    with conn.cursor() as cur:
                        cur.execute("SELECT COUNT(*) FROM app_users WHERE username = %s", ("brandnew_local",))
                        (n,) = cur.fetchone()
                finally:
                    conn.close()
                self.assertEqual(n, 0, "no LOCAL account should have been created")

            return client, do_post, verify_unchanged

        self._run_scenario(actor_provider="LOCAL", build_request=build)


if __name__ == "__main__":
    unittest.main()
