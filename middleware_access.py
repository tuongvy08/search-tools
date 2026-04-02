"""
Giới hạn truy cập theo IP văn phòng.

- Nếu KHÔNG cấu hình gì (env trống + bảng DB trống): cho phép mọi IP (dev).
- Nếu CÓ ít nhất một rule (OFFICE_IP_ALLOWLIST hoặc bảng office_ip_allowlist):
  chỉ cho phép IP khớp một trong các rule.

Biến môi trường:
  DISABLE_IP_ALLOWLIST=1   — tắt hoàn toàn (khuyên dùng trên máy dev)
  OFFICE_IP_ALLOWLIST      — ví dụ: 203.0.113.10,192.0.2.0/24

Rule áp dụng trên IP mà server nhận được từ kết nối (thường là IP công khai WAN
của modem văn phòng sau NAT, không phải 192.168.x.x). Sau reverse proxy (nginx),
IP khách thật thường nằm trong X-Forwarded-For — cần cấu hình proxy đúng.
"""

from __future__ import annotations

import ipaddress
import os

from flask import abort, request


def _client_ip() -> str:
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return (request.remote_addr or "").strip()


def _parse_env_allowlist() -> list[str]:
    raw = (os.environ.get("OFFICE_IP_ALLOWLIST") or "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


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


def _load_db_cidrs() -> list[str]:
    try:
        from db import get_connection

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT cidr FROM office_ip_allowlist WHERE is_active = TRUE AND cidr IS NOT NULL AND TRIM(cidr) <> ''"
                )
                return [r[0].strip() for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def register_ip_access_control(app, base_path=None):
    if base_path:
        app.logger.debug("register_ip_access_control: base_path=%s", base_path)

    @app.before_request
    def _restrict_office_ip():
        if os.environ.get("DISABLE_IP_ALLOWLIST", "").lower() in ("1", "true", "yes", "on"):
            return None
        if request.endpoint == "static" or request.path.startswith("/static"):
            return None

        env_rules = _parse_env_allowlist()
        db_rules = _load_db_cidrs()

        if not env_rules and not db_rules:
            return None

        client = _client_ip()
        for rule in env_rules:
            if _ip_matches_rule(client, rule):
                return None
        for rule in db_rules:
            if _ip_matches_rule(client, rule):
                return None

        app.logger.warning("IP denied: %s path=%s", client, request.path)
        abort(403)
