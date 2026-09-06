#!/usr/bin/env python3
"""
Đổ dữ liệu từ SQLite (products) sang PostgreSQL.

*** DEPRECATED (Phase 6B2B2) ***
Đây là công cụ one-time legacy, viết ra từ trước khi có canonical Brand
Master (migration_017): nó bulk-insert `products` không qua Brand Gateway
và không set `products.source_brand`, nên KHÔNG còn an toàn để chạy trên
bất kỳ database nào đã áp dụng migration_017 trở đi -- sẽ vi phạm ràng buộc
`chk_products_source_brand_not_null` / FK `fk_products_brand_master`.
Script fail closed (không đụng dữ liệu) nếu phát hiện `brand_master` đã tồn
tại trên database đích. Xem `scripts/import_excel.py` cho import hiện hành.

Cách dùng (chỉ trên database CHƯA có brand_master):
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
from brand_gateway import LegacyMigrationBlockedError, refuse_if_canonical_brand_master_present  # noqa: E402

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

    pg = get_connection()
    try:
        with pg.cursor() as cur_pg:
            try:
                refuse_if_canonical_brand_master_present(cur_pg, "migrate_sqlite_to_postgres.py")
            except LegacyMigrationBlockedError as e:
                print(str(e), file=sys.stderr)
                sys.exit(2)
    finally:
        pg.close()

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
                    # Re-check inside the write transaction: a concurrent
                    # migration_017 run between the preflight check above and
                    # here must still block this destructive DELETE+bulk-insert.
                    try:
                        refuse_if_canonical_brand_master_present(cur_pg, "migrate_sqlite_to_postgres.py")
                    except LegacyMigrationBlockedError as e:
                        print(str(e), file=sys.stderr)
                        sys.exit(2)

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
