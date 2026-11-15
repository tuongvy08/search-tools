#!/usr/bin/env python3
"""Cấp / liệt kê / thu hồi thẻ (token) cho cổng API máy (machine_api.py).

    python scripts/machine_token.py new  --file /etc/search-tools/machine_tokens.json \
        --id chatbot-khach --team-id 7 --days 365
    python scripts/machine_token.py list   --file ...
    python scripts/machine_token.py revoke --file ... --id chatbot-khach

- `new` in THẺ đúng MỘT lần ra màn hình; file chỉ lưu mã băm SHA-256 (không khôi phục
  được thẻ từ file). Hãy chép thẻ vào nơi giữ bí mật của hệ thống bên ngoài (ví dụ
  Credentials của n8n) ngay lúc đó.
- File luôn được ghi nguyên tử (ghi tệp tạm rồi đổi tên) với quyền 0600; dịch vụ tự đọc
  lại trong vài giây, không cần khởi động lại.
- Công cụ không kết nối database và không in thẻ/mã băm ở lệnh `list`.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import sys
import tempfile
from datetime import datetime, timedelta, timezone

TOKEN_PREFIX = "stm_"


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {"tokens": []}
    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict) or not isinstance(document.get("tokens"), list):
        raise SystemExit("File thẻ có cấu trúc không hợp lệ (cần {\"tokens\": [...]}).")
    return document


def _save(path: str, document: dict) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".tokens-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


class _locked:
    """Khóa độc quyền (flock trên thư mục chứa file thẻ, không tạo tệp phụ) khi đọc-sửa-ghi:
    hai quản trị viên chạy cùng lúc không ghi đè nhau."""

    def __init__(self, path: str) -> None:
        self.directory = os.path.dirname(os.path.abspath(path)) or "."

    def __enter__(self):
        os.makedirs(self.directory, exist_ok=True)
        self.fd = os.open(self.directory, os.O_RDONLY)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def cmd_new(args: argparse.Namespace) -> int:
    with _locked(args.file):
        return _cmd_new(args)


def _cmd_new(args: argparse.Namespace) -> int:
    document = _load(args.file)
    if any(isinstance(entry, dict) and entry.get("id") == args.id for entry in document["tokens"]):
        raise SystemExit(f"Đã có thẻ với id '{args.id}'. Hãy thu hồi trước hoặc chọn id khác.")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)  # >= 256 bit ngẫu nhiên
    entry = {
        "id": args.id,
        "sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "team_id": args.team_id,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=args.days)).isoformat(timespec="seconds"),
        "enabled": True,
        "stock_detail": args.stock_detail,
        "rate_per_minute": args.rate_per_minute,
    }
    if args.allowed_ip:
        entry["allowed_ips"] = args.allowed_ip
    # Dùng chính bộ kiểm của cổng: không cấp thẻ mà cổng sẽ âm thầm bỏ qua.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from machine_api import _validate_entry  # noqa: E402
    try:
        _validate_entry(entry)
    except (ValueError, TypeError) as exc:
        raise SystemExit(f"Giá trị không hợp lệ ({exc}): id chỉ gồm chữ thường/số/gạch (2-41 ký tự), "
                         "team-id > 0, rate 1-600, IP/CIDR đúng định dạng.")
    document["tokens"].append(entry)
    _save(args.file, document)
    print(f"Đã tạo thẻ '{args.id}' (team {args.team_id}, hết hạn {entry['expires_at']}).")
    print("THẺ (chỉ hiện một lần, hãy chép ngay và không gửi qua chat/email):")
    print(token)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    document = _load(args.file)
    now = datetime.now(timezone.utc)
    for entry in document["tokens"]:
        if not isinstance(entry, dict):
            print("(mục hỏng, không phải đối tượng — cổng sẽ bỏ qua)")
            continue
        try:
            expires = datetime.fromisoformat(str(entry.get("expires_at")).replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            state = "HẾT HẠN" if expires <= now else "còn hạn"
        except ValueError:
            state = "HẠN SAI ĐỊNH DẠNG"
        status = "bật" if entry.get("enabled", True) else "TẮT"
        print(f"{entry.get('id')}: team={entry.get('team_id')} {status} {state} "
              f"tồn_kho={entry.get('stock_detail', 'status')} "
              f"tần_suất={entry.get('rate_per_minute', 60)}/phút "
              f"ip={'hạn chế' if entry.get('allowed_ips') else 'không hạn chế'}")
    if not document["tokens"]:
        print("(chưa có thẻ nào)")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    with _locked(args.file):
        return _cmd_revoke(args)


def _cmd_revoke(args: argparse.Namespace) -> int:
    document = _load(args.file)
    matched = [entry for entry in document["tokens"] if isinstance(entry, dict) and entry.get("id") == args.id]
    if not matched:
        raise SystemExit(f"Không có thẻ id '{args.id}'.")
    if args.delete:
        document["tokens"] = [entry for entry in document["tokens"] if not (isinstance(entry, dict) and entry.get("id") == args.id)]
    else:
        for entry in matched:
            entry["enabled"] = False
    _save(args.file, document)
    print(f"Đã {'xóa' if args.delete else 'tắt'} thẻ '{args.id}'. Có hiệu lực sau vài giây.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="tạo thẻ mới")
    new.add_argument("--file", required=True)
    new.add_argument("--id", required=True, help="tên gợi nhớ, chữ thường/số/gạch, ví dụ chatbot-khach")
    new.add_argument("--team-id", required=True, type=int, help="team đã tạo ở trang admin")
    new.add_argument("--days", type=int, default=365, help="số ngày hiệu lực (mặc định 365)")
    new.add_argument("--stock-detail", choices=["status", "quantity"], default="status",
                     help="status = chỉ Còn/Hết hàng (cho khách); quantity = có số lượng (nội bộ)")
    new.add_argument("--rate-per-minute", type=int, default=60)
    new.add_argument("--allowed-ip", action="append", help="IP/CIDR được phép (lặp lại được); bỏ trống = không hạn chế")
    new.set_defaults(func=cmd_new)
    listing = sub.add_parser("list", help="liệt kê thẻ (không in thẻ hay mã băm)")
    listing.add_argument("--file", required=True)
    listing.set_defaults(func=cmd_list)
    revoke = sub.add_parser("revoke", help="tắt (hoặc xóa) một thẻ")
    revoke.add_argument("--file", required=True)
    revoke.add_argument("--id", required=True)
    revoke.add_argument("--delete", action="store_true", help="xóa hẳn thay vì chỉ tắt")
    revoke.set_defaults(func=cmd_revoke)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
