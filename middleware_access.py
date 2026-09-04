"""
Giới hạn truy cập theo IP văn phòng + chính sách IP theo team (Phase 6A,
vá Fix1: đóng lỗi fail-open khi đọc chính sách/rule lỗi).

Thứ tự bắt buộc: `search.py` phải đăng ký `session_security.init_app(app)`
TRƯỚC `register_ip_access_control(app, ...)`. Flask chạy các `before_request`
theo đúng thứ tự đăng ký, nên khi request tới đây, một trong hai điều đã
đúng: (a) endpoint được miễn (xem `PRE_AUTH_EXEMPT_ENDPOINTS`), hoặc (b)
`session_security.enforce_session_validity` đã xác nhận `session["user_id"]`
(nếu có) vẫn `ACTIVE` và khớp `auth_version` -- nếu không, request đã bị
chặn/redirect (hoặc session đã bị `session.clear()`) ở đó rồi và không bao
giờ chạy tới hàm này. Nhờ vậy hàm dưới đây có thể tin `session.get(...)`
một cách an toàn: "Session phải được xác minh trước khi dùng quyền/bypass"
-- kể cả khi cookie cũ của client vẫn còn mang `ip_bypass_allowlist=True`
hoặc `team_id` của một chính sách rộng, một session đã bị thu hồi
(auth_version lệch / account không ACTIVE) sẽ bị chặn ở hook đó TRƯỚC khi
tới đây, nên không bao giờ "còn bypass" thật.

Ba chế độ chính sách IP của TEAM (cột `teams.ip_policy`, migration 015),
đọc MỚI từ DB mỗi request (không cache trong session) để đổi chính sách
team có hiệu lực ngay từ request tiếp theo:
  - INHERIT (mặc định, và luôn áp dụng cho admin / khách chưa đăng nhập):
    hành vi cũ -- không rule nào (env trống + bảng trống, ĐỌC THÀNH CÔNG
    và thực sự không có dòng nào) thì không chặn; có rule thì phải khớp,
    trừ khi có ngoại lệ cá nhân (`ip_bypass_allowlist` hoặc
    `IP_ALLOWLIST_BYPASS_USERS`).
  - ALLOWLIST_ONLY: chỉ IP khớp rule (env hoặc DB) mới vào được; KHÔNG có
    rule nào (đọc thành công, thực sự 0 dòng) thì từ chối (không ngầm mở
    mọi IP); ngoại lệ cá nhân KHÔNG áp dụng ở mode này.
  - ANY_AUTHENTICATED: mọi IP, nhưng chỉ khi request đã đăng nhập với tài
    khoản hợp lệ (đảm bảo bởi session_security ở trên) -- không mở truy
    cập ẩn danh. Vì `_resolve_effective_policy` chỉ tra chính sách team
    khi `session["authenticated"]` đã True, một request ẩn danh (hoặc một
    cookie bị giả mạo có `team_id` nhưng không có `authenticated=True`)
    KHÔNG BAO GIỜ nhận được chính sách này -- nó luôn rơi về INHERIT.

QUAN TRỌNG (Fix1) -- phân biệt RÕ hai loại "không có rule":
  1. Đọc DB THÀNH CÔNG, kết quả thực sự là danh sách/giá trị rỗng-hợp-lệ
     (VD: bảng `office_ip_allowlist` có 0 dòng active, hoặc
     `teams.ip_policy` đọc được đúng giá trị `'INHERIT'`) -- đây là dữ
     liệu hợp lệ, xử lý theo đúng contract của từng mode ở trên.
  2. Đọc DB THẤT BẠI vì BẤT KỲ lý do gì -- mất kết nối, cột/bảng chưa tồn
     tại (migration 015 chưa apply), team đã bị xoá, giá trị policy lạ --
     đây KHÔNG BAO GIỜ được coi là "trường hợp (1)" hay quy về bất kỳ giá
     trị chính sách cụ thể nào (kể cả INHERIT). `_load_db_cidrs` và
     `_load_team_ip_policy` đều raise `_PolicyUnavailableError` cho MỌI
     lỗi đọc, không còn nhánh nào swallow lỗi thành `[]`/`"INHERIT"` nữa.
     `_restrict_office_ip` bắt riêng lỗi này và trả **503** (dependency/
     schema không sẵn sàng) với thông báo chung, không lộ SQL/DSN/traceback
     -- KHÁC với **403** (chính sách đã đọc được rõ ràng, chỉ là IP/trạng
     thái này không được phép).

  Staff (non-admin) không có `team_id` hợp lệ trong session, hoặc
  `team_id` trỏ tới một team đã bị xoá, đều rơi vào nhánh lỗi (2) --
  KHÔNG bao giờ tự động lùi về INHERIT (có thể lỏng hơn ý định) chỉ vì
  thiếu dữ liệu. Chỉ admin/anonymous mới có INHERIT là một giá trị "mặc
  định hợp lệ theo model" (họ vốn không có team).

Biến môi trường:
  DISABLE_IP_ALLOWLIST=1   — tắt hoàn toàn (khuyên dùng trên máy dev)
  OFFICE_IP_ALLOWLIST      — ví dụ: 203.0.113.10,192.0.2.0/24

Địa chỉ khách dùng để so khớp rule LUÔN là `request.remote_addr` SAU khi đã
qua `ProxyFix` (đã cấu hình đúng 1 hop tin cậy trong `search.py`) -- không
tự đọc trực tiếp header `X-Forwarded-For` ở đây lần thứ hai, vì header đó
do client gửi và có thể bị giả mạo nếu tự parse lại bỏ qua cơ chế
trusted-hop của ProxyFix. Với `x_for=1`, ProxyFix tin đúng MỘT giá trị --
giá trị gần server nhất (bên phải nhất sau khi tách dấu phẩy) -- nên nếu
client tự chèn thêm một IP giả ở đầu chuỗi `X-Forwarded-For` (hy vọng được
coi là IP nội bộ), Nginx (1 hop) vẫn nối IP thật của client vào bên phải và
ProxyFix vẫn chỉ lấy giá trị đó, không lấy giá trị client tự chèn. Đây
không phải tuyên bố "mọi client không thể giả mạo trong mọi cấu hình" --
nếu ai đó gọi thẳng vào tiến trình Flask/Gunicorn (bỏ qua Nginx), giả định
một-hop tin cậy có thể bị phá; việc đó thuộc phạm vi firewall/VPS, không
sửa ở lượt này.
"""

from __future__ import annotations

import ipaddress
import os

from flask import abort, request, session

from db import get_connection
from session_security import PRE_AUTH_EXEMPT_ENDPOINTS

_VALID_TEAM_IP_POLICIES = frozenset({"INHERIT", "ALLOWLIST_ONLY", "ANY_AUTHENTICATED"})

# Thông báo CHUNG duy nhất khi không thể xác định chính sách/rule -- không
# bao giờ lộ SQL, DSN, hay nội dung exception ra response; chi tiết thật
# chỉ ghi vào app.logger (server-side) ở nơi bắt _PolicyUnavailableError.
_ERR_POLICY_UNAVAILABLE = "Không thể xác thực quyền truy cập lúc này. Vui lòng thử lại sau."


def _client_ip() -> str:
    """Trust ONLY the address ProxyFix already resolved for this request
    (`x_for=1` in `search.py` -- exactly one hop, i.e. the app's own Nginx).
    Never re-parse `X-Forwarded-For` directly here: that header is
    client-supplied, and taking its first/leftmost entry ourselves would
    let a client set an arbitrary "trusted" IP that ProxyFix's trusted-hop
    logic was specifically designed to prevent.
    """
    return (request.remote_addr or "").strip()


def _parse_env_allowlist() -> list[str]:
    raw = (os.environ.get("OFFICE_IP_ALLOWLIST") or "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def _parse_bypass_users() -> set[str]:
    raw = (os.environ.get("IP_ALLOWLIST_BYPASS_USERS") or "").strip()
    if not raw:
        return set()
    return {x.strip().lower() for x in raw.split(",") if x and x.strip()}


def _ip_matches_rule(ip_str: str, cidr_or_ip: str) -> bool:
    cidr_or_ip = (cidr_or_ip or "").strip()
    if not cidr_or_ip or not ip_str:
        return False
    try:
        ip = ipaddress.ip_address(ip_str)
        net = ipaddress.ip_network(cidr_or_ip, strict=False)
        return ip in net
    except ValueError:
        return ip_str == cidr_or_ip


class _PolicyUnavailableError(Exception):
    """Fix1: raised by `_load_db_cidrs` / `_load_team_ip_policy` /
    `_resolve_effective_policy` for EVERY failure to determine a policy or
    rule set -- lost DB connection, missing table/column (migration not
    applied), a team row that no longer exists, an unexpected stored
    value, or a staff session with no usable team_id.

    Deliberately a single generic type for all of these: the caller
    (`_restrict_office_ip`) must react to ALL of them the exact same way
    -- deny with 503 (dependency/schema unavailable) -- and must NEVER
    treat any of them as equivalent to "read succeeded, list/value is
    empty/INHERIT" (which is a completely different, valid state handled
    by the normal return path of those functions, never by raising).
    """


def _load_db_cidrs() -> list[str]:
    """Global office-IP rules (env is handled separately by the caller).
    On success, returns the REAL list -- which may legitimately be empty
    (zero active rows is an ordinary, valid state, not an error). Any
    failure to read -- connection lost, table missing, permission denied,
    anything -- raises `_PolicyUnavailableError` instead of returning
    `[]`. Callers must never conflate the two: only a genuine empty *read*
    is safe to treat as "no rule configured" (fail-open is the explicit,
    documented INHERIT contract for a REAL empty list; it must never be
    the accidental result of a read that never actually happened).
    """
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cidr FROM office_ip_allowlist WHERE is_active = TRUE AND cidr IS NOT NULL AND TRIM(cidr) <> ''"
                )
                return [r[0].strip() for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as exc:
        raise _PolicyUnavailableError(f"office_ip_allowlist read failed: {exc}") from exc


def _load_team_ip_policy(team_id) -> str:
    """Fresh per-request DB read (never cached in session) of ONE team's
    `ip_policy`, so a change made on the Team admin page takes effect on
    the very next request.

    Fix1: NO fallback to "INHERIT" for a missing column/table anymore.
    Every failure -- connection lost, `teams.ip_policy` column/table
    missing (migration 015 not applied), the team row no longer existing
    (deleted team), or a stored value outside the 3 known enum values --
    raises `_PolicyUnavailableError`. "INHERIT" is only ever returned here
    when a REAL row with `ip_policy = 'INHERIT'` was actually read --
    never as a stand-in for "couldn't tell". Silently returning "INHERIT"
    for "the column doesn't exist" or "the team was deleted" would let a
    staff account whose team disappeared (or whose DB is mid-migration)
    quietly inherit the broadest legacy policy instead of being denied --
    exactly the "staff must not self-upgrade to a broader policy on a
    missing/invalid team" failure this function exists to prevent.
    """
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT ip_policy FROM teams WHERE id = %s", (team_id,))
                row = cur.fetchone()
        finally:
            conn.close()
    except Exception as exc:
        raise _PolicyUnavailableError(f"teams.ip_policy read failed: {exc}") from exc

    if row is None:
        raise _PolicyUnavailableError(f"team_id {team_id!r} not found (deleted team?)")
    policy = (row[0] or "").strip().upper()
    if policy not in _VALID_TEAM_IP_POLICIES:
        raise _PolicyUnavailableError(f"unexpected ip_policy value: {policy!r}")
    return policy


def _resolve_effective_policy() -> str:
    """Which of the 3 policies applies to the CURRENT request's session.

    - Anonymous (`session["authenticated"]` not True) or admin: always
      INHERIT -- this IS a valid, final INHERIT (by definition of the
      team model: neither has a team to look up), never a fallback for a
      failed read. Admins are never auto-exempt from IP checks just for
      being admin -- INHERIT itself still enforces whatever env/DB rules
      are configured.
    - Authenticated, non-admin, WITH a `team_id`: that team's own
      `ip_policy`, read fresh from DB via `_load_team_ip_policy` (may
      raise `_PolicyUnavailableError`, propagated as-is).
    - Authenticated, non-admin, WITHOUT a `team_id`: violates the team
      model's own invariant (every staff account must belong to a team);
      can only happen from a data/session inconsistency, e.g. a stale/
      forged cookie. Fix1: raises `_PolicyUnavailableError` here too --
      never silently returns INHERIT, which could be a broader policy
      than whatever the (missing) team would have had.

    Checking `authenticated` FIRST (before ever looking at `team_id`) also
    means a forged/leftover cookie that carries a `team_id` but not
    `authenticated=True` can never reach `ANY_AUTHENTICATED` (or any
    team-specific policy) -- it is always evaluated as anonymous INHERIT.
    """
    if not session.get("authenticated"):
        return "INHERIT"
    if session.get("is_admin"):
        return "INHERIT"
    team_id = session.get("team_id")
    if team_id is None:
        raise _PolicyUnavailableError("authenticated non-admin session has no team_id")
    return _load_team_ip_policy(team_id)


def register_ip_access_control(app, base_path=None):
    if base_path:
        app.logger.debug("register_ip_access_control: base_path=%s", base_path)

    @app.before_request
    def _restrict_office_ip():
        if os.environ.get("DISABLE_IP_ALLOWLIST", "").lower() in ("1", "true", "yes", "on"):
            return None
        if request.endpoint == "static" or request.path.startswith("/static"):
            return None
        # Cùng bộ endpoint session_security coi là "phải tiếp cận được
        # trước khi có phiên hợp lệ": login, logout, Google entry/callback.
        # So khớp CHÍNH XÁC theo endpoint (không suy đoán theo substring
        # đường dẫn) -- một route nào đó chỉ vì path *chứa* "/login" hay
        # "/google/..." không tự động được miễn; chỉ path "/login" đúng
        # (bản thân route login không thuộc blueprint nào, không có
        # prefix) mới cần so thêm ngoài endpoint, để chịu được dấu "/"
        # cuối URL.
        path = (request.path or "").rstrip("/")
        if request.endpoint in PRE_AUTH_EXEMPT_ENDPOINTS or path == "/login":
            return None

        try:
            policy = _resolve_effective_policy()
        except _PolicyUnavailableError as exc:
            # Fix1: KHÔNG BAO GIỜ coi lỗi đọc chính sách là "cho qua" hay
            # quy về một chính sách cụ thể -- 503 (dependency/schema chưa
            # sẵn sàng), khác với 403 (chính sách đã đọc rõ, IP này không
            # được phép). Không lộ SQL/DSN/traceback ra response.
            app.logger.warning("IP/team policy unavailable, denying request: %s path=%s", exc, request.path)
            abort(503, description=_ERR_POLICY_UNAVAILABLE)

        client = _client_ip()
        authenticated = bool(session.get("authenticated"))

        if policy == "ANY_AUTHENTICATED":
            # Reaching this hook for a non-exempt endpoint with
            # `authenticated=True` already implies session_security's
            # per-request liveness check (registered BEFORE this hook)
            # passed for this exact request -- ACTIVE account, matching
            # auth_version. A genuinely anonymous request (or one with a
            # forged team_id but no authenticated=True) never even
            # resolves to this policy -- see `_resolve_effective_policy`.
            # This check is kept anyway as defense-in-depth.
            if authenticated:
                return None
            abort(403)

        if policy == "ALLOWLIST_ONLY":
            try:
                rules = _parse_env_allowlist() + _load_db_cidrs()
            except _PolicyUnavailableError as exc:
                app.logger.warning("ALLOWLIST_ONLY rules unavailable, denying request: %s path=%s", exc, request.path)
                abort(503, description=_ERR_POLICY_UNAVAILABLE)
            if not rules:
                # Explicit mode, rules read SUCCESSFULLY and are genuinely
                # empty => deny. Never the implicit "no rule => allow"
                # behaviour INHERIT has, and never conflated with a read
                # failure (that path already returned 503 above).
                app.logger.warning("IP denied (ALLOWLIST_ONLY, no rules configured): path=%s", request.path)
                abort(403)
            for rule in rules:
                if _ip_matches_rule(client, rule):
                    return None
            app.logger.warning("IP denied (ALLOWLIST_ONLY): %s path=%s", client, request.path)
            abort(403)

        # policy == "INHERIT" -- legacy global behaviour, unchanged
        # contract, but the CIDR read itself no longer fails open.
        try:
            env_rules = _parse_env_allowlist()
            db_rules = _load_db_cidrs()
        except _PolicyUnavailableError as exc:
            app.logger.warning("INHERIT rules unavailable, denying request: %s path=%s", exc, request.path)
            abort(503, description=_ERR_POLICY_UNAVAILABLE)

        if not env_rules and not db_rules:
            # Genuinely zero rules configured (both reads succeeded) --
            # the documented dev-friendly INHERIT contract: allow.
            return None

        if authenticated and session.get("ip_bypass_allowlist"):
            return None
        if authenticated:
            bypass_users = _parse_bypass_users()
            if bypass_users:
                current_user = str(session.get("username") or "").strip().lower()
                if current_user and current_user in bypass_users:
                    return None

        for rule in env_rules:
            if _ip_matches_rule(client, rule):
                return None
        for rule in db_rules:
            if _ip_matches_rule(client, rule):
                return None

        app.logger.warning("IP denied: %s path=%s", client, request.path)
        abort(403)
