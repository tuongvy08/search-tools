#!/usr/bin/env python3
"""
Đổ dữ liệu từ SQLite (products) sang PostgreSQL.

Cách dùng:
  export DATABASE_URL='postgresql://user:pass@localhost:5432/dbname'
  python scripts/migrate_sqlite_to_postgres.py /đường/dẫn/products.db

File lớn: migrate theo lô để tránh tốn RAM.
"""

from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection  # noqa: E402

BATCH = 8000
INSERT_SQL = """
    INSERT INTO products (name, code, cas, brand, size, ship, price, note)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""


def main() -> None:
    sqlite_path = (
        (sys.argv[1] if len(sys.argv) > 1 else None)
        or os.environ.get("SQLITE_PATH")
    )
    if not sqlite_path or not os.path.isfile(sqlite_path):
        print(
            "Cần đường dẫn file SQLite.\n"
            "  python scripts/migrate_sqlite_to_postgres.py /path/to/products.db\n"
            "hoặc SQLITE_PATH=...",
            file=sys.stderr,
        )
        sys.exit(1)

    sl = sqlite3.connect(sqlite_path)
    try:
        cur_sl = sl.cursor()
        cur_sl.execute(
            "SELECT name, code, cas, brand, size, ship, price, note FROM products"
        )

        pg = get_connection()
        total = 0
        try:
            with pg:
                with pg.cursor() as cur_pg:
                    cur_pg.execute("DELETE FROM products")
                    while True:
                        rows = cur_sl.fetchmany(BATCH)
                        if not rows:
                            break
                        cur_pg.executemany(INSERT_SQL, rows)
                        total += len(rows)
                        if total % (BATCH * 25) == 0:
                            print(f"  ... đã chèn ~{total} dòng", flush=True)
            print(f"Đã chuyển {total} dòng vào PostgreSQL.")
        finally:
            pg.close()
    finally:
        sl.close()


if __name__ == "__main__":
    main()
