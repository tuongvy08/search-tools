#!/usr/bin/env python3
"""
Thêm user thường (không admin), gắn với một team (chỉ xem brand đã gán trong team_brands).

  export DATABASE_URL='...'
  python scripts/add_user.py ten_dang_nhap mat_khau 1

(team_id lấy từ bảng teams, ví dụ 1 = Team mẫu)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash  # noqa: E402

from db import get_connection  # noqa: E402


def main() -> None:
    if len(sys.argv) != 4:
        print("Cách dùng: python scripts/add_user.py <username> <password> <team_id>", file=sys.stderr)
        sys.exit(1)
    username, password, team_id_s = sys.argv[1], sys.argv[2], sys.argv[3]
    team_id = int(team_id_s)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_users (username, password_hash, team_id, is_admin)
                VALUES (%s, %s, %s, FALSE)
                """,
                (username, generate_password_hash(password), team_id),
            )
        conn.commit()
        print(f"Đã tạo user {username} (team_id={team_id}).")
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
