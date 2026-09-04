"""Phase 5D2A: session validation, logout, and CSRF.

Kept out of `search.py` on purpose (same rationale as `auth_google.py`).
This module owns:
- the shared per-request session-liveness check — confirms the account in
  `session["user_id"]` still exists, is `account_status = 'ACTIVE'`, and
  that `session["auth_version"]` still matches the DB. Exactly one
  verification query per request, and only for requests that already carry
  a `user_id` in session (anonymous requests, legacy break-glass sessions
  with no per-user row, static assets, and pre-login endpoints are all
  skipped — no query at all),
- CSRF token issuance/verification for the logout form (token bound to the
  session, compared constant-time), and
- the `POST /logout` route.

This module never authenticates anyone — it only ever reads `app_users` to
reject/clear sessions, and writes non-identifying rows to the existing
`login_audit_events` table (no email/secret/token is ever included).
"""
import hmac
import secrets

from flask import Blueprint, jsonify, redirect, request, session, url_for

import auth_google
from db import get_connection

session_bp = Blueprint("session_security", __name__)

_LOGIN_NOTICE_KEY = "_login_notice"
_CSRF_SESSION_KEY = "csrf_token"
_GENERIC_SESSION_INVALID_MESSAGE = "Phiên đăng nhập đã hết hiệu lực. Vui lòng đăng nhập lại."

# Requests to these endpoints are exempt from the per-request DB validation:
# Flask's own static file handler, and every endpoint someone must be able
# to reach *before* they have a valid/authenticated session.
_EXEMPT_ENDPOINTS = {
    "static",
    "login",
    "session_security.logout",
    "auth_google.google_login",
    "auth_google.google_callback",
}


def get_csrf_token() -> str:
    """Return the CSRF token bound to the current session, creating it if absent."""
    token = session.get(_CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_CSRF_SESSION_KEY] = token
    return token


def verify_csrf_token(candidate) -> bool:
    expected = session.get(_CSRF_SESSION_KEY)
    if not expected or not candidate:
        return False
    return hmac.compare_digest(expected, candidate)


def pop_login_notice():
    """Consumed once by the login page GET handler; never survives a second render."""
    return session.pop(_LOGIN_NOTICE_KEY, None)


def _write_audit_event(*, user_id, provider, outcome, reason_code):
    """Minimal insert into the shared `login_audit_events` table (migration
    014). Deliberately narrower than `auth_google._write_audit`: this module
    never has an email/domain/request_id to attach, and must never guess a
    provider — the caller always supplies the session's own `auth_provider`.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO login_audit_events (user_id, provider, outcome, reason_code)
                VALUES (%s, %s, %s, %s)
                """,
                (user_id, provider, outcome, reason_code),
            )
        conn.commit()
    finally:
        conn.close()


def _audit_revocation(user_id, reason_code):
    """Best-effort audit write for a forced session revocation. Never raises —
    a logging failure must never turn into an authorization bypass or a 500
    that leaks details; the session is cleared either way by the caller.
    """
    provider = session.get("auth_provider") or "LOCAL"
    try:
        _write_audit_event(user_id=user_id, provider=provider, outcome="DENIED", reason_code=reason_code)
    except Exception:
        pass


def _reject_invalid_session(user_id, reason_code):
    _audit_revocation(user_id, reason_code)
    session.clear()
    if (request.path or "").startswith("/api/"):
        return jsonify({"error": _GENERIC_SESSION_INVALID_MESSAGE}), 401
    session[_LOGIN_NOTICE_KEY] = _GENERIC_SESSION_INVALID_MESSAGE
    return redirect(url_for("login"))


def _legacy_password_login_enabled() -> bool:
    """Computed independently of search.py's own module-level flag (which
    this module must not import, to avoid a circular import) via the same
    shared strict boolean parser, so both stay in lockstep by construction.
    """
    return auth_google.strict_bool_env("ENABLE_LEGACY_PASSWORD_LOGIN", False)


def enforce_session_validity():
    """Registered as a Flask `before_request` hook. Returning None lets the
    normal view run; returning a response short-circuits the request.
    """
    if request.endpoint is None or request.endpoint in _EXEMPT_ENDPOINTS:
        return None

    user_id = session.get("user_id")
    if user_id is None:
        # Anonymous request: nothing to validate, no DB call.
        if not session.get("authenticated"):
            return None
        # A session claiming to be authenticated but with no `user_id` is
        # the legacy break-glass shape (manager/staff passwords), which has
        # no per-user `app_users` row to check. That bypass is ONLY valid
        # while ENABLE_LEGACY_PASSWORD_LOGIN=true -- it must NEVER be
        # treated as a valid production break-glass path on its own. When
        # legacy password login is disabled, any such session must be
        # rejected/cleared here (fail closed), not silently passed through.
        if _legacy_password_login_enabled():
            return None
        return _reject_invalid_session(None, "LEGACY_SESSION_DISABLED")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT account_status, auth_version FROM app_users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None:
        return _reject_invalid_session(user_id, "ACCOUNT_NOT_FOUND")
    account_status, auth_version = row
    if account_status != "ACTIVE":
        return _reject_invalid_session(user_id, "ACCOUNT_NOT_ACTIVE")
    if auth_version != session.get("auth_version"):
        return _reject_invalid_session(user_id, "AUTH_VERSION_MISMATCH")
    return None


@session_bp.route("/logout", methods=["POST"])
def logout():
    submitted = request.form.get("csrf_token", "")
    if not verify_csrf_token(submitted):
        return "Yêu cầu không hợp lệ.", 400

    user_id = session.get("user_id")
    provider = session.get("auth_provider") or "LOCAL"
    session.clear()

    try:
        _write_audit_event(user_id=user_id, provider=provider, outcome="SUCCESS", reason_code="LOGOUT")
    except Exception:
        pass

    return redirect(url_for("login"))


def init_app(app) -> None:
    app.register_blueprint(session_bp)
    app.before_request(enforce_session_validity)
    app.context_processor(lambda: {"csrf_token": get_csrf_token})
