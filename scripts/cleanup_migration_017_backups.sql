-- ============================================================================
-- Post-Migration Backup Tables Cleanup Runbook (Phase 6B2B1-C / D / E)
--
-- PURPOSE:
-- Drops temporary emergency rollback tables created during Migration 017 once
-- UAT is complete and the rollback observation window has safely expired.
--
-- USAGE (both variables are REQUIRED -- the script fails closed without them):
--   psql "$DATABASE_URL" \
--     -v expected_dbname='<exact target database name>' \
--     -v target_snapshot_id='<_backup_p6b2b1c_metadata.snapshot_id to remove>' \
--     -v ON_ERROR_STOP=1 \
--     -f scripts/cleanup_migration_017_backups.sql
--
-- SAFETY CHECKS (Phase 6B2B1-E fail-closed gate -- ALL enforced below, not
-- just documented in comments):
-- 1. `\set ON_ERROR_STOP on` -- any failed check below aborts the WHOLE
--    script immediately; the DROP/VACUUM statements are never reached.
-- 2. Target database identity is verified against `:expected_dbname`
--    (never guess/assume which DB the running psql session is connected to).
-- 3. A `_backup_p6b2b1c_metadata` row for `:target_snapshot_id` must exist,
--    have `status = 'VERIFIED'` (i.e. a rollback rehearsal already proved
--    100% restorability via hash comparison -- see
--    scripts/rehearsal_phase6b2b1_c.py), and have
--    `external_backup_confirmed_at` NOT NULL (an operator must have
--    explicitly recorded, out-of-band, that a `pg_dump -Fc` was taken and
--    validated with `pg_restore --list` -- see the UPDATE template below).
-- 4. Only the backup tables belonging to that EXACT snapshot are touched;
--    if any other `_backup_p6b2b1c_*` snapshot happens to coexist, this
--    script does not guess which one you meant and still requires the
--    exact `target_snapshot_id` match above.
--
-- To record the external-backup confirmation ahead of running this script:
--   UPDATE _backup_p6b2b1c_metadata
--      SET external_backup_confirmed_at = NOW(), external_backup_confirmed_by = '<your name>'
--    WHERE snapshot_id = '<target_snapshot_id>';
-- ============================================================================

\set ON_ERROR_STOP on

-- psql's `:'var'` substitution is NOT performed inside dollar-quoted ($$...$$)
-- PL/pgSQL bodies (verified empirically -- it errors with "syntax error at
-- or near ':'"). Stage both required variables into session-local GUCs via
-- plain (non-dollar-quoted) statements, then read them back inside the DO
-- blocks below with current_setting().
SELECT set_config('cleanup_p6b2b1c.expected_dbname', :'expected_dbname', false);
SELECT set_config('cleanup_p6b2b1c.target_snapshot_id', :'target_snapshot_id', false);

DO $$
BEGIN
    IF current_database() <> current_setting('cleanup_p6b2b1c.expected_dbname') THEN
        RAISE EXCEPTION
            'Refusing to proceed: connected to database "%", but -v expected_dbname=''%''  was requested. '
            'Re-run against the correct database.',
            current_database(), current_setting('cleanup_p6b2b1c.expected_dbname');
    END IF;

    IF to_regclass('_backup_p6b2b1c_metadata') IS NULL THEN
        RAISE EXCEPTION
            'Refusing to proceed: no `_backup_p6b2b1c_metadata` table on database "%". '
            'Backup tables without matching metadata cannot be trusted -- investigate manually, '
            'do not drop blindly.',
            current_database();
    END IF;
END $$;

DO $$
DECLARE
    v_snapshot_id TEXT := current_setting('cleanup_p6b2b1c.target_snapshot_id');
    v_status TEXT;
    v_confirmed_at TIMESTAMPTZ;
BEGIN
    SELECT status, external_backup_confirmed_at
      INTO v_status, v_confirmed_at
      FROM _backup_p6b2b1c_metadata
     WHERE snapshot_id = v_snapshot_id;

    IF v_status IS NULL THEN
        RAISE EXCEPTION
            'Refusing to proceed: no _backup_p6b2b1c_metadata row for snapshot_id=''%''  on database "%". '
            'Pass the exact snapshot_id you intend to remove via -v target_snapshot_id=...',
            v_snapshot_id, current_database();
    END IF;

    IF v_status <> 'VERIFIED' THEN
        RAISE EXCEPTION
            'Refusing to proceed: snapshot ''%''  has status ''%''  (expected ''VERIFIED''). '
            'A rollback rehearsal must prove 100%% restorability (hash-matched) before this '
            'snapshot may be dropped.',
            v_snapshot_id, v_status;
    END IF;

    IF v_confirmed_at IS NULL THEN
        RAISE EXCEPTION
            'Refusing to proceed: snapshot ''%''  has no external_backup_confirmed_at. '
            'An operator must confirm a sufficient external backup (pg_dump -Fc, validated with '
            'pg_restore --list) exists BEFORE this script may drop the in-DB safety net. See the '
            'UPDATE template in this file''s header comment.',
            v_snapshot_id;
    END IF;

    RAISE NOTICE 'All fail-closed checks passed for snapshot ''%''  on database "%". Proceeding.',
        v_snapshot_id, current_database();
END $$;

-- 1. Report sizes of backup tables before removal (informational only)
SELECT
    relname AS backup_table,
    pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relname LIKE '_backup_p6b2b1c_%'
  AND n.nspname = 'public'
ORDER BY c.relname;

-- 2. Drop the 5 data backup tables + the metadata table for this snapshot.
-- (All checks above already passed, or ON_ERROR_STOP aborted before here.)
DROP TABLE IF EXISTS _backup_p6b2b1c_deleted_products;
DROP TABLE IF EXISTS _backup_p6b2b1c_modified_products;
DROP TABLE IF EXISTS _backup_p6b2b1c_team_brands;
DROP TABLE IF EXISTS _backup_p6b2b1c_exchange_rates;
DROP TABLE IF EXISTS _backup_p6b2b1c_brand_compliance_settings;
DROP TABLE IF EXISTS _backup_p6b2b1c_metadata;

-- 3. Maintenance: Reclaim space and update planner statistics
-- NOTE ON VACUUM FULL vs VACUUM (ANALYZE):
-- VACUUM (ANALYZE) is intentionally used here instead of VACUUM FULL, and
-- run as standalone top-level statements (never inside a BEGIN/COMMIT
-- block -- PostgreSQL rejects VACUUM inside a transaction block outright).
-- - VACUUM (ANALYZE) frees dead tuples for in-place page reuse and updates
--   statistics WITHOUT acquiring an AccessExclusiveLock, keeping the app
--   online and fully queryable.
-- - VACUUM FULL creates an exclusive table lock blocking all concurrent
--   SELECT/UPDATE queries, which causes severe application downtime on
--   1.34M rows. Never used here.
VACUUM (ANALYZE) products;
VACUUM (ANALYZE) team_brands;
VACUUM (ANALYZE) exchange_rates;
VACUUM (ANALYZE) brand_compliance_settings;

DO $$
BEGIN
    RAISE NOTICE 'Migration 017 backup cleanup (snapshot ''%'') and VACUUM ANALYZE completed successfully.',
        current_setting('cleanup_p6b2b1c.target_snapshot_id');
END $$;
