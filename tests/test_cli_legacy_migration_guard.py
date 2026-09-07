"""Focused tests for Phase 6B2B2 Section 6 (Legacy CLI blocker).

1. `scripts/import_excel.py` upgraded to use the Brand Gateway:
   - unknown brand is registered atomically, with no team grant.
   - alias -> canonical resolution + `source_brand` written correctly.
   - `--dry-run` resolves/counts but writes nothing.
   - `--replace-brands-from-file` replaces every historical `source_brand`
     under each Brand Gateway-resolved canonical brand.
   - the products-import advisory lock is acquired before any mutation.

2. `scripts/migrate_sqlite_to_postgres.py` and
   `scripts/migrate_legacy_regulatory_from_products.py` refuse to run (fail
   closed, exit code 2, no data touched) once `brand_master` exists on the
   target database.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
from dotenv import load_dotenv
from openpyxl import Workbook

from brand_gateway import (
    PRODUCTS_IMPORT_LOCK_KEY,
    LegacyMigrationBlockedError,
    refuse_if_canonical_brand_master_present,
)
from pg_temp_db import (
    apply_brand_master_and_currency_migrations,
    apply_dynamic_brand_currency_migration,
    create_full_schema_temp_db,
    drop_temp_db,
    probe_postgres_reachable,
)

load_dotenv(dotenv_path=".env")

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _ROOT / "scripts"
_MIGRATION_017_PATH = _ROOT / "sql" / "migration_017_brand_master.sql"

sys.path.insert(0, str(_SCRIPTS_DIR))
import import_excel  # noqa: E402
import migrate_sqlite_to_postgres  # noqa: E402
import migrate_legacy_regulatory_from_products  # noqa: E402


class ImportExcelCanonicalReplaceUnitTests(unittest.TestCase):
    def test_incoming_rows_are_counted_by_canonical_brand(self):
        counts = import_excel._canonical_brand_insert_counts(
            [
                {"brand": "Sigma", "canonical_brand": "Sigma"},
                {"brand": "Sigma", "canonical_brand": "Sigma"},
                {"brand": "PhytoLab", "canonical_brand": "PhytoLab"},
            ]
        )
        self.assertEqual(counts, {"Sigma": 2, "PhytoLab": 1})

    def test_missing_resolved_canonical_brand_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "canonical brand"):
            import_excel._canonical_brand_insert_counts([{"brand": "  "}])

    def test_dry_run_plan_renders_exact_production_sized_scope(self):
        output = StringIO()
        with redirect_stdout(output):
            import_excel._print_canonical_brand_replace_plan(
                {"AccuStandard": {"delete": 26371, "insert": 13155}}
            )
        text = output.getvalue()
        self.assertIn("AccuStandard: xóa 26,371, chèn 13,155 dòng", text)
        self.assertIn("TỔNG: xóa 26,371, chèn 13,155 dòng", text)


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class LegacyRegulatoryDeleteSafetyTests(unittest.TestCase):
    def setUp(self):
        self.db_name, self.dsn = create_full_schema_temp_db()
        self.conn = psycopg2.connect(self.dsn)
        self.conn.autocommit = True

    def tearDown(self):
        self.conn.close()
        drop_temp_db(self.db_name)

    def test_blank_source_key_is_rejected_before_delete(self):
        with self.conn.cursor() as cur:
            with self.assertRaisesRegex(RuntimeError, "no CAS or name"):
                migrate_legacy_regulatory_from_products.assert_legacy_delete_is_safe(cur, 1)

    def test_inactive_conflict_is_rejected_before_delete(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products (name, cas, brand) VALUES (%s, %s, %s)",
                ("Legacy source", "50-00-0", "CẤM NHẬP"),
            )
            cur.execute(
                """
                INSERT INTO regulatory_rules
                    (rule_type, rule_label, match_field, match_value, priority, is_active)
                VALUES ('CAM_NHAP', 'CẤM NHẬP', 'cas', '50-00-0', 10, FALSE)
                """
            )
            with self.assertRaisesRegex(RuntimeError, "lack an active equivalent"):
                migrate_legacy_regulatory_from_products.assert_legacy_delete_is_safe(cur, 0)


def _write_xlsx(path, headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])
    wb.save(path)


class RefuseIfCanonicalBrandMasterPresentUnitTests(unittest.TestCase):
    def test_raises_when_table_exists(self):
        class FakeCursor:
            def execute(self, *a, **k):
                pass

            def fetchone(self):
                return ("brand_master",)

        with self.assertRaises(LegacyMigrationBlockedError):
            refuse_if_canonical_brand_master_present(FakeCursor(), "some_script.py")

    def test_noop_when_table_missing(self):
        class FakeCursor:
            def execute(self, *a, **k):
                pass

            def fetchone(self):
                return (None,)

        # Must not raise.
        refuse_if_canonical_brand_master_present(FakeCursor(), "some_script.py")


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class ImportExcelBrandGatewayCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        conn = psycopg2.connect(cls.dsn)
        conn.autocommit = True
        with conn.cursor() as cur:
            apply_brand_master_and_currency_migrations(cur)
            apply_dynamic_brand_currency_migration(cur)
        conn.close()
        cls._env_patch = mock.patch.dict(os.environ, {"DATABASE_URL": cls.dsn})
        cls._env_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._env_patch.stop()
        drop_temp_db(cls.db_name)

    def setUp(self):
        self.conn = psycopg2.connect(self.dsn)
        self.conn.autocommit = True
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM products")
        self.conn.close()
        self._tmpdir.cleanup()

    def _xlsx_path(self, name="import.xlsx"):
        return os.path.join(self._tmpdir.name, name)

    def _run_main(self, argv):
        try:
            import_excel.main(argv)
            return 0
        except SystemExit as e:
            return e.code or 0

    def test_unknown_brand_is_registered_atomically(self):
        path = self._xlsx_path()
        _write_xlsx(
            path,
            ["name", "code", "cas", "brand", "size", "ship", "price", "note"],
            [
                {"name": "Valid Product", "code": "C-1", "cas": "1-1-1", "brand": "Sigma", "size": "1g", "ship": "1", "price": "100", "note": ""},
                {"name": "Bad Product", "code": "C-2", "cas": "2-2-2", "brand": "Completely Unknown Brand Xyz", "size": "1g", "ship": "1", "price": "100", "note": ""},
            ],
        )
        exit_code = self._run_main([path])
        self.assertEqual(exit_code, 0)
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products")
            self.assertEqual(cur.fetchone()[0], 2)
            cur.execute(
                "SELECT currency_code FROM brand_master "
                "WHERE normalized_name = 'COMPLETELY UNKNOWN BRAND XYZ'"
            )
            self.assertIsNone(cur.fetchone()[0])
            cur.execute(
                "SELECT source_brand FROM products WHERE code='C-2'"
            )
            self.assertEqual(cur.fetchone()[0], "Completely Unknown Brand Xyz")
            cur.execute(
                "SELECT COUNT(*) FROM team_brands WHERE brand='Completely Unknown Brand Xyz'"
            )
            self.assertEqual(cur.fetchone()[0], 0)

    def test_alias_resolves_to_canonical_and_writes_source_brand(self):
        path = self._xlsx_path()
        _write_xlsx(
            path,
            ["name", "code", "cas", "brand", "size", "ship", "price", "note"],
            [
                {"name": "Cefdinir", "code": "C-1105", "cas": "1-1-1", "brand": "Sigma (Mỹ)", "size": "10mg", "ship": "1", "price": "100", "note": ""},
            ],
        )
        exit_code = self._run_main([path])
        self.assertEqual(exit_code, 0)
        with self.conn.cursor() as cur:
            cur.execute("SELECT brand, source_brand FROM products WHERE code = 'C-1105'")
            row = cur.fetchone()
        self.assertEqual(row, ("Sigma", "Sigma (Mỹ)"))

    def test_replace_dry_run_prints_mixed_canonical_counts_and_writes_nothing(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products (name, code, brand, source_brand) VALUES "
                "('Sigma A', 'S-A', 'Sigma', 'Sigma (Mỹ)'), "
                "('Sigma B', 'S-B', 'Sigma', 'Sigma-Aldrich'), "
                "('Phyto', 'P-A', 'PhytoLab', 'PhytoLab'), "
                "('Unrelated', 'A-A', 'AccuStandard', 'AccuStandard')"
            )
        path = self._xlsx_path()
        _write_xlsx(
            path,
            ["name", "code", "cas", "brand", "size", "ship", "price", "note"],
            [
                {"name": "Sigma New 1", "code": "S-N1", "brand": "Sigma"},
                {"name": "Sigma New 2", "code": "S-N2", "brand": "Sigma"},
                {"name": "Phyto New", "code": "P-N1", "brand": "PhytoLab"},
            ],
        )
        output = StringIO()
        with redirect_stdout(output):
            exit_code = self._run_main([path, "--replace-brands-from-file", "--dry-run"])
        self.assertEqual(exit_code, 0)
        text = output.getvalue()
        self.assertIn("Sigma: xóa 2, chèn 2 dòng", text)
        self.assertIn("PhytoLab: xóa 1, chèn 1 dòng", text)
        self.assertIn("TỔNG: xóa 3, chèn 3 dòng", text)
        with self.conn.cursor() as cur:
            cur.execute("SELECT code, name FROM products ORDER BY code")
            self.assertEqual(
                cur.fetchall(),
                [("A-A", "Unrelated"), ("P-A", "Phyto"), ("S-A", "Sigma A"), ("S-B", "Sigma B")],
            )

    def test_canonical_brand_with_multiple_source_aliases_is_replaced_in_full(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products (name, code, cas, brand, source_brand, size, ship, price, note) "
                "VALUES ('Existing A', 'EX-A', '', 'Sigma', 'Sigma (Mỹ)', '1g', '1', '100', ''), "
                "       ('Existing B', 'EX-B', '', 'Sigma', 'Sigma-Aldrich', '1g', '1', '100', ''), "
                "       ('Unrelated', 'KEEP-1', '', 'PhytoLab', 'PhytoLab', '1g', '1', '100', '')"
            )
        path = self._xlsx_path()
        _write_xlsx(
            path,
            ["name", "code", "cas", "brand", "size", "ship", "price", "note"],
            [{"name": "New Sigma Product", "code": "NEW-1", "cas": "", "brand": "Sigma", "size": "1g", "ship": "1", "price": "100", "note": ""}],
        )
        exit_code = self._run_main([path, "--replace-brands-from-file"])
        self.assertEqual(exit_code, 0)
        with self.conn.cursor() as cur:
            cur.execute("SELECT code, source_brand FROM products WHERE brand = 'Sigma' ORDER BY code")
            self.assertEqual(cur.fetchall(), [("NEW-1", "Sigma")])
            cur.execute("SELECT name FROM products WHERE code = 'KEEP-1'")
            self.assertEqual(cur.fetchone()[0], "Unrelated")

    def test_alias_input_resolves_and_replaces_the_same_full_canonical_scope(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products (name, code, brand, source_brand) VALUES "
                "('Existing A', 'EX-A', 'Sigma', 'Sigma (Mỹ)'), "
                "('Existing B', 'EX-B', 'Sigma', 'Sigma-Aldrich')"
            )
        path = self._xlsx_path()
        _write_xlsx(
            path,
            ["name", "code", "cas", "brand", "size", "ship", "price", "note"],
            [{"name": "Alias Replacement", "code": "NEW-ALIAS", "brand": "Sigma (Mỹ)"}],
        )
        self.assertEqual(self._run_main([path, "--replace-brands-from-file"]), 0)
        with self.conn.cursor() as cur:
            cur.execute("SELECT code, brand, source_brand FROM products WHERE brand = 'Sigma'")
            self.assertEqual(cur.fetchall(), [("NEW-ALIAS", "Sigma", "Sigma (Mỹ)")])

    def test_blank_brand_fails_before_any_write(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products (name, code, brand, source_brand) "
                "VALUES ('Keep', 'KEEP-BLANK', 'Sigma', 'Sigma')"
            )
        path = self._xlsx_path()
        _write_xlsx(
            path,
            ["name", "code", "cas", "brand", "size", "ship", "price", "note"],
            [{"name": "Invalid", "code": "BAD", "brand": ""}],
        )
        self.assertNotEqual(self._run_main([path, "--replace-brands-from-file"]), 0)
        with self.conn.cursor() as cur:
            cur.execute("SELECT code, name FROM products")
            self.assertEqual(cur.fetchall(), [("KEEP-BLANK", "Keep")])

    def test_missing_brand_header_fails_before_any_write(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products (name, code, brand, source_brand) "
                "VALUES ('Keep', 'KEEP-HEADER', 'Sigma', 'Sigma')"
            )
        path = self._xlsx_path()
        _write_xlsx(
            path,
            ["name", "code", "cas", "size", "ship", "price", "note"],
            [{"name": "Invalid", "code": "BAD"}],
        )
        self.assertNotEqual(self._run_main([path, "--replace-brands-from-file"]), 0)
        with self.conn.cursor() as cur:
            cur.execute("SELECT code, name FROM products")
            self.assertEqual(cur.fetchall(), [("KEEP-HEADER", "Keep")])

    def test_replace_waits_for_the_shared_advisory_lock_before_writing(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products (name, code, brand, source_brand) "
                "VALUES ('Original', 'LOCK-OLD', 'PhytoLab', 'PhytoLab')"
            )
        path = self._xlsx_path()
        _write_xlsx(
            path,
            ["name", "code", "cas", "brand", "size", "ship", "price", "note"],
            [{"name": "Replacement", "code": "LOCK-NEW", "brand": "PhytoLab"}],
        )

        lock_conn = psycopg2.connect(self.dsn)
        lock_conn.autocommit = False
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (PRODUCTS_IMPORT_LOCK_KEY,))

        result = {}

        def run_import():
            result["code"] = self._run_main([path, "--replace-brands-from-file"])

        thread = threading.Thread(target=run_import)
        try:
            thread.start()
            thread.join(timeout=1.5)
            self.assertTrue(thread.is_alive(), "CLI replace did not block on the shared import lock")
            with self.conn.cursor() as cur:
                cur.execute("SELECT code FROM products WHERE brand = 'PhytoLab'")
                self.assertEqual(cur.fetchall(), [("LOCK-OLD",)])
            lock_conn.commit()
        finally:
            lock_conn.close()

        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "CLI replace hung after the shared lock was released")
        self.assertEqual(result.get("code"), 0)
        with self.conn.cursor() as cur:
            cur.execute("SELECT code FROM products WHERE brand = 'PhytoLab'")
            self.assertEqual(cur.fetchall(), [("LOCK-NEW",)])

    def test_missing_brand_master_table_blocks_import(self):
        # A database that only has `products` (no brand_master at all).
        raw_db_name, raw_dsn = create_full_schema_temp_db()
        try:
            path = self._xlsx_path("no_master.xlsx")
            _write_xlsx(
                path,
                ["name", "code", "cas", "brand", "size", "ship", "price", "note"],
                [{"name": "P1", "code": "C-1", "cas": "", "brand": "Sigma", "size": "1g", "ship": "1", "price": "100", "note": ""}],
            )
            with mock.patch.dict(os.environ, {"DATABASE_URL": raw_dsn}):
                exit_code = self._run_main([path])
            self.assertNotEqual(exit_code, 0)
        finally:
            drop_temp_db(raw_db_name)


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class LegacyCliScriptsRefuseCanonicalSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        conn = psycopg2.connect(cls.dsn)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(_MIGRATION_017_PATH.read_text(encoding="utf-8"))
        conn.close()
        # NOTE: a genuine 'CẤM NHẬP'-style legacy pseudo-brand row cannot
        # coexist with a fully-applied migration_017 in the same fixture --
        # migration_017's own preflight (and its `fk_products_brand_master`
        # constraint) would already reject that unmapped brand. That is
        # exactly why this script must have already been run BEFORE
        # migration_017; these tests only need to prove the guard fires once
        # `brand_master` exists, which is independent of any specific
        # pre-017 data shape.

    @classmethod
    def tearDownClass(cls):
        drop_temp_db(cls.db_name)

    def setUp(self):
        self.conn = psycopg2.connect(self.dsn)
        self.conn.autocommit = True

    def tearDown(self):
        self.conn.close()

    def test_migrate_sqlite_to_postgres_refuses_when_brand_master_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_path = os.path.join(tmpdir, "legacy.db")
            sl = sqlite3.connect(sqlite_path)
            sl.execute("CREATE TABLE products (name TEXT, code TEXT, cas TEXT, brand TEXT, size TEXT, ship TEXT, price TEXT, note TEXT)")
            sl.execute("INSERT INTO products VALUES ('X','C1','1-1-1','SomeBrand','1g','1','100','')")
            sl.commit()
            sl.close()

            with self.conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM products")
                count_before = cur.fetchone()[0]

            old_argv = sys.argv
            sys.argv = ["migrate_sqlite_to_postgres.py", sqlite_path]
            try:
                with mock.patch.dict(os.environ, {"DATABASE_URL": self.dsn}):
                    with self.assertRaises(SystemExit) as ctx:
                        migrate_sqlite_to_postgres.main()
                    self.assertEqual(ctx.exception.code, 2)
            finally:
                sys.argv = old_argv

            with self.conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM products")
                self.assertEqual(cur.fetchone()[0], count_before, "Blocked script must not touch products")

    def test_migrate_legacy_regulatory_refuses_when_brand_master_present(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM regulatory_rules")
            rules_before = cur.fetchone()[0]

        old_argv = sys.argv
        sys.argv = ["migrate_legacy_regulatory_from_products.py"]
        try:
            with mock.patch.dict(os.environ, {"DATABASE_URL": self.dsn}):
                with self.assertRaises(SystemExit) as ctx:
                    migrate_legacy_regulatory_from_products.main()
                self.assertEqual(ctx.exception.code, 2)
        finally:
            sys.argv = old_argv

        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM regulatory_rules")
            self.assertEqual(cur.fetchone()[0], rules_before, "Blocked script must not write regulatory_rules")


if __name__ == "__main__":
    unittest.main()
