"""Phase 5D3: Admin login-history screen (read-only).

Kept in its own module (same rationale as `auth_google.py` /
`admin_google_users.py` / `session_security.py`). This module owns:
- the GET `/admin/login-history` page: admin-only, staff never sees the nav
  link and is rejected (403) if it hits the route directly,
- server-side pagination (default 50 rows/page, hard max 100) with a
  stable `ORDER BY created_at DESC, id DESC` (secondary key = primary key,
  so ordering is deterministic even when two events share a timestamp),
- server-side validated filters (date range, outcome, event type, account)
  using ONLY parameterized queries, and
- fixed Vietnamese labels for outcome/provider/reason, with a safe
  fallback for any reason code this module doesn't recognize yet (never
  raises, never guesses a meaning).

This module NEVER writes to `login_audit_events` (or any other table) --
every query issued here is a `SELECT`. It never reads/returns Google `sub`,
password_hash, tokens, cookies, session/OAuth state, or raw DB error text.

Phase 5D3 Final correction: event classification (login/logout vs admin
action) is based on `reason_code` membership in the EXPLICIT
`_ADMIN_ACTION_REASON_CODES` set below (the exact 5 codes
`admin_google_users._write_admin_audit` writes), never on
`actor_user_id IS NOT NULL` alone. `actor_user_id` has `ON DELETE SET
NULL` (see migration_014), so a deleted admin's historical audit rows
would read back with `actor_user_id = NULL` while still describing a real
admin action -- using that column as the sole signal would silently
misclassify those rows as login/logout. `reason_code` is never nulled out
by any FK, so it is the stable signal; a present-but-deleted actor is only
used for the "Người thực hiện" display (falls back to "Không xác định"),
never to change the event's classification.

Schema note (Phase 5D3 audit): as of this writing, migration 014 has NOT
been applied to the local app database (`login_audit_events` does not
exist yet, and `app_users` does not yet have `email`/`display_name`/
`account_status`/`auth_version`/etc.). This module is written against the
migration_014 schema (the one `auth_google.py` / `admin_google_users.py` /
`session_security.py` already write to) and is exercised in tests against
a temporary Postgres database with that migration applied -- exactly like
`tests/test_admin_pg_integration.py` already does for the mutation
endpoints. It is NOT this module's job to apply that migration to the real
app database; if the table is missing, the route reports a generic error
instead of crashing or fabricating rows.
"""
import math
from datetime import datetime

from flask import Blueprint, redirect, render_template, request, session, url_for
from psycopg2 import errors as pg_errors

from db import get_connection

admin_login_history_bp = Blueprint("admin_login_history", __name__)

# --------------------------------------------------------------------------
# Fixed choices / validation bounds
# --------------------------------------------------------------------------
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100

# Real values enforced by the `login_audit_events_outcome_check` CHECK
# constraint in migration_014 -- never guessed.
VALID_OUTCOMES = ("SUCCESS", "FAILURE", "PENDING_APPROVAL", "DENIED")

# "OTHER" = a `reason_code` this module doesn't recognize (including NULL).
# Never defaulted to "AUTH" -- see `_classify_event_type`.
VALID_EVENT_TYPES = ("ALL", "AUTH", "ADMIN", "OTHER")

# The exact 5 reason codes `admin_google_users._write_admin_audit` writes
# for an admin lifecycle action (read directly from that module, not
# guessed). This is the ONLY signal used to classify a row as "ADMIN" --
# see the module docstring for why `actor_user_id` alone is unsafe.
_ADMIN_ACTION_REASON_CODES = frozenset({
    "USER_APPROVED",
    "USER_INVITED",
    "USER_SUSPENDED",
    "USER_REACTIVATED",
    "USER_SESSIONS_REVOKED",
})

# Every reason code `auth_google.py` (login attempts) and
# `session_security.py` (logout + forced session revocation) currently
# write. Read directly from those modules, not guessed.
_AUTH_REASON_CODES = frozenset({
    "LOGIN",
    "LOGOUT",
    "NEW_IDENTITY_PENDING",
    "AWAITING_APPROVAL",
    "ACCOUNT_SUSPENDED",
    "UNEXPECTED_STATUS",
    "MISSING_NONCE",
    "TOKEN_INVALID",
    "MISSING_SUB",
    "EMAIL_NOT_VERIFIED",
    "DOMAIN_NOT_ALLOWED",
    "IDENTITY_CONFLICT",
    "ACCOUNT_PROVISION_CONFLICT",
    "ACCOUNT_NOT_FOUND",
    "ACCOUNT_NOT_ACTIVE",
    "AUTH_VERSION_MISMATCH",
    "LEGACY_SESSION_DISABLED",
})


def _classify_event_type(reason_code):
    """Single source of truth for event classification, used identically by
    the SQL filter/count (`_where_clause`) and the row label (`_row_to_event`)
    so a row can never be counted under one bucket and labelled under
    another. Never raises; an unrecognized/NULL reason_code is "OTHER", not
    silently treated as a login.
    """
    if reason_code in _ADMIN_ACTION_REASON_CODES:
        return "ADMIN"
    if reason_code in _AUTH_REASON_CODES:
        return "AUTH"
    return "OTHER"

_VN_TZ = "Asia/Ho_Chi_Minh"
_UNKNOWN_LABEL = "Không xác định"

_OUTCOME_LABELS = {
    "SUCCESS": "Thành công",
    "FAILURE": "Thất bại",
    "PENDING_APPROVAL": "Chờ phê duyệt",
    "DENIED": "Bị từ chối",
}

_PROVIDER_LABELS = {
    "LOCAL": "Mật khẩu nội bộ",
    "GOOGLE": "Google Workspace",
}

# Every reason_code currently written by auth_google.py, session_security.py
# and admin_google_users.py (read directly from those modules -- not
# guessed). Anything not in this map falls back to a safe generic label
# (see `_reason_label`) instead of raising or hiding the row.
_REASON_LABELS = {
    # auth_google.py -- Google login attempts
    "LOGIN": "Đăng nhập thành công",
    "NEW_IDENTITY_PENDING": "Tài khoản Google mới, đang chờ phê duyệt",
    "AWAITING_APPROVAL": "Tài khoản đang chờ phê duyệt",
    "ACCOUNT_SUSPENDED": "Tài khoản đã bị tạm khoá",
    "UNEXPECTED_STATUS": "Trạng thái tài khoản không hợp lệ",
    "MISSING_NONCE": "Phiên đăng nhập Google không hợp lệ hoặc đã hết hạn",
    "TOKEN_INVALID": "Đăng nhập Google không thành công (token không hợp lệ)",
    "MISSING_SUB": "Đăng nhập Google không thành công (thiếu định danh)",
    "EMAIL_NOT_VERIFIED": "Email Google chưa được xác minh",
    "DOMAIN_NOT_ALLOWED": "Tài khoản Google không thuộc tổ chức được phép",
    "IDENTITY_CONFLICT": "Xung đột định danh tài khoản Google",
    "ACCOUNT_PROVISION_CONFLICT": "Xung đột khi tạo tài khoản Google",
    # session_security.py -- logout + forced session revocation
    "LOGOUT": "Đăng xuất",
    "ACCOUNT_NOT_FOUND": "Phiên bị hủy: tài khoản không còn tồn tại",
    "ACCOUNT_NOT_ACTIVE": "Phiên bị hủy: tài khoản không còn hoạt động",
    "AUTH_VERSION_MISMATCH": "Phiên bị hủy: đã bị thu hồi",
    "LEGACY_SESSION_DISABLED": "Phiên bị hủy: đăng nhập cũ đã bị tắt",
    # admin_google_users.py -- admin lifecycle actions (actor_user_id set)
    "USER_APPROVED": "Quản trị: phê duyệt tài khoản",
    "USER_INVITED": "Quản trị: mời tài khoản mới",
    "USER_SUSPENDED": "Quản trị: tạm khoá tài khoản",
    "USER_REACTIVATED": "Quản trị: kích hoạt lại tài khoản",
    "USER_SESSIONS_REVOKED": "Quản trị: vô hiệu hoá phiên đăng nhập cũ",
}

_EVENT_TYPE_LABELS = {
    "AUTH": "Đăng nhập / Đăng xuất",
    "ADMIN": "Hành động quản trị",
    "OTHER": "Sự kiện khác",
}


def _reason_label(reason_code):
    if not reason_code:
        return "—"
    return _REASON_LABELS.get(reason_code, f"{_UNKNOWN_LABEL} ({reason_code})")


def _outcome_label(outcome):
    return _OUTCOME_LABELS.get(outcome, _UNKNOWN_LABEL)


def _provider_label(provider):
    return _PROVIDER_LABELS.get(provider, _UNKNOWN_LABEL)


def _account_label(user_id, username, email, display_name):
    """`user_id IS NULL` (never resolved to an account, or the account was
    since deleted -- migration_014's `user_id` FK is `ON DELETE SET NULL`,
    so a deleted account's historical rows already read back as NULL here)
    and "the join found no matching row" are both treated the same way:
    render "Không xác định", never a raw id/blank cell.
    """
    if user_id is None:
        return _UNKNOWN_LABEL
    label = display_name or email or username
    return label if label else _UNKNOWN_LABEL


# --------------------------------------------------------------------------
# Filter parsing -- every value is validated; nothing free-form ever reaches
# SQL string interpolation (only bound parameters).
# --------------------------------------------------------------------------

def _parse_page(raw):
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 1
    return n if n >= 1 else 1


def _parse_page_size(raw):
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    if n < 1:
        return DEFAULT_PAGE_SIZE
    return min(n, MAX_PAGE_SIZE)


def _parse_date(raw):
    """Returns a validated 'YYYY-MM-DD' string, or None if absent/invalid.
    Invalid input is silently ignored (filter not applied) -- never raises,
    never guesses a different date.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None
    return raw


def _parse_outcome(raw):
    raw = (raw or "").strip().upper()
    return raw if raw in VALID_OUTCOMES else ""


def _parse_event_type(raw):
    raw = (raw or "ALL").strip().upper()
    return raw if raw in VALID_EVENT_TYPES else "ALL"


def _escape_like(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_filters(args):
    return {
        "date_from": _parse_date(args.get("date_from")),
        "date_to": _parse_date(args.get("date_to")),
        "outcome": _parse_outcome(args.get("outcome")),
        "event_type": _parse_event_type(args.get("event_type")),
        "account": (args.get("account") or "").strip()[:255],
        "page": _parse_page(args.get("page")),
        "page_size": _parse_page_size(args.get("page_size")),
    }


def _where_clause(filters):
    clauses = []
    params = []
    if filters["date_from"]:
        clauses.append("e.created_at >= ((%s::timestamp) AT TIME ZONE %s)")
        params.extend([filters["date_from"], _VN_TZ])
    if filters["date_to"]:
        clauses.append("e.created_at < (((%s::timestamp) AT TIME ZONE %s) + interval '1 day')")
        params.extend([filters["date_to"], _VN_TZ])
    if filters["outcome"]:
        clauses.append("e.outcome = %s")
        params.append(filters["outcome"])
    # Same reason_code-based classification as `_classify_event_type` --
    # kept in sync deliberately so filter/count and row labels never
    # disagree about which bucket a row belongs to.
    if filters["event_type"] == "AUTH":
        clauses.append("e.reason_code = ANY(%s)")
        params.append(list(_AUTH_REASON_CODES))
    elif filters["event_type"] == "ADMIN":
        clauses.append("e.reason_code = ANY(%s)")
        params.append(list(_ADMIN_ACTION_REASON_CODES))
    elif filters["event_type"] == "OTHER":
        clauses.append(
            "(e.reason_code IS NULL OR NOT (e.reason_code = ANY(%s) OR e.reason_code = ANY(%s)))"
        )
        params.extend([list(_ADMIN_ACTION_REASON_CODES), list(_AUTH_REASON_CODES)])
    if filters["account"]:
        pattern = "%" + _escape_like(filters["account"]) + "%"
        clauses.append("(au.username ILIKE %s OR au.email ILIKE %s OR au.display_name ILIKE %s)")
        params.extend([pattern, pattern, pattern])
    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where_sql, params


_COUNT_SQL_TEMPLATE = """
    SELECT COUNT(*)
    FROM login_audit_events e
    LEFT JOIN app_users au ON au.id = e.user_id
    {where}
"""

_LIST_SQL_TEMPLATE = """
    SELECT
        e.id,
        to_char(e.created_at AT TIME ZONE %s, 'YYYY-MM-DD HH24:MI:SS') AS ts_vn,
        e.user_id,
        au.username AS account_username,
        au.email AS account_email,
        au.display_name AS account_display_name,
        e.actor_user_id,
        actor.username AS actor_username,
        actor.email AS actor_email,
        actor.display_name AS actor_display_name,
        e.provider,
        e.outcome,
        e.reason_code,
        e.source_ip
    FROM login_audit_events e
    LEFT JOIN app_users au ON au.id = e.user_id
    LEFT JOIN app_users actor ON actor.id = e.actor_user_id
    {where}
    ORDER BY e.created_at DESC, e.id DESC
    LIMIT %s OFFSET %s
"""


def _row_to_event(row):
    (event_id, ts_vn, user_id, account_username, account_email, account_display_name,
     actor_user_id, actor_username, actor_email, actor_display_name,
     provider, outcome, reason_code, source_ip) = row

    event_type = _classify_event_type(reason_code)
    is_admin_action = event_type == "ADMIN"
    account_label = _account_label(user_id, account_username, account_email, account_display_name)
    # Actor label is shown for ADMIN rows regardless of whether the actor
    # account still exists -- `actor_user_id` may already be NULL (deleted
    # actor, ON DELETE SET NULL) while `reason_code` still correctly
    # classifies the row as an admin action; `_account_label` itself falls
    # back to "Không xác định" when `actor_user_id` is None.
    actor_label = _account_label(actor_user_id, actor_username, actor_email, actor_display_name) \
        if is_admin_action else None
    # Admin actions never show a login "phương thức": `provider` on those
    # rows is only ever the literal 'GOOGLE' written unconditionally by
    # `admin_google_users._write_admin_audit` (an artifact of that insert,
    # not a record of how the acting admin actually authenticated) -- never
    # inferred/relabelled from the target's provider either.
    provider_label = "—" if is_admin_action else _provider_label(provider)

    return {
        "id": event_id,
        "time_vn": ts_vn,
        "account": account_label,
        "provider_label": provider_label,
        "outcome_label": _outcome_label(outcome),
        "reason_label": _reason_label(reason_code),
        "source_ip": source_ip or None,
        "event_type": event_type,
        "event_type_label": _EVENT_TYPE_LABELS[event_type],
        "actor": actor_label,
    }


def fetch_login_history(cur, args):
    """Runs exactly two read-only SELECTs (count + page) against
    `login_audit_events`. Never writes anything. Raises the underlying
    psycopg2 error on failure (e.g. table missing) -- the caller decides how
    to present that.
    """
    filters = parse_filters(args)
    where_sql, where_params = _where_clause(filters)

    cur.execute(_COUNT_SQL_TEMPLATE.format(where=where_sql), where_params)
    (total_count,) = cur.fetchone()

    page_size = filters["page_size"]
    total_pages = max(1, math.ceil(total_count / page_size)) if total_count else 1
    page = min(filters["page"], total_pages)
    offset = (page - 1) * page_size

    cur.execute(
        _LIST_SQL_TEMPLATE.format(where=where_sql),
        [_VN_TZ] + where_params + [page_size, offset],
    )
    events = [_row_to_event(row) for row in cur.fetchall()]

    return {
        "events": events,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "filters": filters,
    }


# --------------------------------------------------------------------------
# Route
# --------------------------------------------------------------------------

def _require_admin():
    """Same shape as `search.py`'s `_require_admin_page()` -- duplicated
    rather than imported, to avoid a circular import between this module
    and `search.py` (same tradeoff `admin_google_users.py` already makes
    for its own POST-only guard).
    """
    if not session.get("authenticated"):
        return redirect(url_for("login"))
    if not session.get("is_admin"):
        return "Chỉ admin mới được truy cập.", 403
    return None


_ERR_LOAD_FAILED = (
    "Không thể tải lịch sử đăng nhập (có thể migration cơ sở dữ liệu chưa được áp dụng)."
)


@admin_login_history_bp.route("/admin/login-history", methods=["GET"])
def index():
    guard = _require_admin()
    if guard is not None:
        return guard

    filters = parse_filters(request.args)
    result = None
    load_error = None

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            result = fetch_login_history(cur, request.args)
    except pg_errors.UndefinedTable:
        load_error = _ERR_LOAD_FAILED
    except Exception:
        # Never leak raw DB error text (constraint names, query text, etc.)
        # to the browser -- same convention as admin_google_users.py.
        load_error = _ERR_LOAD_FAILED
    finally:
        conn.close()

    if result is None:
        result = {
            "events": [],
            "total_count": 0,
            "page": 1,
            "page_size": filters["page_size"],
            "total_pages": 1,
            "filters": filters,
        }

    return render_template(
        "admin_login_history.html",
        load_error=load_error,
        valid_outcomes=VALID_OUTCOMES,
        outcome_labels=_OUTCOME_LABELS,
        **result,
    )
