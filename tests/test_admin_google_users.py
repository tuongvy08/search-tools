"""Phase 5D2B tests: Admin approval & Google Workspace user management.

No real DB is used -- a small in-memory fake stands in for `app_users` /
`login_audit_events`, matching only the exact SQL `admin_google_users.py`
issues. No real Google/network calls are made anywhere in this file.
"""
import os
import unittest
from unittest import mock

from flask import render_template
from psycopg2 import IntegrityError

import admin_google_users
import search
import session_security

FOUR_DOMAINS = "standards.com.vn,standards.vn,labmall.vn,biosciences.vn"


# session_security.py's before_request hook runs on every request that
# carries a `user_id` in session, regardless of endpoint. It uses its own
# `get_connection` (separate from `admin_google_users.get_connection`, which
# is what the tests below actually exercise). This tiny stand-in always
# reports the acting admin's own account as ACTIVE with auth_version=1 so
# that hook passes through without touching a real DB, independent of
# whatever FakeDB is under test for the action itself.
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


# --------------------------------------------------------------------------
# In-memory fake DB matching the exact SQL contract in admin_google_users.py
# --------------------------------------------------------------------------

class FakeDB:
    def __init__(self, users=None, teams=None):
        self.users = {u["id"]: dict(u) for u in (users or [])}
        self.teams = set(teams or [1, 2])
        self.audits = []
        self._next_id = max([u["id"] for u in self.users.values()], default=0) + 1
        # Phase 5D2B.2: with actor revalidation in place, a *valid* distinct
        # actor is by construction always counted as one of the "other
        # active admins", so a fake-cursor-only test can no longer reach a
        # real COUNT()==0 through normal data (that specific race is only
        # reachable with real concurrent transactions -- see the separate
        # real-Postgres integration test). This flag lets an isolated unit
        # test still verify that suspend()/update_user() correctly ABORT
        # when the count comes back 0, without pretending that proves
        # concurrency safety.
        self.force_zero_admin_count = False

    def email_or_username_taken(self, value):
        return any(
            u.get("email") == value or u.get("username") == value
            for u in self.users.values()
        )

    def insert_invited(self, email):
        if self.email_or_username_taken(email):
            raise IntegrityError("unique_violation")
        uid = self._next_id
        self._next_id += 1
        self.users[uid] = {
            "id": uid, "username": email, "email": email, "auth_provider": "GOOGLE",
            "google_sub": None, "display_name": None, "account_status": "INVITED",
            "is_admin": False, "team_id": None, "auth_version": 1,
        }
        return uid


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        db = self.db

        if s.startswith("SELECT pg_advisory_xact_lock"):
            # Phase 5D2B.1: acquired unconditionally at the top of
            # suspend()'s transaction; the fake DB doesn't need to model
            # real locking semantics here, just accept the call.
            self._result = []

        elif s.startswith("SELECT account_status, is_admin, auth_version FROM app_users WHERE id = %s"):
            # Phase 5D2B.2: actor revalidation, fresh read of the ACTING
            # admin's own row (never the target's).
            (uid,) = params
            u = db.users.get(uid)
            self._result = [(u["account_status"], u["is_admin"], u["auth_version"])] if u else []

        elif s.startswith("SELECT account_status FROM app_users WHERE id = %s AND auth_provider = 'GOOGLE' FOR UPDATE"):
            (uid,) = params
            u = db.users.get(uid)
            self._result = [(u["account_status"],)] if u and u.get("auth_provider") == "GOOGLE" else []

        elif s.startswith("SELECT id FROM teams WHERE id = %s"):
            (tid,) = params
            self._result = [(tid,)] if tid in db.teams else []

        elif s.startswith("UPDATE app_users SET account_status = 'ACTIVE', is_admin"):
            is_admin, team_id, approved_by, uid = params
            u = db.users[uid]
            u.update(account_status="ACTIVE", is_admin=is_admin, team_id=team_id,
                      approved_by=approved_by, approved_at="NOW")
            u["auth_version"] = u.get("auth_version", 1) + 1
            self._result = []

        elif s.startswith("SELECT account_status, is_admin FROM app_users WHERE id = %s AND auth_provider = 'GOOGLE' FOR UPDATE"):
            (uid,) = params
            u = db.users.get(uid)
            self._result = [(u["account_status"], u["is_admin"])] if u and u.get("auth_provider") == "GOOGLE" else []

        elif s.startswith("SELECT COUNT(*) FROM app_users WHERE is_admin = TRUE AND account_status = 'ACTIVE' AND id <> %s"):
            if db.force_zero_admin_count:
                self._result = [(0,)]
            else:
                (exclude_id,) = params
                count = sum(
                    1 for u in db.users.values()
                    if u.get("is_admin") and u.get("account_status") == "ACTIVE" and u["id"] != exclude_id
                )
                self._result = [(count,)]

        elif s.startswith("UPDATE app_users SET account_status = 'SUSPENDED'"):
            (uid,) = params
            u = db.users[uid]
            u["account_status"] = "SUSPENDED"
            u["auth_version"] = u.get("auth_version", 1) + 1
            self._result = []

        elif s.startswith("SELECT account_status, google_sub FROM app_users WHERE id = %s AND auth_provider = 'GOOGLE' FOR UPDATE"):
            (uid,) = params
            u = db.users.get(uid)
            self._result = [(u["account_status"], u.get("google_sub"))] if u and u.get("auth_provider") == "GOOGLE" else []

        elif s.startswith("UPDATE app_users SET account_status = %s,"):
            new_status, uid = params
            u = db.users[uid]
            u["account_status"] = new_status
            u["auth_version"] = u.get("auth_version", 1) + 1
            self._result = []

        elif s.startswith("SELECT id FROM app_users WHERE id = %s AND auth_provider = 'GOOGLE' FOR UPDATE"):
            (uid,) = params
            u = db.users.get(uid)
            self._result = [(uid,)] if u and u.get("auth_provider") == "GOOGLE" else []

        elif s.startswith("UPDATE app_users SET auth_version = auth_version + 1 WHERE id = %s"):
            (uid,) = params
            u = db.users[uid]
            u["auth_version"] = u.get("auth_version", 1) + 1
            self._result = []

        elif s.startswith("INSERT INTO app_users"):
            username, email = params
            uid = db.insert_invited(email)
            self._result = [(uid,)]

        elif s.startswith("UPDATE teams SET updated_at = NOW() WHERE id = ANY(%s)"):
            # Phase 6A-Fix2: admin_google_users.touch_team_updated_at,
            # called from approve()/update() whenever a user's team_id is
            # set/changed. This fake DB doesn't model `updated_at` at all;
            # just accept the call.
            self._result = []

        elif s.startswith("INSERT INTO login_audit_events"):
            db.audits.append(params)
            self._result = []

        else:
            raise AssertionError(f"Unexpected SQL in fake DB: {s}")

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


class FakeConnection:
    """Mimics psycopg2's connection context-manager semantics: commit on
    clean exit, rollback (without suppressing) on exception.
    """
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _fake_get_connection(db):
    return lambda: FakeConnection(db)


def _refuse_to_connect():
    raise AssertionError("must not query the DB for this request/action")


class _ClientTestCase(unittest.TestCase):
    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()
        # See _PassthroughSessionConn above: isolates the admin actor's own
        # session-liveness check (session_security.py) from the FakeDB used
        # to exercise the action under test (admin_google_users.py).
        patcher = mock.patch.object(session_security, "get_connection", _passthrough_session_connection)
        patcher.start()
        self.addCleanup(patcher.stop)
        # This file is about the admin-vs-staff ROLE guard and the Google
        # user actions themselves, not IP/team policy. Some sessions here
        # (e.g. staff without a team_id) would otherwise now be denied by
        # middleware_access.py's own (correct, Phase 6A-Fix1) fail-closed
        # behaviour before ever reaching the role guard under test --
        # disable the IP allowlist middleware here via its documented
        # escape hatch so this file keeps testing exactly what it says.
        env_patcher = mock.patch.dict(os.environ, {"DISABLE_IP_ALLOWLIST": "1"})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def _set_session(self, **kwargs):
        with self.client.session_transaction() as sess:
            sess.clear()
            sess.update(kwargs)

    def _admin_session(self, user_id=1):
        self._set_session(authenticated=True, user_id=user_id, auth_version=1,
                           is_admin=True, role="admin", username="admin1")

    def _csrf_form(self, **fields):
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "the-real-token"
        fields["csrf_token"] = "the-real-token"
        return fields


# --------------------------------------------------------------------------
# Access control: staff must not reach page or any admin-users API
# --------------------------------------------------------------------------

class AccessControlTests(_ClientTestCase):
    def test_staff_cannot_load_admin_users_page(self):
        # _require_admin_page() rejects before any DB access is attempted,
        # so no connection mock is needed (and none is installed) here.
        self._set_session(authenticated=True, user_id=9, auth_version=1, is_admin=False, role="user", username="staff1")
        resp = self.client.get("/admin/users")
        self.assertEqual(resp.status_code, 403)

    def test_staff_cannot_call_approve_action(self):
        self._set_session(authenticated=True, user_id=9, auth_version=1, is_admin=False, role="user")
        with mock.patch.object(admin_google_users, "get_connection", _refuse_to_connect):
            resp = self.client.post("/admin/users/google/approve", data=self._csrf_form(user_id="5", role="staff", team_id="1"))
        self.assertEqual(resp.status_code, 403)

    def test_staff_cannot_call_any_google_users_action(self):
        self._set_session(authenticated=True, user_id=9, auth_version=1, is_admin=False, role="user")
        actions = [
            "/admin/users/google/invite",
            "/admin/users/google/suspend",
            "/admin/users/google/reactivate",
            "/admin/users/google/revoke-sessions",
        ]
        with mock.patch.object(admin_google_users, "get_connection", _refuse_to_connect):
            for path in actions:
                resp = self.client.post(path, data=self._csrf_form(user_id="5", email="x@standards.vn"))
                self.assertEqual(resp.status_code, 403, path)

    def test_anonymous_cannot_call_actions(self):
        with mock.patch.object(admin_google_users, "get_connection", _refuse_to_connect):
            resp = self.client.post("/admin/users/google/suspend", data={"user_id": "5", "csrf_token": "x"})
        self.assertEqual(resp.status_code, 403)

    def test_legacy_breakglass_admin_without_user_id_cannot_act_as_actor(self):
        # A break-glass admin session (no per-user app_users row) must never
        # be recorded as the actor of an account-lifecycle action -- even
        # when legacy password login is enabled (so the session itself is
        # allowed to exist) and even less so when it is disabled.
        self._set_session(authenticated=True, is_admin=True, role="manager", username="__legacy_manager__")
        with mock.patch.dict(os.environ, {"ENABLE_LEGACY_PASSWORD_LOGIN": "true"}, clear=False), \
             mock.patch.object(admin_google_users, "get_connection", _refuse_to_connect):
            resp = self.client.post("/admin/users/google/suspend", data=self._csrf_form(user_id="5"))
        self.assertEqual(resp.status_code, 403)


# --------------------------------------------------------------------------
# CSRF
# --------------------------------------------------------------------------

class CsrfTests(_ClientTestCase):
    def test_missing_csrf_rejected_without_db_call(self):
        self._admin_session()
        with mock.patch.object(admin_google_users, "get_connection", _refuse_to_connect):
            resp = self.client.post("/admin/users/google/suspend", data={"user_id": "5"})
        self.assertEqual(resp.status_code, 400)

    def test_wrong_csrf_rejected_without_db_call(self):
        self._admin_session()
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "the-real-token"
        with mock.patch.object(admin_google_users, "get_connection", _refuse_to_connect):
            resp = self.client.post("/admin/users/google/suspend", data={"user_id": "5", "csrf_token": "wrong"})
        self.assertEqual(resp.status_code, 400)


# --------------------------------------------------------------------------
# Approve
# --------------------------------------------------------------------------

class ApproveTests(_ClientTestCase):
    def test_approve_pending_staff_with_valid_team(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "new@standards.vn", "email": "new@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": None, "display_name": None, "account_status": "PENDING", "is_admin": False,
             "team_id": None, "auth_version": 1},
        ], teams=[1, 2])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/approve",
                                     data=self._csrf_form(user_id="5", role="staff", team_id="2"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[5]["account_status"], "ACTIVE")
        self.assertEqual(db.users[5]["team_id"], 2)
        self.assertFalse(db.users[5]["is_admin"])
        self.assertEqual(db.users[5]["approved_by"], 1)
        self.assertEqual(db.users[5]["auth_version"], 2)
        self.assertEqual(db.audits[-1][4], "USER_APPROVED")

    def test_approve_admin_role_forces_team_null_even_if_team_sent(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "new@standards.vn", "email": "new@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": None, "display_name": None, "account_status": "PENDING", "is_admin": False,
             "team_id": None, "auth_version": 1},
        ], teams=[1])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/approve",
                                     data=self._csrf_form(user_id="5", role="admin", team_id="1"))
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(db.users[5]["team_id"])
        self.assertTrue(db.users[5]["is_admin"])

    def test_approve_staff_missing_team_rejected(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "new@standards.vn", "email": "new@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": None, "display_name": None, "account_status": "PENDING", "is_admin": False,
             "team_id": None, "auth_version": 1},
        ], teams=[1])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/approve",
                                     data=self._csrf_form(user_id="5", role="staff", team_id=""))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[5]["account_status"], "PENDING")  # untouched
        self.assertEqual(db.users[5]["auth_version"], 1)
        self.assertEqual(db.audits, [])

    def test_approve_staff_invalid_team_id_rejected(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "new@standards.vn", "email": "new@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": None, "display_name": None, "account_status": "PENDING", "is_admin": False,
             "team_id": None, "auth_version": 1},
        ], teams=[1])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/approve",
                                     data=self._csrf_form(user_id="5", role="staff", team_id="999"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[5]["account_status"], "PENDING")
        self.assertEqual(db.audits, [])

    def test_approve_non_pending_account_rejected_stale_status_not_overwritten(self):
        # Race/stale status: someone already approved (or the account is no
        # longer PENDING for another reason) -- must not be re-approved.
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "active@standards.vn", "email": "active@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s5", "display_name": None, "account_status": "ACTIVE", "is_admin": False,
             "team_id": 1, "auth_version": 3},
        ], teams=[1])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/approve",
                                     data=self._csrf_form(user_id="5", role="admin", team_id=""))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[5]["account_status"], "ACTIVE")
        self.assertEqual(db.users[5]["auth_version"], 3)  # unchanged
        self.assertFalse(db.users[5]["is_admin"])  # unchanged, not silently promoted
        self.assertEqual(db.audits, [])

    def test_approve_invalid_role_rejected(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "new@standards.vn", "email": "new@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": None, "display_name": None, "account_status": "PENDING", "is_admin": False,
             "team_id": None, "auth_version": 1},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/approve",
                                     data=self._csrf_form(user_id="5", role="superadmin", team_id=""))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[5]["account_status"], "PENDING")


# --------------------------------------------------------------------------
# Invite
# --------------------------------------------------------------------------

class InviteTests(_ClientTestCase):
    def setUp(self):
        super().setUp()
        self.env_patch = mock.patch.dict(os.environ, {"GOOGLE_WORKSPACE_ALLOWED_DOMAINS": FOUR_DOMAINS}, clear=False)
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    def test_invite_valid_domain_creates_invited_google_account(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/invite",
                                     data=self._csrf_form(email="New.User@Standards.VN"))
        self.assertEqual(resp.status_code, 302)
        created = [u for u in db.users.values() if u["email"] == "new.user@standards.vn"]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["account_status"], "INVITED")
        self.assertIsNone(created[0]["google_sub"])
        self.assertEqual(created[0]["auth_provider"], "GOOGLE")
        self.assertEqual(db.audits[-1][4], "USER_INVITED")

    def test_invite_disallowed_domain_rejected(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/invite",
                                     data=self._csrf_form(email="someone@evil.com"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(db.users), 1)  # only the actor -- no new account created
        self.assertEqual(db.audits, [])

    def test_duplicate_invite_fails_closed_no_overwrite(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "existing@standards.vn", "email": "existing@standards.vn",
             "auth_provider": "GOOGLE", "google_sub": "s5", "display_name": None,
             "account_status": "ACTIVE", "is_admin": True, "team_id": None, "auth_version": 1},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/invite",
                                     data=self._csrf_form(email="existing@standards.vn"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(db.users), 2)  # actor + the pre-existing target, nothing new
        self.assertEqual(db.users[5]["account_status"], "ACTIVE")  # untouched
        self.assertEqual(db.audits, [])


# --------------------------------------------------------------------------
# Suspend
# --------------------------------------------------------------------------

class SuspendTests(_ClientTestCase):
    def test_suspend_active_account_bumps_auth_version(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "staff5@standards.vn", "email": "staff5@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s5", "display_name": None, "account_status": "ACTIVE", "is_admin": False,
             "team_id": 1, "auth_version": 4},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/suspend", data=self._csrf_form(user_id="5"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[5]["account_status"], "SUSPENDED")
        self.assertEqual(db.users[5]["auth_version"], 5)
        self.assertEqual(db.audits[-1][4], "USER_SUSPENDED")

    def test_cannot_self_suspend(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 2, "username": "admin2", "email": "admin2@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s2", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/suspend", data=self._csrf_form(user_id="1"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[1]["account_status"], "ACTIVE")
        self.assertEqual(db.users[1]["auth_version"], 1)
        self.assertEqual(db.audits, [])

    def test_cannot_suspend_last_active_admin(self):
        # Isolated logic test (fake cursor, NOT a concurrency proof): with
        # actor revalidation in place, a valid distinct actor is always
        # itself counted as one "other active admin", so a real COUNT()==0
        # can only actually happen through a genuine concurrent race
        # between transactions (covered separately by the real-Postgres
        # integration test). Here we force the COUNT query to return 0 to
        # verify suspend() correctly aborts on that condition rather than
        # proceeding. Actor id=1 is a valid ACTIVE admin so it passes
        # actor-revalidation and we reach the count check itself.
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 2, "username": "admin2", "email": "admin2@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s2", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 9},
        ])
        db.force_zero_admin_count = True
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/suspend", data=self._csrf_form(user_id="2"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[2]["account_status"], "ACTIVE")
        self.assertEqual(db.users[2]["auth_version"], 9)
        self.assertEqual(db.audits, [])

    def test_suspend_rejects_when_actor_no_longer_valid_admin(self):
        # Phase 5D2B.2: actor revalidation. Session still says is_admin,
        # but the actor's OWN row in the DB is no longer an ACTIVE admin
        # (e.g. suspended/demoted by someone else while this request was
        # waiting for the lock). Must be rejected, target untouched.
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "SUSPENDED", "is_admin": True,
             "team_id": None, "auth_version": 2},
            {"id": 2, "username": "admin2", "email": "admin2@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s2", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 9},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/suspend", data=self._csrf_form(user_id="2"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[2]["account_status"], "ACTIVE")
        self.assertEqual(db.users[2]["auth_version"], 9)
        self.assertEqual(db.audits, [])

    def test_suspend_rejects_when_actor_auth_version_stale(self):
        # Actor's DB row is ACTIVE admin but auth_version no longer matches
        # the session (their sessions were revoked in the meantime) -> the
        # session itself would normally be invalidated by
        # enforce_session_validity before reaching here, but this verifies
        # the defense-in-depth check inside suspend() itself too.
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 2},
            {"id": 2, "username": "admin2", "email": "admin2@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s2", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 9},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/suspend", data=self._csrf_form(user_id="2"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[2]["account_status"], "ACTIVE")
        self.assertEqual(db.audits, [])

    def test_suspend_allowed_when_another_active_admin_remains(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 2, "username": "admin2", "email": "admin2@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s2", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 9},
            {"id": 3, "username": "admin3", "email": "admin3@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s3", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/suspend", data=self._csrf_form(user_id="2"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[2]["account_status"], "SUSPENDED")
        self.assertEqual(db.users[2]["auth_version"], 10)

    def test_suspend_non_active_account_rejected(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "invited@standards.vn", "email": "invited@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": None, "display_name": None, "account_status": "INVITED", "is_admin": False,
             "team_id": None, "auth_version": 1},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/suspend", data=self._csrf_form(user_id="5"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[5]["account_status"], "INVITED")
        self.assertEqual(db.audits, [])


# --------------------------------------------------------------------------
# Reactivate
# --------------------------------------------------------------------------

class ReactivateTests(_ClientTestCase):
    def test_reactivate_suspended_with_google_sub_becomes_active(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "u5@standards.vn", "email": "u5@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "sub-5", "display_name": None, "account_status": "SUSPENDED", "is_admin": False,
             "team_id": 1, "auth_version": 2},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/reactivate", data=self._csrf_form(user_id="5"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[5]["account_status"], "ACTIVE")
        self.assertEqual(db.users[5]["auth_version"], 3)
        self.assertEqual(db.audits[-1][4], "USER_REACTIVATED")

    def test_reactivate_suspended_without_google_sub_becomes_invited(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 6, "username": "u6@standards.vn", "email": "u6@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": None, "display_name": None, "account_status": "SUSPENDED", "is_admin": False,
             "team_id": None, "auth_version": 1},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/reactivate", data=self._csrf_form(user_id="6"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[6]["account_status"], "INVITED")
        self.assertEqual(db.users[6]["auth_version"], 2)

    def test_reactivate_non_suspended_rejected(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 7, "username": "u7@standards.vn", "email": "u7@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s7", "display_name": None, "account_status": "ACTIVE", "is_admin": False,
             "team_id": 1, "auth_version": 3},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/reactivate", data=self._csrf_form(user_id="7"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[7]["account_status"], "ACTIVE")
        self.assertEqual(db.users[7]["auth_version"], 3)
        self.assertEqual(db.audits, [])


# --------------------------------------------------------------------------
# Revoke sessions
# --------------------------------------------------------------------------

class RevokeSessionsTests(_ClientTestCase):
    def test_revoke_bumps_auth_version_without_changing_status(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "u5@standards.vn", "email": "u5@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s5", "display_name": None, "account_status": "ACTIVE", "is_admin": False,
             "team_id": 1, "auth_version": 7},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/revoke-sessions", data=self._csrf_form(user_id="5"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[5]["account_status"], "ACTIVE")  # unchanged
        self.assertEqual(db.users[5]["auth_version"], 8)
        self.assertEqual(db.audits[-1][4], "USER_SESSIONS_REVOKED")

    def test_revoke_on_unknown_or_non_google_user_rejected(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/revoke-sessions", data=self._csrf_form(user_id="999"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.audits, [])


# --------------------------------------------------------------------------
# Phase 5D2B Final: EVERY action here (not just suspend()) must acquire the
# shared advisory lock and revalidate the actor's own DB row FIRST. These
# are regression tests, one per newly-protected action, all following the
# exact same shape as SuspendTests.test_suspend_rejects_when_actor_no_longer_valid_admin:
# actor's session still claims is_admin, but their OWN row says otherwise
# -> reject, target completely untouched, no audit written.
# --------------------------------------------------------------------------

class ActorRevalidationAppliesToEveryActionTests(_ClientTestCase):
    def test_approve_rejected_when_actor_no_longer_valid_admin(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "SUSPENDED", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "new@standards.vn", "email": "new@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": None, "display_name": None, "account_status": "PENDING", "is_admin": False,
             "team_id": None, "auth_version": 1},
        ], teams=[1])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/approve",
                                     data=self._csrf_form(user_id="5", role="staff", team_id="1"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[5]["account_status"], "PENDING")
        self.assertEqual(db.users[5]["auth_version"], 1)
        self.assertIsNone(db.users[5].get("approved_by"))
        self.assertEqual(db.audits, [])

    def test_invite_rejected_when_actor_no_longer_valid_admin(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": False,
             "team_id": None, "auth_version": 1},  # demoted -- no longer is_admin
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/invite",
                                     data=self._csrf_form(email="new.person@standards.vn"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(db.users), 1)  # no new account created
        self.assertEqual(db.audits, [])

    def test_reactivate_rejected_when_actor_no_longer_valid_admin(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "SUSPENDED", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "u5@standards.vn", "email": "u5@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "sub-5", "display_name": None, "account_status": "SUSPENDED", "is_admin": False,
             "team_id": 1, "auth_version": 2},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/reactivate", data=self._csrf_form(user_id="5"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[5]["account_status"], "SUSPENDED")
        self.assertEqual(db.users[5]["auth_version"], 2)
        self.assertEqual(db.audits, [])

    def test_revoke_sessions_rejected_when_actor_no_longer_valid_admin(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 9},  # stale auth_version vs session's 1
            {"id": 5, "username": "u5@standards.vn", "email": "u5@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s5", "display_name": None, "account_status": "ACTIVE", "is_admin": False,
             "team_id": 1, "auth_version": 7},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/revoke-sessions", data=self._csrf_form(user_id="5"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[5]["auth_version"], 7)  # unchanged
        self.assertEqual(db.audits, [])


# --------------------------------------------------------------------------
# Audit content safety
# --------------------------------------------------------------------------

class AuditSafetyTests(_ClientTestCase):
    def test_audit_rows_contain_no_secret_sub_or_token(self):
        self._admin_session(user_id=1)
        db = FakeDB(users=[
            {"id": 1, "username": "admin1", "email": "admin1@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "s1", "display_name": None, "account_status": "ACTIVE", "is_admin": True,
             "team_id": None, "auth_version": 1},
            {"id": 5, "username": "u5@standards.vn", "email": "u5@standards.vn", "auth_provider": "GOOGLE",
             "google_sub": "super-secret-sub-value", "display_name": None, "account_status": "ACTIVE",
             "is_admin": False, "team_id": 1, "auth_version": 1},
        ])
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            self.client.post("/admin/users/google/suspend", data=self._csrf_form(user_id="5"))
        self.assertEqual(len(db.audits), 1)
        target_user_id, actor_user_id, provider, outcome, reason_code, target_team_id = db.audits[0]
        self.assertEqual(target_user_id, 5)
        self.assertEqual(actor_user_id, 1)
        self.assertEqual(provider, "GOOGLE")
        self.assertEqual(outcome, "SUCCESS")
        self.assertEqual(reason_code, "USER_SUSPENDED")
        self.assertIsNone(target_team_id)
        for value in db.audits[0]:
            self.assertNotIn("super-secret-sub-value", str(value))
            self.assertNotIn("token", str(value).lower())

    def test_generic_error_never_leaks_db_detail(self):
        self._admin_session(user_id=1)
        db = FakeDB()  # user_id 42 does not exist
        with mock.patch.object(admin_google_users, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/admin/users/google/reactivate", data=self._csrf_form(user_id="42"))
        self.assertNotIn(b"constraint", resp.data.lower())
        self.assertNotIn(b"psycopg2", resp.data.lower())


# --------------------------------------------------------------------------
# Template escaping (Jinja auto-escape; no innerHTML anywhere in the page)
# --------------------------------------------------------------------------

class TemplateEscapingTests(unittest.TestCase):
    def test_admin_users_template_escapes_malicious_google_user_fields(self):
        malicious_user = {
            "id": 1,
            "email": '"><script>alert(1)</script>@evil.com',
            "display_name": "<img src=x onerror=alert(2)>",
            "role": "staff",
            "team_id": None,
            "team_name": '<b>team</b>',
            "account_status": "PENDING",
            "approved_at": None,
            "last_login_at": None,
        }
        with search.app.test_request_context("/admin/users"):
            html = render_template(
                "admin_users.html",
                users=[], distinct_brands=[],
                google_users=[malicious_user], google_teams=[],
                google_allowed_domains=[], message=None, error=None,
            )
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x onerror=alert(2)>", html)
        self.assertNotIn("<b>team</b>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_template_uses_no_innerhtml(self):
        # Checks for actual `.innerHTML` usage (property access/assignment),
        # not the word appearing in an explanatory comment.
        with open(os.path.join(os.path.dirname(__file__), "..", "templates", "admin_users.html"),
                   encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn(".innerHTML", content)


if __name__ == "__main__":
    unittest.main()
