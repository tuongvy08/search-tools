#!/usr/bin/env python3
"""
Tạo một team mới trong bảng teams (để sau đó gán user và team_brands).

  set -a && source .env && set +a
  python scripts/add_team.py "Tên team hiển thị"

Nếu tên đã tồn tại, in ra id hiện có (không tạo trùng).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print('Cách dùng: python scripts/add_team.py "Tên team"', file=sys.stderr)
        sys.exit(1)
    name = sys.argv[1].strip()
    if not name:
        print("Tên team không được để trống.", file=sys.stderr)
        sys.exit(1)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM teams WHERE name = %s", (name,))
            row = cur.fetchone()
            if row:
                print(f"Team đã tồn tại: id={row[0]} — {name}")
                return
            cur.execute(
                "INSERT INTO teams (name) VALUES (%s) RETURNING id",
                (name,),
            )
            tid = cur.fetchone()[0]
        conn.commit()
        print(f"Đã tạo team id={tid}: {name}")
    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
