#!/usr/bin/env python3
"""
Chuyển dữ liệu pháp lý cũ khỏi products.brand sang regulatory_rules.

Nguồn legacy: products.brand IN ('CẤM NHẬP','Phụ lục II','Phụ lục III','TỒN KHO')
Đích mới: regulatory_rules (ưu tiên match theo CAS, fallback theo NAME nếu thiếu CAS).

Mặc định KHÔNG xóa dòng legacy trong products để an toàn.
Thêm --delete-legacy để xóa sau khi migrate thành công.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection  # noqa: E402

LEGACY_MAP = {
    'CẤM NHẬP': ('CAM_NHAP', 'CẤM NHẬP', 10),
    'Phụ lục II': ('PHU_LUC_II', 'Phụ lục II', 20),
    'Phụ lục III': ('PHU_LUC_III', 'Phụ lục III', 30),
    'TỒN KHO': ('TON_KHO', 'TỒN KHO', 40),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--delete-legacy', action='store_true', help='Xóa dòng legacy khỏi products sau khi tạo rules')
    args = parser.parse_args()

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT brand, NULLIF(TRIM(cas), ''), NULLIF(TRIM(name), '')
                    FROM products
                    WHERE brand IN ('CẤM NHẬP', 'Phụ lục II', 'Phụ lục III', 'TỒN KHO')
                    """
                )
                rows = cur.fetchall()

                inserted = 0
                skipped = 0
                for brand, cas, name in rows:
                    rule_type, rule_label, priority = LEGACY_MAP[brand]

                    if cas:
                        match_field, match_value = 'cas', cas
                    elif name:
                        match_field, match_value = 'name', name
                    else:
                        skipped += 1
                        continue

                    cur.execute(
                        """
                        INSERT INTO regulatory_rules (rule_type, rule_label, match_field, match_value, priority, is_active, note)
                        VALUES (%s, %s, %s, %s, %s, TRUE, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (
                            rule_type,
                            rule_label,
                            match_field,
                            match_value,
                            priority,
                            f'Migrated from products.brand={brand}',
                        ),
                    )
                    inserted += cur.rowcount

                if args.delete_legacy:
                    cur.execute(
                        "DELETE FROM products WHERE brand IN ('CẤM NHẬP', 'Phụ lục II', 'Phụ lục III', 'TỒN KHO')"
                    )
                    deleted = cur.rowcount
                else:
                    deleted = 0

        print(f'Inserted rules: {inserted}')
        print(f'Skipped rows (no cas/name): {skipped}')
        print(f'Deleted legacy product rows: {deleted}')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
