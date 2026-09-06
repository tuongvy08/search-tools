"""Phase 5D2A tests: session validation, logout, CSRF, and role-gated
navigation. Companion to `tests/test_google_oidc_auth.py`.

No real DB is used — a tiny in-memory fake stands in for `app_users` /
`login_audit_events`, matching only the exact SQL `session_security.py`
issues.

This file is about SESSION liveness/CSRF/nav, not IP/team policy, and
several sessions here intentionally predate the team model (no
`team_id`). `middleware_access.py`'s `before_request` hook is also
registered on `search.app` and would otherwise try to resolve a real
team IP policy for those non-admin sessions (Phase 6A-Fix1: an
authenticated non-admin session with no usable `team_id` now fails
closed, by design -- see `tests/test_middleware_access.py` for that
behaviour). Disable it here via the documented `DISABLE_IP_ALLOWLIST`
escape hatch so this file keeps testing exactly what it says it tests,
without depending on the real `office_ip_allowlist`/`teams` tables.
"""
import os
import unittest
from unittest import mock

import search
import session_security


class _FakeUserDB:
    def __init__(self, users=None):
        self.users = dict(users or {})  # user_id -> (account_status, auth_version)
        self.audits = []


class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if "SELECT account_status, auth_version FROM app_users WHERE id" in s:
            (user_id,) = params
            row = self.db.users.get(user_id)
            self._result = [row] if row is not None else []
        elif "INSERT INTO login_audit_events" in s:
            self.db.audits.append(params)
            self._result = []
        else:
            raise AssertionError(f"Unexpected SQL in fake DB: {s}")

    def fetchone(self):
        return self._result[0] if self._result else None


class _FakeConnection:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _FakeCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _fake_get_connection(db):
    return lambda: _FakeConnection(db)


def _refuse_to_connect():
    raise AssertionError("must not query the DB for this request")


class _ClientTestCase(unittest.TestCase):
    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()
        env_patcher = mock.patch.dict(os.environ, {"DISABLE_IP_ALLOWLIST": "1"})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

    def _set_session(self, **kwargs):
        with self.client.session_transaction() as sess:
            sess.clear()
            sess.update(kwargs)


# --------------------------------------------------------------------------
# Per-request session-liveness check (before_request)
# --------------------------------------------------------------------------

class SessionRevocationTests(_ClientTestCase):
    def test_auth_version_mismatch_clears_session_and_redirects(self):
        db = _FakeUserDB({7: ("ACTIVE", 5)})
        self._set_session(authenticated=True, user_id=7, username="u", role="user", auth_version=4)
        with mock.patch.object(session_security, "get_connection", _fake_get_connection(db)):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        with self.client.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)
            self.assertNotIn("user_id", sess)
        self.assertEqual(len(db.audits), 1)
        self.assertEqual(db.audits[0][2], "DENIED")
        self.assertEqual(db.audits[0][3], "AUTH_VERSION_MISMATCH")

    def test_suspended_account_clears_existing_session(self):
        db = _FakeUserDB({8: ("SUSPENDED", 1)})
        self._set_session(authenticated=True, user_id=8, auth_version=1)
        with mock.patch.object(session_security, "get_connection", _fake_get_connection(db)):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)
        self.assertEqual(db.audits[0][3], "ACCOUNT_NOT_ACTIVE")

    def test_deleted_account_clears_existing_session(self):
        db = _FakeUserDB({})  # user_id 999 no longer exists
        self._set_session(authenticated=True, user_id=999, auth_version=1)
        with mock.patch.object(session_security, "get_connection", _fake_get_connection(db)):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.audits[0][3], "ACCOUNT_NOT_FOUND")

    def test_matching_active_account_and_version_passes_through(self):
        db = _FakeUserDB({9: ("ACTIVE", 3)})
        self._set_session(authenticated=True, user_id=9, auth_version=3, username="ok", role="user")
        with mock.patch.object(session_security, "get_connection", _fake_get_connection(db)):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(db.audits, [])

    def test_api_path_gets_json_401_instead_of_redirect(self):
        db = _FakeUserDB({7: ("ACTIVE", 5)})
        self._set_session(authenticated=True, user_id=7, auth_version=4, team_id=1)
        with mock.patch.object(session_security, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/api/quote-assistant/preflight", json={})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.mimetype, "application/json")

    def test_legacy_break_glass_session_without_user_id_skips_db_check_when_enabled(self):
        # This bypass is ONLY valid while ENABLE_LEGACY_PASSWORD_LOGIN=true --
        # it is never a legitimate production break-glass path on its own.
        self._set_session(authenticated=True, username="__legacy_manager__", is_admin=True, role="manager")
        with mock.patch.dict(os.environ, {"ENABLE_LEGACY_PASSWORD_LOGIN": "true"}, clear=False), \
             mock.patch.object(session_security, "get_connection", _refuse_to_connect):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_legacy_break_glass_session_without_user_id_fails_closed_when_disabled(self):
        # Phase 5D2B: with legacy password login disabled (the default), a
        # session that is `authenticated=True` but has no `user_id` must be
        # rejected and cleared -- never silently passed through.
        self._set_session(authenticated=True, username="__legacy_manager__", is_admin=True, role="manager")
        db = _FakeUserDB({})
        with mock.patch.dict(os.environ, {"ENABLE_LEGACY_PASSWORD_LOGIN": "false"}, clear=False), \
             mock.patch.object(session_security, "get_connection", _fake_get_connection(db)):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        with self.client.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)
            self.assertNotIn("is_admin", sess)
        self.assertEqual(len(db.audits), 1)
        self.assertIsNone(db.audits[0][0])  # user_id: no per-user row to attribute this to
        self.assertEqual(db.audits[0][3], "LEGACY_SESSION_DISABLED")

    def test_anonymous_request_skips_db_check(self):
        with mock.patch.object(session_security, "get_connection", _refuse_to_connect):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)  # redirected to /login by the view itself

    def test_login_page_is_exempt_from_db_check(self):
        self._set_session(authenticated=True, user_id=123, auth_version=1)
        with mock.patch.object(session_security, "get_connection", _refuse_to_connect):
            resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 200)

    def test_static_asset_is_exempt_from_db_check(self):
        self._set_session(authenticated=True, user_id=123, auth_version=1)
        with mock.patch.object(session_security, "get_connection", _refuse_to_connect):
            resp = self.client.get("/static/styles.css")
        self.assertIn(resp.status_code, (200, 304, 404))  # never 500 from a DB call


# --------------------------------------------------------------------------
# POST /logout + CSRF
# --------------------------------------------------------------------------

class LogoutCsrfTests(_ClientTestCase):
    def _login_session(self):
        self._set_session(authenticated=True, user_id=42, username="someone",
                           auth_provider="LOCAL", role="user")

    def test_get_logout_is_not_allowed_and_does_not_log_out(self):
        self._login_session()
        resp = self.client.get("/logout")
        self.assertEqual(resp.status_code, 405)
        with self.client.session_transaction() as sess:
            self.assertTrue(sess.get("authenticated"))

    def test_post_logout_missing_csrf_is_rejected(self):
        self._login_session()
        resp = self.client.post("/logout", data={})
        self.assertEqual(resp.status_code, 400)
        with self.client.session_transaction() as sess:
            self.assertTrue(sess.get("authenticated"))

    def test_post_logout_wrong_csrf_is_rejected(self):
        self._login_session()
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "the-real-token"
        resp = self.client.post("/logout", data={"csrf_token": "not-the-real-token"})
        self.assertEqual(resp.status_code, 400)
        with self.client.session_transaction() as sess:
            self.assertTrue(sess.get("authenticated"))

    def test_post_logout_valid_csrf_clears_session_and_audits_without_secrets(self):
        self._login_session()
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "the-real-token"
        db = _FakeUserDB({})
        with mock.patch.object(session_security, "get_connection", _fake_get_connection(db)):
            resp = self.client.post("/logout", data={"csrf_token": "the-real-token"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])
        with self.client.session_transaction() as sess:
            self.assertEqual(dict(sess), {})
        self.assertEqual(len(db.audits), 1)
        user_id, provider, outcome, reason_code = db.audits[0]
        self.assertEqual(user_id, 42)
        self.assertEqual(provider, "LOCAL")
        self.assertEqual(outcome, "SUCCESS")
        self.assertEqual(reason_code, "LOGOUT")
        for value in db.audits[0]:
            self.assertNotIn("the-real-token", str(value))


# --------------------------------------------------------------------------
# Role-gated shared navigation partial
# --------------------------------------------------------------------------

class NavigationVisibilityTests(_ClientTestCase):
    # Phase 5D2B: a real login (LOCAL or GOOGLE) always sets `user_id`; only
    # the legacy break-glass path omits it, and that path is gated by
    # ENABLE_LEGACY_PASSWORD_LOGIN (see SessionRevocationTests above). So a
    # session used to exercise the nav partial must carry `user_id` +
    # `auth_version` like a real session would, with the per-request
    # liveness check mocked to match.
    def test_staff_sees_search_and_quick_quote_but_not_admin_links(self):
        self._set_session(authenticated=True, user_id=101, auth_version=1, username="staff1", role="user", is_admin=False)
        db = _FakeUserDB({101: ("ACTIVE", 1)})
        with mock.patch.object(session_security, "get_connection", _fake_get_connection(db)):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Quick Quote", resp.data)
        self.assertNotIn(b"/admin/imports", resp.data)
        self.assertNotIn(b"/admin/quote-templates", resp.data)
        self.assertNotIn(b"/admin/users", resp.data)
        self.assertIn("Đăng xuất".encode("utf-8"), resp.data)

    def test_admin_sees_admin_navigation_links(self):
        self._set_session(authenticated=True, user_id=102, auth_version=1, username="admin1", role="admin", is_admin=True)
        db = _FakeUserDB({102: ("ACTIVE", 1)})
        with mock.patch.object(session_security, "get_connection", _fake_get_connection(db)):
            resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"/admin/imports", resp.data)
        self.assertIn(b"/admin/quote-templates", resp.data)
        self.assertIn(b"/admin/users", resp.data)

    def test_logged_out_visitor_sees_no_user_nav(self):
        resp = self.client.get("/login")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"sq-user-nav", resp.data)

    def test_logout_form_carries_a_csrf_token(self):
        self._set_session(authenticated=True, user_id=103, auth_version=1, username="staff1", role="user", is_admin=False)
        db = _FakeUserDB({103: ("ACTIVE", 1)})
        with mock.patch.object(session_security, "get_connection", _fake_get_connection(db)):
            resp = self.client.get("/")
        self.assertIn(b'name="csrf_token"', resp.data)


if __name__ == "__main__":
    unittest.main()
