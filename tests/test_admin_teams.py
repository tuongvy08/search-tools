"""Phase 6A-Fix2: fast, no-real-DB tests for `admin_teams.py`.

Scope here is deliberately narrow -- pure validation logic and "does the
route even attempt a mutation" guards that don't need a real transaction/
lock/staleness proof (those live in `tests/test_team_permissions_pg.py`,
against a real temporary Postgres, per AGENTS.md's "mock for validation,
Postgres for transaction/staleness" split):

  - `_validate_brands`: an invalid brand rejects the WHOLE submission
    (Phase 6A-Fix2 fix -- previously silently dropped and saved the rest).
  - CSRF-missing/wrong and non-admin/staff-direct-call requests are
    rejected BEFORE `get_connection()` is ever called -- proven by
    patching `admin_teams.get_connection` to raise if invoked, which is a
    stronger guarantee than inspecting a mock's call log after the fact.

No real Google/network calls; no `products_local` access.
"""
import unittest
from unittest import mock

import admin_teams
import search
import session_security


# session_security.enforce_session_validity runs as a before_request hook
# on EVERY request carrying session["user_id"] (regardless of endpoint),
# using its OWN `get_connection` (separate from admin_teams.get_connection,
# which the "no DB call" guard below targets). This tiny stand-in always
# reports the acting user's account as ACTIVE with auth_version=1 so that
# hook passes through -- same pattern already used by
# tests/test_admin_google_users.py.
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


def _no_db_allowed():
    """Patches `admin_teams.get_connection` to blow up if a route calls it
    -- used to prove a rejected request (bad CSRF, non-admin, etc.) never
    even attempts to open a DB connection, let alone mutate anything.
    """
    def _boom(*a, **kw):
        raise AssertionError("get_connection() must not be called for a rejected request")
    return mock.patch("admin_teams.get_connection", side_effect=_boom)


class ValidateBrandsUnitTests(unittest.TestCase):
    """Pure-function tests, no Flask/DB involved at all."""

    def test_all_valid_brands_returned_sorted_deduped(self):
        result = admin_teams._validate_brands(["B", "A", "A"], ["A", "B", "C"])
        self.assertEqual(result, ["A", "B"])

    def test_empty_submission_is_valid_and_returns_empty(self):
        # A team may legitimately have zero brands ("Team có thể không có
        # brand; staff team đó không thấy sản phẩm nào").
        self.assertEqual(admin_teams._validate_brands([], ["A", "B"]), [])
        self.assertEqual(admin_teams._validate_brands(None, ["A", "B"]), [])

    def test_any_unknown_brand_rejects_the_whole_submission(self):
        # Fix2: must NOT silently drop "Forged" and keep "A" -- the whole
        # call must raise, and the caller must not persist "A" alone.
        with self.assertRaises(admin_teams._ActionError) as ctx:
            admin_teams._validate_brands(["A", "Forged-Brand"], ["A", "B"])
        self.assertEqual(str(ctx.exception), admin_teams._ERR_INVALID_BRAND)

    def test_rejection_message_never_echoes_the_invalid_value(self):
        # Generic message only -- never interpolate the attacker-controlled
        # brand string into the error shown to the client.
        with self.assertRaises(admin_teams._ActionError) as ctx:
            admin_teams._validate_brands(["<script>alert(1)</script>"], ["A"])
        self.assertNotIn("<script>", str(ctx.exception))


class _ClientTestCase(unittest.TestCase):
    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()
        # Fix1 collateral: this file only exercises admin_teams' own
        # CSRF/actor/role guards (all of which run and reject BEFORE the
        # IP middleware would matter), not IP policy itself -- disabling
        # it here keeps this file focused, exactly like the other mocked
        # admin test files already do.
        self._disable_ip_patch = mock.patch.dict(
            "os.environ", {"DISABLE_IP_ALLOWLIST": "1"}
        )
        self._disable_ip_patch.start()
        self._session_conn_patch = mock.patch.object(
            session_security, "get_connection", side_effect=_passthrough_session_connection
        )
        self._session_conn_patch.start()

    def tearDown(self):
        self._session_conn_patch.stop()
        self._disable_ip_patch.stop()

    def _admin_session(self, user_id=1, auth_version=1):
        with self.client.session_transaction() as sess:
            sess.clear()
            sess.update(authenticated=True, user_id=user_id, is_admin=True,
                        auth_version=auth_version, role="admin", username="admin1")
            sess["csrf_token"] = "the-real-token"

    def _staff_session(self, user_id=2, team_id=1, auth_version=1):
        with self.client.session_transaction() as sess:
            sess.clear()
            sess.update(authenticated=True, user_id=user_id, is_admin=False,
                        team_id=team_id, auth_version=auth_version, role="staff",
                        username="staff1")
            sess["csrf_token"] = "the-real-token"


class NoMutationOnRejectedRequestTests(_ClientTestCase):
    """For each mutation route: CSRF failure or non-admin caller must be
    rejected without ever touching the DB (patched to explode if called).
    """

    ROUTES = [
        "/admin/teams/create",
        "/admin/teams/rename",
        "/admin/teams/preview",
        "/admin/teams/confirm",
    ]

    def test_missing_csrf_token_rejected_before_any_db_call(self):
        self._admin_session()
        with _no_db_allowed():
            for route in self.ROUTES:
                resp = self.client.post(route, data={})
                self.assertEqual(resp.status_code, 400, route)

    def test_wrong_csrf_token_rejected_before_any_db_call(self):
        self._admin_session()
        with _no_db_allowed():
            for route in self.ROUTES:
                resp = self.client.post(route, data={"csrf_token": "not-the-real-token"})
                self.assertEqual(resp.status_code, 400, route)

    def test_staff_direct_call_rejected_before_any_db_call(self):
        self._staff_session()
        with _no_db_allowed():
            for route in self.ROUTES:
                resp = self.client.post(route, data={"csrf_token": "the-real-token"})
                self.assertEqual(resp.status_code, 403, route)

    def test_unauthenticated_call_rejected_before_any_db_call(self):
        with self.client.session_transaction() as sess:
            sess.clear()
        with _no_db_allowed():
            for route in self.ROUTES:
                resp = self.client.post(route, data={"csrf_token": "x"})
                self.assertEqual(resp.status_code, 403, route)

    def test_admin_page_get_redirects_or_403_for_non_admin_without_db(self):
        # GET /admin/teams as staff: `_require_admin_page` returns 403
        # before `index()`'s own `get_connection()` call is ever reached.
        self._staff_session()
        with _no_db_allowed():
            resp = self.client.get("/admin/teams")
            self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
