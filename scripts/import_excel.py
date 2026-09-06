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
  - Dùng cùng advisory lock (`acquire_products_import_lock`) mà mọi đường
    ghi products khác trong app phải xin trước khi mutate.
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

from db import get_connection  # noqa: E402
from excel_io import load_product_rows_from_xlsx  # noqa: E402
from brand_gateway import (  # noqa: E402
    acquire_products_import_lock,
    inspect_replace_by_brand_scopes,
    load_brand_gateway,
    preview_import_rows_brands,
    register_and_resolve_import_rows,
    resolve_replace_by_brand_target_ids,
)

INSERT_SQL = """
    INSERT INTO products (name, code, cas, brand, size, ship, price, note, source_brand)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _rows_as_dicts(data_rows: list[tuple]) -> list[dict]:
    keys = ("name", "code", "cas", "brand", "size", "ship", "price", "note")
    return [dict(zip(keys, row)) for row in data_rows]


def _rows_as_insert_tuples(rows: list[dict]) -> list[tuple]:
    return [
        (
            row.get("name"), row.get("code"), row.get("cas"), row.get("brand"),
            row.get("size"), row.get("ship"), row.get("price"), row.get("note"),
            row.get("source_brand"),
        )
        for row in rows
    ]


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
        help="Xóa theo canonical brand + source_brand scope (an toàn hơn full delete)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ resolve Brand Gateway + đếm số dòng sẽ ghi/xóa, KHÔNG ghi vào DB.",
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
    try:
        with pg.cursor() as cur:
            gateway = load_brand_gateway(cur)

        if not gateway.table_exists:
            print(
                "Import bị từ chối: bảng brand_master không tồn tại trên database đích.\n"
                "Script này yêu cầu sql/migration_017_brand_master.sql đã được áp dụng trước.",
                file=sys.stderr,
            )
            sys.exit(1)

        import_rows = _rows_as_dicts(data_rows)
        preview_rows, preview_errors, new_brands = preview_import_rows_brands(import_rows, gateway)
        if preview_errors:
            for error in preview_errors[:20]:
                print(f"  - {error}", file=sys.stderr)
            sys.exit(1)

        if args.dry_run:
            print(f"[DRY-RUN] Brand Gateway kiểm tra OK: {len(preview_rows)}/{len(data_rows)} dòng hợp lệ.")
            for brand in new_brands:
                print(f"[DRY-RUN] Brand mới: {brand['name']} ({brand['row_count']} dòng).")
            with pg.cursor() as cur:
                if args.replace_brands:
                    _, scope_errors, delete_count = inspect_replace_by_brand_scopes(cur, preview_rows, gateway)
                    if scope_errors:
                        for error in scope_errors:
                            print(f"  - {error}", file=sys.stderr)
                        pg.rollback()
                        sys.exit(1)
                    print(f"[DRY-RUN] Sẽ xóa {delete_count} dòng hiện có theo brand/source scope.")
                elif not args.append:
                    cur.execute("SELECT COUNT(*) FROM products")
                    print(f"[DRY-RUN] Sẽ xóa TOÀN BỘ {cur.fetchone()[0]} dòng hiện có.")
                else:
                    print("[DRY-RUN] Chế độ --append: không xóa dòng nào.")
            print(f"[DRY-RUN] Sẽ chèn {len(preview_rows)} dòng mới. KHÔNG có gì được ghi vào DB.")
            pg.rollback()
            return

        try:
            with pg.cursor() as cur:
                acquire_products_import_lock(cur)

                resolved_dicts, created_brands = register_and_resolve_import_rows(cur, import_rows)
                resolved_rows = _rows_as_insert_tuples(resolved_dicts)
                scope_rows = resolved_dicts
                gateway = load_brand_gateway(cur)

                delete_target_ids: list[int] = []
                full_delete_count = 0

                if args.replace_brands:
                    brand_to_sources, scope_errors, _total_deletable = inspect_replace_by_brand_scopes(
                        cur, scope_rows, gateway
                    )
                    if scope_errors:
                        print("Import bị TỪ CHỐI (an toàn phạm vi xóa, chưa ghi gì vào DB):", file=sys.stderr)
                        for e in scope_errors:
                            print(f"  - {e}", file=sys.stderr)
                        pg.rollback()
                        sys.exit(1)
                    target_ids_by_brand = resolve_replace_by_brand_target_ids(cur, brand_to_sources)
                    delete_target_ids = [i for ids in target_ids_by_brand.values() for i in ids]
                elif not args.append:
                    cur.execute("SELECT COUNT(*) FROM products")
                    full_delete_count = cur.fetchone()[0]

                if args.replace_brands:
                    if delete_target_ids:
                        cur.execute("DELETE FROM products WHERE id = ANY(%s)", (delete_target_ids,))
                elif not args.append:
                    cur.execute("DELETE FROM products")

                cur.executemany(INSERT_SQL, resolved_rows)
            pg.commit()
        except Exception:
            pg.rollback()
            raise

        print(f"Đã import {len(resolved_rows)} dòng.")
        if created_brands:
            print(f"Đã tạo {len(created_brands)} brand mới chưa gán tiền tệ.")
    finally:
        pg.close()


if __name__ == "__main__":
    main()
