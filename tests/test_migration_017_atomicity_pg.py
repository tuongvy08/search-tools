"""Phase 6B2B2-Fix1 regression tests: prove `migration_017_brand_master.sql`
(and the 017->018 chain) is genuinely atomic under the EXACT production
invocation method documented in the deploy runbook:

    psql -v ON_ERROR_STOP=1 -f sql/migration_017_brand_master.sql

-- psql's *default* per-statement autocommit, no `--single-transaction`,
no `-1`. These tests deliberately shell out to the real `psql` binary
(`pg_temp_db.run_migration_via_psql`) instead of using a psycopg2
connection: a psycopg2 `cur.execute(full_file_text)` call sends the whole
file as ONE simple-query protocol message, and PostgreSQL implicitly wraps
a multi-statement message like that in its own transaction unless the
message itself contains explicit `BEGIN`/`COMMIT` -- a DIFFERENT code path
from `psql -f`, which sends one statement per protocol message and
autocommits each individually. That difference is exactly what let the
pre-fix `migration_017_brand_master.sql` (its `CREATE TEMP TABLE ... ON
COMMIT DROP` staging tables with no enclosing transaction) pass every
psycopg2-based rehearsal/test green while failing for real on staging
(2026-09-06 incident) with:

    ERROR: relation "staging_brand_mapping" does not exist

Root cause: each top-level statement autocommitted individually under
plain `psql -f`, so each `CREATE TEMP TABLE ... ON COMMIT DROP` dropped
its own table again immediately -- before the very next `INSERT INTO
staging_brand_mapping ...` could use it -- leaving `brand_master`,
`brand_aliases`, and `products.source_brand` partially created and
COMMITTED (autocommit again) before the script aborted.

Fix (this phase): wrap the whole file in one explicit `BEGIN;`/`COMMIT;`.
These tests prove that fix actually holds under `psql -f`, both for a
clean run and for a forced failure (must roll back to ZERO partial
schema/data), and that the 017->018 chain still works end-to-end.

Every database here is a brand-new, uniquely-prefixed throwaway created via
`tests/pg_temp_db.py` (`create_full_schema_temp_db()`) and dropped in
`tearDown` even on failure. NONE of these tests ever open a connection to
`products_local`, staging, or production -- `pg_temp_db.maintenance_dsn()`
only ever targets the local Postgres server's own `postgres` maintenance
database to CREATE/DROP the throwaway DB.
"""

from __future__ import annotations

import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
from dotenv import load_dotenv

from currency_rates import load_currency_rate_resolver
from pg_temp_db import (
    create_full_schema_temp_db,
    drop_temp_db,
    probe_postgres_reachable,
    psql_runner_available,
    run_migration_via_psql,
)

load_dotenv(dotenv_path=".env")

_ROOT = Path(__file__).resolve().parents[1]
_SQL_DIR = _ROOT / "sql"
_MIGRATION_017 = _SQL_DIR / "migration_017_brand_master.sql"
_MIGRATION_018 = _SQL_DIR / "migration_018_currency_rates.sql"

# `ON COMMIT DROP` staging tables migration_017 creates inside Section 4 --
# must exist DURING the migration and be gone once it (successfully OR
# unsuccessfully) finishes and the session/transaction ends.
_STAGING_TEMP_TABLES = (
    "staging_brand_mapping",
    "approved_delete_manifest",
    "delete_set_brands",
    "staging_canonical_rates",
)

_REQUIRE_PG = unittest.skipUnless(
    probe_postgres_reachable(), "local Postgres required (DATABASE_URL) for migration atomicity tests"
)
_REQUIRE_PSQL = unittest.skipUnless(
    psql_runner_available(),
    "psql CLI (host-installed or via docker compose `db` service) required to reproduce the real "
    "production invocation",
)


def _connect(dsn):
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


def _to_regclass(conn, name):
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
        return bool(cur.fetchone()[0])


def _column_exists(conn, table, column):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s)",
            (table, column),
        )
        return bool(cur.fetchone()[0])


def _count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


def _table_hash(conn, table, order_col="id"):
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT COALESCE(md5(string_agg(t::text, ',' ORDER BY {order_col})), 'EMPTY') FROM {table} t"
        )
        return cur.fetchone()[0]


def _backup_tables(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname LIKE '\\_backup\\_p6b2b1c\\_%'"
        )
        return [r[0] for r in cur.fetchall()]


@_REQUIRE_PG
@_REQUIRE_PSQL
class Migration017CleanRunAtomicityTests(unittest.TestCase):
    """Clean run: migration_017 applied via plain `psql -f` on a fresh,
    empty schema succeeds, temp mapping tables are gone after commit, and
    the documented post-migration invariants hold."""

    def setUp(self):
        self.db_name, self.dsn = create_full_schema_temp_db()

    def tearDown(self):
        drop_temp_db(self.db_name)

    def test_clean_run_succeeds_and_temp_tables_are_gone_after_commit(self):
        exit_code, output = run_migration_via_psql(self.dsn, _MIGRATION_017)
        self.assertEqual(exit_code, 0, f"migration_017 failed via plain `psql -f`:\n{output}")

        conn = _connect(self.dsn)
        try:
            self.assertTrue(_to_regclass(conn, "brand_master"))
            self.assertTrue(_to_regclass(conn, "brand_aliases"))
            self.assertTrue(_column_exists(conn, "products", "source_brand"))
            self.assertEqual(_count(conn, "brand_master"), 35)
            self.assertEqual(_count(conn, "brand_aliases"), 153)

            # `ON COMMIT DROP` -- must be gone now that psql's session has
            # committed (this is the exact invariant the bug violated:
            # these used to be gone WAY too early, before the INSERTs that
            # needed them).
            for temp_table in _STAGING_TEMP_TABLES:
                self.assertFalse(
                    _to_regclass(conn, temp_table), f"{temp_table} should not survive past COMMIT"
                )
        finally:
            conn.close()

    def test_idempotent_second_run_via_plain_psql_is_stable(self):
        exit_code_1, output_1 = run_migration_via_psql(self.dsn, _MIGRATION_017)
        self.assertEqual(exit_code_1, 0, output_1)

        conn = _connect(self.dsn)
        try:
            bm_before = _count(conn, "brand_master")
            ba_before = _count(conn, "brand_aliases")
        finally:
            conn.close()

        exit_code_2, output_2 = run_migration_via_psql(self.dsn, _MIGRATION_017)
        self.assertEqual(exit_code_2, 0, f"idempotent re-run via plain `psql -f` failed:\n{output_2}")

        conn = _connect(self.dsn)
        try:
            self.assertEqual(_count(conn, "brand_master"), bm_before)
            self.assertEqual(_count(conn, "brand_aliases"), ba_before)
            self.assertEqual(bm_before, 35)
            for temp_table in _STAGING_TEMP_TABLES:
                self.assertFalse(_to_regclass(conn, temp_table))
        finally:
            conn.close()


@_REQUIRE_PG
@_REQUIRE_PSQL
class Migration017ForcedFailureAtomicityTests(unittest.TestCase):
    """Forced failure: a deliberately-unmapped brand in `products` trips
    migration_017's own Section 4 fail-closed preflight
    (`RAISE EXCEPTION ... unmapped brand(s) found`). Run via plain
    `psql -f`, this MUST leave ZERO partial schema/data behind -- this is
    the regression test for the exact staging incident."""

    _UNMAPPED_BRAND = "TOTALLY_UNKNOWN_BRAND_NOT_IN_ANY_MAPPING_6B2B2FIX1"

    def setUp(self):
        self.db_name, self.dsn = create_full_schema_temp_db()
        conn = _connect(self.dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO products (name, code, cas, brand) VALUES (%s, %s, %s, %s)",
                    ("Drift fixture product", "DRIFT-001", "0-00-0", self._UNMAPPED_BRAND),
                )
            self._baseline_products_hash = _table_hash(conn, "products")
            self._baseline_products_count = _count(conn, "products")
            self._baseline_team_brands_count = _count(conn, "team_brands")
            self._baseline_exchange_rates_count = _count(conn, "exchange_rates")
            self._baseline_bcs_count = _count(conn, "brand_compliance_settings")
        finally:
            conn.close()

    def tearDown(self):
        drop_temp_db(self.db_name)

    def test_forced_failure_leaves_zero_partial_schema_or_data(self):
        exit_code, output = run_migration_via_psql(self.dsn, _MIGRATION_017)
        self.assertNotEqual(
            exit_code, 0, "migration_017 was expected to fail closed on an unmapped brand, but exited 0"
        )
        self.assertIn("unmapped", output.lower())

        conn = _connect(self.dsn)
        try:
            # Zero partial schema: none of migration_017's new objects may
            # exist after a rolled-back run.
            self.assertFalse(_to_regclass(conn, "brand_master"))
            self.assertFalse(_to_regclass(conn, "brand_aliases"))
            self.assertFalse(_column_exists(conn, "products", "source_brand"))
            for temp_table in _STAGING_TEMP_TABLES:
                self.assertFalse(_to_regclass(conn, temp_table))
            self.assertEqual(_backup_tables(conn), [])

            # Zero partial data: every table migration_017 would have
            # mutated must be byte-for-byte unchanged from before the
            # forced-failure run.
            self.assertEqual(_count(conn, "products"), self._baseline_products_count)
            self.assertEqual(_table_hash(conn, "products"), self._baseline_products_hash)
            self.assertEqual(_count(conn, "team_brands"), self._baseline_team_brands_count)
            self.assertEqual(_count(conn, "exchange_rates"), self._baseline_exchange_rates_count)
            self.assertEqual(_count(conn, "brand_compliance_settings"), self._baseline_bcs_count)
        finally:
            conn.close()


@_REQUIRE_PG
@_REQUIRE_PSQL
class Migration017Then018ChainTests(unittest.TestCase):
    """Chain: on a clean schema, run migration_017 then migration_018 in
    that order via plain `psql -f` (production semantics, no
    `--single-transaction`), then prove the currency resolver actually
    works end-to-end against the result -- no browser, no Flask app run
    required."""

    def setUp(self):
        self.db_name, self.dsn = create_full_schema_temp_db()

    def tearDown(self):
        drop_temp_db(self.db_name)

    def test_017_then_018_chain_and_currency_resolver(self):
        exit_017, out_017 = run_migration_via_psql(self.dsn, _MIGRATION_017)
        self.assertEqual(exit_017, 0, f"migration_017 failed:\n{out_017}")

        exit_018, out_018 = run_migration_via_psql(self.dsn, _MIGRATION_018)
        self.assertEqual(exit_018, 0, f"migration_018 failed:\n{out_018}")

        conn = psycopg2.connect(self.dsn)
        conn.autocommit = True
        try:
            self.assertTrue(_to_regclass(conn, "brand_master"))
            self.assertTrue(_to_regclass(conn, "currency_rates"))
            self.assertEqual(_count(conn, "currency_rates"), 5)

            resolver = load_currency_rate_resolver(conn, str(_ROOT))
            self.assertTrue(resolver.schema_ready)
            self.assertIsNone(resolver.load_error)

            expected = {
                "Sigma": ("USD", Decimal("26500")),
                "A2S": ("EUR", Decimal("31500")),
                "BP": ("GBP", Decimal("35500")),
                "NMI": ("AUD", Decimal("17200")),
            }
            for brand, (currency, rate) in expected.items():
                resolution = resolver.resolve(brand)
                self.assertTrue(resolution.is_valid, f"{brand} did not resolve: {resolution}")
                self.assertEqual(resolution.currency_code, currency)
                self.assertEqual(resolution.rate, rate)

            unknown = resolver.resolve("NOT_A_REAL_BRAND")
            self.assertFalse(unknown.is_valid)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
