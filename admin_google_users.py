"""Phase 5D2B: Admin approval & Google Workspace user lifecycle management.

Kept in its own module (same rationale as `auth_google.py` / `session_security.py`
-- see their module docstrings). This module owns:
- the GET data assembly for the Google-account section of `/admin/users`
  (called from search.py's existing `admin_users()` view, which still owns
  the LOCAL/legacy user+brand-management section on the same page), and
- the five POST actions below: approve / invite / suspend / reactivate /
  revoke-sessions.

Every POST action here is: admin-guarded, CSRF-checked, wrapped in exactly
one DB transaction with `SELECT ... FOR UPDATE` on the target row (so a
concurrent action, or a concurrent Google login racing an approval, can
never silently overwrite a stale status), and audited via
`login_audit_events` (migration 014 adds `actor_user_id` = the admin;
`user_id` = the target account).

Phase 5D2B Final: EVERY one of these five actions -- not just suspend() --
now follows the exact same unconditional sequence, in this order, before
touching any target row: (1) admin+CSRF check at the route entry (already
true), (2) begin the transaction, (3) `acquire_last_admin_lock` (the shared
advisory lock -- see its docstring), (4) `revalidate_actor` (fresh DB read
of the ACTING admin's own row). Only after that does each action read/lock
its target row, validate, mutate, and write its audit row -- all still
inside the same transaction. This lock/revalidation pair is scoped
EXCLUSIVELY to admin user-management mutations in this module and in
search.py's legacy create_user/update_user branches; it must never be
applied to login, search/match, export, or any other ordinary request.

Never reads, returns, or logs: Google `sub`, password_hash, tokens, cookies,
or raw DB/constraint error text. Every user-facing error is one of the fixed
generic Vietnamese strings below -- never `str(exc)`.
"""
import os
import re

from flask import Blueprint, request, redirect, session, url_for
from psycopg2 import IntegrityError

from auth_google import parse_allowed_domains
from db import get_connection
from session_security import verify_csrf_token

admin_google_users_bp = Blueprint("admin_google_users", __name__)

# --------------------------------------------------------------------------
# Fixed, generic Vietnamese error strings. Never derived from an exception's
# message, a DB constraint name, or any other internal detail.
# --------------------------------------------------------------------------
_ERR_GENERIC = "Không thể thực hiện thao tác. Vui lòng thử lại."
_ERR_CSRF = "Yêu cầu không hợp lệ."
_ERR_NOT_ADMIN = "Chỉ admin mới được thao tác."
_ERR_NO_ACTOR = "Tài khoản quản trị này không thể thực hiện thao tác này."
_ERR_BAD_ROLE = "Vai trò không hợp lệ."
_ERR_STAFF_NEEDS_TEAM = "Vui lòng chọn team hợp lệ cho nhân viên."
_ERR_NOT_PENDING = "Tài khoản không còn ở trạng thái chờ phê duyệt."
_ERR_NOT_ACTIVE = "Tài khoản không ở trạng thái đang hoạt động."
_ERR_NOT_SUSPENDED = "Tài khoản không ở trạng thái đã tạm khoá."
_ERR_SELF_SUSPEND = "Không thể tự khoá tài khoản của chính mình."
_ERR_LAST_ADMIN = "Không thể khoá admin đang hoạt động cuối cùng."
_ERR_BAD_EMAIL_DOMAIN = "Email phải thuộc một trong các domain được phép."
_ERR_DUPLICATE_INVITE = "Email này đã có tài khoản hoặc đã được mời."
_ERR_ACTOR_INVALID = "Phiên quản trị không còn hợp lệ, vui lòng đăng nhập lại."

_VALID_ROLES = {"admin", "staff"}
_EMAIL_RE = re.compile(r"^[^@\s]+@([a-z0-9.-]+)$")

# Phase 5D2B.1: transaction-scoped advisory lock shared by EVERY code path
# that can reduce the number of ACTIVE admins -- today that is this
# module's suspend() and search.py's legacy `update_user` demote path.
# Locking only the target row with `FOR UPDATE` and then running
# `SELECT COUNT(*) ...` is NOT enough: under READ COMMITTED, that COUNT is
# a plain read that does not block on (or see) another concurrent
# transaction's uncommitted row lock, so two admins each suspending/
# demoting the OTHER at the same time can both independently observe
# "someone else is still active" and both proceed -- zeroing out active
# admins. `pg_advisory_xact_lock` is a whole-database mutex held for the
# rest of the current transaction (auto-released on commit/rollback) that
# fully serializes these decisions across connections. It must always be
# acquired FIRST, before any per-row `FOR UPDATE`, with this exact same key,
# by every such path, so lock order is identical everywhere and cannot
# deadlock against itself.
_LAST_ADMIN_LOCK_KEY = 891273465


def acquire_last_admin_lock(cur) -> None:
    """Call as the very first statement of the transaction, before any
    target-row `FOR UPDATE` and before counting other active admins.
    """
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (_LAST_ADMIN_LOCK_KEY,))


def revalidate_actor(cur, admin_id, expected_auth_version) -> None:
    """Phase 5D2B.2: MUST be called immediately after `acquire_last_admin_lock`
    and before reading/modifying any target row, by every path that
    acquires that lock. `_current_admin_actor()` only trusts the session's
    `is_admin`/`user_id` -- both can go stale while a request sits waiting
    for the shared advisory lock (e.g. another admin suspends/demotes this
    very actor, or revokes their sessions, in the meantime). This re-reads
    the actor's OWN row fresh (under the lock's serialization, so it can't
    itself be racing) and rejects the mutation if the actor is no longer an
    ACTIVE admin or their auth_version no longer matches the session that
    authenticated this request.
    """
    cur.execute(
        "SELECT account_status, is_admin, auth_version FROM app_users WHERE id = %s",
        (admin_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise _ActionError(_ERR_ACTOR_INVALID)
    account_status, is_admin, auth_version = row
    if account_status != "ACTIVE" or not is_admin or auth_version != expected_auth_version:
        raise _ActionError(_ERR_ACTOR_INVALID)


class _ActionError(Exception):
    """Carries a pre-approved, generic Vietnamese message ONLY. Must never
    be constructed from a raw exception/DB-error string.
    """


def _current_admin_actor():
    """Returns (admin_user_id, None) on success, or (None, (message, status))
    on failure. A legacy break-glass admin session (no `user_id`) is
    rejected here -- it must never be recorded as the actor of an
    account-lifecycle action.
    """
    if not session.get("authenticated") or not session.get("is_admin"):
        return None, (_ERR_NOT_ADMIN, 403)
    admin_user_id = session.get("user_id")
    if not admin_user_id:
        return None, (_ERR_NO_ACTOR, 403)
    return admin_user_id, None


def _check_csrf() -> bool:
    return verify_csrf_token(request.form.get("csrf_token", ""))


def _redirect_result(msg=None, err=None):
    return redirect(url_for("admin_users", msg=msg, err=err))


def _parse_user_id():
    raw = (request.form.get("user_id") or "").strip()
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    return parsed


def _write_admin_audit(cur, *, actor_user_id, target_user_id, outcome, reason_code):
    """Minimal audit insert: identifies actor + target only. No email, no
    sub, no token -- those are never needed for these admin-action events.
    """
    cur.execute(
        """
        INSERT INTO login_audit_events (user_id, actor_user_id, provider, outcome, reason_code)
        VALUES (%s, %s, 'GOOGLE', %s, %s)
        """,
        (target_user_id, actor_user_id, outcome, reason_code),
    )


def _allowed_invite_domains() -> set:
    return parse_allowed_domains(os.environ.get("GOOGLE_WORKSPACE_ALLOWED_DOMAINS", ""))


# --------------------------------------------------------------------------
# GET data assembly (called from search.py's admin_users() view, using the
# SAME connection/cursor as the existing LOCAL user listing -- no extra
# connection is opened for this).
# --------------------------------------------------------------------------

def fetch_google_admin_context(cur):
    """Returns (google_users, teams).

    `google_users` never includes google_sub, password_hash, or any audit
    row. Ordered so PENDING (needs action) surfaces first.
    """
    cur.execute(
        """
        SELECT a.id, a.email, a.display_name, a.is_admin, a.team_id, t.name,
               a.account_status, a.approved_at, a.last_login_at
        FROM app_users a
        LEFT JOIN teams t ON t.id = a.team_id
        WHERE a.auth_provider = 'GOOGLE'
        ORDER BY
            CASE a.account_status
                WHEN 'PENDING' THEN 0
                WHEN 'INVITED' THEN 1
                WHEN 'ACTIVE' THEN 2
                WHEN 'SUSPENDED' THEN 3
                ELSE 4
            END,
            a.id DESC
        """
    )
    google_users = [
        {
            "id": uid,
            "email": email,
            "display_name": display_name,
            "role": "admin" if is_admin else "staff",
            "team_id": team_id,
            "team_name": team_name,
            "account_status": account_status,
            "approved_at": approved_at,
            "last_login_at": last_login_at,
        }
        for (uid, email, display_name, is_admin, team_id, team_name,
             account_status, approved_at, last_login_at) in cur.fetchall()
    ]

    cur.execute("SELECT id, name FROM teams ORDER BY name")
    teams = [{"id": tid, "name": name} for (tid, name) in cur.fetchall()]

    return google_users, teams


# --------------------------------------------------------------------------
# POST actions
# --------------------------------------------------------------------------

@admin_google_users_bp.route("/admin/users/google/approve", methods=["POST"])
def approve():
    admin_id, err = _current_admin_actor()
    if err:
        return err
    if not _check_csrf():
        return _ERR_CSRF, 400

    expected_auth_version = session.get("auth_version")
    target_id = _parse_user_id()
    role = (request.form.get("role") or "").strip().lower()
    team_id_raw = (request.form.get("team_id") or "").strip()

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                acquire_last_admin_lock(cur)
                revalidate_actor(cur, admin_id, expected_auth_version)

                if target_id is None or role not in _VALID_ROLES:
                    raise _ActionError(_ERR_BAD_ROLE)

                cur.execute(
                    "SELECT account_status FROM app_users "
                    "WHERE id = %s AND auth_provider = 'GOOGLE' FOR UPDATE",
                    (target_id,),
                )
                row = cur.fetchone()
                if row is None or row[0] != "PENDING":
                    raise _ActionError(_ERR_NOT_PENDING)

                is_admin_role = role == "admin"
                team_id = None
                if not is_admin_role:
                    # Staff must have a valid, existing team. Admins always
                    # get team_id=NULL regardless of what the form sent.
                    try:
                        candidate_team_id = int(team_id_raw)
                    except (TypeError, ValueError):
                        raise _ActionError(_ERR_STAFF_NEEDS_TEAM)
                    cur.execute("SELECT id FROM teams WHERE id = %s", (candidate_team_id,))
                    if cur.fetchone() is None:
                        raise _ActionError(_ERR_STAFF_NEEDS_TEAM)
                    team_id = candidate_team_id

                cur.execute(
                    """
                    UPDATE app_users
                    SET account_status = 'ACTIVE', is_admin = %s, team_id = %s,
                        approved_by = %s, approved_at = NOW(), auth_version = auth_version + 1
                    WHERE id = %s
                    """,
                    (is_admin_role, team_id, admin_id, target_id),
                )
                _write_admin_audit(cur, actor_user_id=admin_id, target_user_id=target_id,
                                    outcome="SUCCESS", reason_code="USER_APPROVED")
    except _ActionError as e:
        return _redirect_result(err=str(e))
    except Exception:
        return _redirect_result(err=_ERR_GENERIC)
    finally:
        conn.close()
    return _redirect_result(msg="Đã phê duyệt tài khoản.")


@admin_google_users_bp.route("/admin/users/google/invite", methods=["POST"])
def invite():
    admin_id, err = _current_admin_actor()
    if err:
        return err
    if not _check_csrf():
        return _ERR_CSRF, 400

    expected_auth_version = session.get("auth_version")
    canonical_email = (request.form.get("email") or "").strip().lower()
    match = _EMAIL_RE.match(canonical_email)
    domain = match.group(1) if match else ""
    allowed_domains = _allowed_invite_domains()

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                acquire_last_admin_lock(cur)
                revalidate_actor(cur, admin_id, expected_auth_version)

                if not match or domain not in allowed_domains:
                    raise _ActionError(_ERR_BAD_EMAIL_DOMAIN)

                try:
                    cur.execute(
                        """
                        INSERT INTO app_users
                            (username, password_hash, auth_provider, google_sub, email,
                             display_name, account_status)
                        VALUES (%s, NULL, 'GOOGLE', NULL, %s, NULL, 'INVITED')
                        RETURNING id
                        """,
                        (canonical_email, canonical_email),
                    )
                except IntegrityError:
                    # Unique (lower(email)) or unique(username) already
                    # taken -- never overwrite an existing account.
                    raise _ActionError(_ERR_DUPLICATE_INVITE)

                (new_id,) = cur.fetchone()
                _write_admin_audit(cur, actor_user_id=admin_id, target_user_id=new_id,
                                    outcome="SUCCESS", reason_code="USER_INVITED")
    except _ActionError as e:
        return _redirect_result(err=str(e))
    except Exception:
        return _redirect_result(err=_ERR_GENERIC)
    finally:
        conn.close()
    return _redirect_result(msg="Đã tạo lời mời.")


@admin_google_users_bp.route("/admin/users/google/suspend", methods=["POST"])
def suspend():
    admin_id, err = _current_admin_actor()
    if err:
        return err
    if not _check_csrf():
        return _ERR_CSRF, 400

    expected_auth_version = session.get("auth_version")
    target_id = _parse_user_id()
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                # Consistent global lock order (see _LAST_ADMIN_LOCK_KEY):
                # acquired unconditionally, before any per-row lock, even
                # though it only matters when the target turns out to be an
                # admin -- this keeps lock order identical on every path.
                acquire_last_admin_lock(cur)
                revalidate_actor(cur, admin_id, expected_auth_version)

                if target_id is None:
                    raise _ActionError(_ERR_GENERIC)
                if target_id == admin_id:
                    raise _ActionError(_ERR_SELF_SUSPEND)

                cur.execute(
                    "SELECT account_status, is_admin FROM app_users "
                    "WHERE id = %s AND auth_provider = 'GOOGLE' FOR UPDATE",
                    (target_id,),
                )
                row = cur.fetchone()
                if row is None or row[0] != "ACTIVE":
                    raise _ActionError(_ERR_NOT_ACTIVE)

                target_is_admin = bool(row[1])
                if target_is_admin:
                    # Count ANY other active admin in the whole system
                    # (LOCAL or GOOGLE) -- not just Google-provisioned ones.
                    # Safe from the TOCTOU race described above because we
                    # already hold the advisory lock acquired at the top of
                    # this transaction.
                    cur.execute(
                        "SELECT COUNT(*) FROM app_users "
                        "WHERE is_admin = TRUE AND account_status = 'ACTIVE' AND id <> %s",
                        (target_id,),
                    )
                    (other_active_admins,) = cur.fetchone()
                    if other_active_admins == 0:
                        raise _ActionError(_ERR_LAST_ADMIN)

                cur.execute(
                    "UPDATE app_users SET account_status = 'SUSPENDED', "
                    "auth_version = auth_version + 1 WHERE id = %s",
                    (target_id,),
                )
                _write_admin_audit(cur, actor_user_id=admin_id, target_user_id=target_id,
                                    outcome="SUCCESS", reason_code="USER_SUSPENDED")
    except _ActionError as e:
        return _redirect_result(err=str(e))
    except Exception:
        return _redirect_result(err=_ERR_GENERIC)
    finally:
        conn.close()
    return _redirect_result(msg="Đã khoá tài khoản.")


@admin_google_users_bp.route("/admin/users/google/reactivate", methods=["POST"])
def reactivate():
    admin_id, err = _current_admin_actor()
    if err:
        return err
    if not _check_csrf():
        return _ERR_CSRF, 400

    expected_auth_version = session.get("auth_version")
    target_id = _parse_user_id()
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                acquire_last_admin_lock(cur)
                revalidate_actor(cur, admin_id, expected_auth_version)

                if target_id is None:
                    raise _ActionError(_ERR_GENERIC)

                cur.execute(
                    "SELECT account_status, google_sub FROM app_users "
                    "WHERE id = %s AND auth_provider = 'GOOGLE' FOR UPDATE",
                    (target_id,),
                )
                row = cur.fetchone()
                if row is None or row[0] != "SUSPENDED":
                    raise _ActionError(_ERR_NOT_SUSPENDED)

                new_status = "ACTIVE" if row[1] else "INVITED"
                cur.execute(
                    "UPDATE app_users SET account_status = %s, "
                    "auth_version = auth_version + 1 WHERE id = %s",
                    (new_status, target_id),
                )
                _write_admin_audit(cur, actor_user_id=admin_id, target_user_id=target_id,
                                    outcome="SUCCESS", reason_code="USER_REACTIVATED")
    except _ActionError as e:
        return _redirect_result(err=str(e))
    except Exception:
        return _redirect_result(err=_ERR_GENERIC)
    finally:
        conn.close()
    return _redirect_result(msg="Đã kích hoạt lại tài khoản.")


@admin_google_users_bp.route("/admin/users/google/revoke-sessions", methods=["POST"])
def revoke_sessions():
    admin_id, err = _current_admin_actor()
    if err:
        return err
    if not _check_csrf():
        return _ERR_CSRF, 400

    expected_auth_version = session.get("auth_version")
    target_id = _parse_user_id()
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                acquire_last_admin_lock(cur)
                revalidate_actor(cur, admin_id, expected_auth_version)

                if target_id is None:
                    raise _ActionError(_ERR_GENERIC)

                cur.execute(
                    "SELECT id FROM app_users WHERE id = %s AND auth_provider = 'GOOGLE' FOR UPDATE",
                    (target_id,),
                )
                if cur.fetchone() is None:
                    raise _ActionError(_ERR_GENERIC)

                # Only bumps auth_version -- account_status is untouched.
                cur.execute(
                    "UPDATE app_users SET auth_version = auth_version + 1 WHERE id = %s",
                    (target_id,),
                )
                _write_admin_audit(cur, actor_user_id=admin_id, target_user_id=target_id,
                                    outcome="SUCCESS", reason_code="USER_SESSIONS_REVOKED")
    except _ActionError as e:
        return _redirect_result(err=str(e))
    except Exception:
        return _redirect_result(err=_ERR_GENERIC)
    finally:
        conn.close()
    return _redirect_result(msg="Đã vô hiệu hoá các phiên đăng nhập cũ.")
