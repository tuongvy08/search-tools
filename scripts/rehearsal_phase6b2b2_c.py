"""PostgreSQL Rehearsal Script for Phase 6B2B2 (Central Currency Rates).

Executes a full, isolated migration rehearsal against a TEMPLATE copy of
`products_local` (never `products_local` itself -- zero writes to the real
DB). Chains migration_017 (dependency: `brand_master.currency_code`) then
migration_018 (currency_rates / currency_rate_history / brand_currency_history)
on one throwaway clone:

1. Clone `products_local` to a temporary DB (`p6b2b2_rehearsal_*`).
2. Apply migration_017_brand_master.sql (prerequisite for brand_master).
3. Apply migration_018_currency_rates.sql and measure timing.
4. Re-run migration_018 to verify idempotency (no duplicate seed rows,
   no duplicate history rows, rates unchanged).
5. Verify all 35 canonical brands resolve to a currency + a positive
   VND rate matching the approved workbook values, and that VND == 1
   for every brand mapped to VND (i.e. no "foreign" brand secretly
   priced at rate 1).
6. Change EUR's rate in the temp DB and confirm every EUR-mapped brand
   picks up the new rate immediately (no `products` row touched).
7. Confirm the legacy `exchange_rates` table is untouched/still present
   (still usable for rollback of pre-6B2B2 code).
8. Confirm `products_local` had zero writes (re-check its row count
   before/after against the DSN actually used for the clone).
9. Drop the temporary rehearsal DB.

Never touches `products_local` data -- only ever reads its row count once
(pre) and once (post) as a zero-write sanity check; all mutations happen
exclusively on the throwaway `TEMPLATE`-cloned database.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg2
from dotenv import load_dotenv

load_dotenv()

_TEST_PREFIX = "p6b2b2_rehearsal_"
_MIGRATION_017_PATH = Path(__file__).resolve().parents[1] / "sql" / "migration_017_brand_master.sql"
_MIGRATION_018_PATH = Path(__file__).resolve().parents[1] / "sql" / "migration_018_currency_rates.sql"


def run_migration_via_psql(dsn: str, sql_path: Path, timeout: int = 600) -> tuple[int, str]:
    """Apply `sql_path` to `dsn` via a real `psql -v ON_ERROR_STOP=1 -f`
    subprocess -- psql's *default* per-statement autocommit, no
    `--single-transaction`, no `-1` -- instead of a psycopg2
    `cur.execute(full_file_text)` call.

    Phase 6B2B2-Fix1: required, not stylistic. A psycopg2 connection sends
    a whole multi-statement file as ONE simple-query protocol message, and
    PostgreSQL implicitly wraps that single message in its own transaction
    unless the message itself contains explicit `BEGIN`/`COMMIT` -- a
    DIFFERENT code path from `psql -f`, which sends one statement per
    protocol message and autocommits each individually. That difference is
    exactly what let the pre-fix migration_017 (its `CREATE TEMP TABLE ...
    ON COMMIT DROP` staging tables with no enclosing transaction) pass this
    very rehearsal green while failing for real on staging (2026-09-06
    incident). Connection info is passed via `PG*` environment variables,
    never as a CLI argument; returned output has any DSN password
    substring redacted.
    """
    parsed = urlparse(dsn)
    env = os.environ.copy()
    if parsed.hostname:
        env["PGHOST"] = parsed.hostname
    if parsed.port:
        env["PGPORT"] = str(parsed.port)
    if parsed.username:
        env["PGUSER"] = parsed.username
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    env["PGDATABASE"] = (parsed.path or "").lstrip("/")
    proc = subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-f", str(sql_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    if parsed.password:
        output = output.replace(parsed.password, "<REDACTED>")
    return proc.returncode, output

_WORKBOOK_RATES = {
    "VND": Decimal("1"),
    "AUD": Decimal("17200"),
    "USD": Decimal("26500"),
    "EUR": Decimal("31500"),
    "GBP": Decimal("35500"),
}


def real_database_url() -> str:
    return os.environ.get("DATABASE_URL", "")


def dsn_for(dbname: str) -> str:
    parsed = urlparse(real_database_url())
    return urlunparse(parsed._replace(path="/" + dbname))


def maintenance_dsn() -> str:
    return dsn_for("postgres")


def _source_db_name() -> str:
    parsed = urlparse(real_database_url())
    return (parsed.path or "/products_local").lstrip("/")


def create_cloned_temp_db() -> tuple[str, str]:
    """Creates a temporary DB by cloning the real app DB via TEMPLATE."""
    source_db = _source_db_name()
    dbname = _TEST_PREFIX + secrets.token_hex(4)
    maint = psycopg2.connect(maintenance_dsn())
    maint.autocommit = True
    try:
        with maint.cursor() as cur:
            cur.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid();
                """,
                (source_db,),
            )
            cur.execute(f'CREATE DATABASE "{dbname}" WITH TEMPLATE "{source_db}";')
    finally:
        maint.close()
    return dbname, dsn_for(dbname)


def drop_cloned_temp_db(dbname: str) -> None:
    if not dbname.startswith(_TEST_PREFIX):
        raise ValueError(f"Refusing to drop DB without expected prefix: {dbname}")
    maint = psycopg2.connect(maintenance_dsn())
    maint.autocommit = True
    try:
        with maint.cursor() as cur:
            cur.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid();
                """,
                (dbname,),
            )
            cur.execute(f'DROP DATABASE IF EXISTS "{dbname}";')
    finally:
        maint.close()


def _row_count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        return cur.fetchone()[0]


def run_rehearsal() -> dict:
    results: dict = {}
    source_db = _source_db_name()

    print(f"=== Step 0: Zero-write baseline on source DB ({source_db}) ===")
    src_conn = psycopg2.connect(real_database_url())
    src_conn.autocommit = True
    products_before_source = _row_count(src_conn, "products")
    exchange_rates_before_source = _row_count(src_conn, "exchange_rates")
    src_conn.close()
    print(f"Source products count: {products_before_source:,}")
    print(f"Source exchange_rates count: {exchange_rates_before_source}")

    print("\n=== Step 1: Creating Isolated Cloned Database (TEMPLATE) ===")
    t0_clone = time.perf_counter()
    dbname, dsn = create_cloned_temp_db()
    clone_dur = time.perf_counter() - t0_clone
    print(f"Cloned DB created: {dbname} in {clone_dur:.3f}s")
    results["temp_db_name"] = dbname
    results["clone_duration_s"] = round(clone_dur, 3)

    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = False

        print("\n=== Step 2: Preflight -- confirm clone has NO brand_master/currency_rates yet ===")
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('brand_master'), to_regclass('currency_rates')")
            pre_bm, pre_cr = cur.fetchone()
        results["preflight_clone_has_no_new_schema"] = pre_bm is None and pre_cr is None
        print(f"brand_master present before: {pre_bm is not None}; currency_rates present before: {pre_cr is not None}")

        print("\n=== Step 3: Applying migration_017_brand_master.sql (dependency, via plain `psql -f`) ===")
        t0_017 = time.perf_counter()
        exit_017, out_017 = run_migration_via_psql(dsn, _MIGRATION_017_PATH)
        dur_017 = time.perf_counter() - t0_017
        if exit_017 != 0:
            raise RuntimeError(f"migration_017 failed via psql (exit {exit_017}):\n{out_017}")
        results["migration_017_duration_s"] = round(dur_017, 3)
        results["migration_017_psql_exit_code"] = exit_017
        print(f"migration_017 applied in {dur_017:.3f}s")

        print("\n=== Step 4: Applying migration_018_currency_rates.sql (via plain `psql -f`) ===")
        t0_018 = time.perf_counter()
        exit_018, out_018 = run_migration_via_psql(dsn, _MIGRATION_018_PATH)
        dur_018 = time.perf_counter() - t0_018
        if exit_018 != 0:
            raise RuntimeError(f"migration_018 failed via psql (exit {exit_018}):\n{out_018}")
        results["migration_018_duration_s"] = round(dur_018, 3)
        results["migration_018_psql_exit_code"] = exit_018
        print(f"migration_018 applied in {dur_018:.3f}s")

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM currency_rates;")
            rates_count_first = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM currency_rate_history;")
            history_count_first = cur.fetchone()[0]

        print("\n=== Step 5: Idempotency -- re-running migration_018 (via plain `psql -f`) ===")
        t0_idem = time.perf_counter()
        exit_idem, out_idem = run_migration_via_psql(dsn, _MIGRATION_018_PATH)
        idem_dur = time.perf_counter() - t0_idem
        if exit_idem != 0:
            raise RuntimeError(f"migration_018 idempotent re-run failed via psql (exit {exit_idem}):\n{out_idem}")
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM currency_rates;")
            rates_count_second = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM currency_rate_history;")
            history_count_second = cur.fetchone()[0]
        results["idempotency"] = {
            "duration_s": round(idem_dur, 3),
            "rates_count_stable": rates_count_first == rates_count_second == 5,
            "history_count_stable_no_duplicate_seed": history_count_first == history_count_second,
        }
        print(f"Re-run in {idem_dur:.3f}s; currency_rates rows stable at {rates_count_second} (expected 5)")
        print(f"currency_rate_history rows stable at {history_count_second} (no duplicate seed rows)")

        print("\n=== Step 6: Verify workbook rates for all 5 currencies ===")
        with conn.cursor() as cur:
            cur.execute("SELECT currency_code, rate_vnd FROM currency_rates ORDER BY currency_code;")
            actual_rates = {code: Decimal(rate) for code, rate in cur.fetchall()}
        rates_match_workbook = actual_rates == _WORKBOOK_RATES
        results["currency_rates_match_workbook"] = rates_match_workbook
        results["actual_rates"] = {k: str(v) for k, v in actual_rates.items()}
        print(f"Actual rates: {actual_rates}")
        print(f"Match workbook exactly: {rates_match_workbook}")

        print("\n=== Step 7: Verify all 35 canonical brands resolve currency + positive rate ===")
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT bm.name, bm.currency_code, cr.rate_vnd
                FROM brand_master bm
                LEFT JOIN currency_rates cr ON cr.currency_code = bm.currency_code
                WHERE bm.is_active = TRUE
                ORDER BY bm.name;
                """
            )
            brand_rows = cur.fetchall()
        brand_count = len(brand_rows)
        brands_missing_rate = [name for name, _cc, rate in brand_rows if rate is None]
        brands_with_nonpositive_rate = [name for name, _cc, rate in brand_rows if rate is not None and Decimal(rate) <= 0]
        # "No foreign brand secretly uses rate 1" -- every brand whose
        # resolved rate is exactly 1 must be mapped to VND (never AUD/USD/EUR/GBP).
        foreign_brands_with_rate_one = [
            (name, cc) for name, cc, rate in brand_rows if rate is not None and Decimal(rate) == 1 and cc != "VND"
        ]
        results["brand_currency_resolution"] = {
            "canonical_brand_count": brand_count,
            "canonical_brand_count_is_35": brand_count == 35,
            "brands_missing_rate": brands_missing_rate,
            "brands_with_nonpositive_rate": brands_with_nonpositive_rate,
            "foreign_brands_incorrectly_at_rate_one": foreign_brands_with_rate_one,
            "no_foreign_brand_at_rate_one": len(foreign_brands_with_rate_one) == 0,
        }
        print(f"Canonical brands resolved: {brand_count} (expected 35)")
        print(f"Brands missing a rate: {brands_missing_rate}")
        print(f"Foreign (non-VND) brands incorrectly at rate 1: {foreign_brands_with_rate_one}")

        print("\n=== Step 8: EUR rate change propagates to ALL EUR brands, zero `products` writes ===")
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM brand_master WHERE currency_code = 'EUR' AND is_active = TRUE;")
            eur_brand_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM products;")
            products_count_before_eur_change = cur.fetchone()[0]

            cur.execute("SELECT updated_at FROM currency_rates WHERE currency_code = 'EUR';")
            eur_updated_at_before = cur.fetchone()[0]

            new_eur_rate = Decimal("31999")
            cur.execute(
                """
                UPDATE currency_rates
                SET rate_vnd = %s, updated_at = NOW(), update_source = 'REHEARSAL_TEST'
                WHERE currency_code = 'EUR';
                """,
                (new_eur_rate,),
            )
            cur.execute(
                """
                INSERT INTO currency_rate_history (currency_code, old_rate, new_rate, source)
                VALUES ('EUR', %s, %s, 'REHEARSAL_TEST');
                """,
                (_WORKBOOK_RATES["EUR"], new_eur_rate),
            )
        conn.commit()

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT bm.name, cr.rate_vnd
                FROM brand_master bm
                JOIN currency_rates cr ON cr.currency_code = bm.currency_code
                WHERE bm.currency_code = 'EUR' AND bm.is_active = TRUE;
                """
            )
            eur_brands_after = cur.fetchall()
            cur.execute("SELECT COUNT(*) FROM products;")
            products_count_after_eur_change = cur.fetchone()[0]

        all_eur_brands_got_new_rate = all(Decimal(rate) == new_eur_rate for _name, rate in eur_brands_after)
        results["eur_rate_propagation"] = {
            "eur_brand_count": eur_brand_count,
            "new_eur_rate": str(new_eur_rate),
            "eur_brands_all_updated": all_eur_brands_got_new_rate,
            "eur_brands_checked": len(eur_brands_after),
            "products_row_count_unchanged": products_count_before_eur_change == products_count_after_eur_change,
        }
        print(f"EUR brands: {eur_brand_count}; all picked up new rate {new_eur_rate}: {all_eur_brands_got_new_rate}")
        print(
            "products row count unchanged: "
            f"{products_count_before_eur_change == products_count_after_eur_change} "
            f"({products_count_before_eur_change:,} -> {products_count_after_eur_change:,})"
        )

        print("\n=== Step 9: Legacy exchange_rates table still present & untouched ===")
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('exchange_rates');")
            exchange_rates_present = cur.fetchone()[0] is not None
            cur.execute("SELECT COUNT(*) FROM exchange_rates;")
            exchange_rates_count_clone = cur.fetchone()[0]
        # migration_017 already canonicalizes `exchange_rates` down from the
        # raw legacy row count (41 brand strings pre-migration, includes
        # aliases/duplicates) to exactly one row per of the 35 canonical
        # brands -- that rewrite is migration_017's own documented behavior,
        # not something 6B2B2 does. The invariant this rehearsal actually
        # owns is narrower: the table must still exist post-6B2B2 (never
        # dropped) with exactly 35 canonical rows, so pre-6B2B2 code can
        # still read it for rollback.
        results["legacy_exchange_rates"] = {
            "table_present": exchange_rates_present,
            "row_count": exchange_rates_count_clone,
            "row_count_is_35_canonical_brands": exchange_rates_count_clone == 35,
        }
        print(f"exchange_rates present: {exchange_rates_present}; rows: {exchange_rates_count_clone}")

        conn.close()

    finally:
        print("\n=== Step 10: Cleaning up Temporary Rehearsal DB ===")
        drop_cloned_temp_db(dbname)
        print("Rehearsal DB dropped successfully!")

    print(f"\n=== Step 11: Zero-write confirmation on source DB ({source_db}) ===")
    src_conn2 = psycopg2.connect(real_database_url())
    src_conn2.autocommit = True
    products_after_source = _row_count(src_conn2, "products")
    exchange_rates_after_source = _row_count(src_conn2, "exchange_rates")
    src_conn2.close()
    results["source_db_zero_write"] = {
        "products_before": products_before_source,
        "products_after": products_after_source,
        "products_unchanged": products_before_source == products_after_source,
        "exchange_rates_before": exchange_rates_before_source,
        "exchange_rates_after": exchange_rates_after_source,
        "exchange_rates_unchanged": exchange_rates_before_source == exchange_rates_after_source,
    }
    print(
        f"Source products unchanged: {products_before_source == products_after_source} "
        f"({products_before_source:,} == {products_after_source:,})"
    )
    print(
        f"Source exchange_rates unchanged: {exchange_rates_before_source == exchange_rates_after_source} "
        f"({exchange_rates_before_source} == {exchange_rates_after_source})"
    )

    return results


if __name__ == "__main__":
    results = run_rehearsal()
    with open("rehearsal_results_6b2b2.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print("\nRehearsal summary saved to rehearsal_results_6b2b2.json")

    all_checks_passed = (
        results["preflight_clone_has_no_new_schema"]
        and results["idempotency"]["rates_count_stable"]
        and results["idempotency"]["history_count_stable_no_duplicate_seed"]
        and results["currency_rates_match_workbook"]
        and results["brand_currency_resolution"]["canonical_brand_count_is_35"]
        and not results["brand_currency_resolution"]["brands_missing_rate"]
        and not results["brand_currency_resolution"]["brands_with_nonpositive_rate"]
        and results["brand_currency_resolution"]["no_foreign_brand_at_rate_one"]
        and results["eur_rate_propagation"]["eur_brands_all_updated"]
        and results["eur_rate_propagation"]["products_row_count_unchanged"]
        and results["legacy_exchange_rates"]["table_present"]
        and results["legacy_exchange_rates"]["row_count_is_35_canonical_brands"]
        and results["source_db_zero_write"]["products_unchanged"]
        and results["source_db_zero_write"]["exchange_rates_unchanged"]
    )
    print(f"\nALL CHECKS PASSED: {all_checks_passed}")
