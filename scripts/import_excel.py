#!/usr/bin/env python3
"""
Nhập sản phẩm từ Excel (.xlsx) vào PostgreSQL.

Dòng đầu: name, code, cas, brand, size, ship, price, note

Phase 6B2B2: nâng cấp để tương thích với canonical Brand Master
(migration_017 -- `products.source_brand NOT NULL` + FK `products.brand ->
brand_master.name`). Mọi dòng phải được resolve qua Brand Gateway TRƯỚC khi
ghi bất kỳ thay đổi nào xuống DB:
  - brand trong file (alias hoặc canonical) -> canonical brand chuẩn.
  - source_brand được ghi lại (giá trị alias/brand gốc trong file).
  - Unknown brand được báo trong --dry-run và được tự tạo trong brand_master
    khi apply, chưa gán currency và không tự cấp quyền team.
  - `--replace-brands-from-file` không còn xóa theo brand text thô của file
    (đã lỗi thời từ khi products.brand là canonical) -- dùng cùng logic an
    toàn phạm vi (`inspect_replace_by_brand_scopes` /
    `resolve_replace_by_brand_target_ids`) mà `/admin/imports/apply` dùng,
    từ chối xóa toàn bộ canonical brand khi thiếu source_brand scope.
  - Dùng cùng thứ tự advisory lock products rồi danh mục tình trạng như
    worker web; cả hai khóa được giữ đến commit/rollback.
  - Không in credential/DSN ra log/stdout/stderr.

Chế độ:
  (mặc định)     Xóa TOÀN BỘ products rồi import (giống import full cũ).
  --append       Chỉ thêm dòng, không xóa.
  --replace-brands-from-file
                 Xóa trong DB các dòng thuộc đúng phạm vi canonical brand +
                 source_brand xuất hiện trong file, rồi chèn lại toàn bộ
                 dòng trong file. Bị từ chối nếu một canonical brand có
                 nhiều source_brand trong DB nhưng file không chỉ rõ phạm vi.
  --dry-run      Chỉ resolve qua Brand Gateway + đếm số dòng sẽ ghi/xóa,
                 KHÔNG ghi gì vào DB (rollback toàn bộ transaction).

  python scripts/import_excel.py du_lieu.xlsx --replace-brands-from-file
  python scripts/import_excel.py du_lieu.xlsx --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _ROOT)
sys.path.insert(0, _SCRIPTS)

from db import get_connection
from brand_gateway import acquire_products_import_lock
from import_engine import ImportProblem, workbook_rows, create_stage, fill_stage, build_plan, apply_plan
from regulatory import acquire_regulatory_lock


def main():
    parser = argparse.ArgumentParser(description="Import products từ Excel vào PostgreSQL")
    parser.add_argument("xlsx_path")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--append", action="store_true")
    modes.add_argument("--replace-brands-from-file", dest="replace_brands", action="store_true")
    modes.add_argument("--upsert", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    mode = "append" if args.append else "replace_by_brand" if args.replace_brands else "upsert" if args.upsert else "replace_all"
    pg = get_connection()
    try:
        with pg.cursor() as cur:
            create_stage(cur)
            fill_stage(cur, workbook_rows(args.xlsx_path))
            acquire_products_import_lock(cur)
            acquire_regulatory_lock(cur)
            plan = build_plan(cur, mode, apply=not args.dry_run)
            if args.dry_run:
                print(f"[DRY-RUN] {plan['row_count']} dòng; sẽ chèn {plan['inserted']}, cập nhật {plan['updated']}, xóa {plan['deleted']}.")
                for brand in plan['new_brands']:
                    print(f"[DRY-RUN] Brand mới: {brand}")
                pg.rollback()
                return
            apply_plan(cur, plan)
        pg.commit()
        print(f"Đã import {plan['row_count']} dòng; tạo {len(plan['new_brands'])} brand mới chưa gán tiền tệ.")
    except Exception as exc:
        pg.rollback()
        print(str(exc) if isinstance(exc, ImportProblem) else "Import thất bại; chưa ghi dữ liệu. Kiểm tra cấu hình database/workbook.", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        pg.close()


if __name__ == "__main__":
    main()
