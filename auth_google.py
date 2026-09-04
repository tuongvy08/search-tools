"""Google Workspace OIDC login (Phase 5D1 / 5D1B).

Kept out of `search.py` on purpose so the main module doesn't keep growing.
This module owns:
- env-based config/validation for Google OAuth (incl. the shared strict
  boolean env parser used by both this module and `search.py`),
- the Authlib OAuth client registration (Authlib does signature/issuer/
  audience/expiry/nonce/state/PKCE validation; we never hand-roll JWT
  verification or a partial nonce/state comparison of our own),
- the `/auth/google` + `/auth/google/callback` routes,
- the new-user / invited-user / active-user account lifecycle, and
- login audit logging (no tokens/codes/secrets are ever persisted).

When `GOOGLE_AUTH_ENABLED` is false (the default), nothing in this module
talks to the network or requires any Google secret to be set.
"""
import os
import re
import secrets
from uuid import uuid4

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, redirect, render_template, request, session, url_for
from psycopg2 import IntegrityError

from db import get_connection

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

auth_bp = Blueprint("auth_google", __name__)
oauth = OAuth()


class ConfigError(RuntimeError):
    """Raised for invalid/missing environment configuration.

    Error messages must never include actual secret/id/password values,
    only variable names.
    """


class GoogleAuthConfigError(ConfigError):
    """Raised when Google auth is enabled but misconfigured. Fail closed."""


def strict_bool_env(name: str, default: bool = False) -> bool:
    """Shared strict boolean env parser (used by search.py and this module).

    - Missing value -> `default`.
    - True:  "1", "true", "yes", "on" (case-insensitive, trimmed).
    - False: "0", "false", "no", "off" (case-insensitive, trimmed).
    - Anything else (typos like "treu", "enable", or an explicit empty
      string) raises ConfigError instead of silently guessing.
    """
    val = os.environ.get(name)
    if val is None:
        return default
    normalized = val.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigError(
        f"Invalid boolean value for {name}. Expected one of "
        f"{sorted(_TRUE_VALUES | _FALSE_VALUES)} (case-insensitive)."
    )


def google_auth_enabled() -> bool:
    return strict_bool_env("GOOGLE_AUTH_ENABLED", False)


def parse_allowed_domains(raw: str) -> set:
    """Lowercase, trim, drop empty/invalid entries. Never raises."""
    domains = set()
    for part in (raw or "").split(","):
        candidate = part.strip().lower()
        if not candidate:
            continue
        if not _DOMAIN_RE.match(candidate):
            continue
        domains.add(candidate)
    return domains


def get_google_config() -> dict:
    """Return validated Google OAuth config, or raise GoogleAuthConfigError.

    Only called when GOOGLE_AUTH_ENABLED is true. Error messages never
    include the actual secret/id values, only the missing variable names.
    """
    client_id = (os.environ.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
    redirect_uri = (os.environ.get("GOOGLE_OAUTH_REDIRECT_URI") or "").strip()
    allowed_domains = parse_allowed_domains(os.environ.get("GOOGLE_WORKSPACE_ALLOWED_DOMAINS", ""))

    missing = [
        name
        for name, value in (
            ("GOOGLE_OAUTH_CLIENT_ID", client_id),
            ("GOOGLE_OAUTH_CLIENT_SECRET", client_secret),
            ("GOOGLE_OAUTH_REDIRECT_URI", redirect_uri),
        )
        if not value
    ]
    if not allowed_domains:
        missing.append("GOOGLE_WORKSPACE_ALLOWED_DOMAINS")
    if missing:
        raise GoogleAuthConfigError(
            "Google auth is enabled but required configuration is missing: "
            + ", ".join(missing)
        )
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "allowed_domains": allowed_domains,
    }


def init_app(app) -> None:
    """Register the blueprint always; register the OAuth client only if enabled.

    Disabled: no secrets required, no network call, app still starts.
    Enabled + misconfigured: raises GoogleAuthConfigError at startup (fail
    closed) instead of silently accepting logins with broken config.
    """
    app.register_blueprint(auth_bp)
    if not google_auth_enabled():
        return
    config = get_google_config()
    oauth.init_app(app)
    oauth.register(
        name="google",
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_kwargs={
            "scope": "openid email profile",
            "code_challenge_method": "S256",
        },
    )


def _write_audit(cur, *, user_id, outcome, reason_code, email=None, domain=None, request_id=None):
    source_ip = (request.remote_addr or None) if request else None
    user_agent = (request.headers.get("User-Agent") or "")[:512] if request else ""
    cur.execute(
        """
        INSERT INTO login_audit_events
            (user_id, provider, outcome, reason_code, email_snapshot, domain_snapshot,
             source_ip, user_agent, request_id)
        VALUES (%s, 'GOOGLE', %s, %s, %s, %s, %s, %s, %s)
        """,
        (user_id, outcome, reason_code, (email or None), (domain or None),
         source_ip, user_agent or None, request_id),
    )


def _audit_standalone(*, outcome, reason_code, email=None, domain=None, request_id=None):
    """Used for pre-DB-lookup failures (bad token, unverified email, bad hd)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _write_audit(cur, user_id=None, outcome=outcome, reason_code=reason_code,
                         email=email, domain=domain, request_id=request_id)
        conn.commit()
    finally:
        conn.close()


_USER_COLUMNS = "id, username, account_status, is_admin, team_id, ip_bypass_allowlist, auth_version"

_IDENTITY_SESSION_KEYS = (
    "authenticated", "username", "user_id", "team_id", "is_admin",
    "ip_bypass_allowlist", "role", "auth_provider",
)


def _clear_authenticated_identity():
    """Drop any pre-existing authenticated identity + OAuth transient nonce.

    Only pops our own explicit keys — never touches Authlib's own
    state/code_verifier session bookkeeping, so it's always safe relative
    to Authlib's state/nonce validation.
    """
    for key in _IDENTITY_SESSION_KEYS:
        session.pop(key, None)
    session.pop("google_oauth_nonce", None)


def _establish_session(row):
    user_id, username, _status, is_admin, team_id, ip_bypass, auth_version = row
    session.clear()
    session["authenticated"] = True
    session["username"] = username
    session["user_id"] = user_id
    session["team_id"] = team_id
    session["is_admin"] = bool(is_admin)
    session["ip_bypass_allowlist"] = bool(ip_bypass)
    session["role"] = "admin" if is_admin else "user"
    session["auth_provider"] = "GOOGLE"
    session["auth_version"] = auth_version


def _resolve_or_create_identity(sub: str, canonical_email: str, display_name, request_id: str):
    """Returns (row_or_None, event) where event is one of:
    'existing', 'bound_invite', 'created_pending', 'identity_conflict', 'conflict'.

    `row` is only non-None for 'existing'/'bound_invite'/'created_pending'.
    For 'identity_conflict' and 'conflict', row is ALWAYS None — an account
    belonging to a different google_sub (or a non-GOOGLE account) must never
    be returned/logged into, even on a unique-index race.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # google_sub is the sole identity key: look it up first, always.
            cur.execute(f"SELECT {_USER_COLUMNS} FROM app_users WHERE google_sub = %s", (sub,))
            row = cur.fetchone()
            if row is not None:
                conn.commit()
                return row, "existing"

            # Email-based linking is allowed ONLY for an un-bound GOOGLE
            # invite: auth_provider='GOOGLE', account_status='INVITED',
            # google_sub IS NULL, case-insensitive email match.
            cur.execute(
                "SELECT id FROM app_users "
                "WHERE auth_provider = 'GOOGLE' AND account_status = 'INVITED' "
                "AND google_sub IS NULL AND lower(email) = %s "
                "FOR UPDATE",
                (canonical_email,),
            )
            invited = cur.fetchone()
            if invited is not None:
                cur.execute(
                    f"""
                    UPDATE app_users
                    SET google_sub = %s, account_status = 'ACTIVE',
                        display_name = COALESCE(display_name, %s), last_login_at = NOW()
                    WHERE id = %s
                    RETURNING {_USER_COLUMNS}
                    """,
                    (sub, display_name, invited[0]),
                )
                row = cur.fetchone()
                conn.commit()
                return row, "bound_invite"

            try:
                cur.execute(
                    f"""
                    INSERT INTO app_users
                        (username, password_hash, auth_provider, google_sub, email,
                         display_name, account_status)
                    VALUES (%s, NULL, 'GOOGLE', %s, %s, %s, 'PENDING')
                    RETURNING {_USER_COLUMNS}
                    """,
                    (canonical_email, sub, canonical_email, display_name),
                )
                row = cur.fetchone()
                conn.commit()
                return row, "created_pending"
            except IntegrityError:
                conn.rollback()
                with conn.cursor() as cur2:
                    # Re-query by google_sub FIRST (and only). If a
                    # concurrent request already inserted this exact sub,
                    # adopt that row. Never fall back to an email match to
                    # "recover" a login — that is exactly the P1 identity
                    # conflict this must prevent.
                    cur2.execute(
                        f"SELECT {_USER_COLUMNS} FROM app_users WHERE google_sub = %s",
                        (sub,),
                    )
                    row = cur2.fetchone()
                    if row is not None:
                        conn.commit()
                        return row, "existing"

                    # Insert failed for a reason other than "this sub already
                    # exists" — most likely the case-insensitive unique email
                    # index collided with a different account. Determine
                    # whether that other account is itself a GOOGLE identity
                    # (P1: same email, different sub) purely for audit
                    # classification. The row is NEVER returned either way.
                    cur2.execute(
                        "SELECT auth_provider FROM app_users WHERE lower(email) = %s",
                        (canonical_email,),
                    )
                    colliding = cur2.fetchone()
                conn.commit()
                if colliding is not None and colliding[0] == "GOOGLE":
                    return None, "identity_conflict"
                return None, "conflict"
    finally:
        conn.close()


_GENERIC_OAUTH_CONFLICT_MESSAGE = "Không thể đăng nhập."


def _generic_oauth_conflict_response():
    """Single response shape shared by every external OAuth-identity/
    provisioning conflict outcome (`identity_conflict` and `conflict`).
    Same status code, same body, same template — callers must not be able
    to distinguish one failure reason from another from the response alone.
    """
    return render_template("login.html", error=_GENERIC_OAUTH_CONFLICT_MESSAGE, google_auth_enabled=True), 409


@auth_bp.route("/auth/google")
def google_login():
    if not google_auth_enabled():
        return render_template("login.html", error="Đăng nhập Google chưa được bật.", google_auth_enabled=False), 404
    config = get_google_config()
    # Drop any previously-authenticated identity before starting a new OAuth
    # transaction, without disturbing the nonce we are about to store.
    _clear_authenticated_identity()
    nonce = secrets.token_urlsafe(24)
    session["google_oauth_nonce"] = nonce
    # hd=* is a UX hint only (biases Google's account chooser toward Workspace
    # accounts); the actual domain check happens on the returned `hd` claim.
    return oauth.google.authorize_redirect(config["redirect_uri"], nonce=nonce, hd="*")


@auth_bp.route("/auth/google/callback")
def google_callback():
    if not google_auth_enabled():
        return render_template("login.html", error="Đăng nhập Google chưa được bật.", google_auth_enabled=False), 404

    request_id = str(uuid4())

    try:
        config = get_google_config()
    except GoogleAuthConfigError:
        return render_template("login.html", error="Đăng nhập Google chưa được cấu hình.", google_auth_enabled=False), 503

    # Read our own nonce WITHOUT touching Authlib's own state/code_verifier
    # session keys — those are read internally by authorize_access_token()
    # below and must not be disturbed beforehand.
    nonce = session.pop("google_oauth_nonce", None)
    if not nonce:
        _audit_standalone(outcome="FAILURE", reason_code="MISSING_NONCE", request_id=request_id)
        _clear_authenticated_identity()
        return render_template("login.html", error="Phiên đăng nhập Google không hợp lệ hoặc đã hết hạn.", google_auth_enabled=True), 401

    try:
        # authorize_access_token() validates `state` against the session;
        # parse_id_token() validates signature, issuer, audience, expiry,
        # and nonce. All of this is Authlib's own verification — we never
        # substitute a partial hand-rolled comparison for any of it.
        token = oauth.google.authorize_access_token()
        claims = oauth.google.parse_id_token(token, nonce=nonce)
    except Exception:
        token = None
        _audit_standalone(outcome="FAILURE", reason_code="TOKEN_INVALID", request_id=request_id)
        _clear_authenticated_identity()
        return render_template("login.html", error="Đăng nhập Google không thành công.", google_auth_enabled=True), 401
    finally:
        # Token/claims container only lives for this request; nothing here
        # is written to session or the database.
        token = None

    email = (claims.get("email") or "").strip()
    canonical_email = email.lower()
    # Strict: only the boolean `True` is accepted. "true"/1/missing/false
    # are all rejected — no truthy coercion.
    email_verified = claims.get("email_verified") is True
    sub = (claims.get("sub") or "").strip()
    hd = (claims.get("hd") or "").strip().lower()
    display_name = (claims.get("name") or "").strip() or None
    domain_hint = hd or (canonical_email.rsplit("@", 1)[-1] if "@" in canonical_email else "")

    if not sub:
        _audit_standalone(outcome="FAILURE", reason_code="MISSING_SUB", email=canonical_email, domain=domain_hint, request_id=request_id)
        _clear_authenticated_identity()
        return render_template("login.html", error="Đăng nhập Google không thành công.", google_auth_enabled=True), 401

    if not email_verified:
        _audit_standalone(outcome="FAILURE", reason_code="EMAIL_NOT_VERIFIED", email=canonical_email, domain=domain_hint, request_id=request_id)
        _clear_authenticated_identity()
        return render_template("login.html", error="Email Google chưa được xác minh.", google_auth_enabled=True), 401

    # Security check uses ONLY the `hd` claim — never an email suffix.
    if not hd or hd not in config["allowed_domains"]:
        _audit_standalone(outcome="DENIED", reason_code="DOMAIN_NOT_ALLOWED", email=canonical_email, domain=domain_hint, request_id=request_id)
        _clear_authenticated_identity()
        return render_template("login.html", error="Tài khoản Google không thuộc tổ chức được phép.", google_auth_enabled=True), 403

    row, event = _resolve_or_create_identity(sub, canonical_email, display_name, request_id)

    if event == "identity_conflict":
        # Same email already bound to a DIFFERENT google_sub. Absolutely no
        # account is returned/logged into; generic error only. The reason
        # code differs internally from "conflict" below purely for audit
        # classification — the HTTP status/body returned to the browser
        # must be byte-for-byte identical either way (no side-channel that
        # would let a caller distinguish "identity conflict" from a plain
        # provisioning failure).
        _audit_standalone(outcome="DENIED", reason_code="IDENTITY_CONFLICT", email=canonical_email, domain=hd, request_id=request_id)
        _clear_authenticated_identity()
        return _generic_oauth_conflict_response()

    if event == "conflict" or row is None:
        _audit_standalone(outcome="FAILURE", reason_code="ACCOUNT_PROVISION_CONFLICT", email=canonical_email, domain=hd, request_id=request_id)
        _clear_authenticated_identity()
        return _generic_oauth_conflict_response()

    user_id, _username, status, _is_admin, _team_id, _ip_bypass, _auth_version = row
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if event == "created_pending":
                _write_audit(cur, user_id=user_id, outcome="PENDING_APPROVAL", reason_code="NEW_IDENTITY_PENDING", email=canonical_email, domain=hd, request_id=request_id)
                conn.commit()
                _clear_authenticated_identity()
                return render_template("pending_approval.html"), 200

            if status == "SUSPENDED":
                _write_audit(cur, user_id=user_id, outcome="DENIED", reason_code="ACCOUNT_SUSPENDED", email=canonical_email, domain=hd, request_id=request_id)
                conn.commit()
                _clear_authenticated_identity()
                return render_template("login.html", error="Tài khoản đã bị tạm khoá.", google_auth_enabled=True), 403

            if status == "PENDING":
                _write_audit(cur, user_id=user_id, outcome="PENDING_APPROVAL", reason_code="AWAITING_APPROVAL", email=canonical_email, domain=hd, request_id=request_id)
                conn.commit()
                _clear_authenticated_identity()
                return render_template("pending_approval.html"), 200

            if status == "ACTIVE":
                cur.execute("UPDATE app_users SET last_login_at = NOW() WHERE id = %s", (user_id,))
                _write_audit(cur, user_id=user_id, outcome="SUCCESS", reason_code="LOGIN", email=canonical_email, domain=hd, request_id=request_id)
                conn.commit()
                _establish_session(row)  # session.clear() then fresh identity
                return redirect(url_for("home"))

            # Any other/unexpected status: fail closed.
            _write_audit(cur, user_id=user_id, outcome="DENIED", reason_code="UNEXPECTED_STATUS", email=canonical_email, domain=hd, request_id=request_id)
            conn.commit()
            _clear_authenticated_identity()
            return render_template("login.html", error="Không thể đăng nhập.", google_auth_enabled=True), 403
    finally:
        conn.close()
