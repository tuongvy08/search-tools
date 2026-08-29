"""End-to-end /search compliance precedence checks (local DB only)."""

import os
import re
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

import search  # noqa: E402


def _local_dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    host = urlparse(dsn).hostname or ""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    return dsn


class RecordingCursor:
    def __init__(self, cursor, recorder):
        self._cursor = cursor
        self._recorder = recorder

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._cursor.close()

    def execute(self, query, params=None):
        self._recorder.append((query, params or ()))
        return self._cursor.execute(query, params)

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchone(self):
        return self._cursor.fetchone()


class RecordingConnection:
    def __init__(self, dsn, recorder):
        self._conn = psycopg2.connect(dsn)
        self._recorder = recorder

    def cursor(self, *args, **kwargs):
        return RecordingCursor(self._conn.cursor(*args, **kwargs), self._recorder)

    def close(self):
        self._conn.close()


@unittest.skipUnless(_local_dsn(), "local DATABASE_URL required")
class SearchCompliancePrecedenceTests(unittest.TestCase):
    PREFIX = "CURSOR_PRECEDENCE"
    BRAND_ENABLED = "CURSOR_PRECEDENCE_ENABLED"
    BRAND_DISABLED = "CURSOR_PRECEDENCE_DISABLED"
    BRAND_ENABLED_BLANK = "CURSOR_PRECEDENCE_ENABLED_BLANK"
    BRAND_NOTE_ONLY = "CURSOR_PRECEDENCE_NOTE_ONLY"
    CAS_LEGACY = "CURSOR-PRECEDENCE-CAS"

    @classmethod
    def setUpClass(cls):
        cls.conn = psycopg2.connect(_local_dsn())
        cls.conn.autocommit = True
        cls._ensure_schema()
        cls._reset_fixture()

    @classmethod
    def tearDownClass(cls):
        try:
            cls._cleanup_fixture()
        finally:
            cls.conn.close()

    @classmethod
    def _ensure_schema(cls):
        with cls.conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'products'
                  AND column_name IN ('manual_compliance', 'manual_compliance_note')
                HAVING COUNT(*) = 2
                """
            )
            if cur.fetchone() is None:
                raise unittest.SkipTest("Run sql/migration_011_manual_compliance.sql on local DB first.")
            cur.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = 'brand_compliance_settings'
                """
            )
            if cur.fetchone() is None:
                raise unittest.SkipTest("Run sql/migration_011_manual_compliance.sql on local DB first.")

    @classmethod
    def _cleanup_fixture(cls):
        brands = [cls.BRAND_ENABLED, cls.BRAND_DISABLED, cls.BRAND_ENABLED_BLANK, cls.BRAND_NOTE_ONLY]
        with cls.conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM regulatory_rules
                WHERE UPPER(TRIM(match_value)) = ANY(%s)
                """,
                ([cls.CAS_LEGACY],),
            )
            cur.execute(
                """
                DELETE FROM products
                WHERE UPPER(TRIM(code)) LIKE %s
                   OR UPPER(TRIM(COALESCE(brand, ''))) = ANY(%s)
                """,
                (f"{cls.PREFIX}%", [brand.upper() for brand in brands]),
            )
            cur.execute(
                "DELETE FROM brand_compliance_settings WHERE brand_norm = ANY(%s)",
                ([brand.upper() for brand in brands],),
            )

    @classmethod
    def _reset_fixture(cls):
        cls._cleanup_fixture()
        with cls.conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO brand_compliance_settings (brand_norm, manual_compliance_priority)
                VALUES (%s, %s)
                ON CONFLICT (brand_norm)
                DO UPDATE SET manual_compliance_priority = EXCLUDED.manual_compliance_priority
                """,
                [
                    (cls.BRAND_ENABLED.upper(), True),
                    (cls.BRAND_DISABLED.upper(), False),
                    (cls.BRAND_ENABLED_BLANK.upper(), True),
                    (cls.BRAND_NOTE_ONLY.upper(), True),
                ],
            )
            cur.execute(
                """
                INSERT INTO regulatory_rules
                    (rule_type, rule_label, match_field, match_value, priority, is_active, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                ("CAM_NHAP", "CẤM NHẬP", "cas", cls.CAS_LEGACY, 10, True, "legacy regulatory note"),
            )
            cur.executemany(
                """
                INSERT INTO products
                    (name, code, cas, brand, size, ship, price, note, manual_compliance, manual_compliance_note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        "Manual conflict",
                        f"{cls.PREFIX}_MANUAL_CONFLICT",
                        cls.CAS_LEGACY,
                        cls.BRAND_ENABLED,
                        "1g",
                        "1",
                        "100",
                        "product note manual",
                        "Được bán",
                        "manual note only",
                    ),
                    (
                        "Disabled conflict",
                        f"{cls.PREFIX}_DISABLED_CONFLICT",
                        cls.CAS_LEGACY,
                        cls.BRAND_DISABLED,
                        "1g",
                        "1",
                        "100",
                        "product note disabled",
                        "Được bán",
                        "disabled manual note",
                    ),
                    (
                        "Manual no cas",
                        f"{cls.PREFIX}_MANUAL_NO_CAS",
                        "",
                        cls.BRAND_ENABLED,
                        "1g",
                        "1",
                        "100",
                        "product note no cas",
                        "Được bán",
                        "manual no cas note",
                    ),
                    (
                        "Blank manual legacy",
                        f"{cls.PREFIX}_BLANK_LEGACY",
                        cls.CAS_LEGACY,
                        cls.BRAND_ENABLED_BLANK,
                        "1g",
                        "1",
                        "100",
                        "product note blank legacy",
                        "",
                        "ignored note",
                    ),
                    (
                        "Blank manual no cas",
                        f"{cls.PREFIX}_BLANK_NO_CAS",
                        "",
                        cls.BRAND_ENABLED_BLANK,
                        "1g",
                        "1",
                        "100",
                        "product note blank no cas",
                        "",
                        "",
                    ),
                    (
                        "Blank manual unknown cas",
                        f"{cls.PREFIX}_BLANK_UNKNOWN_CAS",
                        "CURSOR-PRECEDENCE-UNKNOWN-CAS",
                        cls.BRAND_ENABLED_BLANK,
                        "1g",
                        "1",
                        "100",
                        "product note unknown cas",
                        "",
                        "",
                    ),
                    (
                        "Note only manual",
                        f"{cls.PREFIX}_NOTE_ONLY",
                        "CURSOR-PRECEDENCE-NOTE-ONLY-CAS",
                        cls.BRAND_NOTE_ONLY,
                        "1g",
                        "1",
                        "100",
                        "product note note-only",
                        None,
                        "note without manual compliance",
                    ),
                ],
            )

    def _call_search(self, query=None):
        recorder = []

        def _recording_connection():
            return RecordingConnection(_local_dsn(), recorder)

        with patch("search.get_connection", _recording_connection):
            search.app.testing = True
            with search.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["authenticated"] = True
                    sess["is_admin"] = True
                response = client.get("/search", query_string={"query": query or self.PREFIX})

        self.assertEqual(response.status_code, 200)
        return response.get_json()["results"], recorder

    def _rows_by_code(self):
        rows, recorder = self._call_search()
        return {row["Code"]: row for row in rows}, recorder

    def _main_search_query(self, recorder):
        matches = [
            item
            for item in recorder
            if "FROM products p" in item[0] and "brand_compliance_settings" in item[0]
        ]
        self.assertEqual(len(matches), 1)
        return matches[0]

    def test_search_compliance_precedence_rules(self):
        rows, recorder = self._rows_by_code()

        manual = rows[f"{self.PREFIX}_MANUAL_CONFLICT"]
        self.assertEqual(manual["compliance"], "Được bán")
        self.assertEqual(manual["Compliance_Status"], "Được bán")
        self.assertEqual(manual["compliance_note"], "manual note only")
        self.assertEqual(manual["compliance_css"], "warning-duoc-ban")
        self.assertEqual(manual["compliance_source"], "manual")
        self.assertEqual(manual["note"], "product note manual")
        self.assertNotIn("legacy regulatory note", manual["compliance_note"])

        disabled = rows[f"{self.PREFIX}_DISABLED_CONFLICT"]
        self.assertEqual(disabled["compliance"], "CẤM NHẬP")
        self.assertEqual(disabled["compliance_note"], "legacy regulatory note")
        self.assertEqual(disabled["compliance_source"], "legacy")

        no_cas = rows[f"{self.PREFIX}_MANUAL_NO_CAS"]
        self.assertEqual(no_cas["compliance"], "Được bán")
        self.assertEqual(no_cas["compliance_source"], "manual")

        blank_legacy = rows[f"{self.PREFIX}_BLANK_LEGACY"]
        self.assertEqual(blank_legacy["compliance"], "CẤM NHẬP")
        self.assertEqual(blank_legacy["compliance_note"], "legacy regulatory note")
        self.assertEqual(blank_legacy["compliance_source"], "legacy")

        blank_no_cas = rows[f"{self.PREFIX}_BLANK_NO_CAS"]
        self.assertEqual(blank_no_cas["compliance"], "Chưa xác định")
        self.assertEqual(blank_no_cas["compliance_note"], "")
        self.assertEqual(blank_no_cas["compliance_source"], "unresolved")

        blank_unknown_cas = rows[f"{self.PREFIX}_BLANK_UNKNOWN_CAS"]
        self.assertEqual(blank_unknown_cas["compliance"], "Không phát hiện hạn chế")
        self.assertEqual(blank_unknown_cas["compliance_note"], "")
        self.assertEqual(blank_unknown_cas["compliance_source"], "unresolved")

        note_only = rows[f"{self.PREFIX}_NOTE_ONLY"]
        self.assertEqual(note_only["compliance"], "Không phát hiện hạn chế")
        self.assertEqual(note_only["compliance_note"], "")
        self.assertEqual(note_only["note"], "product note note-only")

        main_query, _params = self._main_search_query(recorder)
        self.assertIn("LEFT JOIN brand_compliance_settings bcs", main_query)
        self.assertIn("NULLIF(TRIM(COALESCE(p.manual_compliance, '')), '') IS NOT NULL", main_query)

    def test_search_has_no_per_row_query(self):
        rows, recorder = self._call_search()
        self.assertGreaterEqual(len(rows), 7)
        self.assertEqual(len(recorder), 2)

    def test_specific_search_plan_uses_trigram_index(self):
        _rows, recorder = self._call_search(f"{self.PREFIX}_MANUAL_CONFLICT")
        query, params = self._main_search_query(recorder)
        with self.conn.cursor() as cur:
            cur.execute("BEGIN")
            try:
                cur.execute("SET LOCAL enable_seqscan = off")
                cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + query, params)
                plan = "\n".join(row[0] for row in cur.fetchall())
            finally:
                cur.execute("ROLLBACK")
        self.assertRegex(plan, re.compile(r"idx_products_(name|code|cas)_trgm"))


if __name__ == "__main__":
    unittest.main()
