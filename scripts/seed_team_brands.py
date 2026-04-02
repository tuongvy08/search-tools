#!/usr/bin/env python3
"""
Gán mọi giá trị brand đang có trong bảng products cho một team (để team đó xem được).

  python scripts/seed_team_brands.py 1

(team id 1 thường là "Team mẫu" sau migration_002.)
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("team_id", type=int, help="ID team (teams.id)")
    args = p.parse_args()

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT brand FROM products WHERE brand IS NOT NULL AND TRIM(brand) <> ''"
                )
                brands = [r[0] for r in cur.fetchall()]
                cur.execute("DELETE FROM team_brands WHERE team_id = %s", (args.team_id,))
                if brands:
                    cur.executemany(
                        """
                        INSERT INTO team_brands (team_id, brand) VALUES (%s, %s)
                        ON CONFLICT (team_id, brand) DO NOTHING
                        """,
                        [(args.team_id, b) for b in brands],
                    )
        print(f"Đã gán {len(brands)} brand cho team_id={args.team_id}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
