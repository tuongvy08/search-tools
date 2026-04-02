#!/usr/bin/env python3
"""
Tạo user admin đầu tiên (chỉ khi bảng app_users còn trống).

  export DATABASE_URL='...'
  export ADMIN_USERNAME=admin
  export ADMIN_PASSWORD='mat-khau-manh'
  python scripts/bootstrap_admin.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402

from db import get_connection  # noqa: E402


def main() -> None:
    username = os.environ.get("ADMIN_USERNAME", "admin").strip()
    password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not password:
        print("Đặt biến ADMIN_PASSWORD (và tuỳ chọn ADMIN_USERNAME).", file=sys.stderr)
        sys.exit(1)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM app_users")
            (n,) = cur.fetchone()
            if n > 0:
                print("Đã có user trong app_users — bỏ qua (không tạo thêm admin).")
                return
            cur.execute(
                """
                INSERT INTO app_users (username, password_hash, team_id, is_admin)
                VALUES (%s, %s, NULL, TRUE)
                """,
                (username, generate_password_hash(password)),
            )
        conn.commit()
        print(f"Đã tạo admin: {username}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
