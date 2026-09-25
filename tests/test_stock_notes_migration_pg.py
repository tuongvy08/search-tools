"""Migration 031 contract on disposable PostgreSQL databases only."""
from pathlib import Path
import unittest

import psycopg2

import pg_temp_db


MIGRATION = Path(__file__).resolve().parents[1] / "sql/migration_031_stock_notes.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
CONSTRAINT = "stock_items_stock_note_length_check"


@unittest.skipUnless(pg_temp_db.probe_postgres_reachable(), "isolated local PostgreSQL required")
class StockNotesMigrationTests(unittest.TestCase):
    def setUp(self):
        name, self.dsn = pg_temp_db.create_full_schema_temp_db()
        self.addCleanup(pg_temp_db.drop_temp_db, name)
        self.conn = psycopg2.connect(self.dsn)
        self.addCleanup(self.conn.close)
        self.conn.autocommit = True
        self.cur = self.conn.cursor()
        self.addCleanup(self.cur.close)
        self.cur.execute("""
            INSERT INTO stock_snapshots(id,source_kind,actor,row_count,content_sha256)
            VALUES ('00000000-0000-0000-0000-000000000001','IMPORT','test',1,'test');
            INSERT INTO stock_items(snapshot_id,name,code,brand,size,quantity,brand_norm,code_norm,size_norm)
            VALUES ('00000000-0000-0000-0000-000000000001','A','A','A','1g',7,'a','a','1g');
            UPDATE stock_state SET active_snapshot_id='00000000-0000-0000-0000-000000000001',revision=1;
            INSERT INTO products(name,code,brand) VALUES ('Catalog','CAT','A');
        """)

    def business_state(self):
        result = []
        for table in ("stock_state", "stock_snapshots", "products"):
            self.cur.execute(f"SELECT row_to_json(t),xmin::text FROM {table} t")
            result.append(self.cur.fetchall())
        return result

    def column_state(self):
        self.cur.execute("""
            SELECT a.atttypid,a.atttypmod,a.attnotnull,pg_get_expr(d.adbin,d.adrelid)
            FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid='stock_items'::regclass AND a.attname='stock_note' AND NOT a.attisdropped
        """)
        column = self.cur.fetchall()
        self.cur.execute("""SELECT conname,pg_get_constraintdef(oid),convalidated
                            FROM pg_constraint WHERE conrelid='stock_items'::regclass ORDER BY conname""")
        constraints = self.cur.fetchall()
        self.cur.execute("SELECT row_to_json(t),xmin::text FROM stock_items t")
        return column, constraints, self.cur.fetchall()

    def test_add_backfills_blank_and_reapply_preserves_rows(self):
        business = self.business_state()
        self.cur.execute("ALTER TABLE stock_items DROP COLUMN stock_note")
        self.cur.execute(SQL)
        self.cur.execute("SELECT quantity,stock_note FROM stock_items")
        self.assertEqual(self.cur.fetchall(), [(7, "")])
        self.cur.execute("UPDATE stock_items SET stock_note=%s", ("Kho A\nHàng mẫu",))
        before = self.column_state()
        self.cur.execute(SQL)
        self.cur.execute(SQL)
        self.assertEqual(self.column_state(), before)
        self.assertEqual(self.business_state(), business)

    def test_rejects_partial_or_incompatible_columns_without_repair(self):
        definitions = {
            "nullable": "TEXT DEFAULT ''",
            "missing_default": "TEXT NOT NULL DEFAULT ''",
            "wrong_default": "TEXT NOT NULL DEFAULT 'wrong'",
            "wrong_type": "VARCHAR(2000) NOT NULL DEFAULT ''",
            "integer_type": "INTEGER NOT NULL DEFAULT 0",
            "missing_check": "TEXT NOT NULL DEFAULT ''",
            "weak_check": "TEXT NOT NULL DEFAULT ''",
            "wrong_check_column": "TEXT NOT NULL DEFAULT ''",
            "unvalidated_check": "TEXT NOT NULL DEFAULT ''",
        }
        business = self.business_state()
        for variant, definition in definitions.items():
            with self.subTest(variant=variant):
                self.cur.execute(f"ALTER TABLE stock_items DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
                self.cur.execute("ALTER TABLE stock_items DROP COLUMN stock_note")
                self.cur.execute("ALTER TABLE stock_items ADD COLUMN stock_note " + definition)
                if variant == "missing_default":
                    self.cur.execute("ALTER TABLE stock_items ALTER COLUMN stock_note DROP DEFAULT")
                if variant not in ("missing_check", "integer_type"):
                    expression = "length(stock_note) <= 2001" if variant == "weak_check" else "length(stock_note) <= 2000"
                    if variant == "wrong_check_column":
                        expression = "quantity <= 2000"
                    suffix = " NOT VALID" if variant == "unvalidated_check" else ""
                    self.cur.execute(f"ALTER TABLE stock_items ADD CONSTRAINT {CONSTRAINT} CHECK ({expression}){suffix}")
                before = self.column_state()
                with self.assertRaisesRegex(psycopg2.Error, "Migration 031"):
                    self.cur.execute(SQL)
                self.cur.execute("ROLLBACK")
                self.assertEqual(self.column_state(), before)
                self.assertEqual(self.business_state(), business)

    def test_database_enforces_limit_and_not_null(self):
        for valid in ("", "Ghi chú\nKho A", "đ" * 2000):
            self.cur.execute("UPDATE stock_items SET stock_note=%s", (valid,))
            self.cur.execute("SELECT stock_note FROM stock_items")
            self.assertEqual(self.cur.fetchone()[0], valid)
        for invalid in (None, "đ" * 2001):
            with self.subTest(invalid=invalid is None), self.assertRaises(psycopg2.IntegrityError):
                self.cur.execute("UPDATE stock_items SET stock_note=%s", (invalid,))

    @unittest.skipUnless(pg_temp_db.psql_runner_available(), "psql required for production invocation")
    def test_real_psql_add_reapply_and_failure_are_atomic(self):
        business = self.business_state()
        self.cur.execute("ALTER TABLE stock_items DROP COLUMN stock_note")
        for _ in range(2):
            code, output = pg_temp_db.run_migration_via_psql(self.dsn, MIGRATION)
            self.assertEqual(code, 0, output)
        self.assertEqual(self.business_state(), business)
        self.cur.execute("ALTER TABLE stock_items ALTER COLUMN stock_note SET DEFAULT 'wrong'")
        before = self.column_state()
        code, output = pg_temp_db.run_migration_via_psql(self.dsn, MIGRATION)
        self.assertNotEqual(code, 0)
        self.assertIn("Migration 031", output)
        self.assertEqual(self.column_state(), before)
        # A conflicting constraint forces failure while adding the new column.
        self.cur.execute("ALTER TABLE stock_items DROP COLUMN stock_note")
        self.cur.execute(f"ALTER TABLE stock_items ADD CONSTRAINT {CONSTRAINT} CHECK (quantity <= 2000)")
        before = self.column_state()
        code, _ = pg_temp_db.run_migration_via_psql(self.dsn, MIGRATION)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.column_state(), before)
        self.assertEqual(self.business_state(), business)


if __name__ == "__main__":
    unittest.main()
