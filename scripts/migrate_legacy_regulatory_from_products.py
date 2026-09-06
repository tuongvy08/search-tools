#!/usr/bin/env python3
"""
Chuyển dữ liệu pháp lý cũ khỏi products.brand sang regulatory_rules.

*** DEPRECATED (Phase 6B2B2) ***
Đây là công cụ one-time legacy chạy TRƯỚC canonical Brand Master
(migration_017): các pseudo-brand 'CẤM NHẬP'/'Phụ lục II'/'Phụ lục III'/
'TỒN KHO' không nằm trong brand_master, và migration_017's preflight fail
closed nếu products.brand còn brand chưa được map -- nghĩa là script này
(và --delete-legacy) đáng lẽ đã phải chạy XONG trước khi migration_017 có
thể pass. Chạy lại nó SAU khi brand_master đã tồn tại không còn ý nghĩa
(dữ liệu nguồn đáng lẽ không còn) và không an toàn để "sửa rộng" cho phù
hợp Brand Master. Script fail closed nếu phát hiện `brand_master` đã tồn
tại trên database đích.

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
from brand_gateway import LegacyMigrationBlockedError, refuse_if_canonical_brand_master_present  # noqa: E402

LEGACY_MAP = {
    'CẤM NHẬP': ('CAM_NHAP', 'CẤM NHẬP', 10),
    'Phụ lục II': ('PHU_LUC_II', 'Phụ lục II', 20),
    'Phụ lục III': ('PHU_LUC_III', 'Phụ lục III', 30),
    'TỒN KHO': ('TON_KHO', 'TỒN KHO', 40),
}


def assert_legacy_delete_is_safe(cur, skipped: int) -> None:
    """Fail closed before deleting legacy source rows.

    Every distinct source row must have a usable match key and an ACTIVE
    equivalent regulatory rule. This catches ON CONFLICT against an existing
    inactive rule, which would otherwise leave compliance inactive while the
    only legacy source row is deleted.
    """
    if skipped:
        raise RuntimeError(
            f"Refusing --delete-legacy: {skipped} distinct source row(s) have no CAS or name."
        )

    cur.execute(
        """
        WITH legacy AS (
            SELECT DISTINCT
                brand,
                CASE
                    WHEN NULLIF(TRIM(cas), '') IS NOT NULL THEN 'cas'
                    ELSE 'name'
                END AS match_field,
                COALESCE(NULLIF(TRIM(cas), ''), NULLIF(TRIM(name), '')) AS match_value
            FROM products
            WHERE brand IN ('CẤM NHẬP', 'Phụ lục II', 'Phụ lục III', 'TỒN KHO')
        ), mapped AS (
            SELECT
                match_field,
                match_value,
                CASE brand
                    WHEN 'CẤM NHẬP' THEN 'CAM_NHAP'
                    WHEN 'Phụ lục II' THEN 'PHU_LUC_II'
                    WHEN 'Phụ lục III' THEN 'PHU_LUC_III'
                    WHEN 'TỒN KHO' THEN 'TON_KHO'
                END AS rule_type
            FROM legacy
            WHERE match_value IS NOT NULL
        )
        SELECT COUNT(*)
        FROM mapped m
        WHERE NOT EXISTS (
            SELECT 1
            FROM regulatory_rules r
            WHERE r.rule_type = m.rule_type
              AND r.match_field = m.match_field
              AND UPPER(TRIM(r.match_value)) = UPPER(TRIM(m.match_value))
              AND r.is_active = TRUE
        )
        """
    )
    missing_active = cur.fetchone()[0]
    if missing_active:
        raise RuntimeError(
            "Refusing --delete-legacy: "
            f"{missing_active} distinct source key(s) lack an active equivalent regulatory rule."
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--delete-legacy', action='store_true', help='Xóa dòng legacy khỏi products sau khi tạo rules')
    args = parser.parse_args()

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                try:
                    refuse_if_canonical_brand_master_present(cur, "migrate_legacy_regulatory_from_products.py")
                except LegacyMigrationBlockedError as e:
                    print(str(e), file=sys.stderr)
                    sys.exit(2)

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
                    assert_legacy_delete_is_safe(cur, skipped)
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
