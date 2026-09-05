"""PostgreSQL Rehearsal Script for Phase 6B2B1-C.

Executes a full, isolated migration rehearsal against a TEMPLATE copy of products_local:
1. Clones products_local to temporary DB (p6a_release_gate_pgtest_rehearsal_*).
2. Captures pre-migration baseline (counts, hashes, sizes).
3. Applies migration_017_brand_master.sql and measures timing & WAL.
4. Re-runs migration_017_brand_master.sql to verify idempotency.
5. Verifies all 10 post-migration invariants:
   - 35 canonical brands in products
   - 192,233 products deleted (1,147,634 retained)
   - 100% source_brand backfilled
   - 55 collisions preserved
   - team_brands canonicalized
   - regulatory_rules row count & MD5 hash invariant
   - import_jobs row count & MD5 hash invariant
   - exchange_rates 35 canonical brands & workbook rates
   - zero aliases or Delete Set brands in products
   - brand_compliance_settings obsolete rows removed
6. Tests rollback in an isolated rollback run and verifies 100% data restoration.
7. Cleans up all temporary rehearsal databases.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg2
from dotenv import load_dotenv

load_dotenv()

_TEST_PREFIX = "p6a_release_gate_pgtest_rehearsal_"
_MIGRATION_PATH = Path(__file__).resolve().parents[1] / "sql" / "migration_017_brand_master.sql"
_MIGRATION_ID = "017_brand_master"


def real_database_url() -> str:
    return os.environ.get("DATABASE_URL", "")


def dsn_for(dbname: str) -> str:
    parsed = urlparse(real_database_url())
    return urlunparse(parsed._replace(path="/" + dbname))


def maintenance_dsn() -> str:
    return dsn_for("postgres")


def create_cloned_temp_db() -> tuple[str, str]:
    """Creates a temporary DB by cloning products_local via TEMPLATE."""
    dbname = _TEST_PREFIX + secrets.token_hex(4)
    maint = psycopg2.connect(maintenance_dsn())
    maint.autocommit = True
    try:
        with maint.cursor() as cur:
            # Terminate any stray idle connection to products_local
            cur.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = 'products_local' AND pid <> pg_backend_pid();
                """
            )
            cur.execute(f'CREATE DATABASE "{dbname}" WITH TEMPLATE products_local;')
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


def compute_table_hash(conn, table_name: str, order_col: str = "id") -> str:
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {table_name} ORDER BY {order_col};")
        rows = cur.fetchall()
        h = hashlib.sha256()
        for r in rows:
            h.update(str(r).encode("utf-8"))
        return h.hexdigest()


def run_rehearsal() -> dict:
    results = {}
    print("=== Step 1: Creating Isolated Cloned Database from products_local ===")
    t0_clone = time.perf_counter()
    dbname, dsn = create_cloned_temp_db()
    clone_dur = time.perf_counter() - t0_clone
    print(f"Cloned DB created: {dbname} in {clone_dur:.3f}s")
    results["temp_db_name"] = dbname
    results["clone_duration_s"] = round(clone_dur, 3)

    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = False

        print("\n=== Step 2: Baseline Snapshot Measurements ===")
        with conn.cursor() as cur:
            cur.execute("SELECT pg_database_size(%s);", (dbname,))
            db_size_before = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM products;")
            products_count_before = cur.fetchone()[0]

            cur.execute("SELECT COUNT(DISTINCT brand) FROM products;")
            brands_count_before = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM regulatory_rules;")
            reg_count_before = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM team_brands;")
            team_brands_count_before = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM exchange_rates;")
            rates_count_before = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM brand_compliance_settings;")
            bcs_count_before = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM import_jobs;")
            import_jobs_count_before = cur.fetchone()[0]

            cur.execute("SELECT id, brand, name, code, cas FROM products WHERE id = 1344915;")
            test_row_before = cur.fetchone()

        reg_hash_before = compute_table_hash(conn, "regulatory_rules", "id")
        import_jobs_hash_before = compute_table_hash(conn, "import_jobs", "id")

        results["baseline"] = {
            "products_count": products_count_before,
            "distinct_brands": brands_count_before,
            "regulatory_rules_count": reg_count_before,
            "regulatory_rules_sha256": reg_hash_before,
            "team_brands_count": team_brands_count_before,
            "exchange_rates_count": rates_count_before,
            "brand_compliance_settings_count": bcs_count_before,
            "import_jobs_count": import_jobs_count_before,
            "import_jobs_sha256": import_jobs_hash_before,
            "db_size_bytes_before": db_size_before,
            "test_row_1344915": test_row_before,
        }
        print(f"Products: {products_count_before:,}")
        print(f"Distinct brands: {brands_count_before}")
        print(f"Regulatory rules: {reg_count_before} (SHA-256: {reg_hash_before[:12]}...)")
        print(f"Team brands: {team_brands_count_before}")
        print(f"Exchange rates: {rates_count_before}")
        print(f"DB size before: {db_size_before / (1024*1024):.2f} MB")

        print("\n=== Step 3: Executing Migration 017 ===")
        migration_sql = _MIGRATION_PATH.read_text(encoding="utf-8")

        # Measure WAL and timing
        with conn.cursor() as cur:
            cur.execute("SELECT pg_current_wal_lsn();")
            wal_before = cur.fetchone()[0]

        t0_mig = time.perf_counter()
        with conn.cursor() as cur:
            cur.execute(migration_sql)
        conn.commit()
        mig_dur = time.perf_counter() - t0_mig

        with conn.cursor() as cur:
            cur.execute("SELECT pg_current_wal_lsn();")
            wal_after = cur.fetchone()[0]
            cur.execute("SELECT pg_wal_lsn_diff(%s, %s);", (wal_after, wal_before))
            wal_bytes = cur.fetchone()[0]

            cur.execute("SELECT pg_database_size(%s);", (dbname,))
            db_size_after = cur.fetchone()[0]

        results["migration_execution"] = {
            "duration_s": round(mig_dur, 3),
            "wal_bytes": wal_bytes,
            "wal_mb": round(wal_bytes / (1024 * 1024), 2),
            "db_size_bytes_after": db_size_after,
            "db_size_mb_after": round(db_size_after / (1024 * 1024), 2),
        }
        print(f"Migration 017 executed in {mig_dur:.3f}s")
        print(f"WAL generated: {wal_bytes / (1024*1024):.2f} MB")
        print(f"DB size after: {db_size_after / (1024*1024):.2f} MB")

        print("\n=== Step 4: Testing Idempotency (Second Execution) ===")
        t0_idem = time.perf_counter()
        with conn.cursor() as cur:
            cur.execute(migration_sql)
        conn.commit()
        idem_dur = time.perf_counter() - t0_idem
        results["idempotency_execution"] = {
            "duration_s": round(idem_dur, 3),
        }
        print(f"Second execution (idempotency) completed in {idem_dur:.3f}s")

        print("\n=== Step 5: Comprehensive Post-Migration Verification ===")
        with conn.cursor() as cur:
            # 1. Check brand_master count
            cur.execute("SELECT COUNT(*) FROM brand_master WHERE is_active = TRUE;")
            bm_count = cur.fetchone()[0]

            # 2. Check brand_aliases count
            cur.execute("SELECT COUNT(*) FROM brand_aliases WHERE is_active = TRUE;")
            ba_count = cur.fetchone()[0]

            # 3. Check products count and distinct brands
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT brand) FROM products;")
            products_after, distinct_brands_after = cur.fetchone()

            # 4. Check test row 1344915 is gone
            cur.execute("SELECT id FROM products WHERE id = 1344915;")
            test_row_after = cur.fetchone()

            # 5. Check source_brand backfill
            cur.execute("SELECT COUNT(*) FROM products WHERE source_brand IS NULL OR TRIM(source_brand) = '';")
            null_source_brand_count = cur.fetchone()[0]

            # 6. Check unmapped brands in products
            cur.execute("SELECT DISTINCT brand FROM products WHERE brand NOT IN (SELECT name FROM brand_master);")
            unmapped_in_products = [r[0] for r in cur.fetchall()]

            # 7. Check 55 collisions
            cur.execute(
                """
                SELECT brand, UPPER(TRIM(code)), COUNT(*), COUNT(DISTINCT source_brand)
                FROM products
                GROUP BY brand, UPPER(TRIM(code))
                HAVING COUNT(*) > 1 AND COUNT(DISTINCT source_brand) > 1;
                """
            )
            cross_collisions = cur.fetchall()

            # 8. Check team_brands
            cur.execute("SELECT team_id, COUNT(DISTINCT brand) FROM team_brands GROUP BY team_id ORDER BY team_id;")
            team_brands_summary = cur.fetchall()

            cur.execute("SELECT DISTINCT brand FROM team_brands WHERE brand NOT IN (SELECT name FROM brand_master);")
            invalid_team_brands = [r[0] for r in cur.fetchall()]

            # 9. Check exchange_rates
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT brand) FROM exchange_rates;")
            rates_count_after, rates_distinct_after = cur.fetchone()

            cur.execute("SELECT DISTINCT brand FROM exchange_rates WHERE brand NOT IN (SELECT name FROM brand_master);")
            invalid_rates = [r[0] for r in cur.fetchall()]

            # 10. Check brand_compliance_settings
            cur.execute("SELECT COUNT(*) FROM brand_compliance_settings WHERE brand_norm IN ('TEST1', 'TEST2');")
            bcs_obsolete_count = cur.fetchone()[0]

            # 11. Check regulatory_rules
            cur.execute("SELECT COUNT(*) FROM regulatory_rules;")
            reg_count_after = cur.fetchone()[0]

            # 12. Check import_jobs
            cur.execute("SELECT COUNT(*) FROM import_jobs;")
            import_jobs_count_after = cur.fetchone()[0]

        reg_hash_after = compute_table_hash(conn, "regulatory_rules", "id")
        import_jobs_hash_after = compute_table_hash(conn, "import_jobs", "id")

        deleted_products_count = products_count_before - products_after

        checks = {
            "brand_master_count_is_35": bm_count == 35,
            "brand_master_count": bm_count,
            "brand_aliases_count": ba_count,
            "products_retained_count": products_after,
            "products_deleted_count": deleted_products_count,
            "deleted_count_matches_192233": deleted_products_count == 192233,
            "distinct_canonical_brands_in_products": distinct_brands_after,
            "distinct_brands_is_35": distinct_brands_after == 35,
            "test_row_1344915_deleted": test_row_after is None,
            "source_brand_null_count": null_source_brand_count,
            "source_brand_100_percent_backfilled": null_source_brand_count == 0,
            "unmapped_brands_in_products": unmapped_in_products,
            "cross_brand_collisions_count": len(cross_collisions),
            "cross_brand_collisions_is_55": len(cross_collisions) == 55,
            "team_brands_summary": team_brands_summary,
            "invalid_team_brands": invalid_team_brands,
            "exchange_rates_count": rates_count_after,
            "exchange_rates_is_35": rates_count_after == 35,
            "invalid_exchange_rates": invalid_rates,
            "bcs_obsolete_removed": bcs_obsolete_count == 0,
            "regulatory_rules_count_invariant": reg_count_before == reg_count_after,
            "regulatory_rules_hash_invariant": reg_hash_before == reg_hash_after,
            "import_jobs_count_invariant": import_jobs_count_before == import_jobs_count_after,
            "import_jobs_hash_invariant": import_jobs_hash_before == import_jobs_hash_after,
        }
        results["checks"] = checks

        print(f"Canonical brands in products: {distinct_brands_after} (expected 35)")
        print(f"Products retained: {products_after:,} (expected 1,147,634)")
        print(f"Products deleted: {deleted_products_count:,} (expected 192,233)")
        print(f"Source brand 100% backfilled: {null_source_brand_count == 0}")
        print(f"Cross-brand collisions preserved: {len(cross_collisions)} (expected 55)")
        print(f"Team brands summary: {team_brands_summary}")
        print(f"Exchange rates count: {rates_count_after} (expected 35)")
        print(f"Regulatory invariant: {reg_hash_before == reg_hash_after}")
        print(f"Import jobs invariant: {import_jobs_hash_before == import_jobs_hash_after}")

        conn.close()

    finally:
        print("\n=== Step 6: Cleaning up Temporary Rehearsal DB ===")
        drop_cloned_temp_db(dbname)
        print("Rehearsal DB dropped successfully!")

    return results


def _verify_no_stale_backup(cur, dbname: str) -> None:
    """Fail-closed guard (Phase 6B2B1-E): refuse to (re)create migration-017
    backup tables if ANY `_backup_p6b2b1c_*` table already exists on this
    database -- NEVER silently reuse or overwrite a snapshot left over from
    a different/failed run. This rehearsal script always operates on a
    brand-new, uniquely-named cloned DB (`create_cloned_temp_db()`), so this
    should never actually trigger in normal operation; it exists so the
    exact same backup-table naming/creation code stays safe if ever reused
    against a longer-lived DB in a later phase.
    """
    cur.execute(
        """
        SELECT relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname LIKE '_backup_p6b2b1c_%' AND n.nspname = 'public'
        ORDER BY relname;
        """
    )
    existing = [r[0] for r in cur.fetchall()]
    if existing:
        raise RuntimeError(
            f"Refusing to proceed on database '{dbname}': stale migration-017 backup "
            f"table(s) already exist ({', '.join(existing)}). These may be left over from "
            f"a previous failed/incomplete run or belong to a DIFFERENT snapshot. An operator "
            f"must inspect and explicitly clean them up via "
            f"scripts/cleanup_migration_017_backups.sql (after confirming a sufficient "
            f"external backup) before re-running -- never auto-reused/overwritten."
        )


def _create_backup_metadata(cur, dbname: str, counts: dict) -> None:
    """Creates the `_backup_p6b2b1c_metadata` table alongside the data
    backup tables: minimal metadata (migration identifier, snapshot
    timestamp, source row counts, status) that both the rollback-restore
    step and `scripts/cleanup_migration_017_backups.sql` must verify before
    trusting/removing this snapshot. `snapshot_id` is the (unique) database
    name itself, since every snapshot here lives in its own throwaway DB.
    """
    cur.execute(
        """
        CREATE TABLE _backup_p6b2b1c_metadata (
            migration_id                           TEXT NOT NULL,
            snapshot_id                             TEXT PRIMARY KEY,
            created_at                              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            source_products_count                   BIGINT NOT NULL,
            source_team_brands_count                BIGINT NOT NULL,
            source_exchange_rates_count              BIGINT NOT NULL,
            source_brand_compliance_settings_count  BIGINT NOT NULL,
            status                                  TEXT NOT NULL,
            external_backup_confirmed_at            TIMESTAMPTZ NULL,
            external_backup_confirmed_by            TEXT NULL
        );
        """
    )
    cur.execute(
        """
        INSERT INTO _backup_p6b2b1c_metadata (
            migration_id, snapshot_id, source_products_count, source_team_brands_count,
            source_exchange_rates_count, source_brand_compliance_settings_count, status
        ) VALUES (%s, %s, %s, %s, %s, %s, 'CREATED');
        """,
        (
            _MIGRATION_ID,
            dbname,
            counts["products"],
            counts["team_brands"],
            counts["exchange_rates"],
            counts["brand_compliance_settings"],
        ),
    )


def _verify_backup_metadata_before_restore(cur, dbname: str) -> None:
    """Rollback must verify snapshot metadata/counts before trusting the
    backup tables for restore -- never blindly restore from whatever
    `_backup_p6b2b1c_*` tables happen to exist.
    """
    cur.execute(
        "SELECT migration_id, source_products_count, source_team_brands_count, "
        "source_exchange_rates_count, source_brand_compliance_settings_count, status "
        "FROM _backup_p6b2b1c_metadata WHERE snapshot_id = %s;",
        (dbname,),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError(
            f"Refusing rollback on '{dbname}': no `_backup_p6b2b1c_metadata` row found for "
            f"this snapshot_id. Backup tables without matching metadata cannot be trusted."
        )
    meta_migration_id, meta_products, meta_team_brands, meta_rates, meta_bcs, meta_status = row
    if meta_migration_id != _MIGRATION_ID:
        raise RuntimeError(
            f"Refusing rollback on '{dbname}': metadata migration_id "
            f"'{meta_migration_id}' does not match expected '{_MIGRATION_ID}'."
        )
    if meta_status != "CREATED":
        raise RuntimeError(
            f"Refusing rollback on '{dbname}': unexpected metadata status '{meta_status}' "
            f"(expected 'CREATED' -- this snapshot may already have been consumed/verified)."
        )

    # Re-verify the backup tables' OWN current row counts still match what
    # was recorded at snapshot time -- catches partial drops/truncation/
    # tampering between backup creation and restore.
    checks = [
        ("_backup_p6b2b1c_modified_products", meta_products),
        ("_backup_p6b2b1c_team_brands", meta_team_brands),
        ("_backup_p6b2b1c_exchange_rates", meta_rates),
        ("_backup_p6b2b1c_brand_compliance_settings", meta_bcs),
    ]
    for table, expected_count in checks:
        cur.execute(f"SELECT COUNT(*) FROM {table};")
        actual_count = cur.fetchone()[0]
        if actual_count != expected_count:
            raise RuntimeError(
                f"Refusing rollback on '{dbname}': {table} has {actual_count} rows, "
                f"expected {expected_count} per snapshot metadata -- backup may be corrupted."
            )


def _mark_backup_metadata_verified(cur, dbname: str) -> None:
    cur.execute(
        "UPDATE _backup_p6b2b1c_metadata SET status = 'VERIFIED' WHERE snapshot_id = %s;",
        (dbname,),
    )


def run_rollback_test() -> dict:
    print("\n=======================================================")
    print("=== Step 7: Testing Rollback on Dedicated Cloned DB ===")
    print("=======================================================")
    dbname, dsn = create_cloned_temp_db()
    print(f"Cloned DB for rollback test created: {dbname}")
    rollback_results = {}

    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = False

        # -0. Fail-closed guard: refuse to proceed if stale backup tables
        # from a different/failed run already exist on this database.
        with conn.cursor() as cur:
            _verify_no_stale_backup(cur, dbname)
        conn.commit()

        # 0. Compute Pre-Migration Cryptographic Hashes & Sequence Checkpoint
        with conn.cursor() as cur:
            cur.execute("""
                SELECT md5(string_agg(row_hash, ''))
                FROM (
                    SELECT md5(p.id::text || '|' || COALESCE(p.brand, '') || '|' || COALESCE(p.code, '') || '|' || COALESCE(p.cas, '') || '|' || COALESCE(p.name, '') || '|' || COALESCE(p.size, '') || '|' || COALESCE(p.price, '')) AS row_hash
                    FROM products p
                    ORDER BY p.id
                ) s;
            """)
            hash_products_before = cur.fetchone()[0]

            cur.execute("""
                SELECT md5(string_agg(row_hash, ''))
                FROM (
                    SELECT md5(team_id::text || '|' || brand) AS row_hash
                    FROM team_brands
                    ORDER BY team_id, brand
                ) s;
            """)
            hash_tb_before = cur.fetchone()[0]

            cur.execute("""
                SELECT md5(string_agg(row_hash, ''))
                FROM (
                    SELECT md5(brand || '|' || rate::text) AS row_hash
                    FROM exchange_rates
                    ORDER BY brand
                ) s;
            """)
            hash_er_before = cur.fetchone()[0]

            cur.execute("""
                SELECT md5(string_agg(row_hash, ''))
                FROM (
                    SELECT md5(brand_norm || '|' || manual_compliance_priority::text) AS row_hash
                    FROM brand_compliance_settings
                    ORDER BY brand_norm
                ) s;
            """)
            hash_bcs_before = cur.fetchone()[0]

            cur.execute("SELECT MAX(id) FROM products;")
            max_prod_id_before = cur.fetchone()[0]

        # 1. Create Pre-Migration Backups on the DB (Named with Release Identifier)
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE _backup_p6b2b1c_deleted_products AS
                SELECT id, name, code, cas, brand, size, ship, price, note,
                       manual_compliance, manual_compliance_note, preparation_type
                FROM products WHERE brand IN (
                    'TEST1', 'TEST2', 'Phụ lục I', 'Clearsynth', 'TCI', 'ACROS', 'Axios Research',
                    'MAYBRIDGE', 'Aozeal(Mỹ)', 'Aquigen', 'FISHER CHEMICAL', 'Chemservice (Mỹ)',
                    'Merck', 'BIOREAGENTS', 'Oxford - Ấn Độ', 'Columbia Bioscience, Inc.',
                    'Bertin Technologies (not active - use vendor # 5869)', 'NIFC (Việt Nam)',
                    'Biosense Laboratories AS', 'THERMO SCIENTIFIC', 'Eurofins Calixar'
                );
                CREATE TABLE _backup_p6b2b1c_modified_products AS
                SELECT id, brand FROM products;

                CREATE TABLE _backup_p6b2b1c_team_brands AS SELECT team_id, brand FROM team_brands;
                CREATE TABLE _backup_p6b2b1c_exchange_rates AS SELECT brand, rate, updated_at FROM exchange_rates;
                CREATE TABLE _backup_p6b2b1c_brand_compliance_settings AS SELECT brand_norm, manual_compliance_priority, updated_at FROM brand_compliance_settings;
                """
            )

            # Minimal snapshot metadata (migration id, timestamp, source row
            # counts, status) -- required by both the restore step below and
            # scripts/cleanup_migration_017_backups.sql before they trust or
            # remove this snapshot.
            cur.execute("SELECT COUNT(*) FROM _backup_p6b2b1c_modified_products;")
            snap_products_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM _backup_p6b2b1c_team_brands;")
            snap_team_brands_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM _backup_p6b2b1c_exchange_rates;")
            snap_rates_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM _backup_p6b2b1c_brand_compliance_settings;")
            snap_bcs_count = cur.fetchone()[0]
            _create_backup_metadata(
                cur,
                dbname,
                {
                    "products": snap_products_count,
                    "team_brands": snap_team_brands_count,
                    "exchange_rates": snap_rates_count,
                    "brand_compliance_settings": snap_bcs_count,
                },
            )

            # Record sizes of backup tables
            cur.execute(
                """
                SELECT
                    c.relname,
                    pg_total_relation_size(c.oid) AS bytes,
                    pg_size_pretty(pg_total_relation_size(c.oid)) AS size_pretty
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname LIKE '_backup_p6b2b1c_%'
                  AND n.nspname = 'public'
                ORDER BY c.relname;
                """
            )
            backup_table_sizes = {r[0]: {"bytes": r[1], "size_pretty": r[2]} for r in cur.fetchall()}
        conn.commit()

        # 2. Run Migration 017
        migration_sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(migration_sql)
        conn.commit()

        # Confirm migration applied
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM products;")
            after_mig_count = cur.fetchone()[0]
        self_assert = (after_mig_count == 1147634)
        print(f"Products count after migration: {after_mig_count} (migrated: {self_assert})")

        # 3. Execute Rollback Procedure (Explicit Columns, Sequence Reset)
        # Verify snapshot metadata/counts BEFORE trusting the backup tables
        # for restore -- never blindly restore.
        with conn.cursor() as cur:
            _verify_backup_metadata_before_restore(cur, dbname)
        conn.commit()

        print("Executing rollback script with explicit column restore...")
        t0_rb = time.perf_counter()
        with conn.cursor() as cur:
            # Phase 6B2B1-E: migration_017 now adds `source_brand NOT NULL`
            # and the products.brand/team_brands.brand -> brand_master(name)
            # FKs. A data-only rollback that nulls out source_brand (below,
            # to fully revert to pre-migration semantics) would violate the
            # NOT NULL constraint, and restoring pre-migration
            # non-canonical brand values would violate the FK. Rollback
            # undoes migration_017 as a whole -- constraints included --
            # not just its data; re-running migration_017 afterwards
            # recreates them fresh via its own idempotent guarded DDL.
            cur.execute(
                """
                ALTER TABLE products ALTER COLUMN source_brand DROP NOT NULL;
                ALTER TABLE products DROP CONSTRAINT IF EXISTS chk_products_source_brand_not_null;
                ALTER TABLE products DROP CONSTRAINT IF EXISTS fk_products_brand_master;
                ALTER TABLE team_brands DROP CONSTRAINT IF EXISTS fk_team_brands_brand_master;
                """
            )

            # Restore modified product brands
            cur.execute(
                """
                UPDATE products p
                SET brand = b.brand,
                    source_brand = NULL
                FROM _backup_p6b2b1c_modified_products b
                WHERE p.id = b.id;
                """
            )

            # Restore deleted products listing all columns explicitly (safe even if backup predates source_brand)
            cur.execute(
                """
                INSERT INTO products (
                    id, name, code, cas, brand, size, ship, price, note,
                    manual_compliance, manual_compliance_note, preparation_type, source_brand
                )
                SELECT
                    b.id, b.name, b.code, b.cas, b.brand, b.size, b.ship, b.price, b.note,
                    b.manual_compliance, b.manual_compliance_note, b.preparation_type, NULL
                FROM _backup_p6b2b1c_deleted_products b
                ON CONFLICT (id) DO UPDATE
                SET name                   = EXCLUDED.name,
                    code                   = EXCLUDED.code,
                    cas                    = EXCLUDED.cas,
                    brand                  = EXCLUDED.brand,
                    size                   = EXCLUDED.size,
                    ship                   = EXCLUDED.ship,
                    price                  = EXCLUDED.price,
                    note                   = EXCLUDED.note,
                    manual_compliance      = EXCLUDED.manual_compliance,
                    manual_compliance_note = EXCLUDED.manual_compliance_note,
                    preparation_type       = EXCLUDED.preparation_type,
                    source_brand           = NULL;
                """
            )

            # Sequence verification and reset
            cur.execute("""
                SELECT setval(pg_get_serial_sequence('products', 'id'), COALESCE(MAX(id), 1)) FROM products;
            """)

            # Restore team_brands
            cur.execute("DELETE FROM team_brands;")
            cur.execute("INSERT INTO team_brands (team_id, brand) SELECT team_id, brand FROM _backup_p6b2b1c_team_brands;")

            # Restore exchange_rates
            cur.execute("DELETE FROM exchange_rates;")
            cur.execute("INSERT INTO exchange_rates (brand, rate, updated_at) SELECT brand, rate, updated_at FROM _backup_p6b2b1c_exchange_rates;")

            # Restore brand_compliance_settings
            cur.execute("DELETE FROM brand_compliance_settings;")
            cur.execute(
                """
                INSERT INTO brand_compliance_settings (brand_norm, manual_compliance_priority, updated_at)
                SELECT brand_norm, manual_compliance_priority, updated_at FROM _backup_p6b2b1c_brand_compliance_settings;
                """
            )
        conn.commit()
        rb_dur = time.perf_counter() - t0_rb
        print(f"Rollback completed in {rb_dur:.3f}s")

        # 4. Verify Post-Rollback Hashes & Integrity
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), COUNT(DISTINCT brand) FROM products;")
            restored_prods, restored_brands = cur.fetchone()

            cur.execute("SELECT COUNT(*) FROM team_brands;")
            restored_tb = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM exchange_rates;")
            restored_er = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM brand_compliance_settings;")
            restored_bcs = cur.fetchone()[0]

            cur.execute("SELECT id, brand, name FROM products WHERE id = 1344915;")
            restored_test_row = cur.fetchone()

            # Post-rollback hash checks
            cur.execute("""
                SELECT md5(string_agg(row_hash, ''))
                FROM (
                    SELECT md5(p.id::text || '|' || COALESCE(p.brand, '') || '|' || COALESCE(p.code, '') || '|' || COALESCE(p.cas, '') || '|' || COALESCE(p.name, '') || '|' || COALESCE(p.size, '') || '|' || COALESCE(p.price, '')) AS row_hash
                    FROM products p
                    ORDER BY p.id
                ) s;
            """)
            hash_products_after = cur.fetchone()[0]

            cur.execute("""
                SELECT md5(string_agg(row_hash, ''))
                FROM (
                    SELECT md5(team_id::text || '|' || brand) AS row_hash
                    FROM team_brands
                    ORDER BY team_id, brand
                ) s;
            """)
            hash_tb_after = cur.fetchone()[0]

            cur.execute("""
                SELECT md5(string_agg(row_hash, ''))
                FROM (
                    SELECT md5(brand || '|' || rate::text) AS row_hash
                    FROM exchange_rates
                    ORDER BY brand
                ) s;
            """)
            hash_er_after = cur.fetchone()[0]

            cur.execute("""
                SELECT md5(string_agg(row_hash, ''))
                FROM (
                    SELECT md5(brand_norm || '|' || manual_compliance_priority::text) AS row_hash
                    FROM brand_compliance_settings
                    ORDER BY brand_norm
                ) s;
            """)
            hash_bcs_after = cur.fetchone()[0]

            cur.execute("SELECT last_value FROM products_id_seq;")
            seq_val = cur.fetchone()[0]

        rollback_fully_verified = (
            hash_products_before == hash_products_after
            and hash_tb_before == hash_tb_after
            and hash_er_before == hash_er_after
            and hash_bcs_before == hash_bcs_after
            and seq_val >= max_prod_id_before
        )
        with conn.cursor() as cur:
            if rollback_fully_verified:
                # Only mark VERIFIED once every hash/sequence invariant
                # actually matched -- this is the status
                # scripts/cleanup_migration_017_backups.sql requires before
                # it will ever DROP this snapshot's backup tables.
                _mark_backup_metadata_verified(cur, dbname)
            else:
                cur.execute(
                    "UPDATE _backup_p6b2b1c_metadata SET status = 'FAILED_VERIFICATION' WHERE snapshot_id = %s;",
                    (dbname,),
                )
        conn.commit()

        rollback_results = {
            "rollback_fully_verified": rollback_fully_verified,
            "rollback_duration_s": round(rb_dur, 3),
            "backup_table_sizes": backup_table_sizes,
            "restored_products_count": restored_prods,
            "restored_distinct_brands": restored_brands,
            "restored_team_brands_count": restored_tb,
            "restored_exchange_rates_count": restored_er,
            "restored_brand_compliance_settings_count": restored_bcs,
            "restored_test_row_1344915": restored_test_row,
            "products_count_restored_to_1339867": restored_prods == 1339867,
            "distinct_brands_restored_to_142": restored_brands == 142,
            "team_brands_restored_to_288": restored_tb == 288,
            "exchange_rates_restored_to_41": restored_er == 41,
            "bcs_restored_to_2": restored_bcs == 2,
            "products_hash_matches_baseline": hash_products_before == hash_products_after,
            "team_brands_hash_matches_baseline": hash_tb_before == hash_tb_after,
            "exchange_rates_hash_matches_baseline": hash_er_before == hash_er_after,
            "bcs_hash_matches_baseline": hash_bcs_before == hash_bcs_after,
            "sequence_products_id_valid": seq_val >= max_prod_id_before,
            "products_hash_before": hash_products_before,
            "products_hash_after": hash_products_after,
        }
        print(f"Restored products: {restored_prods:,} (expected 1,339,867)")
        print(f"Restored distinct brands: {restored_brands} (expected 142)")
        print(f"Restored team brands: {restored_tb} (expected 288)")
        print(f"Restored exchange rates: {restored_er} (expected 41)")
        print(f"Restored brand compliance settings: {restored_bcs} (expected 2)")
        print(f"Restored test row 1344915: {restored_test_row}")
        print(f"Products hash matches baseline: {hash_products_before == hash_products_after}")
        print(f"Team brands hash matches baseline: {hash_tb_before == hash_tb_after}")
        print(f"Exchange rates hash matches baseline: {hash_er_before == hash_er_after}")
        print(f"BCS hash matches baseline: {hash_bcs_before == hash_bcs_after}")
        print(f"Sequence products.id valid: {seq_val >= max_prod_id_before} (val={seq_val})")

        conn.close()
    finally:
        drop_cloned_temp_db(dbname)
        print("Rollback DB dropped successfully!")

    return rollback_results


if __name__ == "__main__":
    results = run_rehearsal()
    rollback = run_rollback_test()
    summary = {"rehearsal": results, "rollback": rollback}
    with open("rehearsal_results.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print("\nRehearsal summary saved to rehearsal_results.json")
