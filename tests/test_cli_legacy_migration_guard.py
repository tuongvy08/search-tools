"""Focused tests for Phase 6B2B2 Section 6 (Legacy CLI blocker).

1. `scripts/import_excel.py` upgraded to use the Brand Gateway:
   - unknown brand fails the WHOLE import before any DB mutation.
   - alias -> canonical resolution + `source_brand` written correctly.
   - `--dry-run` resolves/counts but writes nothing.
   - `--replace-brands-from-file` refuses to wipe an entire canonical brand
     when the file doesn't scope by `source_brand` and the brand has
     multiple source catalogs in the DB (reuses the same
     `inspect_replace_by_brand_scopes` safety as `/admin/imports/apply`).
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
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
from dotenv import load_dotenv
from openpyxl import Workbook

from brand_gateway import LegacyMigrationBlockedError, refuse_if_canonical_brand_master_present
from pg_temp_db import create_full_schema_temp_db, drop_temp_db, probe_postgres_reachable

load_dotenv(dotenv_path=".env")

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = _ROOT / "scripts"
_MIGRATION_017_PATH = _ROOT / "sql" / "migration_017_brand_master.sql"

sys.path.insert(0, str(_SCRIPTS_DIR))
import import_excel  # noqa: E402
import migrate_sqlite_to_postgres  # noqa: E402
import migrate_legacy_regulatory_from_products  # noqa: E402


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
            cur.execute(_MIGRATION_017_PATH.read_text(encoding="utf-8"))
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
        old_argv = sys.argv
        sys.argv = ["import_excel.py"] + argv
        try:
            import_excel.main()
            return 0
        except SystemExit as e:
            return e.code or 0
        finally:
            sys.argv = old_argv

    def test_unknown_brand_fails_closed_before_any_mutation(self):
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
        self.assertNotEqual(exit_code, 0)
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products")
            self.assertEqual(cur.fetchone()[0], 0, "Unknown brand must block the ENTIRE import, not just that row")

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

    def test_dry_run_writes_nothing(self):
        path = self._xlsx_path()
        _write_xlsx(
            path,
            ["name", "code", "cas", "brand", "size", "ship", "price", "note"],
            [{"name": "P1", "code": "C-DRY", "cas": "1-1-1", "brand": "Sigma", "size": "1g", "ship": "1", "price": "100", "note": ""}],
        )
        exit_code = self._run_main([path, "--dry-run"])
        self.assertEqual(exit_code, 0)
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products WHERE code = 'C-DRY'")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_replace_brands_from_file_rejected_without_source_scope_when_multi_source(self):
        # Seed 2 products under canonical "Sigma" but from 2 different source_brands.
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO products (name, code, cas, brand, source_brand, size, ship, price, note) "
                "VALUES ('Existing A', 'EX-A', '', 'Sigma', 'Sigma (Mỹ)', '1g', '1', '100', ''), "
                "       ('Existing B', 'EX-B', '', 'Sigma', 'Sigma-Aldrich', '1g', '1', '100', '')"
            )
        path = self._xlsx_path()
        _write_xlsx(
            path,
            ["name", "code", "cas", "brand", "size", "ship", "price", "note"],
            [{"name": "New Sigma Product", "code": "NEW-1", "cas": "", "brand": "Sigma", "size": "1g", "ship": "1", "price": "100", "note": ""}],
        )
        exit_code = self._run_main([path, "--replace-brands-from-file"])
        self.assertNotEqual(exit_code, 0)
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products WHERE code IN ('EX-A', 'EX-B')")
            self.assertEqual(cur.fetchone()[0], 2, "Ambiguous multi-source canonical brand must not be wiped")
            cur.execute("SELECT COUNT(*) FROM products WHERE code = 'NEW-1'")
            self.assertEqual(cur.fetchone()[0], 0)

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
