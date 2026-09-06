"""Shared fixture: give pre-Phase-5D2A test sessions a valid session shape.

`session_security.enforce_session_validity` (a Flask `before_request` hook,
see `session_security.py`) rejects any session with `authenticated=True`
unless it also carries `user_id` + `auth_version` matching an ACTIVE
`app_users` row (or ENABLE_LEGACY_PASSWORD_LOGIN=true, which is NOT set in
tests). Tests written before Phase 5D2A only set
`session["authenticated"] = True` (+ role/team flags), so every such
request is now rejected with 401/302 by the hook -- before it ever reaches
the view under test.

This module does NOT patch or weaken `enforce_session_validity` itself, and
NEVER touches a real Postgres database (including `products_local`). It
patches only `session_security.get_connection` -- the same technique already
used by `tests/test_session_security.py` -- with a tiny in-memory fake that
answers ONLY the two queries that module issues:

    SELECT account_status, auth_version FROM app_users WHERE id = %s
    INSERT INTO login_audit_events (...)

Any other SQL raises immediately, so a business-logic query accidentally
routed through this fake fails loudly instead of returning silent garbage.
Whatever fake/real DB a given test wires up for `search.get_connection()`
(business logic) is a completely separate module attribute and is untouched
by this helper.
"""
from unittest import mock

import session_security

DEFAULT_AUTH_VERSION = 1


class FakeAuthUserDB:
    """In-memory stand-in for the `app_users` columns session_security.py
    reads: `user_id -> (account_status, auth_version)`.

    `default`, when set, is returned for any `user_id` not explicitly
    listed -- convenience for call sites that only need "some valid ACTIVE
    user", not multi-identity behaviour (negative cases like suspended /
    version-mismatch / missing-identity accounts are already covered by
    `tests/test_session_security.py`; add explicit entries here instead of
    relying on `default` if a test needs to assert one of those).
    """

    def __init__(self, users=None, default=None):
        self.users = dict(users or {})
        self.default = default
        self.audits = []

    def lookup(self, user_id):
        if user_id in self.users:
            return self.users[user_id]
        return self.default


class _FakeAuthCursor:
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
            row = self.db.lookup(user_id)
            self._result = [row] if row is not None else []
        elif "INSERT INTO login_audit_events" in s:
            self.db.audits.append(params)
            self._result = []
        else:
            raise AssertionError(f"Unexpected SQL against fake auth DB: {s}")

    def fetchone(self):
        return self._result[0] if self._result else None


class _FakeAuthConnection:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _FakeAuthCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _build_db(user_id, auth_version, account_status, users, permissive):
    seed = dict(users or {})
    seed.setdefault(user_id, (account_status, auth_version))
    default = (account_status, auth_version) if permissive else None
    return FakeAuthUserDB(seed, default=default)


def start_auth_db_patch(
    testcase,
    *,
    user_id=1,
    auth_version=DEFAULT_AUTH_VERSION,
    account_status="ACTIVE",
    users=None,
    permissive=True,
):
    """Patch `session_security.get_connection` for the lifetime of
    `testcase` (auto-stopped via `addCleanup`), so the per-request
    session-liveness check finds an ACTIVE account at `auth_version`
    without touching any real database. Returns the `FakeAuthUserDB`
    (inspect `.audits`, or add more identities via `db.users[id] = (status,
    version)` before making a request).
    """
    db = _build_db(user_id, auth_version, account_status, users, permissive)
    patcher = mock.patch.object(session_security, "get_connection", lambda: _FakeAuthConnection(db))
    patcher.start()
    testcase.addCleanup(patcher.stop)
    return db


def auth_db_patch(
    *,
    user_id=1,
    auth_version=DEFAULT_AUTH_VERSION,
    account_status="ACTIVE",
    users=None,
    permissive=True,
):
    """Context-manager form of `start_auth_db_patch`, for a single request /
    `with` block instead of a whole test method."""
    db = _build_db(user_id, auth_version, account_status, users, permissive)
    return mock.patch.object(session_security, "get_connection", lambda: _FakeAuthConnection(db))


def set_authenticated_session(sess, *, user_id=1, auth_version=DEFAULT_AUTH_VERSION, **extra):
    """Populate a Flask test-client session dict (from
    `client.session_transaction()`) with the Phase 5D2A-valid shape --
    `authenticated`, `user_id`, `auth_version` -- plus whatever legacy
    fields (`is_admin`, `team_id`, `role`, `username`, ...) the view under
    test still reads.
    """
    sess["authenticated"] = True
    sess["user_id"] = user_id
    sess["auth_version"] = auth_version
    sess.update(extra)
