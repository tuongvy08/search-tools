"""Phase 6A-Fix1: behavioural tests for `middleware_access.py` run against
the REAL `before_request` hook already registered on `search.app` (via
`register_ip_access_control`, called once at import time) -- not a
reimplementation, and the policy-decision functions
(`_resolve_effective_policy`, `_load_db_cidrs`, `_load_team_ip_policy`) are
never mocked out; only the DB CURSOR layer they call through
`middleware_access.get_connection` is faked, so a real request goes through
real routing logic against controlled, deterministic data.

Never touches a real Postgres connection / `products_local`: this file
patches `middleware_access.get_connection` (the IP/team-policy reads) and
`session_security.get_connection` (the per-request session-liveness check,
via the shared `tests/auth_test_helpers.py` fixture already used by other
real-`search.app` test files) with tiny in-memory fakes.

Covers:
  - INHERIT / ALLOWLIST_ONLY / ANY_AUTHENTICATED: matching/non-matching IP,
    genuinely empty rule sets (valid, not an error).
  - `_load_db_cidrs` / `_load_team_ip_policy` read FAILURES (connection
    lost, migration-015 column/table missing, team deleted, invalid
    stored value, staff session missing team_id) -- all now denied with
    503 (dependency unavailable), never silently opened or downgraded to
    a specific policy value such as INHERIT.
  - A session already rejected by `session_security.enforce_session_validity`
    (revoked/suspended/auth_version-mismatched) never reaches this
    middleware at all, so a stale cookie's `ip_bypass_allowlist=True`
    never actually grants anything.
  - Login / logout / Google OAuth entry+callback stay reachable
    (endpoint-exact exemption) while a path that merely *resembles* one of
    them (but isn't the same endpoint) is NOT exempted.
  - `_client_ip()` under the app's real one-hop `ProxyFix` config: a
    client-injected LEADING `X-Forwarded-For` value never wins over the
    trusted trailing hop; a single trusted-hop value is still honoured.
  - LOCAL and GOOGLE sessions on the identical team get identical
    accept/deny outcomes.
"""
import os
import unittest
from unittest import mock

import search
import middleware_access
import session_security
from auth_test_helpers import start_auth_db_patch


# --------------------------------------------------------------------------
# Fake DB for middleware_access.get_connection -- answers ONLY the two
# queries that module issues (office_ip_allowlist / teams.ip_policy), with
# per-test-configurable data AND per-test-configurable read failures.
# --------------------------------------------------------------------------

class _Boom(Exception):
    """Stand-in for "any DB/schema read failure" -- deliberately NOT a
    psycopg2-specific error class, since Fix1 removed all special-casing
    by exception type: every failure must be handled identically.
    """


class FakeMwDB:
    def __init__(self):
        self.cidrs: list[str] = []
        self.cidr_error: Exception | None = None
        self.team_policies: dict[int, str] = {}
        self.team_error: Exception | None = None
        self.queries: list[str] = []


class _FakeMwCursor:
    def __init__(self, db: FakeMwDB):
        self.db = db
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.db.queries.append(s)
        if "FROM office_ip_allowlist" in s:
            if self.db.cidr_error is not None:
                raise self.db.cidr_error
            self._result = [(c,) for c in self.db.cidrs]
        elif "SELECT ip_policy FROM teams WHERE id" in s:
            if self.db.team_error is not None:
                raise self.db.team_error
            (team_id,) = params
            if team_id in self.db.team_policies:
                self._result = [(self.db.team_policies[team_id],)]
            else:
                self._result = []  # team not found (e.g. deleted)
        else:
            raise AssertionError(f"Unexpected SQL against fake middleware DB: {s}")

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


class _FakeMwConnection:
    def __init__(self, db: FakeMwDB):
        self.db = db

    def cursor(self):
        return _FakeMwCursor(self.db)

    def close(self):
        pass


def _fake_mw_get_connection(db: FakeMwDB):
    return lambda: _FakeMwConnection(db)


# A throwaway, dependency-free route added ONCE to the real `search.app` so
# tests can exercise the REAL before_request chain (session_security ->
# middleware_access) end-to-end without depending on any business view's
# unrelated behaviour/DB calls. Guarded so re-import/test-discovery doesn't
# try to register it twice.
if "_mw_test_probe" not in search.app.view_functions:
    @search.app.route("/__mw_test_probe__")
    def _mw_test_probe():
        return "OK", 200

# Decoy routes registered ONCE at import time (Flask refuses new
# `add_url_rule`/`route` calls after the app has handled its first
# request) -- their PATHS resemble exempt endpoints (Google OAuth, login)
# but their ENDPOINT NAMES are different, so `ExemptEndpointTests` can
# prove the exemption is endpoint-exact, never a path substring match.
if "_mw_test_google_decoy" not in search.app.view_functions:
    @search.app.route("/auth/google/not-the-real-callback")
    def _mw_test_google_decoy():
        return "decoy", 200

if "_mw_test_fake_login" not in search.app.view_functions:
    @search.app.route("/admin/not-a-real-login")
    def _mw_test_fake_login():
        return "nope", 200


class _MiddlewareTestBase(unittest.TestCase):
    """Common setup: real `search.app` test client, IP allowlist middleware
    enabled (never DISABLE_IP_ALLOWLIST), env OFFICE_IP_ALLOWLIST cleared
    unless a test sets it, and `middleware_access.get_connection` patched
    to the fake DB for the lifetime of each test.
    """

    def setUp(self):
        self.db = FakeMwDB()
        patcher = mock.patch.object(middleware_access, "get_connection", _fake_mw_get_connection(self.db))
        patcher.start()
        self.addCleanup(patcher.stop)

        env_patcher = mock.patch.dict(os.environ, {"DISABLE_IP_ALLOWLIST": "", "OFFICE_IP_ALLOWLIST": ""})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

        search.app.testing = True
        self.client = search.app.test_client()

    def _get(self, path="/__mw_test_probe__", **kwargs):
        return self.client.get(path, **kwargs)

    def _set_session(self, **fields):
        with self.client.session_transaction() as sess:
            sess.update(fields)


# --------------------------------------------------------------------------
# INHERIT (default: anonymous, admin, or authenticated-without-team)
# --------------------------------------------------------------------------

class InheritModeTests(_MiddlewareTestBase):
    def test_no_rules_at_all_allows_anonymous(self):
        # Genuinely empty read (both env and DB) -- documented dev-friendly
        # INHERIT contract: allow. Not an error, must not be 503.
        resp = self._get()
        self.assertEqual(resp.status_code, 200)

    def test_rules_configured_matching_ip_allows(self):
        self.db.cidrs = ["203.0.113.50/32"]
        resp = self._get(environ_overrides={"REMOTE_ADDR": "203.0.113.50"})
        self.assertEqual(resp.status_code, 200)

    def test_rules_configured_nonmatching_ip_denies_403(self):
        self.db.cidrs = ["203.0.113.50/32"]
        resp = self._get(environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        self.assertEqual(resp.status_code, 403)

    def test_env_rule_matching_allows(self):
        with mock.patch.dict(os.environ, {"OFFICE_IP_ALLOWLIST": "198.51.100.7"}):
            resp = self._get(environ_overrides={"REMOTE_ADDR": "198.51.100.7"})
        self.assertEqual(resp.status_code, 200)

    def test_cidr_read_error_denies_503_never_open(self):
        # DB rules configured logically but the READ itself fails -- must
        # NOT be treated as "no rules => allow" (the old fail-open bug).
        self.db.cidr_error = _Boom("connection refused")
        resp = self._get(environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        self.assertEqual(resp.status_code, 503)
        # Never leak the raw exception text / any SQL to the client.
        body = resp.get_data(as_text=True)
        self.assertNotIn("connection refused", body)
        self.assertNotIn("office_ip_allowlist", body)
        self.assertNotIn("SELECT", body)

    def test_ip_bypass_allowlist_session_flag_allows_when_authenticated(self):
        # Personal ip_bypass_allowlist exception is an INHERIT-only
        # concept; give this staff member a real team explicitly
        # configured to INHERIT (never team_id=None -- per the Fix1
        # contract a staff session needs a real, valid team to resolve
        # ANY policy, including INHERIT).
        self.db.team_policies[1] = "INHERIT"
        self.db.cidrs = ["203.0.113.50/32"]
        start_auth_db_patch(self, user_id=7, auth_version=3)
        self._set_session(authenticated=True, user_id=7, auth_version=3,
                           username="staffX", is_admin=False, team_id=1,
                           ip_bypass_allowlist=True)
        resp = self._get(environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        self.assertEqual(resp.status_code, 200)

    def test_admin_session_ip_bypass_allowlist_flag_allows(self):
        # Admin (team_id=None BY DESIGN, not a missing-team error) can
        # still carry the personal ip_bypass_allowlist exception under
        # INHERIT.
        self.db.cidrs = ["203.0.113.50/32"]
        start_auth_db_patch(self, user_id=1, auth_version=1)
        self._set_session(authenticated=True, user_id=1, auth_version=1,
                           username="root_admin", is_admin=True, team_id=None,
                           ip_bypass_allowlist=True)
        resp = self._get(environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        self.assertEqual(resp.status_code, 200)


# --------------------------------------------------------------------------
# ALLOWLIST_ONLY (team-scoped, staff with team_id set)
# --------------------------------------------------------------------------

class AllowlistOnlyModeTests(_MiddlewareTestBase):
    def _staff_session(self, team_id=1, user_id=5, auth_version=1):
        start_auth_db_patch(self, user_id=user_id, auth_version=auth_version)
        self._set_session(authenticated=True, user_id=user_id, auth_version=auth_version,
                           username="staff", is_admin=False, team_id=team_id)

    def test_no_rules_configured_denies_403_never_allow(self):
        self.db.team_policies[1] = "ALLOWLIST_ONLY"
        self._staff_session(team_id=1)
        resp = self._get(environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        self.assertEqual(resp.status_code, 403)

    def test_matching_rule_allows(self):
        self.db.team_policies[1] = "ALLOWLIST_ONLY"
        self.db.cidrs = ["203.0.113.0/24"]
        self._staff_session(team_id=1)
        resp = self._get(environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
        self.assertEqual(resp.status_code, 200)

    def test_nonmatching_rule_denies_403(self):
        self.db.team_policies[1] = "ALLOWLIST_ONLY"
        self.db.cidrs = ["203.0.113.0/24"]
        self._staff_session(team_id=1)
        resp = self._get(environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        self.assertEqual(resp.status_code, 403)

    def test_personal_ip_bypass_does_not_apply_in_this_mode(self):
        # Contract: individual `ip_bypass_allowlist` exception is INHERIT-
        # only; ALLOWLIST_ONLY/ANY_AUTHENTICATED are explicit team modes
        # that override it.
        self.db.team_policies[1] = "ALLOWLIST_ONLY"
        self.db.cidrs = ["203.0.113.0/24"]
        start_auth_db_patch(self, user_id=5, auth_version=1)
        self._set_session(authenticated=True, user_id=5, auth_version=1,
                           username="staff", is_admin=False, team_id=1,
                           ip_bypass_allowlist=True)
        resp = self._get(environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        self.assertEqual(resp.status_code, 403)

    def test_cidr_read_error_denies_503(self):
        self.db.team_policies[1] = "ALLOWLIST_ONLY"
        self.db.cidr_error = _Boom("db down")
        self._staff_session(team_id=1)
        resp = self._get(environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
        self.assertEqual(resp.status_code, 503)


# --------------------------------------------------------------------------
# ANY_AUTHENTICATED
# --------------------------------------------------------------------------

class AnyAuthenticatedModeTests(_MiddlewareTestBase):
    def test_authenticated_staff_allowed_from_any_ip(self):
        self.db.team_policies[1] = "ANY_AUTHENTICATED"
        start_auth_db_patch(self, user_id=9, auth_version=1)
        self._set_session(authenticated=True, user_id=9, auth_version=1,
                           username="staff", is_admin=False, team_id=1)
        resp = self._get(environ_overrides={"REMOTE_ADDR": "1.2.3.4"})
        self.assertEqual(resp.status_code, 200)

    def test_forged_team_id_without_authenticated_flag_never_gets_this_policy(self):
        # A session carrying `team_id` (pointing at a real
        # ANY_AUTHENTICATED team) but WITHOUT `authenticated=True` (forged/
        # stale cookie) must resolve to plain INHERIT, never
        # ANY_AUTHENTICATED -- proven end-to-end: with INHERIT rules
        # configured and non-matching, it must be denied exactly like any
        # anonymous request, not silently let through.
        self.db.team_policies[1] = "ANY_AUTHENTICATED"
        self.db.cidrs = ["203.0.113.50/32"]
        self._set_session(team_id=1, is_admin=False, username="ghost")
        resp = self._get(environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        self.assertEqual(resp.status_code, 403)

    def test_resolve_effective_policy_unit_anonymous_with_team_id_is_inherit(self):
        # Direct unit check of the real decision function (not mocked
        # away) for the same property, without depending on the app's
        # other routing/session plumbing.
        with search.app.test_request_context("/__mw_test_probe__"):
            from flask import session as flask_session
            flask_session["team_id"] = 1
            flask_session["is_admin"] = False
            # authenticated intentionally NOT set
            self.assertEqual(middleware_access._resolve_effective_policy(), "INHERIT")


# --------------------------------------------------------------------------
# _load_team_ip_policy / _resolve_effective_policy failure modes (Fix1)
# --------------------------------------------------------------------------

class TeamPolicyUnavailableTests(_MiddlewareTestBase):
    def _staff_session(self, team_id, user_id=11, auth_version=1):
        start_auth_db_patch(self, user_id=user_id, auth_version=auth_version)
        self._set_session(authenticated=True, user_id=user_id, auth_version=auth_version,
                           username="staff", is_admin=False, team_id=team_id)

    def test_staff_missing_team_id_denies_503_not_broader_policy(self):
        self._staff_session(team_id=None)
        # Even with wide-open INHERIT rules, a staff session with no team
        # must be denied (503), never silently treated as INHERIT-allow.
        resp = self._get(environ_overrides={"REMOTE_ADDR": "1.2.3.4"})
        self.assertEqual(resp.status_code, 503)

    def test_team_deleted_denies_503_not_inherit(self):
        # team_id=99 has no entry in self.db.team_policies -> "not found".
        self._staff_session(team_id=99)
        resp = self._get(environ_overrides={"REMOTE_ADDR": "1.2.3.4"})
        self.assertEqual(resp.status_code, 503)

    def test_migration_015_missing_denies_503_not_inherit_fallback(self):
        # Fix1: the OLD behaviour silently fell back to "INHERIT" here.
        # Now it must deny with 503 -- simulate the column/table not
        # existing yet (schema014 present, schema015 absent) as a plain
        # read failure, indistinguishable in handling from any other DB
        # error.
        self.db.team_error = _Boom('column "ip_policy" of relation "teams" does not exist')
        self._staff_session(team_id=1)
        resp = self._get(environ_overrides={"REMOTE_ADDR": "1.2.3.4"})
        self.assertEqual(resp.status_code, 503)
        body = resp.get_data(as_text=True)
        self.assertNotIn("does not exist", body)
        self.assertNotIn("ip_policy", body)

    def test_invalid_policy_value_denies_503(self):
        self.db.team_policies[1] = "SOMETHING_BOGUS"
        self._staff_session(team_id=1)
        resp = self._get(environ_overrides={"REMOTE_ADDR": "1.2.3.4"})
        self.assertEqual(resp.status_code, 503)

    def test_valid_inherit_value_is_real_read_not_a_fallback(self):
        # A team genuinely configured with ip_policy='INHERIT' behaves
        # exactly like the anonymous/admin INHERIT path -- proves this is
        # a real read, not the removed exception-swallowing fallback.
        self.db.team_policies[1] = "INHERIT"
        self.db.cidrs = ["203.0.113.50/32"]
        self._staff_session(team_id=1)
        resp_ok = self._get(environ_overrides={"REMOTE_ADDR": "203.0.113.50"})
        self.assertEqual(resp_ok.status_code, 200)
        resp_denied = self._get(environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        self.assertEqual(resp_denied.status_code, 403)

    def test_admin_without_team_is_valid_inherit_not_unavailable(self):
        # Contrast case: admin has team_id=None BY DESIGN (system-wide
        # scope) -- this must stay a normal, valid INHERIT, not 503.
        start_auth_db_patch(self, user_id=1, auth_version=1)
        self._set_session(authenticated=True, user_id=1, auth_version=1,
                           username="root_admin", is_admin=True, team_id=None)
        self.db.cidrs = ["203.0.113.50/32"]
        resp = self._get(environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        self.assertEqual(resp.status_code, 403)  # valid policy read, IP just not allowed
        resp2 = self._get(environ_overrides={"REMOTE_ADDR": "203.0.113.50"})
        self.assertEqual(resp2.status_code, 200)


# --------------------------------------------------------------------------
# Session ordering: a revoked session must never reach IP-bypass logic
# --------------------------------------------------------------------------

class SessionOrderingTests(_MiddlewareTestBase):
    def test_revoked_session_with_stale_bypass_flag_is_blocked_before_ip_check(self):
        self.db.cidrs = ["203.0.113.50/32"]  # would otherwise deny 9.9.9.9
        # The cookie still carries ip_bypass_allowlist=True and
        # authenticated=True, but the authoritative DB says this
        # auth_version no longer matches (session revoked elsewhere) --
        # `session_security.enforce_session_validity` must reject this
        # BEFORE middleware_access ever looks at ip_bypass_allowlist.
        start_auth_db_patch(self, user_id=42, auth_version=5)  # DB truth: version 5
        self._set_session(authenticated=True, user_id=42, auth_version=1,  # stale cookie: version 1
                           username="staff", is_admin=False, team_id=None,
                           ip_bypass_allowlist=True)
        resp = self._get(environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers.get("Location", ""))
        # Proves middleware_access never even ran its DB read for this
        # request (session_security short-circuited first).
        self.assertEqual(self.db.queries, [])

    def test_suspended_account_with_stale_bypass_flag_is_blocked(self):
        self.db.cidrs = ["203.0.113.50/32"]
        start_auth_db_patch(self, user_id=42, auth_version=1, account_status="SUSPENDED")
        self._set_session(authenticated=True, user_id=42, auth_version=1,
                           username="staff", is_admin=False, team_id=None,
                           ip_bypass_allowlist=True)
        resp = self._get(environ_overrides={"REMOTE_ADDR": "9.9.9.9"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.db.queries, [])


# --------------------------------------------------------------------------
# Exempt endpoints: reachable when they must be, no substring over-reach
# --------------------------------------------------------------------------

class ExemptEndpointTests(_MiddlewareTestBase):
    def setUp(self):
        super().setUp()
        # Configure an INHERIT policy that WOULD deny a non-exempt route
        # from this client IP, so "reachable" below is a real assertion.
        self.db.cidrs = ["203.0.113.50/32"]
        self.remote = {"REMOTE_ADDR": "9.9.9.9"}

    def test_probe_route_denied_under_this_config(self):
        resp = self.client.get("/__mw_test_probe__", environ_overrides=self.remote)
        self.assertEqual(resp.status_code, 403)

    def test_login_get_reachable(self):
        resp = self.client.get("/login", environ_overrides=self.remote)
        self.assertNotIn(resp.status_code, (403, 503))

    def test_logout_post_reachable(self):
        resp = self.client.post("/logout", data={"csrf_token": "x"}, environ_overrides=self.remote)
        self.assertNotIn(resp.status_code, (403, 503))

    def test_google_login_entry_reachable(self):
        resp = self.client.get("/auth/google", environ_overrides=self.remote)
        self.assertNotIn(resp.status_code, (403, 503))

    def test_google_callback_reachable(self):
        resp = self.client.get("/auth/google/callback", environ_overrides=self.remote)
        self.assertNotIn(resp.status_code, (403, 503))

    def test_lookalike_google_path_is_not_exempted_by_substring(self):
        # A decoy route (registered at module import time, see top of
        # file) whose PATH resembles the Google OAuth paths but is a
        # DIFFERENT endpoint -- exemption is by endpoint identity only,
        # never a path substring match, so this must still be blocked.
        resp = self.client.get("/auth/google/not-the-real-callback", environ_overrides=self.remote)
        self.assertEqual(resp.status_code, 403)

    def test_trailing_slash_login_variant_does_not_over_exempt_other_paths(self):
        # Exact-path exemption for "/login" must not accidentally exempt
        # an unrelated path that happens to end with "/login" as a
        # substring of a longer segment (over-broad substring risk).
        resp = self.client.get("/admin/not-a-real-login", environ_overrides=self.remote)
        self.assertEqual(resp.status_code, 403)


# --------------------------------------------------------------------------
# ProxyFix / X-Forwarded-For trust model (app's real one-hop config)
# --------------------------------------------------------------------------

class ProxyTrustTests(_MiddlewareTestBase):
    """`search.py` applies `ProxyFix(app.wsgi_app, x_for=1, ...)` at import
    time -- these tests run against the REAL wrapped `search.app.wsgi_app`
    (via `search.app.test_client()`), so this exercises the actual
    deployed trust model, not a reimplementation.
    """

    def test_single_trusted_hop_value_is_honoured(self):
        # Simulates the real one-proxy deployment: Nginx sets exactly one
        # X-Forwarded-For value (the real client it saw). Must be trusted
        # directly.
        self.db.cidrs = ["203.0.113.50/32"]
        resp = self.client.get(
            "/__mw_test_probe__",
            headers={"X-Forwarded-For": "203.0.113.50"},
        )
        self.assertEqual(resp.status_code, 200)

    def test_client_cannot_self_grant_by_prepending_allowlisted_ip(self):
        # Attacker sends "<allowlisted>, <attacker-real-ip>" hoping the
        # leading value is trusted. With x_for=1, ProxyFix takes the LAST
        # value (what a real single Nginx hop would have appended as the
        # client it actually saw) -- the prepended value must be ignored.
        self.db.cidrs = ["203.0.113.50/32"]
        resp = self.client.get(
            "/__mw_test_probe__",
            headers={"X-Forwarded-For": "203.0.113.50, 9.9.9.9"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_no_xff_header_falls_back_to_direct_remote_addr(self):
        self.db.cidrs = ["203.0.113.50/32"]
        resp = self.client.get(
            "/__mw_test_probe__",
            environ_overrides={"REMOTE_ADDR": "203.0.113.50"},
        )
        self.assertEqual(resp.status_code, 200)


# --------------------------------------------------------------------------
# LOCAL vs GOOGLE on the identical team: equivalent outcomes
# --------------------------------------------------------------------------

class LocalGoogleEquivalenceTests(_MiddlewareTestBase):
    def test_same_team_same_ip_policy_outcome_regardless_of_provider(self):
        self.db.team_policies[1] = "ALLOWLIST_ONLY"
        self.db.cidrs = ["203.0.113.0/24"]

        start_auth_db_patch(self, user_id=101, auth_version=1)
        self._set_session(authenticated=True, user_id=101, auth_version=1,
                           username="local_staff", auth_provider="LOCAL",
                           is_admin=False, team_id=1)
        local_allowed = self._get(environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
        local_denied = self._get(environ_overrides={"REMOTE_ADDR": "9.9.9.9"})

        google_client = search.app.test_client()
        start_auth_db_patch(self, user_id=102, auth_version=1, users={102: ("ACTIVE", 1)})
        with google_client.session_transaction() as sess:
            sess.update(authenticated=True, user_id=102, auth_version=1,
                        username="g@standards.vn", auth_provider="GOOGLE",
                        is_admin=False, team_id=1)
        google_allowed = google_client.get("/__mw_test_probe__", environ_overrides={"REMOTE_ADDR": "203.0.113.9"})
        google_denied = google_client.get("/__mw_test_probe__", environ_overrides={"REMOTE_ADDR": "9.9.9.9"})

        self.assertEqual(local_allowed.status_code, google_allowed.status_code, 200)
        self.assertEqual(local_denied.status_code, google_denied.status_code, 403)
        self.assertEqual(local_allowed.status_code, 200)
        self.assertEqual(local_denied.status_code, 403)


if __name__ == "__main__":
    unittest.main()
