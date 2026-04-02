#!/usr/bin/env python3
"""
Bổ sung note chi tiết cho regulatory_rules từ SQLite legacy products.db.

Nguồn: các dòng products có brand thuộc nhóm kiểm soát pháp lý.
Mỗi rule sẽ lấy note dài nhất (không rỗng) theo key (rule_type + match_field + match_value).

Cách dùng:
  python scripts/enrich_regulatory_notes_from_sqlite.py /path/to/products.db
  # hoặc SQLITE_PATH=/path/to/products.db python scripts/enrich_regulatory_notes_from_sqlite.py
"""

from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import get_connection  # noqa: E402

LEGACY_MAP = {
    'CẤM NHẬP': 'CAM_NHAP',
    'Phụ lục II': 'PHU_LUC_II',
    'Phụ lục III': 'PHU_LUC_III',
    'TỒN KHO': 'TON_KHO',
}


def choose_key(cas, name):
    cas = (cas or '').strip()
    name = (name or '').strip()
    if cas:
        return 'cas', cas
    if name:
        return 'name', name
    return None, None


def main() -> None:
    sqlite_path = (sys.argv[1] if len(sys.argv) > 1 else None) or os.environ.get('SQLITE_PATH')
    if not sqlite_path or not os.path.isfile(sqlite_path):
        print('Cần đường dẫn products.db', file=sys.stderr)
        sys.exit(1)

    sl = sqlite3.connect(sqlite_path)
    sl.row_factory = sqlite3.Row
    cur = sl.cursor()
    cur.execute(
        """
        SELECT brand, cas, name, note
        FROM products
        WHERE brand IN ('CẤM NHẬP', 'Phụ lục II', 'Phụ lục III', 'TỒN KHO')
          AND note IS NOT NULL
          AND TRIM(note) <> ''
        """
    )

    best_note = {}
    for row in cur.fetchall():
        rule_type = LEGACY_MAP[row['brand']]
        match_field, match_value = choose_key(row['cas'], row['name'])
        if not match_field:
            continue
        key = (rule_type, match_field, match_value.strip().upper())
        note = row['note'].strip()
        prev = best_note.get(key)
        if prev is None or len(note) > len(prev):
            best_note[key] = note
    sl.close()

    conn = get_connection()
    updated = 0
    try:
        with conn:
            with conn.cursor() as c:
                for (rule_type, match_field, match_norm), note in best_note.items():
                    c.execute(
                        """
                        UPDATE regulatory_rules
                           SET note = %s,
                               updated_at = NOW()
                         WHERE rule_type = %s
                           AND match_field = %s
                           AND UPPER(TRIM(match_value)) = %s
                           AND (note IS NULL OR note LIKE 'Migrated from products.brand=%%')
                        """,
                        (note, rule_type, match_field, match_norm),
                    )
                    updated += c.rowcount
        print(f'Updated rule notes: {updated}')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
