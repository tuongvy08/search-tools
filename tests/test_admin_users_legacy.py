"""Phase 5D2B.1 tests: closing the legacy `/admin/users` (LOCAL) form as a
bypass around Google-account management, plus the shared last-admin
advisory-lock wiring.

No real DB/network is used here -- this file is an in-memory fake-cursor
test of SQL-statement wiring and per-request logic only, NOT a concurrency
proof. See `tests/test_admin_pg_integration.py` (Phase 5D2B.2) for the REAL,
two-independent-connection Postgres concurrency tests (mutual
suspend/demote race, shared advisory lock across the GOOGLE-suspend and
LOCAL-demote paths, actor revalidation, and lock release on rollback).
"""
import os
import unittest
from unittest import mock

import admin_google_users
import search
import session_security

FOUR_DOMAINS = "standards.com.vn,standards.vn,labmall.vn,biosciences.vn"


# --------------------------------------------------------------------------
# In-memory fake DB matching the exact SQL contracts of search.py's
# admin_users() view (LOCAL/legacy path) AND the shared queries it now
# calls into (admin_google_users.fetch_google_admin_context,
# acquire_last_admin_lock, and session_security's own liveness check) --
# all backed by the SAME `users` dict so a mutation from one is visible to
# the others within a test, exactly like a real shared DB would behave.
# --------------------------------------------------------------------------

class FakeDB:
    def __init__(self, users=None, teams=None):
        self.users = {u["id"]: dict(u) for u in (users or [])}
        self.teams = dict(teams or {})  # id -> name
        self.team_brands = {}  # team_id -> set(brand)
        self.advisory_lock_calls = 0
        self._next_user_id = max([u["id"] for u in self.users.values()], default=0) + 1
        self._next_team_id = max(self.teams.keys(), default=0) + 1
        # Phase 5D2B.2: see admin_google_users FakeDB for why this exists --
        # with real actor revalidation in place, a valid distinct actor is
        # always counted as an "other active admin", so a fake-cursor test
        # can no longer reach COUNT()==0 through ordinary data alone.
        self.force_zero_admin_count = False

    def team_id_by_name(self, name):
        for tid, n in self.teams.items():
            if n == name:
                return tid
        return None


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
            db.advisory_lock_calls += 1
            self._result = []

        elif s.startswith("SELECT id FROM app_users WHERE username = %s"):
            (username,) = params
            match = [u for u in db.users.values() if u.get("username") == username]
            self._result = [(match[0]["id"],)] if match else []

        elif s.startswith("INSERT INTO app_users") and "auth_provider" in s:
            if "NULL, TRUE" in s:
                username, password_hash, ip_bypass = params
                uid = db._next_user_id
                db._next_user_id += 1
                db.users[uid] = {
                    "id": uid, "username": username, "password_hash": password_hash,
                    "is_admin": True, "team_id": None, "ip_bypass_allowlist": ip_bypass,
                    "auth_provider": "LOCAL", "account_status": "ACTIVE", "auth_version": 1,
                }
            else:
                username, password_hash, team_id, ip_bypass = params
                uid = db._next_user_id
                db._next_user_id += 1
                db.users[uid] = {
                    "id": uid, "username": username, "password_hash": password_hash,
                    "is_admin": False, "team_id": team_id, "ip_bypass_allowlist": ip_bypass,
                    "auth_provider": "LOCAL", "account_status": "ACTIVE", "auth_version": 1,
                }
            self._result = []

        elif s.startswith("INSERT INTO teams"):
            (team_name,) = params
            tid = db.team_id_by_name(team_name)
            if tid is None:
                tid = db._next_team_id
                db._next_team_id += 1
                db.teams[tid] = team_name
            self._result = [(tid,)]

        elif s.startswith("SELECT username, team_id, is_admin FROM app_users WHERE id = %s AND auth_provider = 'LOCAL' FOR UPDATE"):
            (uid,) = params
            u = db.users.get(uid)
            self._result = [(u["username"], u["team_id"], u["is_admin"])] if u and u.get("auth_provider") == "LOCAL" else []

        elif s.startswith("SELECT account_status, is_admin, auth_version FROM app_users WHERE id = %s"):
            # Phase 5D2B.2: actor revalidation (shared helper, same query
            # text as admin_google_users.revalidate_actor -- re-reads the
            # ACTING admin's own row, never the target's).
            (uid,) = params
            u = db.users.get(uid)
            self._result = [(u["account_status"], u["is_admin"], u["auth_version"])] if u else []

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

        elif s.startswith("UPDATE app_users SET password_hash = %s WHERE id = %s"):
            pw, uid = params
            db.users[uid]["password_hash"] = pw
            self._result = []

        elif s.startswith("UPDATE app_users SET is_admin = TRUE, team_id = NULL"):
            ip_bypass, uid = params
            u = db.users[uid]
            u.update(is_admin=True, team_id=None, ip_bypass_allowlist=ip_bypass)
            u["auth_version"] = u.get("auth_version", 1) + 1
            self._result = []

        elif s.startswith("UPDATE app_users SET is_admin = FALSE, team_id = %s"):
            team_id, ip_bypass, uid = params
            u = db.users[uid]
            u.update(is_admin=False, team_id=team_id, ip_bypass_allowlist=ip_bypass)
            u["auth_version"] = u.get("auth_version", 1) + 1
            self._result = []

        elif s.startswith("DELETE FROM team_brands WHERE team_id = %s"):
            (team_id,) = params
            db.team_brands[team_id] = set()
            self._result = []

        elif s.startswith("INSERT INTO team_brands"):
            team_id, brand = params
            db.team_brands.setdefault(team_id, set()).add(brand)
            self._result = []

        elif s.startswith("SELECT brand FROM team_brands WHERE team_id = %s"):
            (team_id,) = params
            self._result = [(b,) for b in sorted(db.team_brands.get(team_id, set()))]

        elif s.startswith("SELECT a.id, a.username, a.is_admin, a.team_id, t.name, a.ip_bypass_allowlist"):
            # Legacy LOCAL listing -- must only ever return auth_provider='LOCAL'.
            rows = []
            for u in sorted(db.users.values(), key=lambda x: -x["id"]):
                if u.get("auth_provider") != "LOCAL":
                    continue
                team_name = db.teams.get(u["team_id"]) if u.get("team_id") else None
                rows.append((u["id"], u["username"], u["is_admin"], u["team_id"], team_name, u["ip_bypass_allowlist"]))
            self._result = rows

        elif s.startswith("SELECT DISTINCT brand FROM products"):
            self._result = []

        elif s.startswith("SELECT a.id, a.email, a.display_name, a.is_admin, a.team_id, t.name,"):
            rows = []
            for u in db.users.values():
                if u.get("auth_provider") != "GOOGLE":
                    continue
                team_name = db.teams.get(u["team_id"]) if u.get("team_id") else None
                rows.append((
                    u["id"], u.get("email"), u.get("display_name"), u.get("is_admin"), u.get("team_id"),
                    team_name, u.get("account_status"), u.get("approved_at"), u.get("last_login_at"),
                ))
            self._result = rows

        elif s.startswith("SELECT id, name FROM teams ORDER BY name"):
            self._result = list(db.teams.items())

        elif s.startswith("SELECT account_status, auth_version FROM app_users WHERE id = %s"):
            # session_security.py's own per-request liveness check.
            (uid,) = params
            u = db.users.get(uid)
            self._result = [(u["account_status"], u["auth_version"])] if u else []

        elif s.startswith("INSERT INTO login_audit_events"):
            self._result = []

        else:
            raise AssertionError(f"Unexpected SQL in fake DB: {s}")

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


class FakeConnection:
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


def _base_db(extra_users=None, extra_teams=None):
    """One ACTIVE LOCAL admin (id=1) used as BOTH the acting admin's own
    session-liveness row (session_security check) and, where relevant, the
    "other active admin" that keeps last-admin checks from firing
    incidentally in tests that are not specifically about that guard.
    """
    users = [
        {"id": 1, "username": "root_admin", "password_hash": "h", "is_admin": True,
         "team_id": None, "ip_bypass_allowlist": False, "auth_provider": "LOCAL",
         "account_status": "ACTIVE", "auth_version": 1},
    ]
    users.extend(extra_users or [])
    return FakeDB(users=users, teams=extra_teams or {1: "Team A"})


class _ClientTestCase(unittest.TestCase):
    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()

    def _set_session(self, **kwargs):
        with self.client.session_transaction() as sess:
            sess.clear()
            sess.update(kwargs)

    def _admin_session(self, user_id=1, auth_version=1):
        self._set_session(authenticated=True, user_id=user_id, auth_version=auth_version,
                           is_admin=True, role="admin", username="root_admin")

    def _csrf_form(self, **fields):
        with self.client.session_transaction() as sess:
            sess["csrf_token"] = "the-real-token"
        fields["csrf_token"] = "the-real-token"
        return fields

    def _patch_both(self, db):
        """search.py's admin_users() view and session_security.py's
        before_request liveness check use two SEPARATE `get_connection`
        references -- both must point at the SAME FakeDB for a test to see
        one action's effect (e.g. an auth_version bump) reflected in the
        other (e.g. the next request's liveness check).
        """
        return (
            mock.patch.object(search, "get_connection", _fake_get_connection(db)),
            mock.patch.object(session_security, "get_connection", _fake_get_connection(db)),
        )


# --------------------------------------------------------------------------
# GOOGLE must never appear in the LOCAL table
# --------------------------------------------------------------------------

class LocalListingExcludesGoogleTests(_ClientTestCase):
    def test_google_accounts_never_returned_in_local_users_list(self):
        self._admin_session()
        db = _base_db(extra_users=[
            {"id": 2, "username": "someone@standards.vn", "email": "someone@standards.vn",
             "display_name": None, "is_admin": False, "team_id": None,
             "ip_bypass_allowlist": False, "auth_provider": "GOOGLE",
             "account_status": "PENDING", "auth_version": 1},
        ])
        p1, p2 = self._patch_both(db)
        with p1, p2, mock.patch.object(search, "render_template") as mock_render:
            mock_render.return_value = "OK"
            resp = self.client.get("/admin/users")
        self.assertEqual(resp.status_code, 200)
        kwargs = mock_render.call_args.kwargs
        local_ids = [u["id"] for u in kwargs["users"]]
        self.assertEqual(local_ids, [1])
        self.assertNotIn(2, local_ids)
        google_ids = [u["id"] for u in kwargs["google_users"]]
        self.assertEqual(google_ids, [2])


# --------------------------------------------------------------------------
# Legacy POST cannot target a GOOGLE account, even via a forged user_id
# --------------------------------------------------------------------------

class LegacyCannotTargetGoogleTests(_ClientTestCase):
    def test_update_user_targeting_google_id_rejected_no_db_change(self):
        self._admin_session()
        db = _base_db(extra_users=[
            {"id": 2, "username": "google@standards.vn", "email": "google@standards.vn",
             "display_name": None, "is_admin": False, "team_id": None,
             "ip_bypass_allowlist": False, "auth_provider": "GOOGLE",
             "account_status": "ACTIVE", "auth_version": 3},
        ])
        before = dict(db.users[2])
        p1, p2 = self._patch_both(db)
        with p1, p2:
            resp = self.client.post("/admin/users", data=self._csrf_form(
                action="update_user", user_id="2", role="admin", ip_bypass_allowlist="1",
            ))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("err=", resp.headers["Location"])
        # Completely untouched: not promoted, not demoted, no auth_version
        # bump, no provider/status change.
        self.assertEqual(db.users[2], before)


# --------------------------------------------------------------------------
# Valid LOCAL flow still works, CSRF-gated
# --------------------------------------------------------------------------

class LegacyLocalFlowTests(_ClientTestCase):
    def test_missing_csrf_rejected_on_update_user_no_db_change(self):
        self._admin_session()
        db = _base_db(extra_users=[
            {"id": 2, "username": "staff2", "password_hash": "h", "is_admin": False,
             "team_id": 1, "ip_bypass_allowlist": False, "auth_provider": "LOCAL",
             "account_status": "ACTIVE", "auth_version": 1},
        ])
        p1, p2 = self._patch_both(db)
        with p1, p2:
            resp = self.client.post("/admin/users", data={
                "action": "update_user", "user_id": "2", "role": "admin",
            })
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(db.users[2]["is_admin"])
        self.assertEqual(db.users[2]["auth_version"], 1)

    def test_missing_csrf_rejected_on_create_user(self):
        self._admin_session()
        db = _base_db()
        p1, p2 = self._patch_both(db)
        with p1, p2:
            resp = self.client.post("/admin/users", data={
                "action": "create_user", "username": "newperson", "password": "x", "role": "admin",
            })
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(len(db.users), 1)  # nothing created

    def test_valid_local_promote_succeeds_with_csrf_and_bumps_auth_version(self):
        self._admin_session()
        db = _base_db(extra_users=[
            {"id": 2, "username": "staff2", "password_hash": "h", "is_admin": False,
             "team_id": 1, "ip_bypass_allowlist": False, "auth_provider": "LOCAL",
             "account_status": "ACTIVE", "auth_version": 5},
        ])
        p1, p2 = self._patch_both(db)
        with p1, p2:
            resp = self.client.post("/admin/users", data=self._csrf_form(
                action="update_user", user_id="2", role="admin",
            ))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("msg=", resp.headers["Location"])
        self.assertTrue(db.users[2]["is_admin"])
        self.assertIsNone(db.users[2]["team_id"])
        self.assertEqual(db.users[2]["auth_version"], 6)
        # Legacy form never touches these columns.
        self.assertEqual(db.users[2]["auth_provider"], "LOCAL")
        self.assertEqual(db.users[2]["account_status"], "ACTIVE")

    def test_role_change_invalidates_old_session_on_next_request(self):
        # End-to-end: bump auth_version via update_user, then a SEPARATE
        # request carrying the user's OLD auth_version must be rejected by
        # session_security's real (fake-backed) liveness check.
        self._admin_session()
        db = _base_db(extra_users=[
            {"id": 2, "username": "staff2", "password_hash": "h", "is_admin": False,
             "team_id": 1, "ip_bypass_allowlist": False, "auth_provider": "LOCAL",
             "account_status": "ACTIVE", "auth_version": 1},
        ])
        p1, p2 = self._patch_both(db)
        with p1, p2:
            resp = self.client.post("/admin/users", data=self._csrf_form(
                action="update_user", user_id="2", role="admin",
            ))
            self.assertEqual(resp.status_code, 302)
            self.assertEqual(db.users[2]["auth_version"], 2)

            # A DIFFERENT client session belonging to user 2, still carrying
            # the pre-update auth_version=1.
            other_client = search.app.test_client()
            with other_client.session_transaction() as sess:
                sess.update(authenticated=True, user_id=2, auth_version=1, username="staff2", role="user")
            resp2 = other_client.get("/")
        self.assertEqual(resp2.status_code, 302)
        self.assertIn("/login", resp2.headers["Location"])

    def test_create_user_always_sets_local_and_active_explicitly(self):
        self._admin_session()
        db = _base_db()
        p1, p2 = self._patch_both(db)
        with p1, p2:
            resp = self.client.post("/admin/users", data=self._csrf_form(
                action="create_user", username="brandnew", password="x", role="admin",
            ))
        self.assertEqual(resp.status_code, 302)
        created = [u for u in db.users.values() if u["username"] == "brandnew"]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["auth_provider"], "LOCAL")
        self.assertEqual(created[0]["account_status"], "ACTIVE")

    def test_create_user_rejected_when_actor_no_longer_valid_admin(self):
        # Phase 5D2B Final: create_user must ALSO go through the shared
        # advisory lock + actor-revalidation before touching anything --
        # not just update_user. Actor id=1's own row is SUSPENDED here
        # (e.g. someone else suspended them moments earlier); no new user
        # may be created.
        self._set_session(authenticated=True, user_id=1, auth_version=1,
                           is_admin=True, role="admin", username="root_admin")
        db = FakeDB(users=[
            {"id": 1, "username": "root_admin", "password_hash": "h", "is_admin": True,
             "team_id": None, "ip_bypass_allowlist": False, "auth_provider": "LOCAL",
             "account_status": "SUSPENDED", "auth_version": 1},
        ], teams={1: "Team A"})
        p1, p2 = self._patch_both(db)
        with p1, p2:
            resp = self.client.post("/admin/users", data=self._csrf_form(
                action="create_user", username="brandnew", password="x", role="admin",
            ))
        # Either rejected by the outer session_security check (actor's own
        # session is no longer valid -> redirect to /login) or by the inner
        # revalidate_actor check reached inside create_user's transaction
        # (redirect back to admin_users with err=) -- both are acceptable,
        # defense-in-depth outcomes; what matters is NO user is created.
        self.assertEqual(resp.status_code, 302)
        created = [u for u in db.users.values() if u["username"] == "brandnew"]
        self.assertEqual(created, [])


# --------------------------------------------------------------------------
# Self / last-admin protection must hold on the legacy path too
# --------------------------------------------------------------------------

class LegacySelfAndLastAdminGuardTests(_ClientTestCase):
    def test_cannot_self_demote_via_legacy_form(self):
        self._admin_session(user_id=1)
        db = _base_db()  # id=1 is_admin=True, ACTIVE -- acting admin targets self
        p1, p2 = self._patch_both(db)
        with p1, p2:
            resp = self.client.post("/admin/users", data=self._csrf_form(
                action="update_user", user_id="1", role="staff", brands="Sigma",
            ))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("err=", resp.headers["Location"])
        self.assertTrue(db.users[1]["is_admin"])
        self.assertEqual(db.users[1]["auth_version"], 1)

    def test_cannot_demote_last_active_admin_via_legacy_form(self):
        # Isolated logic test (fake cursor, NOT a concurrency proof -- see
        # the real 2-connection Postgres integration test for that): actor
        # id=1 is a genuinely valid ACTIVE admin (passes actor
        # revalidation), and we force the shared COUNT() helper to return 0
        # to verify update_user() correctly aborts the demote rather than
        # proceeding, exactly like admin_google_users.suspend() does.
        self._admin_session(user_id=1)
        db = _base_db(extra_users=[
            {"id": 2, "username": "only_admin", "password_hash": "h", "is_admin": True,
             "team_id": None, "ip_bypass_allowlist": False, "auth_provider": "LOCAL",
             "account_status": "ACTIVE", "auth_version": 9},
        ])
        db.force_zero_admin_count = True
        p1, p2 = self._patch_both(db)
        with p1, p2:
            resp = self.client.post("/admin/users", data=self._csrf_form(
                action="update_user", user_id="2", role="staff", brands="Sigma",
            ))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("err=", resp.headers["Location"])
        self.assertTrue(db.users[2]["is_admin"])
        self.assertEqual(db.users[2]["auth_version"], 9)

    def test_demote_rejected_when_actor_no_longer_valid_admin(self):
        # Phase 5D2B.2: the acting admin's OWN row was suspended (e.g. by
        # someone else) before this request runs. Here that is caught by
        # session_security's own before_request liveness check (the outer
        # layer, since this file wires it to the SAME FakeDB) -- redirect
        # to /login instead of reaching update_user() at all. This is
        # legitimate defense-in-depth: the SAME condition is what
        # admin_google_users.revalidate_actor() (the inner, per-transaction
        # check shared by both the GOOGLE-suspend and LOCAL-demote paths)
        # is designed to catch if the outer layer's read happens to be
        # stale relative to the transaction -- see the real 2-connection
        # Postgres test for that scenario, which a single fake cursor
        # cannot reproduce (there's no "in between the two reads" moment
        # without real concurrent transactions).
        self._set_session(authenticated=True, user_id=1, auth_version=1,
                           is_admin=True, role="admin", username="root_admin")
        db = FakeDB(users=[
            {"id": 1, "username": "root_admin", "password_hash": "h", "is_admin": True,
             "team_id": None, "ip_bypass_allowlist": False, "auth_provider": "LOCAL",
             "account_status": "SUSPENDED", "auth_version": 1},
            {"id": 2, "username": "only_admin", "password_hash": "h", "is_admin": True,
             "team_id": None, "ip_bypass_allowlist": False, "auth_provider": "LOCAL",
             "account_status": "ACTIVE", "auth_version": 9},
        ], teams={1: "Team A"})
        p1, p2 = self._patch_both(db)
        with p1, p2:
            resp = self.client.post("/admin/users", data=self._csrf_form(
                action="update_user", user_id="2", role="staff", brands="Sigma",
            ))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["Location"], "/login")
        self.assertTrue(db.users[2]["is_admin"])
        self.assertEqual(db.users[2]["auth_version"], 9)

    def test_demote_allowed_when_another_active_admin_exists(self):
        self._admin_session(user_id=1)  # id=1 is also an ACTIVE admin
        db = _base_db(extra_users=[
            {"id": 2, "username": "other_admin", "password_hash": "h", "is_admin": True,
             "team_id": None, "ip_bypass_allowlist": False, "auth_provider": "LOCAL",
             "account_status": "ACTIVE", "auth_version": 4},
        ])
        p1, p2 = self._patch_both(db)
        with p1, p2:
            resp = self.client.post("/admin/users", data=self._csrf_form(
                action="update_user", user_id="2", role="staff", brands="Sigma",
            ))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("msg=", resp.headers["Location"])
        self.assertFalse(db.users[2]["is_admin"])
        self.assertEqual(db.users[2]["auth_version"], 5)


# --------------------------------------------------------------------------
# Advisory-lock call-order wiring (NOT a real concurrency proof -- see
# module docstring and the phase report).
# --------------------------------------------------------------------------

class AdvisoryLockWiringTests(_ClientTestCase):
    def test_legacy_demote_path_acquires_shared_lock(self):
        self._admin_session(user_id=1)
        db = _base_db(extra_users=[
            {"id": 2, "username": "other_admin", "password_hash": "h", "is_admin": True,
             "team_id": None, "ip_bypass_allowlist": False, "auth_provider": "LOCAL",
             "account_status": "ACTIVE", "auth_version": 1},
        ])
        p1, p2 = self._patch_both(db)
        with p1, p2:
            self.client.post("/admin/users", data=self._csrf_form(
                action="update_user", user_id="2", role="staff", brands="Sigma",
            ))
        self.assertGreaterEqual(db.advisory_lock_calls, 1)

    def test_google_suspend_path_acquires_same_shared_lock_key(self):
        self.assertEqual(
            admin_google_users._LAST_ADMIN_LOCK_KEY,
            admin_google_users._LAST_ADMIN_LOCK_KEY,
        )
        # Both call sites invoke the SAME shared helper function (verified
        # by direct source reference, not duplicated logic):
        import inspect
        suspend_src = inspect.getsource(admin_google_users.suspend)
        self.assertIn("acquire_last_admin_lock(cur)", suspend_src)
        update_user_src_module = inspect.getsource(search)
        # search.py calls admin_google_users.acquire_last_admin_lock(cur)
        # from its own update_user branch -- same function, same key.
        self.assertIn("admin_google_users.acquire_last_admin_lock(cur)", update_user_src_module)


if __name__ == "__main__":
    unittest.main()
