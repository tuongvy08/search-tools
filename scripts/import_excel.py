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
  - Unknown brand (không có trong brand_master/brand_aliases) làm TOÀN BỘ
    import fail closed TRƯỚC KHI mutate DB (không import một phần).
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
    resolve_replace_by_brand_target_ids,
)

INSERT_SQL = """
    INSERT INTO products (name, code, cas, brand, size, ship, price, note, source_brand)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _resolve_rows_or_die(data_rows: list[tuple], gateway) -> list[tuple]:
    """Resolves every row's brand via the Brand Gateway BEFORE any mutation.

    Fails closed (prints every unknown brand, exits 1, touches nothing) if
    ANY row's brand cannot be resolved to a canonical brand -- an import is
    all-or-nothing, never partially applied with some rows silently
    skipped/dropped.
    """
    resolved: list[tuple] = []
    errors: list[str] = []

    for idx, row in enumerate(data_rows, start=2):
        name, code, cas, raw_brand, size, ship, price, note = row
        res = gateway.resolve(raw_brand)
        if not res.is_valid:
            errors.append(f"Dòng {idx}: {res.error_message}")
            continue
        resolved.append((name, code, cas, res.canonical_brand, size, ship, price, note, res.source_brand))

    if errors:
        print(
            f"Import bị TỪ CHỐI (chưa ghi gì vào DB): {len(errors)} dòng có brand "
            "không xác định trong Brand Master.",
            file=sys.stderr,
        )
        for e in errors[:20]:
            print(f"  - {e}", file=sys.stderr)
        if len(errors) > 20:
            print(f"  ... và {len(errors) - 20} lỗi khác", file=sys.stderr)
        sys.exit(1)

    return resolved


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

        # Fail closed BEFORE any mutation: resolve/validate every row first.
        resolved_rows = _resolve_rows_or_die(data_rows, gateway)
        scope_rows = [{"brand": r[3], "source_brand": r[8]} for r in resolved_rows]

        try:
            with pg.cursor() as cur:
                acquire_products_import_lock(cur)

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

                if args.dry_run:
                    print(f"[DRY-RUN] Brand Gateway resolve OK: {len(resolved_rows)}/{len(data_rows)} dòng hợp lệ.")
                    if args.replace_brands:
                        print(f"[DRY-RUN] Sẽ xóa {len(delete_target_ids)} dòng hiện có (canonical brand + source scope).")
                    elif not args.append:
                        print(f"[DRY-RUN] Sẽ xóa TOÀN BỘ {full_delete_count} dòng hiện có (full replace).")
                    else:
                        print("[DRY-RUN] Chế độ --append: không xóa dòng nào.")
                    print(f"[DRY-RUN] Sẽ chèn {len(resolved_rows)} dòng mới. KHÔNG có gì được ghi vào DB.")
                    pg.rollback()
                    return

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
    finally:
        pg.close()


if __name__ == "__main__":
    main()
