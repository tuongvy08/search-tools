"""Cổng API chỉ đọc cho bot/AI (n8n, chatbot, MCP) -- dịch vụ ĐỘC LẬP.

Chạy riêng (``gunicorn machine_api:app``), tách hoàn toàn khỏi ứng dụng nhân
viên ``search.py``: không dùng session/cookie, không đăng ký hook nào vào ứng
dụng nhân viên, không sửa module nào của nó (chỉ import các hàm thuần để dùng
chung SQL pháp chế, bảng quyền và quy tắc hạn dùng). Thiết kế: docs/machine-api/PLAN.md;
hợp đồng kiểm chứng: specs/machine-api-readonly/VERIFICATION.md.

Bất biến an toàn (mỗi bất biến có claim kiểm chứng tương ứng):
- Chỉ GET. Kết nối PostgreSQL luôn chỉ-đọc, có statement_timeout.
- Giá/ship/ghi chú nội bộ/mã nội bộ KHÔNG BAO GIỜ nằm trong phản hồi: các trường
  này không được SELECT, và bộ lọc cuối ``_assert_clean`` chặn mọi khóa cấm.
- Thẻ (Bearer) chỉ lưu dưới dạng SHA-256 trong file cấu hình ngoài Git; file được
  đọc lại khi đổi nên thu hồi có hiệu lực trong vài giây, không cần khởi động lại.
- Quyền/ team đọc từ database ở MỖI lần gọi; lỗi đọc => 503, không bao giờ cho qua.
- Nhật ký một dòng/lần gọi, không chứa thẻ, từ khóa tìm hay giá.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import stat
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from typing import Any, Optional

import psycopg2
from flask import Flask, Response, g, jsonify, request
from flask.sessions import SessionInterface
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import team_permissions
from compliance_resolver import resolve_compliance_precedence
from regulatory import EXPORT_ALLOW, EXPORT_BLOCK, cas_resolver_lateral, product_resolver_lateral
from stock import expiry_state, literal_like, normalized_text

API_PREFIX = "/machine/v1"

MIN_QUERY_CHARS = 3
MAX_QUERY_CHARS = 100
DEFAULT_LIMIT = 10
MAX_LIMIT = 10
SAME_CAS_LIMIT = 5
STOCK_ITEMS_LIMIT = 10
STATEMENT_TIMEOUT_MS = 3000
CONFIG_RECHECK_SECONDS = 2.0
DEFAULT_RATE_PER_MINUTE = 60
MAX_RATE_PER_MINUTE = 600
MAX_TOKEN_CHARS = 200

# Khóa tuyệt đối không được xuất hiện trong phản hồi (so không phân biệt hoa thường).
FORBIDDEN_KEYS = frozenset({
    "price", "unit_price", "unit_price_available", "ship", "note", "stock_price",
    "stock_price_vnd", "stock_note", "product_id", "stock_item_id", "id",
    "currency_rate_status", "currency_rate_message", "total", "total_price",
})

DISCLAIMER = (
    "Dữ liệu tham khảo từ hệ thống nội bộ; hệ thống chưa lưu số nghị định/căn cứ "
    "pháp lý. Mọi kết luận về nhập khẩu/giấy phép cần xác nhận với bộ phận pháp chế."
)
NO_RULE_MESSAGE = (
    "Chưa có quy tắc nào trong hệ thống cho mục này. Điều đó KHÔNG có nghĩa là "
    "được phép nhập; cần xác nhận với bộ phận pháp chế."
)
DATA_ERROR_NOTE = (
    "Dữ liệu quản lý của sản phẩm này đang cần được kiểm tra; chưa thể xác định, "
    "cần xác nhận với bộ phận pháp chế."
)
PRICE_NOTICE = (
    "API này không cung cấp giá. Nếu khách hỏi giá/báo giá, hãy chuyển khách tới "
    "nhân viên kinh doanh."
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CAS_FORMAT_RE = re.compile(r"^[0-9]{2,7}-[0-9]{2}-[0-9]$")

access_log = logging.getLogger("machine_api.access")
audit_log = logging.getLogger("machine_api")


def _configure_logging() -> None:
    """Dưới Gunicorn không có cấu hình logging nào: nếu không gắn handler thì dòng truy vết
    (mức INFO) bị bỏ. Ghi ra stderr (journald). Dòng truy cập không chứa thẻ/từ khóa/giá."""
    if not audit_log.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        audit_log.addHandler(handler)
    if audit_log.level == logging.NOTSET:
        audit_log.setLevel(logging.INFO)


_configure_logging()


# --------------------------------------------------------------------------
# Cấu hình thẻ
# --------------------------------------------------------------------------

class ConfigUnavailable(Exception):
    """File thẻ không đọc được / hỏng -- từ chối mọi lời gọi dữ liệu (fail-closed)."""


def _parse_expiry(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expires_at")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validate_entry(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("entry")
    token_id = raw.get("id")
    if not isinstance(token_id, str) or not _ID_RE.fullmatch(token_id):
        raise ValueError("id")
    digest = raw.get("sha256")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ValueError("sha256")
    team_id = raw.get("team_id")
    if isinstance(team_id, bool) or not isinstance(team_id, int) or team_id <= 0:
        raise ValueError("team_id")
    expires_at = _parse_expiry(raw.get("expires_at"))
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("enabled")
    stock_detail = raw.get("stock_detail", "status")
    if stock_detail not in ("status", "quantity"):
        raise ValueError("stock_detail")
    rate = raw.get("rate_per_minute", DEFAULT_RATE_PER_MINUTE)
    if isinstance(rate, bool) or not isinstance(rate, int) or not 1 <= rate <= MAX_RATE_PER_MINUTE:
        raise ValueError("rate_per_minute")
    networks = []
    allowed = raw.get("allowed_ips")
    if allowed is not None:
        if not isinstance(allowed, list) or not allowed:
            raise ValueError("allowed_ips")
        for item in allowed:
            if not isinstance(item, str):
                raise ValueError("allowed_ips")
            networks.append(ipaddress.ip_network(item.strip(), strict=False))
    return {
        "id": token_id, "sha256": digest, "team_id": team_id, "expires_at": expires_at,
        "enabled": enabled, "stock_detail": stock_detail, "rate_per_minute": rate,
        "networks": networks,
    }


def load_token_entries(path: str) -> list[dict[str, Any]]:
    """Đọc + kiểm tra file thẻ. Mục sai bị bỏ qua kèm cảnh báo (không kích hoạt)."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except (OSError, ValueError) as exc:
        raise ConfigUnavailable(type(exc).__name__) from None
    if mode & 0o077:
        audit_log.warning("token file permissions too open: %o (expected 0600)", mode)
    tokens = document.get("tokens") if isinstance(document, dict) else None
    if not isinstance(tokens, list):
        raise ConfigUnavailable("tokens")
    valid: list[dict[str, Any]] = []
    for index, raw in enumerate(tokens):
        try:
            valid.append(_validate_entry(raw))
        except (ValueError, TypeError) as exc:
            audit_log.warning("token entry #%d ignored: invalid field %s", index, exc)
    # Trùng id hoặc trùng băm => mơ hồ => bỏ cả hai.
    ids = defaultdict(int)
    digests = defaultdict(int)
    for entry in valid:
        ids[entry["id"]] += 1
        digests[entry["sha256"]] += 1
    unique = []
    for entry in valid:
        if ids[entry["id"]] > 1 or digests[entry["sha256"]] > 1:
            audit_log.warning("token entry %s ignored: duplicate id or hash", entry["id"])
            continue
        unique.append(entry)
    return unique


class TokenStore:
    """Nạp lại file khi đổi (kiểm tra mtime tối đa mỗi CONFIG_RECHECK_SECONDS)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[dict[str, Any]] = []
        self._signature: Optional[tuple[int, int, int]] = None
        self._checked_at = 0.0
        self._error: Optional[str] = "not loaded"

    def reset(self) -> None:
        with self._lock:
            self._entries, self._signature, self._checked_at, self._error = [], None, 0.0, "not loaded"

    def entries(self) -> list[dict[str, Any]]:
        path = os.environ.get("MACHINE_API_TOKENS_FILE", "").strip()
        now = time.monotonic()
        with self._lock:
            if now - self._checked_at < CONFIG_RECHECK_SECONDS and self._error is None:
                return list(self._entries)
            self._checked_at = now
            if not path:
                self._entries, self._error = [], "no path"
                raise ConfigUnavailable("MACHINE_API_TOKENS_FILE")
            try:
                info = os.stat(path)
                signature = (info.st_mtime_ns, info.st_size, info.st_ino)
                if signature != self._signature or self._error is not None:
                    self._entries = load_token_entries(path)
                    self._signature = signature
                self._error = None
            except (OSError, ConfigUnavailable) as exc:
                self._entries, self._signature = [], None
                self._error = type(exc).__name__
                raise ConfigUnavailable(self._error) from None
            return list(self._entries)


token_store = TokenStore()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def find_entry(token: str) -> Optional[dict[str, Any]]:
    """So khớp hằng-thời-gian với MỌI mục (không thoát sớm)."""
    candidate = hash_token(token)
    found = None
    for entry in token_store.entries():
        if hmac.compare_digest(candidate, entry["sha256"]):
            found = entry
    return found


# --------------------------------------------------------------------------
# Giới hạn tần suất (theo thẻ, theo tiến trình)
# --------------------------------------------------------------------------

class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque] = defaultdict(deque)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def check(self, token_id: str, limit: int, now: Optional[float] = None) -> Optional[int]:
        """None nếu được phép; ngược lại số giây nên đợi."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[token_id]
            while hits and now - hits[0] >= 60.0:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, int(60.0 - (now - hits[0])) + 1)
            hits.append(now)
            return None


rate_limiter = RateLimiter()


# --------------------------------------------------------------------------
# Ứng dụng Flask
# --------------------------------------------------------------------------

class _NullSessionInterface(SessionInterface):
    """Không bao giờ có session/cookie."""

    def is_null_session(self, obj):  # noqa: D401
        return True

    def open_session(self, app, request):  # noqa: ARG002
        return None

    def save_session(self, app, session, response):  # noqa: ARG002
        return None


app = Flask(__name__)
app.session_interface = _NullSessionInterface()
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
app.config["JSON_AS_ASCII"] = False
app.config["JSON_SORT_KEYS"] = False
app.json.ensure_ascii = False
app.json.sort_keys = False
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=0, x_prefix=0)


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: Optional[int] = None):
        super().__init__(code)
        self.status, self.code, self.message, self.retry_after = status, code, message, retry_after


def _error(status: int, code: str, message: str, retry_after: Optional[int] = None):
    response = jsonify(error=code, message=message)
    response.status_code = status
    if retry_after:
        response.headers["Retry-After"] = str(retry_after)
    return response


DATA_ENDPOINTS = frozenset({"products", "compliance", "stock"})


def _bearer_token() -> Optional[str]:
    header = request.headers.get("Authorization", "")
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    if not token or len(token) > MAX_TOKEN_CHARS or any(ch.isspace() for ch in token):
        return None
    return token


@app.before_request
def _authenticate():
    g.started = time.monotonic()
    g.token_id = "anonymous"
    g.result_count = 0
    g.conn = None
    if request.endpoint not in DATA_ENDPOINTS:
        return None
    try:
        token = _bearer_token()
        entry = find_entry(token) if token else None
    except ConfigUnavailable:
        audit_log.error("token file unavailable: refusing data requests")
        raise ApiError(503, "service_unavailable", "Dịch vụ tạm thời chưa sẵn sàng.", 5)
    if entry is None or not entry["enabled"] or entry["expires_at"] <= datetime.now(timezone.utc):
        raise ApiError(401, "unauthorized", "Thẻ truy cập không hợp lệ.")
    g.token_id = entry["id"]
    if entry["networks"]:
        try:
            client = ipaddress.ip_address((request.remote_addr or "").strip())
            if client.version == 6 and client.ipv4_mapped:
                client = client.ipv4_mapped
            allowed = any(client in net for net in entry["networks"])
        except ValueError:
            allowed = False
        if not allowed:
            raise ApiError(403, "forbidden", "Địa chỉ gọi không được phép.")
    wait = rate_limiter.check(entry["id"], entry["rate_per_minute"])
    if wait is not None:
        raise ApiError(429, "rate_limited", "Gọi quá nhiều lần, vui lòng thử lại sau.", wait)
    g.entry = entry
    g.grants = _load_team_grants(entry["team_id"])
    return None


def _connection():
    if g.get("conn") is None:
        dsn = os.environ.get("MACHINE_DATABASE_URL") or os.environ.get("DATABASE_URL")
        if not dsn:
            raise ApiError(503, "service_unavailable", "Dịch vụ tạm thời chưa sẵn sàng.", 5)
        try:
            conn = psycopg2.connect(
                dsn,
                connect_timeout=3,
                options=(
                    f"-c statement_timeout={STATEMENT_TIMEOUT_MS} "
                    "-c default_transaction_read_only=on -c lock_timeout=1000 "
                    "-c idle_in_transaction_session_timeout=10000 "
                    "-c application_name=search-tools-machine-api"
                ),
            )
            conn.set_session(readonly=True)
        except psycopg2.Error:
            audit_log.exception("database connection failed")
            raise ApiError(503, "service_unavailable", "Dịch vụ tạm thời chưa sẵn sàng.", 5) from None
        g.conn = conn
    return g.conn


@app.teardown_request
def _close_connection(_exc):
    conn = g.get("conn")
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _load_team_grants(team_id: int) -> frozenset:
    conn = _connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT permission_keys, lifecycle_status FROM teams WHERE id = %s", (team_id,)
            )
            row = cur.fetchone()
        if row is None or row[1] != "ACTIVE":
            raise ApiError(401, "unauthorized", "Thẻ truy cập không hợp lệ.")
        return frozenset(team_permissions.validate_permissions(row[0]))
    except ApiError:
        raise
    except (psycopg2.Error, ValueError):
        audit_log.exception("team permission lookup failed")
        raise ApiError(503, "service_unavailable", "Dịch vụ tạm thời chưa sẵn sàng.", 5) from None


def _can(key: str) -> bool:
    return team_permissions.allows(key, g.grants)


def _require(key: str) -> None:
    if not _can(key):
        raise ApiError(403, "forbidden", "Thẻ không có quyền thực hiện chức năng này.")


@app.errorhandler(ApiError)
def _handle_api_error(exc: ApiError):
    return _error(exc.status, exc.code, exc.message, exc.retry_after)


@app.errorhandler(psycopg2.errors.QueryCanceled)
def _handle_timeout(_exc):
    return _error(503, "service_busy", "Hệ thống đang bận, vui lòng thử lại sau.", 5)


@app.errorhandler(psycopg2.Error)
def _handle_db_error(_exc):
    audit_log.exception("database error")
    return _error(503, "service_unavailable", "Dịch vụ tạm thời chưa sẵn sàng.", 5)


@app.errorhandler(HTTPException)
def _handle_http_error(exc: HTTPException):
    messages = {400: "Yêu cầu không hợp lệ.", 404: "Không tìm thấy.", 405: "Phương thức không được hỗ trợ.",
                413: "Yêu cầu quá lớn.", 414: "Đường dẫn quá dài.", 431: "Tiêu đề quá lớn."}
    status = exc.code or 500
    return _error(status, "http_error", messages.get(status, "Yêu cầu không hợp lệ."))


@app.errorhandler(Exception)
def _handle_unexpected(_exc):
    audit_log.exception("unexpected error")
    return _error(500, "internal_error", "Đã xảy ra lỗi. Vui lòng thử lại sau.")


@app.after_request
def _finalize(response: Response):
    response.headers.pop("Set-Cookie", None)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    if request.endpoint in DATA_ENDPOINTS or getattr(g, "token_id", "anonymous") != "anonymous":
        started = g.get("started")
        access_log.info(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "token": g.get("token_id", "anonymous"),
            "endpoint": request.endpoint if request.endpoint in DATA_ENDPOINTS else "other",
            "status": response.status_code,
            "results": int(g.get("result_count", 0)),
            "ip": (request.remote_addr or "")[:64],
            "ms": int((time.monotonic() - started) * 1000) if started else 0,
        }, ensure_ascii=True))
    return response


# --------------------------------------------------------------------------
# Truy vấn
# --------------------------------------------------------------------------

def _brand_visibility_sql(alias: str) -> str:
    return (
        f" AND {alias}.brand IN (SELECT tb.brand FROM team_brands tb JOIN teams t ON t.id = tb.team_id "
        "WHERE tb.team_id = %s AND t.lifecycle_status = 'ACTIVE')"
    )


def _compliance_block(label, note, export_policy, source, stable_key) -> dict[str, Any]:
    if not _can("VIEW_COMPLIANCE"):
        return {"state": "HIDDEN", "disclaimer": DISCLAIMER}
    resolved = resolve_compliance_precedence(
        brand_manual_enabled=False, manual_compliance=None, manual_compliance_note=None,
        legacy_compliance=label, legacy_compliance_note=note, cas=None,
        export_policy=export_policy, source=source or "automatic", stable_key=stable_key,
    )
    if not resolved["compliance"]:
        return {"state": "NO_RULE", "message": NO_RULE_MESSAGE, "disclaimer": DISCLAIMER}
    broken = resolved["compliance_stable_key"] == "DATA_ERROR" or resolved["compliance_source"] == "error"
    policy = EXPORT_BLOCK if broken else resolved["compliance_export_policy"]
    block = {
        "state": "DATA_ERROR" if broken else "RULE",
        "label": resolved["compliance"],
        "export_policy": policy,
        "blocked": policy == EXPORT_BLOCK,
        "source": resolved["compliance_source"],
        "disclaimer": DISCLAIMER,
    }
    if _can("VIEW_COMPLIANCE_NOTE"):
        block["regulatory_note"] = DATA_ERROR_NOTE if broken else resolved["compliance_note"]
    return block


def _stock_lots(conn, codes: list[str], cas_values: list[str]) -> list[dict[str, Any]]:
    """Các lô tồn kho đang hoạt động, đã lọc theo hãng của team. KHÔNG SELECT giá/ghi chú."""
    if not codes and not cas_values:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT i.code, i.code_norm, i.cas_norm, i.brand, i.size, i.quantity, i.expiry_date "
            "FROM stock_state st JOIN stock_items i ON i.snapshot_id = st.active_snapshot_id "
            "WHERE st.singleton = TRUE AND (i.code_norm = ANY(%s) OR i.cas_norm = ANY(%s))"
            + _brand_visibility_sql("i")
            + " ORDER BY i.brand_norm, i.code_norm, i.size_norm, i.expiry_date NULLS LAST LIMIT 5000",
            (codes, cas_values, g.entry["team_id"]),
        )
        rows = cur.fetchall()
    lots = []
    for code, code_norm, cas_norm, brand, size, quantity, expiry in rows:
        state, _label = expiry_state(expiry)
        lots.append({
            "code": code, "code_norm": code_norm, "cas_norm": cas_norm or "", "brand": brand,
            "size": size, "brand_key": normalized_text(brand), "size_key": normalized_text(size),
            "quantity": int(quantity), "expiry_state": state,
            "available": int(quantity) > 0 and state != "expired",
        })
    return lots


def _summarize(lots: list[dict[str, Any]], with_quantity: bool) -> dict[str, Any]:
    available = [lot for lot in lots if lot["available"]]
    summary: dict[str, Any] = {
        "status": "IN_STOCK" if available else "OUT_OF_STOCK",
        "expiry_warning": bool(available) and all(lot["expiry_state"] == "near_expiry" for lot in available),
    }
    if with_quantity:
        summary["quantity"] = sum(lot["quantity"] for lot in available)
    return summary


def _allow_same_cas() -> bool:
    return _can("SEARCH_BY_CAS") and _can("VIEW_CAS")


def _same_cas_items(lots, own_code_norm: str, cas_norm: str, with_quantity: bool):
    groups: dict[tuple, dict[str, Any]] = {}
    for lot in lots:
        if lot["cas_norm"] != cas_norm or lot["code_norm"] == own_code_norm or not lot["available"]:
            continue
        key = (lot["code_norm"], normalized_text(lot["brand"]), normalized_text(lot["size"]))
        group = groups.setdefault(key, {"lots": [], "ref": lot})
        group["lots"].append(lot)
    items = []
    for group in groups.values():
        ref = group["ref"]
        item: dict[str, Any] = {}
        if _can("VIEW_CODE"):
            item["code"] = ref["code"]
        if _can("VIEW_BRAND"):
            item["brand"] = ref["brand"]
        if _can("VIEW_SIZE"):
            item["size"] = ref["size"]
        item.update(_summarize(group["lots"], with_quantity))
        items.append(item)
    return items[:SAME_CAS_LIMIT], len(items) > SAME_CAS_LIMIT


def _clean_query(raw: Optional[str], minimum: int = MIN_QUERY_CHARS) -> str:
    query = (raw or "").strip()
    if len(query) < minimum or len(query) > MAX_QUERY_CHARS or "\x00" in query:
        raise ApiError(400, "invalid_query",
                       f"Giá trị phải dài từ {minimum} đến {MAX_QUERY_CHARS} ký tự.")
    return query


def _single_arg(name: str) -> Optional[str]:
    values = request.args.getlist(name)
    if len(values) > 1:
        raise ApiError(400, "invalid_request", "Tham số bị lặp.")
    return values[0] if values else None


def _parse_limit() -> int:
    raw = _single_arg("limit")
    if raw is None or raw == "":
        return DEFAULT_LIMIT
    if not raw.isascii() or not raw.isdigit() or int(raw) < 1:
        raise ApiError(400, "invalid_limit", "limit phải là số nguyên dương.")
    return min(int(raw), MAX_LIMIT)


def _assert_clean(value: Any) -> Any:
    """Lưới an toàn cuối: không khóa cấm nào được lọt ra ngoài."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                raise RuntimeError("forbidden key in machine response")
            _assert_clean(item)
    elif isinstance(value, list):
        for item in value:
            _assert_clean(item)
    return value


def _respond(payload: dict[str, Any], count: int):
    g.result_count = count
    return jsonify(_assert_clean(payload))


# --------------------------------------------------------------------------
# Điểm vào
# --------------------------------------------------------------------------

@app.get(f"{API_PREFIX}/health", endpoint="health")
def health():
    return jsonify(status="ok")


@app.get(f"{API_PREFIX}/products", endpoint="products")
def products():
    _require("SEARCH")
    query = _clean_query(_single_arg("q"))
    limit = _parse_limit()
    match_clauses, params = [], []
    pattern = "%" + literal_like(query) + "%"
    if _can("VIEW_NAME"):
        match_clauses.append("p.name ILIKE %s ESCAPE '!'")
        params.append(pattern)
    if _can("VIEW_CODE"):
        match_clauses.append("p.code ILIKE %s ESCAPE '!'")
        params.append(pattern)
    if _can("SEARCH_BY_CAS") and _can("VIEW_CAS"):
        match_clauses.append("p.cas ILIKE %s ESCAPE '!'")
        params.append(pattern)
    if not match_clauses:
        raise ApiError(403, "forbidden", "Thẻ không có quyền tìm kiếm theo trường nào.")
    team_id = g.entry["team_id"]
    conn = _connection()
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH candidates AS (
                SELECT p.id, p.name, p.code, p.cas, p.brand, p.size,
                       p.manual_compliance, p.manual_compliance_note, p.manual_compliance_status_id
                FROM products p
                WHERE ({' OR '.join(match_clauses)}) {_brand_visibility_sql('p')}
                ORDER BY (UPPER(TRIM(COALESCE(p.code, ''))) = UPPER(%s)) DESC,
                         (UPPER(TRIM(COALESCE(p.cas, ''))) = UPPER(%s)) DESC,
                         UPPER(TRIM(COALESCE(p.brand, ''))), UPPER(TRIM(COALESCE(p.size, ''))),
                         UPPER(TRIM(COALESCE(p.name, ''))), UPPER(TRIM(COALESCE(p.code, ''))), p.id
                LIMIT %s
            )
            SELECT p.name, p.code, p.cas, p.brand, p.size,
                   rr.rule_label, rr.note, rr.export_policy, rr.source, rr.stable_key
            FROM candidates p
            LEFT JOIN brand_compliance_settings bcs
              ON bcs.brand_norm = UPPER(TRIM(COALESCE(p.brand, '')))
            {product_resolver_lateral('p', 'bcs')}
            ORDER BY (UPPER(TRIM(COALESCE(p.code, ''))) = UPPER(%s)) DESC,
                     (UPPER(TRIM(COALESCE(p.cas, ''))) = UPPER(%s)) DESC,
                     UPPER(TRIM(COALESCE(p.brand, ''))), UPPER(TRIM(COALESCE(p.size, ''))),
                     UPPER(TRIM(COALESCE(p.name, ''))), UPPER(TRIM(COALESCE(p.code, ''))), p.id
            """,
            tuple(params) + (team_id, query, query, limit + 1, query, query),
        )
        rows = cur.fetchall()
    more = len(rows) > limit
    rows = rows[:limit]

    code_norms = sorted({normalized_text(r[1]) for r in rows if normalized_text(r[1])})
    cas_norms = sorted({normalized_text(r[2]) for r in rows if normalized_text(r[2])}) if _allow_same_cas() else []
    lots = _stock_lots(conn, code_norms, cas_norms)
    with_quantity = g.entry["stock_detail"] == "quantity"

    results = []
    for name, code, cas, brand, size, label, note, policy, source, stable_key in rows:
        item: dict[str, Any] = {}
        if _can("VIEW_NAME"):
            item["name"] = name
        if _can("VIEW_CODE"):
            item["code"] = code
        if _can("VIEW_CAS"):
            item["cas"] = cas
        if _can("VIEW_BRAND"):
            item["brand"] = brand
        if _can("VIEW_SIZE"):
            item["size"] = size
        item["compliance"] = _compliance_block(label, note, policy, source, stable_key)
        own_code = normalized_text(code)
        exact = [lot for lot in lots if own_code and lot["code_norm"] == own_code]
        stock = _summarize(exact, with_quantity)
        if stock["status"] == "IN_STOCK":
            # Tồn kho khớp theo MÃ (như ô tìm kiếm nhân viên). Cờ này cho biết lô còn hàng có đúng hãng
            # và quy cách của sản phẩm này không; false => chỉ nên nói "có mã tương ứng trong kho, cần xác nhận".
            stock["pack_confirmed"] = any(
                lot["available"] and lot["brand_key"] == normalized_text(brand) and lot["size_key"] == normalized_text(size)
                for lot in exact)
        if _allow_same_cas() and normalized_text(cas):
            same, same_more = _same_cas_items(lots, own_code, normalized_text(cas), with_quantity)
            stock["same_cas"] = same
            stock["same_cas_more"] = same_more
        item["stock"] = stock
        results.append(item)
    return _respond({"query": query, "count": len(results), "more": more, "results": results,
                     "price_notice": PRICE_NOTICE, "disclaimer": DISCLAIMER}, len(results))


@app.get(f"{API_PREFIX}/compliance", endpoint="compliance")
def compliance():
    _require("CHECK_LICENSE")
    cas = (_single_arg("cas") or "").strip()
    if not _CAS_FORMAT_RE.match(cas):
        raise ApiError(400, "invalid_cas", "CAS phải có dạng 64-17-5.")
    conn = _connection()
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT rr.rule_label, rr.note, rr.export_policy, rr.source, rr.stable_key
            FROM (SELECT %s::text AS cas_value) i
            {cas_resolver_lateral('i.cas_value')}
            """,
            (cas,),
        )
        row = cur.fetchone() or (None,) * 5
    block = _compliance_block(*row)
    return _respond({"cas": cas, "compliance": block, "price_notice": PRICE_NOTICE}, 1 if block["state"] != "NO_RULE" else 0)


@app.get(f"{API_PREFIX}/stock", endpoint="stock")
def stock():
    _require("SEARCH")
    code, cas = _single_arg("code"), _single_arg("cas")
    if bool(code) == bool(cas):
        raise ApiError(400, "invalid_request", "Cần đúng một trong hai tham số: code hoặc cas.")
    with_quantity = g.entry["stock_detail"] == "quantity"
    conn = _connection()
    if code:
        _require("VIEW_CODE")
        code = _clean_query(code, minimum=1)
        lots = _stock_lots(conn, [normalized_text(code)], [])
        lots = [lot for lot in lots if lot["code_norm"] == normalized_text(code)]
        groups: dict[tuple, list] = defaultdict(list)
        for lot in lots:
            groups[(normalized_text(lot["brand"]), normalized_text(lot["size"]))].append(lot)
        items = []
        for group in groups.values():
            ref = group[0]
            item: dict[str, Any] = {}
            if _can("VIEW_BRAND"):
                item["brand"] = ref["brand"]
            if _can("VIEW_SIZE"):
                item["size"] = ref["size"]
            item.update(_summarize(group, with_quantity))
            items.append(item)
        payload = {"code": code, **_summarize(lots, with_quantity), "items": items[:STOCK_ITEMS_LIMIT],
                   "more": len(items) > STOCK_ITEMS_LIMIT}
        return _respond(dict(payload, price_notice=PRICE_NOTICE), len(items[:STOCK_ITEMS_LIMIT]))
    if not _allow_same_cas():
        raise ApiError(403, "forbidden", "Thẻ không có quyền tra theo CAS.")
    if not _CAS_FORMAT_RE.match(cas.strip()):
        raise ApiError(400, "invalid_cas", "CAS phải có dạng 64-17-5.")
    cas_norm = normalized_text(cas)
    lots = [lot for lot in _stock_lots(conn, [], [cas_norm]) if lot["cas_norm"] == cas_norm]
    groups = defaultdict(list)
    for lot in lots:
        groups[(lot["code_norm"], normalized_text(lot["brand"]), normalized_text(lot["size"]))].append(lot)
    items = []
    for group in groups.values():
        ref = group[0]
        item = {}
        if _can("VIEW_CODE"):
            item["code"] = ref["code"]
        if _can("VIEW_BRAND"):
            item["brand"] = ref["brand"]
        if _can("VIEW_SIZE"):
            item["size"] = ref["size"]
        item.update(_summarize(group, with_quantity))
        items.append(item)
    payload = {"cas": cas.strip(), **_summarize(lots, with_quantity), "items": items[:STOCK_ITEMS_LIMIT],
               "more": len(items) > STOCK_ITEMS_LIMIT, "price_notice": PRICE_NOTICE}
    return _respond(payload, len(items[:STOCK_ITEMS_LIMIT]))


# --------------------------------------------------------------------------
# OpenAPI (cho n8n / GPT Actions / công cụ khác nhập vào)
# --------------------------------------------------------------------------

def openapi_document() -> dict[str, Any]:
    error_ref = {"$ref": "#/components/schemas/Error"}
    def responses(extra: Optional[dict] = None):
        base = {
            "200": {"description": "Thành công"},
            "400": {"description": "Tham số không hợp lệ", "content": {"application/json": {"schema": error_ref}}},
            "401": {"description": "Thẻ không hợp lệ", "content": {"application/json": {"schema": error_ref}}},
            "403": {"description": "Không có quyền", "content": {"application/json": {"schema": error_ref}}},
            "429": {"description": "Gọi quá nhiều lần", "content": {"application/json": {"schema": error_ref}}},
            "503": {"description": "Dịch vụ tạm thời chưa sẵn sàng", "content": {"application/json": {"schema": error_ref}}},
        }
        if extra:
            base.update(extra)
        return base
    secured = [{"bearerAuth": []}]
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Search-tools — API tra cứu cho bot/AI (chỉ đọc)",
            "version": "1.0.0",
            "description": (
                "Tra cứu hàng hóa, tình trạng pháp chế và tồn kho. API KHÔNG trả giá. "
                "Khi hệ thống chưa có quy tắc (state=NO_RULE) tuyệt đối không được kết luận là được phép nhập; "
                "luôn nhắc cần xác nhận với pháp chế. Chỉ nói điều có trong kết quả. "
                "compliance.state: RULE (có nhãn), NO_RULE (hệ thống chưa có quy tắc), HIDDEN/DATA_ERROR hoặc nhãn 'Chưa xác định' = chưa xác định được; "
                "blocked/export_policy chỉ cho biết hệ thống có chặn báo giá hay không, blocked=false KHÔNG có nghĩa được phép nhập. "
                "Tồn kho khớp theo mã: nếu status=IN_STOCK nhưng pack_confirmed=false thì chỉ nói 'có mã tương ứng trong kho, cần nhân viên xác nhận hãng/quy cách'; "
                "same_cas chỉ là gợi ý thay thế. Không bao giờ nêu giá; khách hỏi giá thì chuyển nhân viên kinh doanh. more=true: hỏi khách cụ thể hơn."
            ),
        },
        "servers": [{"url": API_PREFIX}],
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "schemas": {"Error": {"type": "object", "properties": {
                "error": {"type": "string"}, "message": {"type": "string"}}}},
        },
        "paths": {
            "/products": {"get": {
                "operationId": "tim_san_pham", "security": secured,
                "summary": "Tìm sản phẩm theo tên, mã hoặc CAS; kèm pháp chế và tồn kho",
                "parameters": [
                    {"name": "q", "in": "query", "required": True, "schema": {"type": "string", "minLength": 3, "maxLength": 100}},
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": DEFAULT_LIMIT}},
                ],
                "responses": responses()}},
            "/compliance": {"get": {
                "operationId": "kiem_tra_phap_che", "security": secured,
                "summary": "Tình trạng pháp chế theo số CAS (ví dụ 64-17-5)",
                "parameters": [{"name": "cas", "in": "query", "required": True, "schema": {"type": "string", "example": "64-17-5"}}],
                "responses": responses()}},
            "/stock": {"get": {
                "operationId": "xem_ton_kho", "security": secured,
                "summary": "Còn hàng hay hết hàng theo mã sản phẩm hoặc theo CAS (đúng một trong hai tham số)",
                "parameters": [
                    {"name": "code", "in": "query", "schema": {"type": "string"}},
                    {"name": "cas", "in": "query", "schema": {"type": "string"}},
                ],
                "responses": responses()}},
            "/health": {"get": {"operationId": "health", "summary": "Kiểm tra dịch vụ sống", "responses": {"200": {"description": "ok"}}}},
        },
    }


@app.get(f"{API_PREFIX}/openapi.json", endpoint="openapi")
def openapi():
    return jsonify(openapi_document())
