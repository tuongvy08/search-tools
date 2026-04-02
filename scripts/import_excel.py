#!/usr/bin/env python3
"""
Nhập sản phẩm từ Excel (.xlsx) vào PostgreSQL.

Dòng đầu: name, code, cas, brand, size, ship, price, note

Chế độ:
  (mặc định)     Xóa TOÀN BỘ products rồi import (giống import full cũ).
  --append       Chỉ thêm dòng, không xóa.
  --replace-brands-from-file
                 Xóa trong DB các dòng có brand xuất hiện trong file Excel,
                 rồi chèn lại toàn bộ dòng trong file (giống logic cũ của bạn).

  python scripts/import_excel.py du_lieu.xlsx --replace-brands-from-file
"""

from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SCRIPTS)

from db import get_connection  # noqa: E402
from excel_io import load_product_rows_from_xlsx  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Import products từ Excel vào PostgreSQL")
    parser.add_argument("xlsx_path", help="Đường dẫn file .xlsx")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Thêm vào dữ liệu hiện có (không xóa trước)",
    )
    parser.add_argument(
        "--replace-brands-from-file",
        action="store_true",
        dest="replace_brands",
        help="Xóa theo brand có trong file rồi import (an toàn hơn full delete)",
    )
    args = parser.parse_args()

    if args.append and args.replace_brands:
        print("Chọn một trong hai: --append hoặc --replace-brands-from-file", file=sys.stderr)
        sys.exit(1)

    try:
        data_rows = load_product_rows_from_xlsx(args.xlsx_path)
    except (OSError, ValueError, RuntimeError) as e:
        print(e, file=sys.stderr)
        sys.exit(1)

    pg = get_connection()
    insert_sql = """
        INSERT INTO products (name, code, cas, brand, size, ship, price, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    try:
        with pg:
            with pg.cursor() as cur:
                if args.append:
                    pass
                elif args.replace_brands:
                    brands = sorted({r[3] for r in data_rows if r[3]})
                    if not brands:
                        print("File không có brand — không thể xóa theo brand.", file=sys.stderr)
                        sys.exit(1)
                    cur.execute(
                        "DELETE FROM products WHERE brand = ANY(%s)",
                        (brands,),
                    )
                else:
                    cur.execute("DELETE FROM products")

                cur.executemany(insert_sql, data_rows)
        print(f"Đã import {len(data_rows)} dòng.")
    finally:
        pg.close()


if __name__ == "__main__":
    main()
