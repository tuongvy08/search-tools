"""Phase 6A: Team CRUD + brand assignment + IP policy management.

Kept in its own module (same rationale as `auth_google.py` /
`session_security.py` / `admin_google_users.py` -- see their module
docstrings). This module owns:
- the GET `/admin/teams` page: team list (name, member count, assigned
  brands, IP policy) + create-team form + per-team rename/permission-edit
  forms, and
- the permission-change preview -> confirm flow: editing a team's brand
  set or IP policy NEVER applies immediately. It first computes a diff
  (brands added/removed, IP policy old -> new, affected members) and
  stores it server-side (in-memory, like `search.py`'s own
  `IMPORT_PREVIEWS`) behind a one-time token; only `confirm_permissions`
  actually writes anything, and ONLY if the team's `updated_at` stamp still
  matches what the preview captured (otherwise: reject, require a fresh
  preview -- never silently apply a diff computed against stale data).

Team membership brand visibility (`search.py`'s `_visibility_sql`) already
reads `team_brands` fresh on every request via a live subquery -- so once
`confirm_permissions` commits, every team member's very next request sees
the new brand set / IP policy immediately, with no session/cookie caching
and no need to log out. Only a per-USER team/role reassignment needs an
`auth_version` bump (done in `search.py`'s legacy form and
`admin_google_users.update()`), because THAT is cached in the session.

Every mutation here is: admin-guarded, CSRF-checked, wrapped in exactly one
DB transaction, and reuses `admin_google_users.acquire_last_admin_lock` /
`revalidate_actor` for the same defense-in-depth every other admin
user-management mutation already has (consistent lock order, actor
re-checked fresh under the lock). Brand selection is ALWAYS constrained to
brands that already exist in `products` (fetched fresh, never a free-text
field) -- a request naming a brand outside that set (forged field, stale
checkbox, typo) REJECTS THE WHOLE request (Phase 6A-Fix2: previously this
silently dropped the unknown value and saved the rest, which could mislead
an admin into thinking a brand was applied when it silently wasn't; now
`_validate_brands` raises and nothing is written).

A preview token (see `preview_permissions`/`confirm_permissions` below) is
also bound to the admin who created it: `confirm_permissions` rejects a
token presented by a DIFFERENT admin session, even a currently-valid one
-- "Preview gắn đúng admin và team". A team's `updated_at` stamp is bumped
not only by this module's own brand/ip_policy writes but also by every
user-team(re)assignment path (`search.py`'s legacy create_user/update_user,
`admin_google_users.approve`/`update`) via
`admin_google_users.touch_team_updated_at`, so a change to WHO is a member
of a team also invalidates any in-flight preview for that team, not just a
change to brands/ip_policy.

Never reads/logs password_hash, google_sub, tokens, or raw DB/constraint
error text -- only the fixed, generic Vietnamese strings below.

Phase 6A-UAT: the permission-change preview/confirm token store lives in
Postgres (`team_permission_previews`, migration_016), NOT in a per-process
Python dict. A prior version of this module kept previews in an in-memory
dict (same pattern as `search.py`'s `IMPORT_PREVIEWS`) -- fine for a
single-process dev server, but broken the moment more than one worker
process serves the app: a preview created by one worker is invisible to a
confirm request routed to a different worker, so confirm would wrongly
report "expired" for a token the admin literally just minted. Moving the
store to the same Postgres database every worker already talks to fixes
this with no new infrastructure (no cache/queue service) and keeps the
same one-time-use semantics: `_pop_preview` is a single `DELETE ...
RETURNING`, which Postgres's row-level locking makes atomic across
concurrent requests/processes -- at most one caller ever gets a non-NULL
row back for a given token, exactly like the old `dict.pop(token, None)`
did within a single process.
"""
from uuid import uuid4

from flask import Blueprint, redirect, render_template, request, session, url_for
from psycopg2 import IntegrityError

from admin_google_users import acquire_last_admin_lock, revalidate_actor, write_permission_audit
from db import get_connection
from session_security import verify_csrf_token

admin_teams_bp = Blueprint("admin_teams", __name__)

_ERR_GENERIC = "Không thể thực hiện thao tác. Vui lòng thử lại."
_ERR_CSRF = "Yêu cầu không hợp lệ."
_ERR_NOT_ADMIN = "Chỉ admin mới được thao tác."
_ERR_NO_ACTOR = "Tài khoản quản trị này không thể thực hiện thao tác này."
_ERR_ACTOR_INVALID = "Phiên quản trị không còn hợp lệ, vui lòng đăng nhập lại."
_ERR_MISSING_NAME = "Thiếu tên team."
_ERR_DUPLICATE_NAME = "Tên team đã tồn tại."
_ERR_TEAM_NOT_FOUND = "Không tìm thấy team."
_ERR_BAD_IP_POLICY = "Chính sách IP không hợp lệ."
_ERR_INVALID_BRAND = "Một hoặc nhiều brand đã chọn không hợp lệ. Vui lòng chọn lại từ danh sách."
_ERR_PREVIEW_EXPIRED = "Xem trước đã hết hạn hoặc không tồn tại. Vui lòng xem trước lại."
_ERR_STALE_PREVIEW = "Dữ liệu team đã thay đổi kể từ khi xem trước. Vui lòng xem trước lại."

_VALID_IP_POLICIES = ("INHERIT", "ALLOWLIST_ONLY", "ANY_AUTHENTICATED")
_IP_POLICY_LABELS = {
    "INHERIT": "Kế thừa cấu hình IP hiện tại (mặc định)",
    "ALLOWLIST_ONLY": "Chỉ IP trong danh sách mạng được phép",
    "ANY_AUTHENTICATED": "Mọi IP (chỉ cần tài khoản hợp lệ)",
}

# Preview TTL, enforced in SQL (created_at > NOW() - INTERVAL '<n> seconds')
# by every function below that reads or pops a preview row. Not user input,
# safe to interpolate directly into the query text.
_PREVIEW_TTL_SECONDS = 1800  # 30 minutes
assert isinstance(_PREVIEW_TTL_SECONDS, int) and _PREVIEW_TTL_SECONDS > 0


class _ActionError(Exception):
    """Carries a pre-approved, generic Vietnamese message ONLY."""


def _current_admin_actor():
    """Same contract as `admin_google_users._current_admin_actor` --
    duplicated rather than imported, matching this codebase's existing
    convention (every admin module defines its own guard; see that
    module's and `admin_login_history.py`'s docstrings for why).
    """
    if not session.get("authenticated") or not session.get("is_admin"):
        return None, (_ERR_NOT_ADMIN, 403)
    admin_user_id = session.get("user_id")
    if not admin_user_id:
        return None, (_ERR_NO_ACTOR, 403)
    return admin_user_id, None


def _require_admin_page():
    if not session.get("authenticated"):
        return redirect(url_for("login"))
    if not session.get("is_admin"):
        return "Chỉ admin mới được truy cập.", 403
    return None


def _check_csrf() -> bool:
    return verify_csrf_token(request.form.get("csrf_token", ""))


def _redirect_result(msg=None, err=None, preview=None):
    return redirect(url_for("admin_teams.index", msg=msg, err=err, preview=preview))


def _parse_team_id(raw):
    try:
        return int((raw or "").strip())
    except (TypeError, ValueError):
        return None


def _fetch_distinct_brands(cur) -> list[str]:
    """Every brand a team CAN be assigned -- always sourced from real
    product data, never free text. Same query `search.py`'s admin_users()
    already runs, kept independent on purpose (trivial one-liner; not
    worth a cross-module dependency for this alone).
    """
    cur.execute(
        "SELECT DISTINCT brand FROM products WHERE brand IS NOT NULL AND TRIM(brand) <> '' ORDER BY brand"
    )
    return [r[0] for r in cur.fetchall() if r[0]]


def _validate_brands(submitted, allowed_brands) -> list[str]:
    """Every submitted brand value MUST already exist in the real, current
    brand set (`_fetch_distinct_brands`). Unlike a soft filter, ANY value
    outside that set (forged form field, stale checkbox referencing a
    brand renamed/removed since the page loaded, a client bug) rejects the
    WHOLE request via `_ActionError` -- never silently dropped while the
    rest of the request is still saved, which could mislead an admin into
    thinking a brand assignment took effect when part of it silently
    didn't. Order-independent (checkbox lists are inherently unordered);
    returned sorted for determinism.
    """
    allowed = set(allowed_brands)
    submitted_list = list(submitted or [])
    if any(b not in allowed for b in submitted_list):
        raise _ActionError(_ERR_INVALID_BRAND)
    return sorted(set(submitted_list))


def _fetch_team_brands(cur, team_id) -> list[str]:
    cur.execute("SELECT brand FROM team_brands WHERE team_id = %s ORDER BY brand", (team_id,))
    return [r[0] for r in cur.fetchall()]


def _fetch_team_row(cur, team_id):
    """Returns (id, name, ip_policy, updated_at) or None."""
    cur.execute("SELECT id, name, ip_policy, updated_at FROM teams WHERE id = %s", (team_id,))
    return cur.fetchone()


def _fetch_affected_members(cur, team_id):
    cur.execute(
        """
        SELECT username, email, display_name, auth_provider, account_status
        FROM app_users
        WHERE team_id = %s
        ORDER BY auth_provider, username
        """,
        (team_id,),
    )
    return [
        {
            "label": display_name or email or username,
            "auth_provider": auth_provider,
            "account_status": account_status,
        }
        for (username, email, display_name, auth_provider, account_status) in cur.fetchall()
    ]


def _purge_expired_previews(cur) -> None:
    """Housekeeping only -- deletes previews older than the TTL so the table
    doesn't grow unbounded. Never relied on for correctness: `_fetch_preview`
    / `_pop_preview` independently re-check the same TTL in their own WHERE
    clause, so a not-yet-swept expired row is still treated as absent.
    """
    cur.execute(
        f"DELETE FROM team_permission_previews "
        f"WHERE created_at < NOW() - INTERVAL '{_PREVIEW_TTL_SECONDS} seconds'"
    )


def _insert_preview(cur, *, team_id, new_brands, new_ip_policy, captured_updated_at, created_by) -> str:
    token = uuid4().hex
    cur.execute(
        "INSERT INTO team_permission_previews "
        "(token, team_id, new_brands, new_ip_policy, captured_updated_at, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (token, team_id, new_brands, new_ip_policy, captured_updated_at, created_by),
    )
    return token


def _row_to_preview_record(team_id, new_brands, new_ip_policy, captured_updated_at, created_by) -> dict:
    return {
        "team_id": team_id,
        "new_brands": list(new_brands or []),
        "new_ip_policy": new_ip_policy,
        "captured_updated_at": captured_updated_at,
        "created_by": created_by,
    }


def _fetch_preview(cur, token):
    """Read-only lookup for the GET /admin/teams?preview=<token> redisplay --
    deliberately does NOT consume the token (reloading/bookmarking that page
    must keep showing the same preview until it's confirmed or expires).
    """
    if not token:
        return None
    cur.execute(
        f"SELECT team_id, new_brands, new_ip_policy, captured_updated_at, created_by "
        f"FROM team_permission_previews "
        f"WHERE token = %s AND created_at > NOW() - INTERVAL '{_PREVIEW_TTL_SECONDS} seconds'",
        (token,),
    )
    row = cur.fetchone()
    return None if row is None else _row_to_preview_record(*row)


def _pop_preview(cur, token, admin_id):
    """Atomically consume (delete-and-return) a preview token, but ONLY if
    it belongs to `admin_id`. Backed by Postgres rather than process
    memory, so this is safe both across concurrent requests for the SAME
    token+admin (row-level lock on the DELETE means only one caller ever
    gets a non-NULL row back) and across worker processes (see module
    docstring).

    The `created_by = %s` filter is load-bearing, not just an optimization:
    "Preview gắn đúng admin và team" means a DIFFERENT admin presenting the
    right token must not just fail to confirm it -- they must not be able
    to CONSUME/burn it either. An earlier version deleted unconditionally
    on token match alone and rejected afterwards by comparing
    `created_by` in Python; that meant a wrong-admin confirm attempt (even
    a harmless probe, or someone who merely saw the token over someone's
    shoulder) silently deleted the real owner's row, forcing them to redo
    the preview step with no legitimate access of their own. Filtering by
    `created_by` directly in the DELETE means a wrong-admin attempt matches
    zero rows and the real owner's row is left completely untouched.

    An already-expired token, or a token that exists but belongs to a
    different admin, both simply match nothing here (left for
    `_purge_expired_previews` to sweep later) and are treated identically
    to "token not found" by the caller -- same generic error message
    either way, so this never discloses which case occurred.
    """
    if not token:
        return None
    cur.execute(
        f"DELETE FROM team_permission_previews "
        f"WHERE token = %s AND created_by = %s "
        f"AND created_at > NOW() - INTERVAL '{_PREVIEW_TTL_SECONDS} seconds' "
        f"RETURNING team_id, new_brands, new_ip_policy, captured_updated_at, created_by",
        (token, admin_id),
    )
    row = cur.fetchone()
    return None if row is None else _row_to_preview_record(*row)


# --------------------------------------------------------------------------
# GET /admin/teams
# --------------------------------------------------------------------------

@admin_teams_bp.route("/admin/teams", methods=["GET"])
def index():
    guard = _require_admin_page()
    if guard is not None:
        return guard

    msg = request.args.get("msg")
    err = request.args.get("err")
    teams = []
    distinct_brands = []
    preview_result = None
    preview_token = request.args.get("preview")

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                _purge_expired_previews(cur)

                cur.execute(
                    """
                    SELECT t.id, t.name, t.ip_policy, t.updated_at,
                           (SELECT COUNT(*) FROM app_users a WHERE a.team_id = t.id) AS member_count
                    FROM teams t
                    ORDER BY t.name ASC
                    """
                )
                team_rows = cur.fetchall()
                for (tid, name, ip_policy, updated_at, member_count) in team_rows:
                    teams.append({
                        "id": tid,
                        "name": name,
                        "ip_policy": ip_policy,
                        "ip_policy_label": _IP_POLICY_LABELS.get(ip_policy, ip_policy),
                        "updated_at": updated_at,
                        "member_count": member_count,
                        "brands": _fetch_team_brands(cur, tid),
                    })

                distinct_brands = _fetch_distinct_brands(cur)

                if preview_token:
                    record = _fetch_preview(cur, preview_token)
                    if record is None:
                        err = err or _ERR_PREVIEW_EXPIRED
                    else:
                        team_row = _fetch_team_row(cur, record["team_id"])
                        if team_row is None:
                            err = err or _ERR_TEAM_NOT_FOUND
                        else:
                            (_, team_name, current_ip_policy, current_updated_at) = team_row
                            current_brands = set(_fetch_team_brands(cur, record["team_id"]))
                            new_brands = set(record["new_brands"])
                            preview_result = {
                                "token": preview_token,
                                "team_id": record["team_id"],
                                "team_name": team_name,
                                "brands_added": sorted(new_brands - current_brands),
                                "brands_removed": sorted(current_brands - new_brands),
                                "current_ip_policy": current_ip_policy,
                                "current_ip_policy_label": _IP_POLICY_LABELS.get(current_ip_policy, current_ip_policy),
                                "new_ip_policy": record["new_ip_policy"],
                                "new_ip_policy_label": _IP_POLICY_LABELS.get(record["new_ip_policy"], record["new_ip_policy"]),
                                "ip_policy_changed": record["new_ip_policy"] != current_ip_policy,
                                "affected_members": _fetch_affected_members(cur, record["team_id"]),
                                "stale": current_updated_at != record["captured_updated_at"],
                            }
    except Exception as e:
        err = err or str(e)
    finally:
        conn.close()

    return render_template(
        "admin_teams.html",
        teams=teams,
        distinct_brands=distinct_brands,
        ip_policies=_VALID_IP_POLICIES,
        ip_policy_labels=_IP_POLICY_LABELS,
        preview_result=preview_result,
        message=msg,
        error=err,
    )


# --------------------------------------------------------------------------
# POST actions
# --------------------------------------------------------------------------

@admin_teams_bp.route("/admin/teams/create", methods=["POST"])
def create_team():
    admin_id, err = _current_admin_actor()
    if err:
        return err
    if not _check_csrf():
        return _ERR_CSRF, 400

    expected_auth_version = session.get("auth_version")
    name = (request.form.get("name") or "").strip()
    ip_policy = (request.form.get("ip_policy") or "INHERIT").strip().upper()
    submitted_brands = request.form.getlist("brands")

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                acquire_last_admin_lock(cur)
                revalidate_actor(cur, admin_id, expected_auth_version)

                if not name:
                    raise _ActionError(_ERR_MISSING_NAME)
                if ip_policy not in _VALID_IP_POLICIES:
                    raise _ActionError(_ERR_BAD_IP_POLICY)

                allowed_brands = _fetch_distinct_brands(cur)
                brands = _validate_brands(submitted_brands, allowed_brands)

                try:
                    cur.execute(
                        "INSERT INTO teams (name, ip_policy) VALUES (%s, %s) RETURNING id",
                        (name, ip_policy),
                    )
                except IntegrityError:
                    raise _ActionError(_ERR_DUPLICATE_NAME)
                (team_id,) = cur.fetchone()

                for b in brands:
                    cur.execute(
                        "INSERT INTO team_brands (team_id, brand) VALUES (%s, %s) ON CONFLICT (team_id, brand) DO NOTHING",
                        (team_id, b),
                    )

                write_permission_audit(cur, actor_user_id=admin_id, target_team_id=team_id,
                                        reason_code="TEAM_CREATED")
    except _ActionError as e:
        return _redirect_result(err=str(e))
    except Exception:
        return _redirect_result(err=_ERR_GENERIC)
    finally:
        conn.close()
    return _redirect_result(msg="Đã tạo team.")


@admin_teams_bp.route("/admin/teams/rename", methods=["POST"])
def rename_team():
    admin_id, err = _current_admin_actor()
    if err:
        return err
    if not _check_csrf():
        return _ERR_CSRF, 400

    expected_auth_version = session.get("auth_version")
    team_id = _parse_team_id(request.form.get("team_id"))
    new_name = (request.form.get("name") or "").strip()

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                acquire_last_admin_lock(cur)
                revalidate_actor(cur, admin_id, expected_auth_version)

                if team_id is None:
                    raise _ActionError(_ERR_TEAM_NOT_FOUND)
                if not new_name:
                    raise _ActionError(_ERR_MISSING_NAME)

                cur.execute("SELECT id FROM teams WHERE id = %s FOR UPDATE", (team_id,))
                if cur.fetchone() is None:
                    raise _ActionError(_ERR_TEAM_NOT_FOUND)

                # Rename only -- ID and every relation (team_brands,
                # app_users.team_id) are untouched. Deliberately does NOT
                # bump `updated_at`: that stamp is reserved for
                # permission-relevant changes (brands/ip_policy) so an
                # in-flight permission-change preview for this team isn't
                # invalidated by an unrelated rename.
                try:
                    cur.execute("UPDATE teams SET name = %s WHERE id = %s", (new_name, team_id))
                except IntegrityError:
                    raise _ActionError(_ERR_DUPLICATE_NAME)

                write_permission_audit(cur, actor_user_id=admin_id, target_team_id=team_id,
                                        reason_code="TEAM_RENAMED")
    except _ActionError as e:
        return _redirect_result(err=str(e))
    except Exception:
        return _redirect_result(err=_ERR_GENERIC)
    finally:
        conn.close()
    return _redirect_result(msg="Đã đổi tên team.")


@admin_teams_bp.route("/admin/teams/preview", methods=["POST"])
def preview_permissions():
    """Read-only: computes a diff and stores it behind a token. Never
    mutates `teams`/`team_brands` -- see `confirm_permissions` for the
    only path that actually writes.
    """
    admin_id, err = _current_admin_actor()
    if err:
        return err
    if not _check_csrf():
        return _ERR_CSRF, 400

    team_id = _parse_team_id(request.form.get("team_id"))
    ip_policy = (request.form.get("ip_policy") or "INHERIT").strip().upper()
    submitted_brands = request.form.getlist("brands")

    if team_id is None:
        return _redirect_result(err=_ERR_TEAM_NOT_FOUND)
    if ip_policy not in _VALID_IP_POLICIES:
        return _redirect_result(err=_ERR_BAD_IP_POLICY)

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                team_row = _fetch_team_row(cur, team_id)
                if team_row is None:
                    raise _ActionError(_ERR_TEAM_NOT_FOUND)
                (_, _name, _current_policy, current_updated_at) = team_row
                allowed_brands = _fetch_distinct_brands(cur)
                brands = _validate_brands(submitted_brands, allowed_brands)

                _purge_expired_previews(cur)
                token = _insert_preview(
                    cur,
                    team_id=team_id,
                    new_brands=brands,
                    new_ip_policy=ip_policy,
                    captured_updated_at=current_updated_at,
                    created_by=admin_id,
                )
    except _ActionError as e:
        return _redirect_result(err=str(e))
    except Exception:
        return _redirect_result(err=_ERR_GENERIC)
    finally:
        conn.close()
    return _redirect_result(preview=token)


@admin_teams_bp.route("/admin/teams/confirm", methods=["POST"])
def confirm_permissions():
    admin_id, err = _current_admin_actor()
    if err:
        return err
    if not _check_csrf():
        return _ERR_CSRF, 400

    expected_auth_version = session.get("auth_version")
    token = (request.form.get("preview_token") or "").strip()

    conn = get_connection()
    try:
        # Pop the preview in its OWN, immediately-committed transaction --
        # same "consumed unconditionally, regardless of what happens next"
        # contract the old `dict.pop(token, None)` had (a stale/wrong-admin
        # confirm attempt still burns the token, forcing a fresh preview).
        # Backed by Postgres now (see module docstring) instead of a
        # per-process dict, so this is also safe across worker processes.
        with conn:
            with conn.cursor() as cur:
                # "Preview gắn đúng admin và team" -- the `created_by`
                # filter is IN the DELETE itself (see `_pop_preview`), so a
                # token minted for a DIFFERENT admin session matches zero
                # rows here: not confirmable AND not consumed, leaving the
                # real owner's row untouched for them to still confirm.
                record = _pop_preview(cur, token, admin_id)
        if record is None:
            return _redirect_result(err=_ERR_PREVIEW_EXPIRED)

        team_id = record["team_id"]
        new_brands = set(record["new_brands"])
        new_ip_policy = record["new_ip_policy"]

        with conn:
            with conn.cursor() as cur:
                acquire_last_admin_lock(cur)
                revalidate_actor(cur, admin_id, expected_auth_version)

                cur.execute(
                    "SELECT id, ip_policy, updated_at FROM teams WHERE id = %s FOR UPDATE",
                    (team_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise _ActionError(_ERR_TEAM_NOT_FOUND)
                (_, current_ip_policy, current_updated_at) = row

                # The authoritative stale check -- "Giữ quyền cũ cho tới
                # khi admin xác nhận thay đổi" + "không ghi đè một cấu
                # hình mới hơn". If someone else changed this team's
                # brands/ip_policy since the preview was built, refuse and
                # require a fresh preview instead of applying a diff
                # computed against data that no longer exists.
                if current_updated_at != record["captured_updated_at"]:
                    raise _ActionError(_ERR_STALE_PREVIEW)

                current_brands = set(_fetch_team_brands(cur, team_id))
                added = sorted(new_brands - current_brands)
                removed = sorted(current_brands - new_brands)
                ip_policy_changed = new_ip_policy != current_ip_policy
                brands_changed = bool(added or removed)

                if not brands_changed and not ip_policy_changed:
                    # Nothing to do -- succeed as a no-op, no audit noise.
                    pass
                else:
                    if removed:
                        cur.execute(
                            "DELETE FROM team_brands WHERE team_id = %s AND brand = ANY(%s)",
                            (team_id, removed),
                        )
                    for b in added:
                        cur.execute(
                            "INSERT INTO team_brands (team_id, brand) VALUES (%s, %s) ON CONFLICT (team_id, brand) DO NOTHING",
                            (team_id, b),
                        )
                    if ip_policy_changed:
                        cur.execute("UPDATE teams SET ip_policy = %s, updated_at = NOW() WHERE id = %s",
                                    (new_ip_policy, team_id))
                    else:
                        cur.execute("UPDATE teams SET updated_at = NOW() WHERE id = %s", (team_id,))

                    if brands_changed:
                        write_permission_audit(cur, actor_user_id=admin_id, target_team_id=team_id,
                                                reason_code="TEAM_BRANDS_UPDATED")
                    if ip_policy_changed:
                        write_permission_audit(cur, actor_user_id=admin_id, target_team_id=team_id,
                                                reason_code="TEAM_IP_POLICY_UPDATED")
    except _ActionError as e:
        return _redirect_result(err=str(e))
    except Exception:
        return _redirect_result(err=_ERR_GENERIC)
    finally:
        conn.close()
    return _redirect_result(msg="Đã áp dụng thay đổi quyền team.")
