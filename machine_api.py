"""Cổng API chỉ đọc cho bot/AI (n8n, chatbot, MCP) -- dịch vụ ĐỘC LẬP.

Chạy riêng (``gunicorn machine_api:app``), tách hoàn toàn khỏi ứng dụng nhân
viên ``search.py``: không dùng session/cookie, không đăng ký hook nào vào ứng
dụng nhân viên, không sửa module nào của nó (chỉ import các hàm thuần để dùng
chung SQL pháp chế, bảng quyền và quy tắc hạn dùng). Thiết kế: docs/machine-api/PLAN.md;
hợp đồng kiểm chứng: specs/machine-api-readonly/VERIFICATION.md.

Bất biến an toàn (mỗi bất biến có claim kiểm chứng tương ứng):
- Chỉ đọc: GET, cộng đúng một POST tra cứu hàng loạt (/products/batch, thân JSON ≤ 128 KB, không ghi gì).
  Kết nối PostgreSQL luôn chỉ-đọc, có statement_timeout.
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
import math
import os
import re
import stat
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import psycopg2
from flask import Flask, Response, g, jsonify, request
from flask.sessions import SessionInterface
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import team_permissions
from compliance_resolver import resolve_compliance_precedence
from currency_rates import load_currency_rate_resolver
from regulatory import EXPORT_ALLOW, EXPORT_BLOCK, cas_resolver_lateral, product_resolver_lateral
from stock import expiry_state, literal_like, normalized_text

API_PREFIX = "/machine/v1"

MIN_QUERY_CHARS = 3
MAX_QUERY_CHARS = 100
DEFAULT_LIMIT = 10
MAX_LIMIT = 10
INTERNAL_DEFAULT_LIMIT = 20
INTERNAL_MAX_LIMIT = 50
SAME_CAS_LIMIT = 5
INTERNAL_SAME_CAS_LIMIT = 10
STOCK_ITEMS_LIMIT = 10
INTERNAL_STOCK_ITEMS_LIMIT = 30
INTERNAL_LOT_LIMIT = 200
EXACT_LOT_SCAN_CAP = 50000  # lô của đúng các mã đang hỏi: đọc đầy đủ để số lượng/trạng thái luôn chính xác
SAME_CAS_LOT_SCAN_CAP = 5000  # lô cùng CAS chỉ dùng để gợi ý "tương đương"
MAX_INTERNAL_DAYS = 90
INTERNAL_MIN_PREFIX = {4: 16, 6: 48}  # thẻ có giá không được mở cho dải IP quá rộng
MAX_PRICE_VND = 1e15
PROFILES = ("customer", "internal")
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

# Thẻ NỘI BỘ (n8n lập báo giá, PO duyệt 2026-11-21) được xem giá/ghi chú; chỉ còn chặn khóa kỹ thuật.
INTERNAL_FORBIDDEN_KEYS = frozenset({"product_id", "stock_item_id", "id", "sha256", "token", "password"})

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
    profile = raw.get("profile", "customer")
    if profile not in PROFILES:
        raise ValueError("profile")
    team_id = raw.get("team_id")
    if profile == "customer":
        if isinstance(team_id, bool) or not isinstance(team_id, int) or team_id <= 0:
            raise ValueError("team_id")
    else:
        team_id = None  # thẻ nội bộ không gắn team, không chịu phân quyền team
    expires_at = _parse_expiry(raw.get("expires_at"))
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("enabled")
    stock_detail = raw.get("stock_detail", "status")
    if stock_detail not in ("status", "quantity"):
        raise ValueError("stock_detail")
    if profile == "internal":
        stock_detail = "quantity"
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
    if profile == "internal":
        if not networks:
            raise ValueError("allowed_ips")  # thẻ có giá bắt buộc khóa theo IP
        if any(net.prefixlen < INTERNAL_MIN_PREFIX[net.version] for net in networks):
            raise ValueError("allowed_ips")  # dải quá rộng (ví dụ 0.0.0.0/0) coi như không khóa
        if expires_at > datetime.now(timezone.utc) + timedelta(days=MAX_INTERNAL_DAYS):
            raise ValueError("expires_at")  # thẻ có giá tối đa 90 ngày
    return {
        "id": token_id, "sha256": digest, "team_id": team_id, "expires_at": expires_at,
        "enabled": enabled, "stock_detail": stock_detail, "rate_per_minute": rate,
        "networks": networks, "profile": profile,
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
app.config["MAX_CONTENT_LENGTH"] = 128 * 1024  # đủ cho 500 mục của /products/batch; Nginx chỉ nới đúng đường đó
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


DATA_ENDPOINTS = frozenset({"products", "products_batch", "compliance", "stock"})


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
    g.profile = "none"
    g.internal = False
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
    g.profile = entry["profile"]
    g.internal = entry["profile"] == "internal"
    if g.internal:
        # Thẻ nội bộ: toàn quyền xem 3 loại dữ liệu; kết nối riêng (role riêng) nên thiếu cấu hình => 503.
        g.grants = frozenset(team_permissions.REGISTRY)
        _connection()
    else:
        g.grants = _load_team_grants(entry["team_id"])
    return None


def _connection():
    if g.get("conn") is None:
        internal = bool(g.get("internal"))
        if internal:
            # Không bao giờ lùi về kết nối của thẻ khách (role đó không đọc được giá, và ngược lại).
            dsn = os.environ.get("MACHINE_INTERNAL_DATABASE_URL")
        else:
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
                    f"-c application_name=search-tools-machine-api{'-internal' if internal else ''}"
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
    """Mệnh đề SQL giới hạn hãng theo team (kèm một tham số: team_id). Thẻ nội bộ: không giới hạn (PO 2026-11-21)."""
    if g.get("internal"):
        return ""
    return (
        f" AND {alias}.brand IN (SELECT tb.brand FROM team_brands tb JOIN teams t ON t.id = tb.team_id "
        "WHERE tb.team_id = %s AND t.lifecycle_status = 'ACTIVE')"
    )


def _brand_visibility_params() -> tuple:
    return () if g.get("internal") else (g.entry["team_id"],)


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


def _stock_lots(conn, codes: list[str], cas_values: list[str], cas_cap: int = SAME_CAS_LOT_SCAN_CAP) -> list[dict[str, Any]]:
    """Các lô tồn kho đang hoạt động, đã lọc theo hãng của team. KHÔNG SELECT giá/ghi chú."""
    if not codes and not cas_values:
        return []
    internal = bool(g.get("internal"))
    vis_sql, vis_params = _brand_visibility_sql("i"), _brand_visibility_params()
    extra_cols = ", i.stock_price_vnd, i.stock_note" if internal else ""
    extra_cols_outer = ", t.stock_price_vnd, t.stock_note" if internal else ""
    inner = (f"SELECT i.code, i.code_norm, i.cas_norm, i.brand, i.size, i.quantity, i.expiry_date, i.brand_norm, i.size_norm{extra_cols} "
             "FROM stock_state st JOIN stock_items i ON i.snapshot_id = st.active_snapshot_id "
             "WHERE st.singleton = TRUE AND ")
    # Mỗi nhánh phải SẮP XẾP trước khi cắt trần, để tập lô giữ lại không phụ thuộc thứ tự vật lý của bảng
    lot_order = " ORDER BY i.brand_norm, i.code_norm, i.size_norm, i.expiry_date NULLS LAST"
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT t.code, t.code_norm, t.cas_norm, t.brand, t.size, t.quantity, t.expiry_date{extra_cols_outer} FROM ("
            "(" + inner + "i.code_norm = ANY(%s)" + vis_sql + lot_order + f" LIMIT {EXACT_LOT_SCAN_CAP}) "
            "UNION ALL "
            "(" + inner + "i.cas_norm = ANY(%s) AND NOT (i.code_norm = ANY(%s))" + vis_sql + lot_order + f" LIMIT {cas_cap})"
            ") t ORDER BY t.brand_norm, t.code_norm, t.size_norm, t.expiry_date NULLS LAST",
            (codes,) + vis_params + (cas_values, codes) + vis_params,
        )
        rows = cur.fetchall()
    return [_lot_from_row(row, internal) for row in rows]


def _lots_per_value(conn, column: str, values: list[str], caps: list[int]) -> dict[str, list[dict[str, Any]]]:
    """Batch: với TỪNG giá trị của ``column`` (code_norm hoặc cas_norm), đọc tối đa ``caps[i]`` lô đầu tiên
    theo đúng thứ tự của /products. Trần tính riêng từng giá trị nên các mục trong gói không chiếm chỗ của nhau."""
    assert column in ("code_norm", "cas_norm")
    if not values:
        return {}
    internal = bool(g.get("internal"))
    vis_sql, vis_params = _brand_visibility_sql("i"), _brand_visibility_params()
    extra = ", i.stock_price_vnd, i.stock_note" if internal else ""
    extra_outer = ", t.stock_price_vnd, t.stock_note" if internal else ""
    # MỘT lượt đọc (join với danh sách giá trị) + ROW_NUMBER theo từng giá trị: tránh LATERAL lặp hàng nghìn lần.
    # Thứ tự trong mỗi giá trị = thứ tự của /products (khóa duy nhất của lô trong một snapshot nên không có hòa).
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.code, t.code_norm, t.cas_norm, t.brand, t.size, t.quantity, t.expiry_date" + extra_outer + " FROM ("
            "SELECT i.code, i.code_norm, i.cas_norm, i.brand, i.size, i.quantity, i.expiry_date, i.brand_norm, i.size_norm"
            + extra + ", c.cap, ROW_NUMBER() OVER (PARTITION BY i." + column
            + " ORDER BY i.brand_norm, i.code_norm, i.size_norm, i.expiry_date NULLS LAST) AS rn"
            " FROM unnest(%s::text[], %s::int[]) AS c(match_value, cap)"
            " JOIN stock_items i ON i." + column + " = c.match_value"
            " JOIN stock_state st ON st.singleton = TRUE AND i.snapshot_id = st.active_snapshot_id"
            " WHERE TRUE" + vis_sql +
            ") t WHERE t.rn <= t.cap"
            f" ORDER BY t.{column}, t.brand_norm, t.code_norm, t.size_norm, t.expiry_date NULLS LAST",
            (values, caps) + vis_params,
        )
        rows = cur.fetchall()
    out: dict[str, list] = defaultdict(list)
    key = 1 if column == "code_norm" else 2
    for row in rows:
        out[row[key] or ""].append(_lot_from_row(row, internal))
    return out


def _exact_lot_lists(conn, code_norms: list[str]) -> dict[str, list[dict[str, Any]]]:
    return _lots_per_value(conn, "code_norm", list(code_norms), [EXACT_LOT_SCAN_CAP] * len(code_norms))


def _same_cas_lot_lists(conn, caps: dict[str, int]) -> dict[str, list[dict[str, Any]]]:
    """Batch: với TỪNG CAS, đọc ``caps[cas]`` lô đầu tiên (mọi mã). Mỗi sản phẩm (mã C) sau đó lấy 5.000 lô đầu
    tiên có mã khác C — đúng cửa sổ /products thấy khi tra riêng mã đó (hợp đồng B4 v2); cap = 5.000 + số lô của
    chính mã đó nên cửa sổ không phụ thuộc các mục khác trong gói."""
    values = sorted(caps)
    return _lots_per_value(conn, "cas_norm", values, [caps[v] for v in values])


def _lot_from_row(row, internal: bool) -> dict[str, Any]:
    code, code_norm, cas_norm, brand, size, quantity, expiry = row[:7]
    state, _label = expiry_state(expiry)
    lot = {
        "code": code, "code_norm": code_norm, "cas_norm": cas_norm or "", "brand": brand,
        "size": size, "brand_key": normalized_text(brand), "size_key": normalized_text(size),
        "quantity": int(quantity), "expiry_state": state,
        "available": int(quantity) > 0 and state != "expired",
    }
    if internal:
        price_vnd, stock_note = row[7], row[8]
        lot["expiry_date"] = expiry.isoformat() if expiry else None
        price_value = float(price_vnd) if price_vnd is not None else None
        lot["stock_price_vnd"] = price_value if price_value is None or math.isfinite(price_value) else None
        lot["stock_note"] = stock_note or ""
    return lot


def _lots_fields(lots: list[dict[str, Any]]) -> dict[str, Any]:
    """Các khóa lô cho thẻ nội bộ: danh sách (tối đa INTERNAL_LOT_LIMIT), tổng số lô và cờ bị cắt."""
    return {"lots": _lot_details(lots), "lots_total": len(lots), "lots_truncated": len(lots) > INTERNAL_LOT_LIMIT}


def _lot_details(lots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chi tiết từng lô — chỉ dùng cho thẻ nội bộ."""
    return [
        {
            "brand": lot["brand"], "size": lot["size"], "quantity": lot["quantity"],
            "expiry_date": lot.get("expiry_date"), "expiry_state": lot["expiry_state"],
            "available": lot["available"], "stock_price_vnd": lot.get("stock_price_vnd"),
            "stock_note": lot.get("stock_note", ""),
        }
        for lot in lots[:INTERNAL_LOT_LIMIT]
    ]


# Phép tính giá giữ NGUYÊN công thức của ứng dụng nhân viên (search._compute_unit_price_details):
# round(giá × hệ số ship × tỷ giá, -3). Có test đối chiếu trực tiếp với hàm gốc (claim I4).
def _as_float(value) -> float:
    try:
        return float(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _finite_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _price_block(price, ship, brand, resolver) -> dict[str, Any]:
    try:
        resolution = resolver.resolve((brand or "").strip())
    except Exception:  # noqa: BLE001  (dữ liệu tỷ giá hỏng, ví dụ NaN): báo rõ, không làm hỏng cả truy vấn
        return {"original_price": price, "ship_factor": ship, "currency": None, "rate_vnd": None,
                "unit_price_vnd": None, "price_status": "INVALID_RATE_DATA"}
    block: dict[str, Any] = {
        "original_price": price, "ship_factor": ship,
        "currency": resolution.currency_code,
        "rate_vnd": _finite_or_none(resolution.rate),
    }
    if not resolution.is_valid or resolution.rate is None:
        block.update(unit_price_vnd=None, price_status=str(resolution.status))
        return block
    price_f, ship_f = _as_float(price), _as_float(ship)
    try:
        raw = price_f * ship_f * float(resolution.rate)
        # nan/inf/quá lớn: dữ liệu giá hỏng (ví dụ chuỗi "nan" từ file nhập) — báo rõ, không làm hỏng cả phản hồi.
        if not math.isfinite(price_f) or not math.isfinite(ship_f) or not math.isfinite(raw) or abs(raw) > MAX_PRICE_VND:
            raise ValueError("non-finite price")
        unit = round(raw, -3)
        block["unit_price_vnd"] = int(unit)
    except (ValueError, OverflowError):
        block.update(unit_price_vnd=None, price_status="INVALID_PRICE_DATA")
        return block
    block["price_status"] = "OK" if price_f > 0 and ship_f > 0 and unit > 0 else "NO_PRICE_DATA"
    return block


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
    limit = INTERNAL_SAME_CAS_LIMIT if g.get("internal") else SAME_CAS_LIMIT
    return items[:limit], len(items) > limit


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
    internal = bool(g.get("internal"))
    default, maximum = (INTERNAL_DEFAULT_LIMIT, INTERNAL_MAX_LIMIT) if internal else (DEFAULT_LIMIT, MAX_LIMIT)
    raw = _single_arg("limit")
    if raw is None or raw == "":
        return default
    if not raw.isascii() or not raw.isdigit() or int(raw) < 1:
        raise ApiError(400, "invalid_limit", "limit phải là số nguyên dương.")
    return min(int(raw), maximum)


def _assert_clean(value: Any, forbidden: Optional[frozenset] = None) -> Any:
    """Lưới an toàn cuối: không khóa cấm nào được lọt ra ngoài (thẻ khách: cả giá/ghi chú; thẻ nội bộ: khóa kỹ thuật)."""
    if forbidden is None:
        forbidden = INTERNAL_FORBIDDEN_KEYS if g.get("internal") else FORBIDDEN_KEYS
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in forbidden:
                raise RuntimeError("forbidden key in machine response")
            _assert_clean(item, forbidden)
    elif isinstance(value, list):
        for item in value:
            _assert_clean(item, forbidden)
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
    internal = bool(g.get("internal"))
    can_name, can_code = _can("VIEW_NAME"), _can("VIEW_CODE")
    can_cas = _can("SEARCH_BY_CAS") and _can("VIEW_CAS")
    like = literal_like(query)
    pattern, prefix = "%" + like + "%", like + "%"
    match_clauses, match_params = [], []
    # Xếp hạng (chỉ dùng các trường mà thẻ được phép thấy): 0 trùng mã -> 1 trùng CAS -> 2 trùng tên ->
    # 3 bắt đầu bằng (mã HOẶC tên, MỘT mức) -> 4 chứa. Trong cùng mức, XEN ĐỀU các hãng (brand_rank).
    tier_whens, tier_params = [], []
    code_expr = "UPPER(TRIM(COALESCE(p.code, '')))"
    cas_expr = "UPPER(TRIM(COALESCE(p.cas, '')))"
    name_expr = "UPPER(TRIM(COALESCE(p.name, '')))"
    if can_code:
        tier_whens.append(f"WHEN {code_expr} = UPPER(%s) THEN 0")
        tier_params.append(query)
    if can_cas:
        tier_whens.append(f"WHEN {cas_expr} = UPPER(%s) THEN 1")
        tier_params.append(query)
    if can_name:
        tier_whens.append(f"WHEN {name_expr} = UPPER(%s) THEN 2")
        tier_params.append(query)
    if can_code:
        tier_whens.append(f"WHEN {code_expr} LIKE UPPER(%s) ESCAPE '!' THEN 3")
        tier_params.append(prefix)
    if can_name:
        tier_whens.append(f"WHEN {name_expr} LIKE UPPER(%s) ESCAPE '!' THEN 3")
        tier_params.append(prefix)
    if can_name:
        match_clauses.append("p.name ILIKE %s ESCAPE '!'")
        match_params.append(pattern)
    if can_code:
        match_clauses.append("p.code ILIKE %s ESCAPE '!'")
        match_params.append(pattern)
    if can_cas:
        match_clauses.append("p.cas ILIKE %s ESCAPE '!'")
        match_params.append(pattern)
    if not match_clauses:
        raise ApiError(403, "forbidden", "Thẻ không có quyền tìm kiếm theo trường nào.")
    tier_sql = "CASE " + " ".join(tier_whens) + " ELSE 4 END" if tier_whens else "4"
    vis_sql, vis_params = _brand_visibility_sql("p"), _brand_visibility_params()
    extra_matched = ", p.price AS price_text, p.ship AS ship_text, p.note AS product_note" if internal else ""
    extra_final = ", p.price_text, p.ship_text, p.product_note" if internal else ""
    order_tail = ("UPPER(TRIM(COALESCE({a}.size, ''))), UPPER(TRIM(COALESCE({a}.name, ''))), "
                  "UPPER(TRIM(COALESCE({a}.code, ''))), {a}.id")
    conn = _connection()
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH matched AS (
                SELECT p.id, p.name, p.code, p.cas, p.brand, p.size,
                       p.manual_compliance, p.manual_compliance_note, p.manual_compliance_status_id{extra_matched},
                       {tier_sql} AS tier,
                       UPPER(TRIM(COALESCE(p.brand, ''))) AS brand_key
                FROM products p
                WHERE ({' OR '.join(match_clauses)}) {vis_sql}
            ), candidates AS (
                SELECT * FROM (
                    SELECT m.*, ROW_NUMBER() OVER (
                               PARTITION BY m.tier, m.brand_key ORDER BY {order_tail.format(a='m')}) AS brand_rank
                    FROM matched m
                ) r
                ORDER BY r.tier, r.brand_rank, r.brand_key, {order_tail.format(a='r')}
                LIMIT %s
            )
            SELECT p.name, p.code, p.cas, p.brand, p.size,
                   rr.rule_label, rr.note, rr.export_policy, rr.source, rr.stable_key{extra_final}
            FROM candidates p
            LEFT JOIN brand_compliance_settings bcs
              ON bcs.brand_norm = UPPER(TRIM(COALESCE(p.brand, '')))
            {product_resolver_lateral('p', 'bcs')}
            ORDER BY p.tier, p.brand_rank, p.brand_key, {order_tail.format(a='p')}
            """,
            tuple(tier_params) + tuple(match_params) + vis_params + (limit + 1,),
        )
        rows = cur.fetchall()
    more = len(rows) > limit
    rows = rows[:limit]

    code_norms = sorted({normalized_text(r[1]) for r in rows if normalized_text(r[1])})
    cas_norms = sorted({normalized_text(r[2]) for r in rows if normalized_text(r[2])}) if _allow_same_cas() else []
    lots = _stock_lots(conn, code_norms, cas_norms)
    with_quantity = g.entry["stock_detail"] == "quantity"
    resolver = load_currency_rate_resolver(conn, app.root_path) if internal and rows else None

    lots_by_code, lots_by_cas = _index_lots(lots)
    results = [_result_item(row, lots_by_code, lots_by_cas, resolver, with_quantity) for row in rows]
    payload = {"query": query, "count": len(results), "more": more, "results": results}
    if not internal:
        payload["price_notice"] = PRICE_NOTICE
    payload["disclaimer"] = DISCLAIMER
    return _respond(payload, len(results))


def _index_lots(lots: list[dict[str, Any]]) -> tuple[dict[str, list], dict[str, list]]:
    """Gom lô theo mã và theo CAS (giữ nguyên thứ tự gốc) để tra O(1) thay vì quét cả danh sách mỗi sản phẩm."""
    by_code: dict[str, list] = defaultdict(list)
    by_cas: dict[str, list] = defaultdict(list)
    for lot in lots:
        by_code[lot["code_norm"]].append(lot)
        if lot["cas_norm"]:
            by_cas[lot["cas_norm"]].append(lot)
    return by_code, by_cas


def _result_item(row, lots_by_code, lots_by_cas, resolver, with_quantity: bool,
                 include_compliance: bool = True, include_stock: bool = True,
                 same_cas_lots=None) -> dict[str, Any]:
    """Dựng MỘT sản phẩm trong phản hồi. Dùng chung cho /products và /products/batch (claim B4):
    cùng quyền trường, cùng khối giá, pháp chế, tồn kho. ``row`` = (name, code, cas, brand, size,
    rule_label, note, export_policy, source, stable_key[, price_text, ship_text, product_note])."""
    name, code, cas, brand, size, label, note, policy, source, stable_key = row[:10]
    internal = bool(g.get("internal"))
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
    if internal:
        price_text, ship_text, product_note = row[10], row[11], row[12]
        item["note"] = product_note or ""
        item["price"] = _price_block(price_text, ship_text, brand, resolver)
    if include_compliance:
        item["compliance"] = _compliance_block(label, note, policy, source, stable_key)
    if not include_stock:
        return item
    own_code = normalized_text(code)
    exact = lots_by_code.get(own_code, []) if own_code else []
    stock = _summarize(exact, with_quantity)
    if stock["status"] == "IN_STOCK":
        # Tồn kho khớp theo MÃ (như ô tìm kiếm nhân viên). Cờ này cho biết lô còn hàng có đúng hãng
        # và quy cách của sản phẩm này không; false => chỉ nên nói "có mã tương ứng trong kho, cần xác nhận".
        stock["pack_confirmed"] = any(
            lot["available"] and lot["brand_key"] == normalized_text(brand) and lot["size_key"] == normalized_text(size)
            for lot in exact)
    if internal:
        stock.update(_lots_fields(exact))
    if _allow_same_cas() and normalized_text(cas):
        cas_key = normalized_text(cas)
        cas_lots = same_cas_lots(cas_key, own_code) if same_cas_lots else lots_by_cas.get(cas_key, [])
        same, same_more = _same_cas_items(cas_lots, own_code, cas_key, with_quantity)
        stock["same_cas"] = same
        stock["same_cas_more"] = same_more
    item["stock"] = stock
    return item


# --------------------------------------------------------------------------
# Tra hàng loạt (POST /products/batch) — docs/machine-api/BATCH.md, specs/machine-api-batch/VERIFICATION.md
# --------------------------------------------------------------------------

BATCH_MAX_ITEMS = 500
BATCH_MAX_REF_CHARS = 64
BATCH_DEFAULT_PER_ITEM = 10
BATCH_MAX_PER_ITEM = 10
INTERNAL_BATCH_DEFAULT_PER_ITEM = 20
INTERNAL_BATCH_MAX_PER_ITEM = 50
BATCH_MAX_RESULTS = 10000
BATCH_STATEMENT_TIMEOUT_MS = 8000
BATCH_INCLUDES = ("compliance", "stock")
BATCH_TOP_KEYS = frozenset({"items", "include", "max_per_item"})
# Ký tự điều khiển C0/C1 và nửa cặp UTF-16 lẻ (JSON hợp lệ nhưng không mã hóa lại được) => mục INVALID.
_BAD_CHARS_RE = re.compile("[\x00-\x1f\x7f-\x9f\ud800-\udfff]")


def _batch_bad(message: str, code: str = "invalid_request"):
    raise ApiError(400, code, message)


def _parse_batch_body() -> tuple[list[dict[str, Any]], set, int]:
    try:
        body = request.get_json(silent=True)
    except RecursionError:  # JSON lồng quá sâu
        body = None
    if not isinstance(body, dict):
        _batch_bad("Thân yêu cầu phải là JSON dạng đối tượng, ví dụ {\"items\": [...]}.")
    unknown = set(body) - BATCH_TOP_KEYS
    if unknown:
        _batch_bad("Có trường không được hỗ trợ ở cấp ngoài cùng (chỉ nhận items, include, max_per_item).")
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        _batch_bad("items phải là danh sách có ít nhất 1 mục.")
    if len(raw_items) > BATCH_MAX_ITEMS:
        _batch_bad(f"Tối đa {BATCH_MAX_ITEMS} mục mỗi lần gọi; hãy chia nhỏ.", "too_many_items")
    include = body.get("include", [])
    if not isinstance(include, list) or any(not isinstance(x, str) or x not in BATCH_INCLUDES for x in include):
        _batch_bad("include chỉ nhận các giá trị: compliance, stock.", "invalid_include")
    internal = bool(g.get("internal"))
    default, maximum = ((INTERNAL_BATCH_DEFAULT_PER_ITEM, INTERNAL_BATCH_MAX_PER_ITEM) if internal
                        else (BATCH_DEFAULT_PER_ITEM, BATCH_MAX_PER_ITEM))
    per_item = body.get("max_per_item", default)
    if isinstance(per_item, bool) or not isinstance(per_item, int) or per_item < 1:
        _batch_bad("max_per_item phải là số nguyên dương.", "invalid_limit")
    return raw_items, set(include), min(per_item, maximum)


def _parse_batch_item(position: int, raw: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"position": position}
    if (isinstance(raw, dict) and isinstance(raw.get("ref"), str) and len(raw["ref"]) <= BATCH_MAX_REF_CHARS
            and not _BAD_CHARS_RE.search(raw["ref"])):
        entry["ref"] = raw["ref"]

    def invalid(message: str):
        entry.update(type=None, value=None, status="INVALID", message=message)
        return entry

    if not isinstance(raw, dict):
        return invalid("Mỗi mục phải là đối tượng, ví dụ {\"code\": \"T0050000\"} hoặc {\"cas\": \"64-17-5\"}.")
    if set(raw) - {"code", "cas", "ref"}:
        return invalid("Mục có trường không được hỗ trợ (chỉ nhận code, cas, ref).")
    if "ref" in raw and "ref" not in entry:
        return invalid(f"ref phải là chuỗi tối đa {BATCH_MAX_REF_CHARS} ký tự, không có ký tự điều khiển.")
    kinds = [kind for kind in ("code", "cas") if kind in raw]
    if len(kinds) != 1:
        return invalid("Mỗi mục cần đúng một trong hai trường: code hoặc cas.")
    kind = kinds[0]
    value = raw[kind]
    if not isinstance(value, str):
        return invalid(f"{kind} phải là chuỗi.")
    value = value.strip()
    entry["type"] = kind
    if not value or len(value) > MAX_QUERY_CHARS or _BAD_CHARS_RE.search(value):
        # Không trả lại giá trị sai (có thể chứa ký tự không mã hóa được); n8n ghép dòng bằng position/ref.
        entry.update(value=None, status="INVALID",
                     message=f"{kind} phải dài từ 1 đến {MAX_QUERY_CHARS} ký tự, không có ký tự điều khiển.")
        return entry
    entry["value"] = value
    return entry


def _batch_rows(conn, field: str, values: list[str], per_item: int, include_compliance: bool) -> dict[int, list]:
    """Trả {vị trí trong values: [(match_total, row), ...]} — khớp CHÍNH XÁC UPPER(TRIM(field)) qua chỉ mục biểu thức.

    Cùng thứ tự với /products trong một mức xếp hạng: xen đều các hãng (brand_rank) rồi quy cách, tên, mã, id."""
    assert field in ("code", "cas")
    if not values:
        return {}
    internal = bool(g.get("internal"))
    # Lọc hãng ĐẶT SAU bước tra chỉ mục (q = tập đã khớp, OFFSET 0 chặn planner gộp): nếu để chung WHERE,
    # planner có thể kết hợp idx_products_brand (BitmapAnd) và đọc hàng trăm nghìn dòng mỗi mục (đo staging:
    # thẻ khách, 500 CAS phổ biến → 11 giây). Kết quả không đổi, chỉ đổi đường đọc.
    vis_sql, vis_params = _brand_visibility_sql("q"), _brand_visibility_params()
    extra_inner = ", p.price AS price_text, p.ship AS ship_text, p.note AS product_note" if internal else ""
    extra_final = ", p.price_text, p.ship_text, p.product_note" if internal else ""
    order_tail = ("UPPER(TRIM(COALESCE({a}.size, ''))), UPPER(TRIM(COALESCE({a}.name, ''))), "
                  "UPPER(TRIM(COALESCE({a}.code, ''))), {a}.id")
    if include_compliance:
        rule_cols = "rr.rule_label, rr.note, rr.export_policy, rr.source, rr.stable_key"
        rule_join = ("LEFT JOIN brand_compliance_settings bcs ON bcs.brand_norm = UPPER(TRIM(COALESCE(p.brand, ''))) "
                     + product_resolver_lateral("p", "bcs"))
    else:
        rule_cols = "NULL::text, NULL::text, NULL::text, NULL::text, NULL::text"
        rule_join = ""
    sql = f"""
        WITH input AS (
            SELECT u.val, u.ord FROM unnest(%s::text[]) WITH ORDINALITY AS u(val, ord)
        ), hits AS (
            SELECT i.ord, x.* FROM input i
            CROSS JOIN LATERAL (
                SELECT r.* FROM (
                    SELECT q.*,
                           COUNT(*) OVER () AS match_total,
                           UPPER(TRIM(COALESCE(q.brand, ''))) AS brand_key,
                           ROW_NUMBER() OVER (PARTITION BY UPPER(TRIM(COALESCE(q.brand, '')))
                                              ORDER BY {order_tail.format(a='q')}) AS brand_rank
                    FROM (
                        SELECT p.id, p.name, p.code, p.cas, p.brand, p.size,
                               p.manual_compliance, p.manual_compliance_note, p.manual_compliance_status_id{extra_inner}
                        FROM products p
                        WHERE p.{field} IS NOT NULL AND TRIM(p.{field}) <> ''
                          AND UPPER(TRIM(p.{field})) = UPPER(i.val)
                        OFFSET 0
                    ) q
                    WHERE TRUE {vis_sql}
                ) r
                ORDER BY r.brand_rank, r.brand_key, {order_tail.format(a='r')}
                LIMIT %s
            ) x
        )
        SELECT p.ord, p.match_total, p.name, p.code, p.cas, p.brand, p.size, {rule_cols}{extra_final}
        FROM hits p
        {rule_join}
        ORDER BY p.ord, p.brand_rank, p.brand_key, {order_tail.format(a='p')}
    """
    out: dict[int, list] = defaultdict(list)
    with conn.cursor() as cur:
        cur.execute(sql, (values,) + vis_params + (per_item,))
        for row in cur.fetchall():
            out[int(row[0]) - 1].append((int(row[1]), row[2:]))
    return out


@app.post(f"{API_PREFIX}/products/batch", endpoint="products_batch")
def products_batch():
    _require("SEARCH")
    raw_items, include, per_item = _parse_batch_body()
    internal = bool(g.get("internal"))
    include_compliance, include_stock = "compliance" in include, "stock" in include
    allowed = {"code": _can("VIEW_CODE"), "cas": _allow_same_cas()}
    entries = [_parse_batch_item(position, raw) for position, raw in enumerate(raw_items, start=1)]
    unique: dict[str, dict[str, int]] = {"code": {}, "cas": {}}
    for entry in entries:
        if "status" in entry:
            continue
        if not allowed[entry["type"]]:
            entry.update(status="NOT_ALLOWED", message="Thẻ không có quyền tra theo loại này.")
            continue
        unique[entry["type"]].setdefault(entry["value"], len(unique[entry["type"]]))

    conn = _connection()
    with conn.cursor() as cur:
        # Chỉ trong giao dịch chỉ-đọc của lời gọi này; các đường khác giữ 3 giây.
        cur.execute(f"SET LOCAL statement_timeout = {BATCH_STATEMENT_TIMEOUT_MS}")
    found = {kind: _batch_rows(conn, kind, list(unique[kind]), per_item, include_compliance) for kind in ("code", "cas")}
    # Mục trùng nhau được trả lặp lại, nên đếm theo từng mục gửi lên.
    returned = sum(len(found[e["type"]].get(unique[e["type"]][e["value"]], [])) for e in entries if "status" not in e)
    if returned > BATCH_MAX_RESULTS:
        raise ApiError(400, "too_many_results",
                       f"Kết quả vượt {BATCH_MAX_RESULTS} sản phẩm; hãy gửi ít mục hơn hoặc giảm max_per_item.")

    all_rows = [row for kind in found for rows in found[kind].values() for _total, row in rows]
    lots_by_code: dict[str, list] = {}
    lots_by_cas: dict[str, list] = {}
    same_cas_lots = None
    if include_stock and all_rows:
        code_norms = sorted({normalized_text(r[1]) for r in all_rows if normalized_text(r[1])})
        # Lô của đúng các mã đang hỏi: trần riêng TỪNG mã (như /products cho một trang), nên một mã nhiều lô
        # không chiếm chỗ của mã khác trong cùng gói.
        lots_by_code = _exact_lot_lists(conn, code_norms)
        if _allow_same_cas():
            # Gợi ý cùng CAS: mỗi sản phẩm nhận đúng cửa sổ /products sẽ thấy khi tra riêng mã đó
            # (SAME_CAS_LOT_SCAN_CAP lô đầu tiên của các mã khác), độc lập với các mục khác trong gói.
            caps: dict[str, int] = {}
            for r in all_rows:
                cas_key, own = normalized_text(r[2]), normalized_text(r[1])
                if not cas_key:
                    continue
                own_lots = sum(1 for lot in lots_by_code.get(own, []) if lot["cas_norm"] == cas_key) if own else 0
                caps[cas_key] = max(caps.get(cas_key, 0), SAME_CAS_LOT_SCAN_CAP + own_lots)
            cas_lists = _same_cas_lot_lists(conn, caps)

            def same_cas_lots(cas_key: str, own: str) -> list:
                window = [lot for lot in cas_lists.get(cas_key, []) if not own or lot["code_norm"] != own]
                return window[:SAME_CAS_LOT_SCAN_CAP]
    with_quantity = g.entry["stock_detail"] == "quantity"
    resolver = load_currency_rate_resolver(conn, app.root_path) if internal and all_rows else None

    built: dict[tuple[str, int], list] = {}
    summary = {"found": 0, "not_found": 0, "invalid": 0, "not_allowed": 0, "truncated": 0}
    items = []
    for entry in entries:
        status = entry.get("status")
        if status is None:
            key = (entry["type"], unique[entry["type"]][entry["value"]])
            rows = found[key[0]].get(key[1], [])
            if key not in built:
                built[key] = [_result_item(row, lots_by_code, lots_by_cas, resolver, with_quantity,
                                           include_compliance, include_stock, same_cas_lots) for _total, row in rows]
            results = built[key]
            total = rows[0][0] if rows else 0
            status = "FOUND" if results else "NOT_FOUND"
            entry.update(status=status, matches_total=total, truncated=total > len(results), results=results)
        else:
            entry.update(matches_total=0, truncated=False, results=[])
        summary[status.lower()] += 1
        summary["truncated"] += 1 if entry["truncated"] else 0
        items.append(entry)
    payload: dict[str, Any] = {
        "count": len(items), "max_per_item": per_item, "include": [x for x in BATCH_INCLUDES if x in include],
        "summary": summary, "items": items,
    }
    if not internal:
        payload["price_notice"] = PRICE_NOTICE
    payload["disclaimer"] = DISCLAIMER
    return _respond(payload, returned)


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
    payload = {"cas": cas, "compliance": block}
    if not g.get("internal"):
        payload["price_notice"] = PRICE_NOTICE
    return _respond(payload, 1 if block["state"] != "NO_RULE" else 0)


@app.get(f"{API_PREFIX}/stock", endpoint="stock")
def stock():
    _require("SEARCH")
    code, cas = _single_arg("code"), _single_arg("cas")
    if bool(code) == bool(cas):
        raise ApiError(400, "invalid_request", "Cần đúng một trong hai tham số: code hoặc cas.")
    internal = bool(g.get("internal"))
    items_limit = INTERNAL_STOCK_ITEMS_LIMIT if internal else STOCK_ITEMS_LIMIT
    with_quantity = g.entry["stock_detail"] == "quantity"
    conn = _connection()

    def finish(head: dict[str, Any], lots: list, items: list):
        payload = {**head, **_summarize(lots, with_quantity), "items": items[:items_limit],
                   "more": len(items) > items_limit}
        if not internal:
            payload["price_notice"] = PRICE_NOTICE
        return _respond(payload, len(items[:items_limit]))

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
            if internal:
                item.update(_lots_fields(group))
            items.append(item)
        return finish({"code": code}, lots, items)
    if not _allow_same_cas():
        raise ApiError(403, "forbidden", "Thẻ không có quyền tra theo CAS.")
    if not _CAS_FORMAT_RE.match(cas.strip()):
        raise ApiError(400, "invalid_cas", "CAS phải có dạng 64-17-5.")
    cas_norm = normalized_text(cas)
    # Tra theo CAS: lô cùng CAS chính là dữ liệu cần trả, nên đọc đầy đủ (không phải chỉ gợi ý)
    lots = [lot for lot in _stock_lots(conn, [], [cas_norm], cas_cap=EXACT_LOT_SCAN_CAP) if lot["cas_norm"] == cas_norm]
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
        if internal:
            item.update(_lots_fields(group))
        items.append(item)
    return finish({"cas": cas.strip()}, lots, items)


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
            "/products/batch": {"post": {
                "operationId": "tra_hang_loat", "security": secured,
                "summary": f"Tra tối đa {BATCH_MAX_ITEMS} mã/CAS trong một lần gọi; khớp chính xác, trả mọi sản phẩm khớp",
                "requestBody": {"required": True, "content": {"application/json": {"schema": {
                    "type": "object", "required": ["items"], "additionalProperties": False,
                    "properties": {
                        "items": {"type": "array", "minItems": 1, "maxItems": BATCH_MAX_ITEMS, "items": {
                            "type": "object", "additionalProperties": False, "properties": {
                                "code": {"type": "string", "maxLength": MAX_QUERY_CHARS},
                                "cas": {"type": "string", "maxLength": MAX_QUERY_CHARS},
                                "ref": {"type": "string", "maxLength": BATCH_MAX_REF_CHARS}}}},
                        "include": {"type": "array", "items": {"type": "string", "enum": list(BATCH_INCLUDES)}},
                        "max_per_item": {"type": "integer", "minimum": 1},
                    }}}}},
                "responses": responses({"413": {"description": "Thân yêu cầu quá lớn"}})}},
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
