"""Phase 6C0: reversible LOCAL-user and Team lifecycle actions.

All writes are POST+CSRF, serialized with the existing admin advisory lock,
revalidate the actor from PostgreSQL, preserve rows/FKs, and emit minimal audit
reason codes. Team archive previews are PostgreSQL-backed and one-time-use.
"""
from uuid import uuid4

from flask import Blueprint, redirect, request, session, url_for

from admin_google_users import (
    acquire_last_admin_lock,
    revalidate_actor,
    touch_team_updated_at,
    write_permission_audit,
)
from db import get_connection
from session_security import verify_csrf_token


admin_lifecycle_bp = Blueprint("admin_lifecycle", __name__)

_TTL_SECONDS = 1800
_ERR_GENERIC = "Không thể thực hiện thao tác. Vui lòng thử lại."
_ERR_CSRF = "Yêu cầu không hợp lệ."
_ERR_NOT_ADMIN = "Chỉ admin mới được thao tác."
_ERR_NO_ACTOR = "Tài khoản quản trị này không thể thực hiện thao tác này."
_ERR_USER_NOT_FOUND = "Không tìm thấy tài khoản LOCAL phù hợp."
_ERR_SELF_ARCHIVE = "Không thể tự lưu trữ tài khoản của chính mình."
_ERR_LAST_ADMIN = "Không thể lưu trữ admin đang hoạt động cuối cùng."
_ERR_TEAM_NOT_FOUND = "Không tìm thấy team phù hợp."
_ERR_REPLACEMENT_REQUIRED = "Team đang có thành viên; cần chọn một team đang hoạt động để chuyển toàn bộ thành viên."
_ERR_REPLACEMENT_INVALID = "Team thay thế không hợp lệ hoặc không còn hoạt động."
_ERR_PREVIEW_EXPIRED = "Xem trước đã hết hạn hoặc không tồn tại. Vui lòng xem trước lại."
_ERR_STALE = "Dữ liệu team hoặc số thành viên đã thay đổi. Vui lòng xem trước lại."


class _ActionError(Exception):
    pass


def _actor():
    if not session.get("authenticated") or not session.get("is_admin"):
        return None, (_ERR_NOT_ADMIN, 403)
    actor_id = session.get("user_id")
    if not actor_id:
        return None, (_ERR_NO_ACTOR, 403)
    return actor_id, None


def _parse_id(name):
    try:
        return int((request.form.get(name) or "").strip())
    except (TypeError, ValueError):
        return None


def _users_redirect(*, msg=None, err=None, lifecycle=None):
    return redirect(url_for("admin_users", msg=msg, err=err, lifecycle=lifecycle))


def _teams_redirect(*, msg=None, err=None, lifecycle=None, archive_preview=None):
    return redirect(
        url_for(
            "admin_teams.index",
            msg=msg,
            err=err,
            lifecycle=lifecycle,
            archive_preview=archive_preview,
        )
    )


def _validate_entry():
    actor_id, err = _actor()
    if err:
        return None, err
    if not verify_csrf_token(request.form.get("csrf_token", "")):
        return None, (_ERR_CSRF, 400)
    return actor_id, None


@admin_lifecycle_bp.route("/admin/users/local/archive", methods=["POST"])
def archive_local_user():
    actor_id, err = _validate_entry()
    if err:
        return err
    target_id = _parse_id("user_id")
    if target_id == actor_id:
        return _users_redirect(err=_ERR_SELF_ARCHIVE)

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                acquire_last_admin_lock(cur)
                revalidate_actor(cur, actor_id, session.get("auth_version"))
                cur.execute(
                    "SELECT is_admin, account_status, archived_at FROM app_users "
                    "WHERE id = %s AND auth_provider = 'LOCAL' FOR UPDATE",
                    (target_id,),
                )
                row = cur.fetchone()
                if row is None or row[1] != "ACTIVE" or row[2] is not None:
                    raise _ActionError(_ERR_USER_NOT_FOUND)
                if row[0]:
                    cur.execute(
                        "SELECT COUNT(*) FROM app_users WHERE is_admin = TRUE "
                        "AND account_status = 'ACTIVE' AND archived_at IS NULL AND id <> %s",
                        (target_id,),
                    )
                    if cur.fetchone()[0] == 0:
                        raise _ActionError(_ERR_LAST_ADMIN)
                cur.execute(
                    "UPDATE app_users SET account_status = 'SUSPENDED', archived_at = NOW(), "
                    "archived_by = %s, auth_version = auth_version + 1 WHERE id = %s",
                    (actor_id, target_id),
                )
                write_permission_audit(
                    cur,
                    actor_user_id=actor_id,
                    target_user_id=target_id,
                    target_provider="LOCAL",
                    reason_code="LOCAL_USER_ARCHIVED",
                )
    except _ActionError as exc:
        return _users_redirect(err=str(exc))
    except Exception:
        return _users_redirect(err=_ERR_GENERIC)
    finally:
        conn.close()
    return _users_redirect(msg="Đã lưu trữ tài khoản; mọi phiên cũ đã bị vô hiệu hoá.")


@admin_lifecycle_bp.route("/admin/users/local/restore", methods=["POST"])
def restore_local_user():
    actor_id, err = _validate_entry()
    if err:
        return err
    target_id = _parse_id("user_id")
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                acquire_last_admin_lock(cur)
                revalidate_actor(cur, actor_id, session.get("auth_version"))
                cur.execute(
                    "SELECT is_admin, team_id FROM app_users WHERE id = %s "
                    "AND auth_provider = 'LOCAL' AND archived_at IS NOT NULL FOR UPDATE",
                    (target_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise _ActionError(_ERR_USER_NOT_FOUND)
                is_admin, team_id = row
                if not is_admin:
                    cur.execute(
                        "SELECT 1 FROM teams WHERE id = %s AND lifecycle_status = 'ACTIVE'",
                        (team_id,),
                    )
                    if cur.fetchone() is None:
                        raise _ActionError(_ERR_REPLACEMENT_INVALID)
                cur.execute(
                    "UPDATE app_users SET account_status = 'ACTIVE', archived_at = NULL, "
                    "archived_by = NULL, auth_version = auth_version + 1 WHERE id = %s",
                    (target_id,),
                )
                write_permission_audit(
                    cur,
                    actor_user_id=actor_id,
                    target_user_id=target_id,
                    target_provider="LOCAL",
                    reason_code="LOCAL_USER_RESTORED",
                )
    except _ActionError as exc:
        return _users_redirect(err=str(exc), lifecycle="archived")
    except Exception:
        return _users_redirect(err=_ERR_GENERIC, lifecycle="archived")
    finally:
        conn.close()
    return _users_redirect(msg="Đã khôi phục tài khoản LOCAL.")


def _purge(cur):
    cur.execute(
        f"DELETE FROM team_lifecycle_previews "
        f"WHERE created_at < NOW() - INTERVAL '{_TTL_SECONDS} seconds'"
    )


def fetch_team_archive_preview(cur, token, actor_id):
    """Read a preview for rendering without consuming it; actor-bound."""
    if not token:
        return None
    cur.execute(
        f"""
        SELECT p.team_id, s.name, p.replacement_team_id, r.name,
               p.captured_member_count, p.captured_updated_at,
               s.updated_at, s.lifecycle_status,
               (SELECT COUNT(*) FROM app_users a WHERE a.team_id = s.id)
        FROM team_lifecycle_previews p
        JOIN teams s ON s.id = p.team_id
        LEFT JOIN teams r ON r.id = p.replacement_team_id
        WHERE p.token = %s AND p.created_by = %s
          AND p.created_at > NOW() - INTERVAL '{_TTL_SECONDS} seconds'
        """,
        (token, actor_id),
    )
    row = cur.fetchone()
    if row is None:
        return None
    (team_id, team_name, replacement_id, replacement_name, count,
     captured_updated_at, current_updated_at, status, current_count) = row
    return {
        "token": token,
        "team_id": team_id,
        "team_name": team_name,
        "replacement_team_id": replacement_id,
        "replacement_team_name": replacement_name,
        "member_count": count,
        "stale": status != "ACTIVE" or current_updated_at != captured_updated_at or current_count != count,
    }


@admin_lifecycle_bp.route("/admin/teams/archive/preview", methods=["POST"])
def preview_team_archive():
    actor_id, err = _validate_entry()
    if err:
        return err
    team_id = _parse_id("team_id")
    replacement_id = _parse_id("replacement_team_id")
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                acquire_last_admin_lock(cur)
                revalidate_actor(cur, actor_id, session.get("auth_version"))
                cur.execute(
                    "SELECT updated_at FROM teams WHERE id = %s "
                    "AND lifecycle_status = 'ACTIVE' FOR UPDATE",
                    (team_id,),
                )
                source = cur.fetchone()
                if source is None:
                    raise _ActionError(_ERR_TEAM_NOT_FOUND)
                cur.execute("SELECT COUNT(*) FROM app_users WHERE team_id = %s", (team_id,))
                member_count = cur.fetchone()[0]
                if member_count:
                    if replacement_id is None or replacement_id == team_id:
                        raise _ActionError(_ERR_REPLACEMENT_REQUIRED)
                    cur.execute(
                        "SELECT 1 FROM teams WHERE id = %s AND lifecycle_status = 'ACTIVE'",
                        (replacement_id,),
                    )
                    if cur.fetchone() is None:
                        raise _ActionError(_ERR_REPLACEMENT_INVALID)
                else:
                    replacement_id = None
                _purge(cur)
                token = uuid4().hex
                cur.execute(
                    "INSERT INTO team_lifecycle_previews "
                    "(token, team_id, replacement_team_id, captured_updated_at, "
                    "captured_member_count, created_by) VALUES (%s, %s, %s, %s, %s, %s)",
                    (token, team_id, replacement_id, source[0], member_count, actor_id),
                )
    except _ActionError as exc:
        return _teams_redirect(err=str(exc))
    except Exception:
        return _teams_redirect(err=_ERR_GENERIC)
    finally:
        conn.close()
    return _teams_redirect(archive_preview=token)


def _pop_preview(cur, token, actor_id):
    cur.execute(
        f"DELETE FROM team_lifecycle_previews WHERE token = %s AND created_by = %s "
        f"AND created_at > NOW() - INTERVAL '{_TTL_SECONDS} seconds' "
        "RETURNING team_id, replacement_team_id, captured_updated_at, captured_member_count",
        (token, actor_id),
    )
    return cur.fetchone()


@admin_lifecycle_bp.route("/admin/teams/archive/confirm", methods=["POST"])
def confirm_team_archive():
    actor_id, err = _validate_entry()
    if err:
        return err
    token = (request.form.get("preview_token") or "").strip()
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                preview = _pop_preview(cur, token, actor_id)
        if preview is None:
            return _teams_redirect(err=_ERR_PREVIEW_EXPIRED)
        team_id, replacement_id, captured_updated_at, captured_count = preview

        with conn:
            with conn.cursor() as cur:
                acquire_last_admin_lock(cur)
                revalidate_actor(cur, actor_id, session.get("auth_version"))
                lock_ids = sorted({i for i in (team_id, replacement_id) if i is not None})
                cur.execute(
                    "SELECT id, lifecycle_status, updated_at FROM teams "
                    "WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                    (lock_ids,),
                )
                locked = {row[0]: row[1:] for row in cur.fetchall()}
                source = locked.get(team_id)
                if source is None or source[0] != "ACTIVE":
                    raise _ActionError(_ERR_TEAM_NOT_FOUND)
                if source[1] != captured_updated_at:
                    raise _ActionError(_ERR_STALE)
                cur.execute(
                    "SELECT id FROM app_users WHERE team_id = %s ORDER BY id FOR UPDATE",
                    (team_id,),
                )
                member_ids = [row[0] for row in cur.fetchall()]
                if len(member_ids) != captured_count:
                    raise _ActionError(_ERR_STALE)
                if member_ids:
                    replacement = locked.get(replacement_id)
                    if replacement_id == team_id or replacement is None or replacement[0] != "ACTIVE":
                        raise _ActionError(_ERR_REPLACEMENT_INVALID)
                    cur.execute(
                        "UPDATE app_users SET team_id = %s, auth_version = auth_version + 1 "
                        "WHERE id = ANY(%s)",
                        (replacement_id, member_ids),
                    )
                    touch_team_updated_at(cur, replacement_id)
                cur.execute(
                    "UPDATE teams SET lifecycle_status = 'ARCHIVED', archived_at = NOW(), "
                    "archived_by = %s, updated_at = NOW() WHERE id = %s",
                    (actor_id, team_id),
                )
                write_permission_audit(
                    cur,
                    actor_user_id=actor_id,
                    target_team_id=team_id,
                    reason_code="TEAM_ARCHIVED",
                )
    except _ActionError as exc:
        return _teams_redirect(err=str(exc))
    except Exception:
        return _teams_redirect(err=_ERR_GENERIC)
    finally:
        conn.close()
    msg = f"Đã lưu trữ team và chuyển {captured_count} thành viên."
    return _teams_redirect(msg=msg)


@admin_lifecycle_bp.route("/admin/teams/restore", methods=["POST"])
def restore_team():
    actor_id, err = _validate_entry()
    if err:
        return err
    team_id = _parse_id("team_id")
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                acquire_last_admin_lock(cur)
                revalidate_actor(cur, actor_id, session.get("auth_version"))
                cur.execute(
                    "SELECT id FROM teams WHERE id = %s AND lifecycle_status = 'ARCHIVED' FOR UPDATE",
                    (team_id,),
                )
                if cur.fetchone() is None:
                    raise _ActionError(_ERR_TEAM_NOT_FOUND)
                cur.execute(
                    "UPDATE teams SET lifecycle_status = 'ACTIVE', archived_at = NULL, "
                    "archived_by = NULL, updated_at = NOW() WHERE id = %s",
                    (team_id,),
                )
                write_permission_audit(
                    cur,
                    actor_user_id=actor_id,
                    target_team_id=team_id,
                    reason_code="TEAM_RESTORED",
                )
    except _ActionError as exc:
        return _teams_redirect(err=str(exc), lifecycle="archived")
    except Exception:
        return _teams_redirect(err=_ERR_GENERIC, lifecycle="archived")
    finally:
        conn.close()
    return _teams_redirect(msg="Đã khôi phục team. Thành viên đã chuyển trước đó không tự quay lại.")
