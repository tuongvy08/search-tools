"""Focused real-PostgreSQL tests for migration_019 and dynamic masters."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
import sys
import threading
import unittest

import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import search
from brand_gateway import (
    acquire_products_import_lock,
    load_brand_gateway,
    preview_import_rows_brands,
    register_and_resolve_import_rows,
)
from currency_rates import (
    STATUS_CURRENCY_MISSING,
    apply_brand_currency_update,
    apply_currency_create,
    load_currency_rate_resolver,
)
from pg_temp_db import (
    apply_brand_master_and_currency_migrations,
    apply_dynamic_brand_currency_migration,
    create_full_schema_temp_db,
    drop_temp_db,
    probe_postgres_reachable,
)

load_dotenv(dotenv_path=".env")


@unittest.skipUnless(probe_postgres_reachable(), "local Postgres required")
class DynamicBrandCurrencyPgTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_name, cls.dsn = create_full_schema_temp_db()
        cls.conn = psycopg2.connect(cls.dsn)
        cls.conn.autocommit = True
        with cls.conn.cursor() as cur:
            apply_brand_master_and_currency_migrations(cur)
            apply_dynamic_brand_currency_migration(cur)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.conn.close()
        finally:
            drop_temp_db(cls.db_name)

    def tearDown(self):
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE code LIKE 'DYN-%'")
            cur.execute("DELETE FROM team_brands WHERE brand LIKE 'Dynamic %'")
            cur.execute("DELETE FROM brand_master WHERE normalized_name LIKE 'DYNAMIC %'")
            cur.execute("DELETE FROM currency_rate_history WHERE currency_code = 'JPY'")
            cur.execute("DELETE FROM currency_rates WHERE currency_code = 'JPY'")

    def test_clean_chain_is_idempotent_and_preserves_dynamic_data(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM brand_master")
            self.assertEqual(cur.fetchone()[0], 35)
            cur.execute("SELECT COUNT(*) FROM currency_rates")
            self.assertEqual(cur.fetchone()[0], 5)

        with self.conn:
            apply_currency_create(self.conn, "JPY", Decimal("180"), None, source="TEST")
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO brand_master (name, normalized_name, currency_code) "
                    "VALUES ('Dynamic Preserve', 'DYNAMIC PRESERVE', NULL)"
                )

        with self.conn.cursor() as cur:
            apply_dynamic_brand_currency_migration(cur)
            apply_dynamic_brand_currency_migration(cur)
            cur.execute("SELECT rate_vnd FROM currency_rates WHERE currency_code = 'JPY'")
            self.assertEqual(cur.fetchone()[0], Decimal("180"))
            cur.execute(
                "SELECT currency_code FROM brand_master WHERE normalized_name = 'DYNAMIC PRESERVE'"
            )
            self.assertIsNone(cur.fetchone()[0])

    def test_upgrade_from_018_unlocks_dynamic_currency_and_nullable_brand(self):
        db_name, dsn = create_full_schema_temp_db()
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                apply_brand_master_and_currency_migrations(cur)
                with self.assertRaises(psycopg2.errors.CheckViolation):
                    cur.execute("INSERT INTO currency_rates (currency_code, rate_vnd) VALUES ('JPY', 180)")
                apply_dynamic_brand_currency_migration(cur)
                cur.execute("INSERT INTO currency_rates (currency_code, rate_vnd) VALUES ('JPY', 180)")
                cur.execute(
                    "INSERT INTO brand_master (name, normalized_name, currency_code) "
                    "VALUES ('Dynamic Upgrade', 'DYNAMIC UPGRADE', NULL)"
                )
                cur.execute(
                    "SELECT currency_code FROM brand_master WHERE normalized_name='DYNAMIC UPGRADE'"
                )
                self.assertIsNone(cur.fetchone()[0])
        finally:
            conn.close()
            drop_temp_db(db_name)

    def test_preview_is_read_only_and_counts_case_trim_duplicate_brand(self):
        with self.conn.cursor() as cur:
            gateway = load_brand_gateway(cur)
            cur.execute("SELECT COUNT(*) FROM brand_master")
            before_count = cur.fetchone()[0]
            resolved, errors, new_brands = preview_import_rows_brands(
                [
                    {"brand": " Dynamic Preview ", "code": "DYN-P1"},
                    {"brand": "dynamic preview", "code": "DYN-P2"},
                    {"brand": "Sigma", "code": "DYN-P3"},
                ],
                gateway,
            )
            cur.execute("SELECT COUNT(*) FROM brand_master")
            after_count = cur.fetchone()[0]
        self.assertFalse(errors)
        self.assertEqual(len(resolved), 3)
        self.assertEqual(new_brands, [{
            "name": "Dynamic Preview",
            "normalized_name": "DYNAMIC PREVIEW",
            "row_count": 2,
        }])
        self.assertEqual(before_count, after_count)

    def test_confirm_registers_brand_and_product_atomically_without_team_grant(self):
        conn = psycopg2.connect(self.dsn)
        try:
            with conn:
                with conn.cursor() as cur:
                    acquire_products_import_lock(cur)
                    rows, created = register_and_resolve_import_rows(
                        cur, [{"brand": "Dynamic Atomic", "code": "DYN-A1"}]
                    )
                    row = rows[0]
                    cur.execute(
                        "INSERT INTO products (code, brand, source_brand) VALUES (%s, %s, %s)",
                        (row["code"], row["brand"], row["source_brand"]),
                    )
            self.assertEqual([b["name"] for b in created], ["Dynamic Atomic"])
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT bm.currency_code, p.brand, p.source_brand "
                    "FROM brand_master bm JOIN products p ON p.brand = bm.name "
                    "WHERE p.code = 'DYN-A1'"
                )
                self.assertEqual(cur.fetchone(), (None, "Dynamic Atomic", "Dynamic Atomic"))
                cur.execute("SELECT COUNT(*) FROM team_brands WHERE brand = 'Dynamic Atomic'")
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            conn.close()

    def test_registration_and_product_write_roll_back_together(self):
        conn = psycopg2.connect(self.dsn)
        try:
            with self.assertRaises(RuntimeError):
                with conn:
                    with conn.cursor() as cur:
                        acquire_products_import_lock(cur)
                        register_and_resolve_import_rows(
                            cur, [{"brand": "Dynamic Rollback", "code": "DYN-R1"}]
                        )
                        raise RuntimeError("force rollback")
            with self.conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM brand_master WHERE normalized_name = 'DYNAMIC ROLLBACK'"
                )
                self.assertEqual(cur.fetchone()[0], 0)
        finally:
            conn.close()

    def test_concurrent_case_variants_create_one_master(self):
        barrier = threading.Barrier(2)

        def worker(raw_brand, code):
            conn = psycopg2.connect(self.dsn)
            try:
                barrier.wait(timeout=5)
                with conn:
                    with conn.cursor() as cur:
                        acquire_products_import_lock(cur)
                        rows, created = register_and_resolve_import_rows(
                            cur, [{"brand": raw_brand, "code": code}]
                        )
                        row = rows[0]
                        cur.execute(
                            "INSERT INTO products (code, brand, source_brand) VALUES (%s, %s, %s)",
                            (code, row["brand"], row["source_brand"]),
                        )
                return len(created)
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            created_counts = list(pool.map(
                lambda args: worker(*args),
                [("Dynamic Race", "DYN-C1"), (" dynamic race ", "DYN-C2")],
            ))
        self.assertEqual(sum(created_counts), 1)
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM brand_master WHERE normalized_name = 'DYNAMIC RACE'"
            )
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("SELECT COUNT(DISTINCT brand) FROM products WHERE code IN ('DYN-C1','DYN-C2')")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_missing_currency_fails_closed_then_assignment_applies_next_request(self):
        with self.conn:
            with self.conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO brand_master (name, normalized_name, currency_code) "
                    "VALUES ('Dynamic Pricing', 'DYNAMIC PRICING', NULL) RETURNING id"
                )
                brand_id = cur.fetchone()[0]

        first = load_currency_rate_resolver(self.conn, search.app.root_path).resolve("Dynamic Pricing")
        self.assertFalse(first.is_valid)
        self.assertEqual(first.status, STATUS_CURRENCY_MISSING)
        self.assertIsNone(first.rate)

        with self.conn:
            apply_currency_create(self.conn, "JPY", Decimal("180"), None, source="TEST")
            apply_brand_currency_update(self.conn, brand_id, "JPY", None)
        second = load_currency_rate_resolver(self.conn, search.app.root_path).resolve("Dynamic Pricing")
        self.assertTrue(second.is_valid)
        self.assertEqual(second.rate, Decimal("180"))

        with self.conn.cursor() as cur:
            with self.assertRaises(psycopg2.errors.ForeignKeyViolation):
                cur.execute("DELETE FROM currency_rates WHERE currency_code='JPY'")

    def test_local_and_google_staff_need_explicit_team_brand_grant(self):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO brand_master (name, normalized_name, currency_code) "
                "VALUES ('Dynamic Scoped', 'DYNAMIC SCOPED', NULL)"
            )
            cur.execute("INSERT INTO products (code, brand, source_brand) VALUES ('DYN-S1','Dynamic Scoped','Dynamic Scoped')")
            cur.execute("INSERT INTO teams (name, ip_policy) VALUES ('Dynamic Scope Team','INHERIT') RETURNING id")
            team_id = cur.fetchone()[0]

        for provider in ("LOCAL", "GOOGLE"):
            with self.subTest(provider=provider), search.app.test_request_context("/"):
                from flask import session
                session["authenticated"] = True
                session["is_admin"] = False
                session["team_id"] = team_id
                session["auth_provider"] = provider
                vis, params = search._visibility_sql("p")
                with self.conn.cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) FROM products p WHERE p.code='DYN-S1' {vis}", params)
                    self.assertEqual(cur.fetchone()[0], 0)

        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO team_brands (team_id, brand) VALUES (%s, 'Dynamic Scoped')",
                (team_id,),
            )
            cur.execute(
                "SELECT COUNT(*) FROM products p WHERE p.code='DYN-S1' "
                "AND p.brand IN (SELECT brand FROM team_brands WHERE team_id=%s)",
                (team_id,),
            )
            self.assertEqual(cur.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
