"""Phase 5D1 tests: Google Workspace OIDC foundation.

No real Google network calls are made. The Authlib client (`auth_google.oauth`)
is always mocked, and the database is a small in-memory fake that mimics the
exact SQL statements `auth_google.py` issues (migration 014 is not executed
against any real database as part of these tests).
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from psycopg2 import IntegrityError

import auth_google
import search

REPO_ROOT = Path(__file__).resolve().parent.parent

FOUR_DOMAINS = "standards.com.vn,standards.vn,labmall.vn,biosciences.vn"
VALID_ENV = {
    "GOOGLE_AUTH_ENABLED": "true",
    "GOOGLE_OAUTH_CLIENT_ID": "fake-client-id.apps.googleusercontent.com",
    "GOOGLE_OAUTH_CLIENT_SECRET": "fake-client-secret-not-real",
    "GOOGLE_OAUTH_REDIRECT_URI": "https://example.invalid/auth/google/callback",
    "GOOGLE_WORKSPACE_ALLOWED_DOMAINS": FOUR_DOMAINS,
}


# --------------------------------------------------------------------------
# In-memory fake DB matching the exact SQL contract in auth_google.py
# --------------------------------------------------------------------------

class FakeDB:
    def __init__(self, users=None):
        self.users = {u["id"]: dict(u) for u in (users or [])}
        self.audits = []
        self._next_id = max([u["id"] for u in self.users.values()], default=0) + 1

    def _row(self, u):
        # auth_version is appended at the END so every existing positional
        # assertion in this file (indices 0-5) keeps working unchanged.
        return (u["id"], u["username"], u["account_status"], u["is_admin"], u["team_id"],
                u["ip_bypass_allowlist"], u.get("auth_version", 1))

    def find_by_sub(self, sub):
        for u in self.users.values():
            if u.get("google_sub") == sub:
                return self._row(u)
        return None

    def find_by_email(self, email):
        for u in self.users.values():
            if (u.get("email") or "").lower() == email:
                return self._row(u)
        return None

    def find_google_by_sub_or_email(self, sub, email):
        for u in self.users.values():
            if u.get("auth_provider") != "GOOGLE":
                continue
            if u.get("google_sub") == sub or (u.get("email") or "").lower() == email:
                return self._row(u)
        return None

    def provider_of_email(self, email):
        for u in self.users.values():
            if (u.get("email") or "").lower() == email:
                return u.get("auth_provider")
        return None

    def find_invited(self, email):
        for u in self.users.values():
            if (u.get("auth_provider") == "GOOGLE" and u.get("account_status") == "INVITED"
                    and u.get("google_sub") is None and (u.get("email") or "").lower() == email):
                return u
        return None

    def bind_invite(self, user_id, sub, display_name):
        u = self.users[user_id]
        u["google_sub"] = sub
        u["account_status"] = "ACTIVE"
        if not u.get("display_name"):
            u["display_name"] = display_name
        return self._row(u)

    def insert_pending(self, username, sub, email, display_name):
        for u in self.users.values():
            if u.get("username") == username or u.get("google_sub") == sub or (u.get("email") or "").lower() == email:
                raise IntegrityError("unique_violation")
        uid = self._next_id
        self._next_id += 1
        u = {
            "id": uid, "username": username, "password_hash": None,
            "auth_provider": "GOOGLE", "google_sub": sub, "email": email,
            "display_name": display_name, "account_status": "PENDING",
            "is_admin": False, "team_id": None, "ip_bypass_allowlist": False,
        }
        self.users[uid] = u
        return self._row(u)

    def touch_login(self, user_id):
        self.users[user_id]["last_login_at"] = "NOW"


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
        if "INSERT INTO login_audit_events" in s:
            self.db.audits.append(params)
            self._result = []
        elif "INSERT INTO app_users" in s:
            username, sub, email, display_name = params
            row = self.db.insert_pending(username, sub, email, display_name)
            self._result = [row]
        elif "UPDATE app_users SET google_sub" in s:
            sub, display_name, user_id = params
            self._result = [self.db.bind_invite(user_id, sub, display_name)]
        elif "UPDATE app_users SET last_login_at" in s:
            (user_id,) = params
            self.db.touch_login(user_id)
            self._result = []
        elif "SELECT id FROM app_users" in s and "INVITED" in s:
            (email,) = params
            row = self.db.find_invited(email)
            self._result = [(row["id"],)] if row else []
        elif "SELECT auth_provider FROM app_users WHERE lower(email) = %s" in s:
            (email,) = params
            provider = self.db.provider_of_email(email)
            self._result = [(provider,)] if provider is not None else []
        elif "OR lower(email) = %s" in s:
            sub, email = params
            row = self.db.find_google_by_sub_or_email(sub, email)
            self._result = [row] if row else []
        elif "WHERE google_sub = %s" in s:
            (sub,) = params
            row = self.db.find_by_sub(sub)
            self._result = [row] if row else []
        else:
            raise AssertionError(f"Unexpected SQL in fake DB: {s}")

    def fetchone(self):
        return self._result[0] if self._result else None


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _fake_get_connection(db):
    return lambda: FakeConnection(db)


def _fake_oauth(claims, token=None):
    m = mock.MagicMock()
    m.google.authorize_access_token.return_value = token or {"id_token": "unused-in-tests"}
    m.google.parse_id_token.return_value = claims
    return m


# --------------------------------------------------------------------------
# Config / env contract
# --------------------------------------------------------------------------

class GoogleAuthConfigTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GOOGLE_AUTH_ENABLED", None)
            self.assertFalse(auth_google.google_auth_enabled())

    def test_allowed_domains_exact_four(self):
        domains = auth_google.parse_allowed_domains(FOUR_DOMAINS)
        self.assertEqual(domains, {"standards.com.vn", "standards.vn", "labmall.vn", "biosciences.vn"})

    def test_allowed_domains_lowercase_trim_and_reject_invalid(self):
        raw = "  STANDARDS.COM.VN , , not a domain, evil.com , labmall.vn"
        domains = auth_google.parse_allowed_domains(raw)
        self.assertIn("standards.com.vn", domains)
        self.assertIn("labmall.vn", domains)
        self.assertNotIn("not a domain", domains)
        self.assertNotIn("", domains)
        self.assertIn("evil.com", domains)  # syntactically valid domain, just not in the real allowlist var

    def test_enabled_missing_config_fails_closed_without_leaking_secret(self):
        with mock.patch.dict(os.environ, {"GOOGLE_AUTH_ENABLED": "true"}, clear=False):
            for key in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
                        "GOOGLE_OAUTH_REDIRECT_URI", "GOOGLE_WORKSPACE_ALLOWED_DOMAINS"):
                os.environ.pop(key, None)
            with self.assertRaises(auth_google.GoogleAuthConfigError) as ctx:
                auth_google.get_google_config()
            msg = str(ctx.exception)
            self.assertIn("GOOGLE_OAUTH_CLIENT_ID", msg)
            self.assertNotIn("fake-client-secret", msg)

    def test_enabled_with_full_config_succeeds(self):
        with mock.patch.dict(os.environ, VALID_ENV, clear=False):
            cfg = auth_google.get_google_config()
            self.assertEqual(cfg["allowed_domains"], {"standards.com.vn", "standards.vn", "labmall.vn", "biosciences.vn"})

    def test_init_app_noop_when_disabled(self):
        from flask import Flask
        app = Flask("disabled-test-app")
        with mock.patch.dict(os.environ, {"GOOGLE_AUTH_ENABLED": "false"}, clear=False):
            auth_google.init_app(app)  # must not raise, must not touch network

    def test_init_app_fails_closed_when_enabled_and_misconfigured(self):
        from flask import Flask
        app = Flask("misconfigured-test-app")
        with mock.patch.dict(os.environ, {"GOOGLE_AUTH_ENABLED": "true"}, clear=False):
            os.environ.pop("GOOGLE_OAUTH_CLIENT_ID", None)
            with self.assertRaises(auth_google.GoogleAuthConfigError):
                auth_google.init_app(app)


# --------------------------------------------------------------------------
# Route-level: disabled => 404, no network/config needed
# --------------------------------------------------------------------------

class DisabledRouteTests(unittest.TestCase):
    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()

    def test_google_login_route_404_when_disabled(self):
        with mock.patch.dict(os.environ, {"GOOGLE_AUTH_ENABLED": "false"}, clear=False):
            resp = self.client.get("/auth/google")
            self.assertEqual(resp.status_code, 404)

    def test_google_callback_route_404_when_disabled(self):
        with mock.patch.dict(os.environ, {"GOOGLE_AUTH_ENABLED": "false"}, clear=False):
            resp = self.client.get("/auth/google/callback")
            self.assertEqual(resp.status_code, 404)

    def test_login_page_hides_google_button_when_disabled(self):
        with mock.patch.dict(os.environ, {"GOOGLE_AUTH_ENABLED": "false"}, clear=False):
            resp = self.client.get("/login")
            self.assertEqual(resp.status_code, 200)
            self.assertNotIn(b"auth/google", resp.data)

    def test_login_page_shows_google_button_when_enabled(self):
        with mock.patch.dict(os.environ, {"GOOGLE_AUTH_ENABLED": "true"}, clear=False):
            resp = self.client.get("/login")
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b"/auth/google", resp.data)


# --------------------------------------------------------------------------
# Callback: claim validation + account lifecycle (mocked Authlib + fake DB)
# --------------------------------------------------------------------------

class CallbackLifecycleTests(unittest.TestCase):
    def setUp(self):
        search.app.testing = True
        self.client = search.app.test_client()
        self.env_patch = mock.patch.dict(os.environ, VALID_ENV, clear=False)
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    def _set_nonce(self, pre_session=None):
        with self.client.session_transaction() as sess:
            if pre_session:
                sess.update(pre_session)
            sess["google_oauth_nonce"] = "test-nonce"

    def _get(self, claims, db, pre_session=None, skip_nonce=False):
        if not skip_nonce:
            self._set_nonce(pre_session)
        elif pre_session:
            with self.client.session_transaction() as sess:
                sess.update(pre_session)
        with mock.patch.object(auth_google, "oauth", _fake_oauth(claims)), \
             mock.patch.object(auth_google, "get_connection", _fake_get_connection(db)):
            return self.client.get("/auth/google/callback")

    def test_token_invalid_denies_and_audits(self):
        db = FakeDB()
        self._set_nonce()
        with mock.patch.dict(os.environ, VALID_ENV):
            fake = mock.MagicMock()
            fake.google.authorize_access_token.side_effect = RuntimeError("bad signature")
            with mock.patch.object(auth_google, "oauth", fake), \
                 mock.patch.object(auth_google, "get_connection", _fake_get_connection(db)):
                resp = self.client.get("/auth/google/callback")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(len(db.audits), 1)
        self.assertEqual(db.audits[0][1], "FAILURE")
        self.assertEqual(db.audits[0][2], "TOKEN_INVALID")

    def test_missing_nonce_denied_no_user_or_session(self):
        db = FakeDB()
        # No nonce set in session at all (simulates a callback with no prior
        # /auth/google leg, or a replayed/forged callback request).
        resp = self._get({"sub": "sub-1", "email": "a@standards.vn", "email_verified": True,
                           "hd": "standards.vn"}, db, skip_nonce=True)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(db.audits[-1][2], "MISSING_NONCE")
        self.assertIsNone(db.audits[-1][0])  # user_id
        self.assertEqual(len(db.users), 0)
        with self.client.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)

    def test_email_verified_string_true_denied(self):
        db = FakeDB()
        resp = self._get({"sub": "sub-1", "email": "a@standards.vn", "email_verified": "true",
                           "hd": "standards.vn"}, db)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(db.audits[-1][2], "EMAIL_NOT_VERIFIED")

    def test_email_verified_integer_one_denied(self):
        db = FakeDB()
        resp = self._get({"sub": "sub-1", "email": "a@standards.vn", "email_verified": 1,
                           "hd": "standards.vn"}, db)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(db.audits[-1][2], "EMAIL_NOT_VERIFIED")

    def test_same_email_different_sub_is_identity_conflict_not_reused(self):
        db = FakeDB(users=[{
            "id": 11, "username": "owner@standards.vn", "password_hash": None, "auth_provider": "GOOGLE",
            "google_sub": "sub-original", "email": "owner@standards.vn", "display_name": None,
            "account_status": "ACTIVE", "is_admin": True, "team_id": None, "ip_bypass_allowlist": False,
        }])
        resp = self._get({"sub": "sub-attacker", "email": "owner@standards.vn", "email_verified": True,
                           "hd": "standards.vn"}, db)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(db.audits[-1][1], "DENIED")
        self.assertEqual(db.audits[-1][2], "IDENTITY_CONFLICT")
        self.assertIsNone(db.audits[-1][0])  # user_id never attributed to the other account
        self.assertIsNone(db.find_by_sub("sub-attacker"))
        self.assertEqual(db.users[11]["google_sub"], "sub-original")  # untouched
        with self.client.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)
            self.assertNotEqual(sess.get("username"), "owner@standards.vn")

    def test_pending_denial_clears_pre_existing_authenticated_session(self):
        db = FakeDB(users=[{
            "id": 4, "username": "pending@standards.vn", "password_hash": None, "auth_provider": "GOOGLE",
            "google_sub": "sub-pending", "email": "pending@standards.vn", "display_name": None,
            "account_status": "PENDING", "is_admin": False, "team_id": None, "ip_bypass_allowlist": False,
        }])
        old_session = {"authenticated": True, "username": "old.local.user", "is_admin": True, "role": "admin"}
        resp = self._get({"sub": "sub-pending", "email": "pending@standards.vn", "email_verified": True,
                           "hd": "standards.vn"}, db, pre_session=old_session)
        self.assertEqual(resp.status_code, 200)
        with self.client.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)
            self.assertNotIn("username", sess)

    def test_suspended_denial_clears_pre_existing_authenticated_session(self):
        db = FakeDB(users=[{
            "id": 6, "username": "susp@standards.vn", "password_hash": None, "auth_provider": "GOOGLE",
            "google_sub": "sub-susp", "email": "susp@standards.vn", "display_name": None,
            "account_status": "SUSPENDED", "is_admin": False, "team_id": None, "ip_bypass_allowlist": False,
        }])
        old_session = {"authenticated": True, "username": "old.local.user", "is_admin": False, "role": "user"}
        resp = self._get({"sub": "sub-susp", "email": "susp@standards.vn", "email_verified": True,
                           "hd": "standards.vn"}, db, pre_session=old_session)
        self.assertEqual(resp.status_code, 403)
        with self.client.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)
            self.assertNotIn("username", sess)

    def test_identity_conflict_clears_pre_existing_authenticated_session(self):
        db = FakeDB(users=[{
            "id": 11, "username": "owner@standards.vn", "password_hash": None, "auth_provider": "GOOGLE",
            "google_sub": "sub-original", "email": "owner@standards.vn", "display_name": None,
            "account_status": "ACTIVE", "is_admin": True, "team_id": None, "ip_bypass_allowlist": False,
        }])
        old_session = {"authenticated": True, "username": "old.local.user"}
        resp = self._get({"sub": "sub-attacker", "email": "owner@standards.vn", "email_verified": True,
                           "hd": "standards.vn"}, db, pre_session=old_session)
        self.assertEqual(resp.status_code, 409)
        with self.client.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)
            self.assertNotIn("username", sess)

    def test_active_success_replaces_old_session_completely(self):
        db = FakeDB(users=[{
            "id": 3, "username": "active@standards.vn", "password_hash": None, "auth_provider": "GOOGLE",
            "google_sub": "sub-active", "email": "active@standards.vn", "display_name": "Active",
            "account_status": "ACTIVE", "is_admin": True, "team_id": None, "ip_bypass_allowlist": False,
        }])
        old_session = {"authenticated": True, "username": "old.local.user", "team_id": 999, "is_admin": False, "role": "user"}
        resp = self._get({"sub": "sub-active", "email": "active@standards.vn", "email_verified": True,
                           "hd": "standards.vn"}, db, pre_session=old_session)
        self.assertEqual(resp.status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess["username"], "active@standards.vn")
            self.assertEqual(sess["role"], "admin")
            self.assertNotEqual(sess.get("team_id"), 999)
            self.assertNotIn("google_oauth_nonce", sess)

    def test_missing_sub_denied(self):
        db = FakeDB()
        resp = self._get({"email": "a@standards.vn", "email_verified": True, "hd": "standards.vn"}, db)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(db.audits[-1][2], "MISSING_SUB")

    def test_email_not_verified_denied(self):
        db = FakeDB()
        resp = self._get({"sub": "sub-1", "email": "a@standards.vn", "email_verified": False, "hd": "standards.vn"}, db)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(db.audits[-1][2], "EMAIL_NOT_VERIFIED")

    def test_missing_hd_denied_even_with_matching_email_suffix(self):
        db = FakeDB()
        # Email suffix matches an allowed domain, but hd claim is absent -> must fail.
        resp = self._get({"sub": "sub-1", "email": "a@standards.vn", "email_verified": True}, db)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(db.audits[-1][2], "DOMAIN_NOT_ALLOWED")

    def test_wrong_hd_denied(self):
        db = FakeDB()
        resp = self._get({"sub": "sub-1", "email": "a@evil.com", "email_verified": True, "hd": "evil.com"}, db)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(db.audits[-1][2], "DOMAIN_NOT_ALLOWED")

    def test_unknown_identity_becomes_pending_no_session(self):
        db = FakeDB()
        resp = self._get({"sub": "sub-new", "email": "new.user@standards.vn", "email_verified": True,
                           "hd": "standards.vn", "name": "New User"}, db)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"ch\xe1\xbb\x9d", resp.data)  # "chờ" (waiting) page
        created = db.find_by_sub("sub-new")
        self.assertIsNotNone(created)
        self.assertEqual(created[2], "PENDING")
        self.assertIsNone(created[4])  # team_id
        self.assertFalse(created[3])  # is_admin
        with self.client.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)
        self.assertEqual(db.audits[-1][1], "PENDING_APPROVAL")
        self.assertEqual(db.audits[-1][2], "NEW_IDENTITY_PENDING")

    def test_invited_google_user_binds_and_activates(self):
        db = FakeDB(users=[{
            "id": 5, "username": "invited@standards.vn", "password_hash": None,
            "auth_provider": "GOOGLE", "google_sub": None, "email": "invited@standards.vn",
            "display_name": None, "account_status": "INVITED", "is_admin": False,
            "team_id": 2, "ip_bypass_allowlist": False,
        }])
        resp = self._get({"sub": "sub-invited", "email": "invited@standards.vn", "email_verified": True,
                           "hd": "standards.vn", "name": "Invited User"}, db)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[5]["account_status"], "ACTIVE")
        self.assertEqual(db.users[5]["google_sub"], "sub-invited")
        with self.client.session_transaction() as sess:
            self.assertTrue(sess.get("authenticated"))
            self.assertEqual(sess.get("team_id"), 2)
        self.assertEqual(db.audits[-1][1], "SUCCESS")

    def test_local_account_same_email_is_not_auto_linked(self):
        db = FakeDB(users=[{
            "id": 9, "username": "local.user", "password_hash": "hash", "auth_provider": "LOCAL",
            "google_sub": None, "email": "local.user@standards.vn", "display_name": None,
            "account_status": "ACTIVE", "is_admin": False, "team_id": 1, "ip_bypass_allowlist": False,
        }])
        resp = self._get({"sub": "sub-local-lookalike", "email": "local.user@standards.vn",
                           "email_verified": True, "hd": "standards.vn"}, db)
        # Email is already owned by a LOCAL account (case-insensitive unique
        # email applies across providers) -> must fail closed, NOT log into
        # the LOCAL account and NOT silently create a duplicate-email row.
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(db.users[9]["auth_provider"], "LOCAL")
        self.assertIsNone(db.users[9]["google_sub"])
        self.assertIsNone(db.find_by_sub("sub-local-lookalike"))
        # Colliding account is LOCAL, not GOOGLE -> generic conflict, not
        # the P1 "identity_conflict" classification (that's GOOGLE-vs-GOOGLE only).
        self.assertEqual(db.audits[-1][2], "ACCOUNT_PROVISION_CONFLICT")
        with self.client.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)

    def test_identity_conflict_and_provision_conflict_responses_are_identical(self):
        # Phase 5D2A: an external caller must not be able to distinguish a
        # GOOGLE-vs-GOOGLE identity conflict from a plain provisioning
        # conflict (e.g. colliding with a LOCAL account's email) by status
        # code or response body — only the internal audit reason_code may
        # differ. Reuses the exact two scenarios from the tests above.
        db_identity = FakeDB(users=[{
            "id": 11, "username": "owner@standards.vn", "password_hash": None, "auth_provider": "GOOGLE",
            "google_sub": "sub-original", "email": "owner@standards.vn", "display_name": None,
            "account_status": "ACTIVE", "is_admin": True, "team_id": None, "ip_bypass_allowlist": False,
        }])
        resp_identity = self._get({"sub": "sub-attacker", "email": "owner@standards.vn",
                                    "email_verified": True, "hd": "standards.vn"}, db_identity)

        db_provision = FakeDB(users=[{
            "id": 9, "username": "local.user", "password_hash": "hash", "auth_provider": "LOCAL",
            "google_sub": None, "email": "local.user@standards.vn", "display_name": None,
            "account_status": "ACTIVE", "is_admin": False, "team_id": 1, "ip_bypass_allowlist": False,
        }])
        resp_provision = self._get({"sub": "sub-local-lookalike", "email": "local.user@standards.vn",
                                     "email_verified": True, "hd": "standards.vn"}, db_provision)

        self.assertEqual(resp_identity.status_code, resp_provision.status_code)
        self.assertEqual(resp_identity.status_code, 409)
        self.assertEqual(resp_identity.data, resp_provision.data)
        # Internal audit reason codes still differ, for security triage.
        self.assertEqual(db_identity.audits[-1][2], "IDENTITY_CONFLICT")
        self.assertEqual(db_provision.audits[-1][2], "ACCOUNT_PROVISION_CONFLICT")

    def test_active_user_login_success_updates_last_login(self):
        db = FakeDB(users=[{
            "id": 3, "username": "active@standards.vn", "password_hash": None, "auth_provider": "GOOGLE",
            "google_sub": "sub-active", "email": "active@standards.vn", "display_name": "Active",
            "account_status": "ACTIVE", "is_admin": True, "team_id": None, "ip_bypass_allowlist": False,
        }])
        resp = self._get({"sub": "sub-active", "email": "active@standards.vn", "email_verified": True,
                           "hd": "standards.vn"}, db)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.users[3]["last_login_at"], "NOW")
        with self.client.session_transaction() as sess:
            self.assertTrue(sess["authenticated"])
            self.assertEqual(sess["role"], "admin")
            self.assertEqual(sess["auth_provider"], "GOOGLE")
        self.assertEqual(db.audits[-1][1], "SUCCESS")
        self.assertEqual(db.audits[-1][2], "LOGIN")

    def test_pending_user_denied_app_access(self):
        db = FakeDB(users=[{
            "id": 4, "username": "pending@standards.vn", "password_hash": None, "auth_provider": "GOOGLE",
            "google_sub": "sub-pending", "email": "pending@standards.vn", "display_name": None,
            "account_status": "PENDING", "is_admin": False, "team_id": None, "ip_bypass_allowlist": False,
        }])
        resp = self._get({"sub": "sub-pending", "email": "pending@standards.vn", "email_verified": True,
                           "hd": "standards.vn"}, db)
        self.assertEqual(resp.status_code, 200)
        with self.client.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)
        self.assertEqual(db.audits[-1][1], "PENDING_APPROVAL")

    def test_suspended_user_denied(self):
        db = FakeDB(users=[{
            "id": 6, "username": "susp@standards.vn", "password_hash": None, "auth_provider": "GOOGLE",
            "google_sub": "sub-susp", "email": "susp@standards.vn", "display_name": None,
            "account_status": "SUSPENDED", "is_admin": False, "team_id": None, "ip_bypass_allowlist": False,
        }])
        resp = self._get({"sub": "sub-susp", "email": "susp@standards.vn", "email_verified": True,
                           "hd": "standards.vn"}, db)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(db.audits[-1][1], "DENIED")
        self.assertEqual(db.audits[-1][2], "ACCOUNT_SUSPENDED")
        with self.client.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)

    def test_duplicate_race_does_not_create_duplicate_user(self):
        # Simulates a concurrent callback for a brand-new identity: the
        # insert path raises IntegrityError on the second attempt (unique
        # google_sub/email), and resolution must fall back to the row the
        # first request created instead of raising or duplicating it.
        fresh_db = FakeDB()
        with mock.patch.object(auth_google, "get_connection", _fake_get_connection(fresh_db)):
            row, event = auth_google._resolve_or_create_identity("sub-race", "raced2@standards.vn", None, "req-1")
        self.assertEqual(event, "created_pending")
        with mock.patch.object(auth_google, "get_connection", _fake_get_connection(fresh_db)):
            row2, event2 = auth_google._resolve_or_create_identity("sub-race", "raced2@standards.vn", None, "req-2")
        self.assertEqual(event2, "existing")
        self.assertEqual(len(fresh_db.users), 1)
        self.assertEqual(row, row2)

    def test_no_token_or_code_persisted_or_logged(self):
        db = FakeDB()
        self._get({"sub": "sub-secretcheck", "email": "s@standards.vn", "email_verified": True,
                    "hd": "standards.vn"}, db)
        for audit_params in db.audits:
            for value in audit_params:
                self.assertNotIsInstance(value, dict)
        with self.client.session_transaction() as sess:
            for key in sess.keys():
                self.assertNotIn("token", key.lower())


# --------------------------------------------------------------------------
# Migration shape / idempotency (static inspection only; not executed)
# --------------------------------------------------------------------------

class MigrationShapeTests(unittest.TestCase):
    def setUp(self):
        path = REPO_ROOT / "sql" / "migration_014_google_oidc.sql"
        self.assertTrue(path.exists(), "migration_014_google_oidc.sql must exist")
        self.sql = path.read_text(encoding="utf-8")

    def test_does_not_touch_products_table(self):
        # `products` is only mentioned in explanatory comments; no DDL
        # statement in this migration may target that table.
        ddl_lines = [
            line for line in self.sql.splitlines()
            if not line.strip().startswith("--") and "products" in line.lower()
        ]
        self.assertEqual(ddl_lines, [])

    def test_idempotent_guards_present(self):
        self.assertIn("IF NOT EXISTS", self.sql)
        self.assertIn("pg_constraint", self.sql)

    def test_no_forbidden_secret_columns(self):
        forbidden = ["access_token", "refresh_token", "id_token", "client_secret",
                     "auth_code", "session_cookie", "authorization_code"]
        lowered = self.sql.lower()
        for term in forbidden:
            self.assertNotIn(term, lowered)

    def test_four_account_statuses_and_two_providers(self):
        self.assertIn("'INVITED'", self.sql)
        self.assertIn("'PENDING'", self.sql)
        self.assertIn("'ACTIVE'", self.sql)
        self.assertIn("'SUSPENDED'", self.sql)
        self.assertIn("'LOCAL'", self.sql)
        self.assertIn("'GOOGLE'", self.sql)

    def test_google_sub_and_email_uniqueness_are_partial(self):
        self.assertIn("app_users_google_sub_unique_idx", self.sql)
        self.assertIn("WHERE google_sub IS NOT NULL", self.sql)
        self.assertIn("app_users_email_lower_unique_idx", self.sql)
        self.assertIn("WHERE email IS NOT NULL", self.sql)

    def test_approved_by_self_fk_sets_null_not_cascade(self):
        self.assertIn("REFERENCES app_users (id) ON DELETE SET NULL", self.sql)
        self.assertNotRegex(self.sql, r"approved_by[\s\S]{0,200}ON DELETE CASCADE")

    def test_actor_user_id_added_for_admin_action_audit(self):
        # Phase 5D2B: distinguishes the admin who performed an
        # approve/suspend/reactivate/invite/revoke-sessions action (actor)
        # from the account it was performed on (target = existing user_id).
        self.assertIn("actor_user_id", self.sql)
        self.assertIn("login_audit_events_actor_user_id_fkey", self.sql)
        self.assertRegex(
            self.sql,
            r"actor_user_id[\s\S]{0,300}ON DELETE SET NULL",
        )


# --------------------------------------------------------------------------
# Strict shared boolean env parser
# --------------------------------------------------------------------------

class StrictBoolEnvTests(unittest.TestCase):
    def test_missing_returns_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SOME_FLAG_NOT_SET", None)
            self.assertTrue(auth_google.strict_bool_env("SOME_FLAG_NOT_SET", True))
            self.assertFalse(auth_google.strict_bool_env("SOME_FLAG_NOT_SET", False))

    def test_accepted_true_values(self):
        for raw in ["1", "true", "TRUE", " True ", "yes", "YES", "on", "On"]:
            with mock.patch.dict(os.environ, {"FLAG": raw}):
                self.assertTrue(auth_google.strict_bool_env("FLAG", False), raw)

    def test_accepted_false_values(self):
        for raw in ["0", "false", "FALSE", " False ", "no", "NO", "off", "Off"]:
            with mock.patch.dict(os.environ, {"FLAG": raw}):
                self.assertFalse(auth_google.strict_bool_env("FLAG", True), raw)

    def test_invalid_values_raise_config_error(self):
        for raw in ["treu", "enable", "", "  ", "2", "yesplease"]:
            with mock.patch.dict(os.environ, {"FLAG": raw}):
                with self.assertRaises(auth_google.ConfigError):
                    auth_google.strict_bool_env("FLAG", False)

    def test_used_by_google_auth_enabled_and_legacy_flags(self):
        with mock.patch.dict(os.environ, {"GOOGLE_AUTH_ENABLED": "treu"}):
            with self.assertRaises(auth_google.ConfigError):
                auth_google.google_auth_enabled()


# --------------------------------------------------------------------------
# Legacy defaults removed / fail closed
# --------------------------------------------------------------------------

class LegacyDefaultsTests(unittest.TestCase):
    def test_no_hardcoded_secret_defaults_in_source(self):
        src = (REPO_ROOT / "search.py").read_text(encoding="utf-8")
        self.assertNotIn("Truong@2004", src)
        self.assertNotIn("Truong@123", src)
        self.assertNotIn("dev-only-change-me", src)

    def test_missing_flask_secret_key_fails_closed(self):
        # Set to "" (not removed) so search.py's own load_dotenv() call can't
        # silently repopulate it from the local .env file during the subprocess.
        env = dict(os.environ)
        env["FLASK_SECRET_KEY"] = ""
        result = subprocess.run(
            [sys.executable, "-c", "import search"],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FLASK_SECRET_KEY", result.stderr)

    def test_legacy_enabled_without_password_fails_closed(self):
        env = dict(os.environ)
        env["ENABLE_LEGACY_PASSWORD_LOGIN"] = "true"
        env["APP_PASSWORD_MANAGER"] = ""
        env["APP_PASSWORD_STAFF"] = ""
        result = subprocess.run(
            [sys.executable, "-c", "import search"],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ENABLE_LEGACY_PASSWORD_LOGIN", result.stderr)

    def test_legacy_enabled_missing_only_manager_password_fails_closed(self):
        env = dict(os.environ)
        env["ENABLE_LEGACY_PASSWORD_LOGIN"] = "true"
        env["APP_PASSWORD_MANAGER"] = ""
        env["APP_PASSWORD_STAFF"] = "some-staff-password"
        result = subprocess.run(
            [sys.executable, "-c", "import search"],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ENABLE_LEGACY_PASSWORD_LOGIN", result.stderr)

    def test_legacy_enabled_missing_only_staff_password_fails_closed(self):
        env = dict(os.environ)
        env["ENABLE_LEGACY_PASSWORD_LOGIN"] = "true"
        env["APP_PASSWORD_MANAGER"] = "some-manager-password"
        env["APP_PASSWORD_STAFF"] = ""
        result = subprocess.run(
            [sys.executable, "-c", "import search"],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ENABLE_LEGACY_PASSWORD_LOGIN", result.stderr)

    def test_legacy_enabled_with_both_passwords_starts_ok(self):
        env = dict(os.environ)
        env["ENABLE_LEGACY_PASSWORD_LOGIN"] = "true"
        env["APP_PASSWORD_MANAGER"] = "some-manager-password"
        env["APP_PASSWORD_STAFF"] = "some-staff-password"
        result = subprocess.run(
            [sys.executable, "-c", "import search; print('OK')"],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_legacy_disabled_by_default_blocks_manager_password(self):
        search.app.testing = True
        client = search.app.test_client()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENABLE_LEGACY_PASSWORD_LOGIN", None)
            resp = client.post("/login", data={"username": "", "password": "anything"})
        self.assertEqual(resp.status_code, 403)
        with client.session_transaction() as sess:
            self.assertNotIn("authenticated", sess)


# --------------------------------------------------------------------------
# Cookie / proxy config baseline
# --------------------------------------------------------------------------

class CookieProxyConfigTests(unittest.TestCase):
    def test_session_cookie_flags(self):
        self.assertTrue(search.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(search.app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertIn("SESSION_COOKIE_SECURE", search.app.config)

    def test_proxyfix_trusts_exactly_one_hop(self):
        from werkzeug.middleware.proxy_fix import ProxyFix
        self.assertIsInstance(search.app.wsgi_app, ProxyFix)
        self.assertEqual(search.app.wsgi_app.x_for, 1)
        self.assertEqual(search.app.wsgi_app.x_proto, 1)
        self.assertEqual(search.app.wsgi_app.x_host, 1)


if __name__ == "__main__":
    unittest.main()
