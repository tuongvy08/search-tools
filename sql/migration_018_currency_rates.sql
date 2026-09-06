-- Migration 018: Central Currency Rates (Phase 6B2B2)
--
-- Business goal: Product -> Canonical Brand (brand_master.currency_code) ->
-- Currency -> Current Rate. Admin updates a currency's rate exactly once;
-- every brand using that currency picks up the new rate on its next read.
--
-- Safety and scope:
-- - Purely additive and idempotent. Safe to run repeatedly.
-- - Does NOT touch, backfill from, or drop the legacy per-brand
--   `exchange_rates` table (migration_005/017). That table is left exactly
--   as migration_017 populated it so a rollback to pre-6B2B2 code can keep
--   reading it. No runtime code from this phase reads `exchange_rates`.
-- - Does NOT touch `products`, `regulatory_rules`, `import_jobs`, or any
--   backup tables from migration_017.
-- - No token/cookie/secret/OAuth data is stored in the audit table below;
--   only currency, old/new rate, an optional actor user id, and a source
--   label.
--
-- Depends on migration_017_brand_master.sql (brand_master.currency_code).
-- Requires `app_users` (for the actor FK) which predates this phase.
--
-- Atomicity (Phase 6B2B2-Fix1): wrapped in one explicit BEGIN/COMMIT, same
-- as migration_017. Audit finding: this file has no `CREATE TEMP TABLE`, so
-- it never hits migration_017's exact "relation does not exist" failure --
-- but it shares the same underlying defect class. Its own Section 5
-- fail-closed preflight (`RAISE EXCEPTION` if currency_rates doesn't end up
-- with exactly 5 rows / VND <> 1) runs AFTER the Section 4 INSERTs. Under
-- plain `psql -v ON_ERROR_STOP=1 -f` autocommit (this file's documented
-- production invocation), each statement commits individually the instant
-- it runs, so by the time Section 5 could raise, Section 4's inserts are
-- already permanently committed -- the "fail closed" check could not
-- actually roll anything back, only report after the fact. Explicit
-- BEGIN/COMMIT closes that gap for free: no business logic, rates, or
-- values changed. Audited for CREATE INDEX CONCURRENTLY / VACUUM / other
-- transaction-incompatible statements -- none present, safe to wrap.

BEGIN;

-- ============================================================================
-- 1. DDL: currency_rates (single runtime source of truth for rates)
-- ============================================================================

CREATE TABLE IF NOT EXISTS currency_rates (
    currency_code TEXT PRIMARY KEY,
    rate_vnd      NUMERIC NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by    INTEGER NULL,
    update_source TEXT NOT NULL DEFAULT 'SYSTEM'
);

-- Defensive: guarantee these columns exist even if `currency_rates` was
-- somehow pre-created with an older/partial shape (mirrors the same
-- defensive pattern migration_017 uses for `exchange_rates.updated_at`).
ALTER TABLE currency_rates ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE currency_rates ADD COLUMN IF NOT EXISTS updated_by INTEGER NULL;
ALTER TABLE currency_rates ADD COLUMN IF NOT EXISTS update_source TEXT NOT NULL DEFAULT 'SYSTEM';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        WHERE c.contype = 'f'
          AND c.conrelid = 'currency_rates'::regclass
          AND c.confrelid = 'app_users'::regclass
          AND c.conkey = ARRAY[(
              SELECT attnum FROM pg_attribute
              WHERE attrelid = 'currency_rates'::regclass
                AND attname = 'updated_by'
                AND NOT attisdropped
          )]::smallint[]
          AND c.confkey = ARRAY[(
              SELECT attnum FROM pg_attribute
              WHERE attrelid = 'app_users'::regclass
                AND attname = 'id'
                AND NOT attisdropped
          )]::smallint[]
    ) THEN
        ALTER TABLE currency_rates
            ADD CONSTRAINT fk_currency_rates_updated_by
            FOREIGN KEY (updated_by) REFERENCES app_users (id) ON DELETE SET NULL NOT VALID;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_currency_rates_updated_by'
          AND conrelid = 'currency_rates'::regclass
          AND NOT convalidated
    ) THEN
        ALTER TABLE currency_rates VALIDATE CONSTRAINT fk_currency_rates_updated_by;
    END IF;
END $$;

-- Only the 5 currencies the business actually prices in are allowed. Any
-- other code (brand_master.currency_code included) is rejected upstream by
-- its own CHECK constraint (migration_017); this one guards the rates side.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_currency_rates_code'
    ) THEN
        ALTER TABLE currency_rates
            ADD CONSTRAINT chk_currency_rates_code
            CHECK (currency_code IN ('VND', 'AUD', 'USD', 'EUR', 'GBP')) NOT VALID;
    END IF;
END $$;

ALTER TABLE currency_rates VALIDATE CONSTRAINT chk_currency_rates_code;

-- Rate must always be a strictly positive, finite decimal. NUMERIC only —
-- never float — to avoid binary floating point drift in a value that
-- multiplies directly into every quoted price.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_currency_rates_rate_positive'
    ) THEN
        ALTER TABLE currency_rates
            ADD CONSTRAINT chk_currency_rates_rate_positive
            CHECK (rate_vnd > 0) NOT VALID;
    END IF;
END $$;

ALTER TABLE currency_rates VALIDATE CONSTRAINT chk_currency_rates_rate_positive;

-- VND is definitionally the base unit: it must always be exactly 1. No
-- other currency may ever be 1 or less is not enforced here (a genuinely
-- weak foreign currency could theoretically approach but never be forced to
-- differ from 1 by this table alone) — but VND itself is pinned exactly.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_currency_rates_vnd_is_one'
    ) THEN
        ALTER TABLE currency_rates
            ADD CONSTRAINT chk_currency_rates_vnd_is_one
            CHECK (currency_code <> 'VND' OR rate_vnd = 1) NOT VALID;
    END IF;
END $$;

ALTER TABLE currency_rates VALIDATE CONSTRAINT chk_currency_rates_vnd_is_one;

CREATE INDEX IF NOT EXISTS idx_currency_rates_updated_at ON currency_rates (updated_at);

-- ============================================================================
-- 2. DDL: currency_rate_history (minimal audit trail)
-- ============================================================================
-- No token, cookie, secret, or OAuth data. Only currency + old/new rate +
-- actor + timestamp + a free-text source label (e.g. 'ADMIN_UI', 'MIGRATION_018_SEED').

CREATE TABLE IF NOT EXISTS currency_rate_history (
    id            BIGSERIAL PRIMARY KEY,
    currency_code TEXT NOT NULL,
    old_rate      NUMERIC NULL,
    new_rate      NUMERIC NOT NULL,
    actor_user_id INTEGER NULL REFERENCES app_users (id) ON DELETE SET NULL,
    source        TEXT NOT NULL DEFAULT 'SYSTEM',
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_currency_rate_history_currency_code
    ON currency_rate_history (currency_code, changed_at DESC);

-- ============================================================================
-- 3. DDL: brand_currency_history (minimal audit trail for brand -> currency
--    remapping done from the redesigned admin UI; brand_master.currency_code
--    itself already exists from migration_017, this only adds an audit log
--    of changes to it).
-- ============================================================================

CREATE TABLE IF NOT EXISTS brand_currency_history (
    id                BIGSERIAL PRIMARY KEY,
    brand_id          INTEGER NOT NULL REFERENCES brand_master (id) ON DELETE CASCADE,
    old_currency_code TEXT NULL,
    new_currency_code TEXT NOT NULL,
    actor_user_id     INTEGER NULL REFERENCES app_users (id) ON DELETE SET NULL,
    changed_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brand_currency_history_brand_id
    ON brand_currency_history (brand_id, changed_at DESC);

-- ============================================================================
-- 4. Idempotent seed: exactly the 5 approved currencies at the approved
--    workbook rates. Re-running this file must NEVER clobber a rate an
--    admin has since changed through the UI -- ON CONFLICT DO NOTHING, not
--    DO UPDATE. History rows are inserted only for genuinely-new currencies
--    (first seed), not on every re-run.
-- ============================================================================

INSERT INTO currency_rates (currency_code, rate_vnd, update_source)
SELECT v.currency_code, v.rate_vnd, 'MIGRATION_018_SEED'
FROM (
    VALUES
        ('VND', 1::numeric),
        ('AUD', 17200::numeric),
        ('USD', 26500::numeric),
        ('EUR', 31500::numeric),
        ('GBP', 35500::numeric)
) AS v(currency_code, rate_vnd)
ON CONFLICT (currency_code) DO NOTHING;

-- Audit exactly one seed row per currency that did not already have a
-- currency_rate_history entry (keeps this file safe to re-run without
-- duplicating history on every deploy).
INSERT INTO currency_rate_history (currency_code, old_rate, new_rate, source)
SELECT cr.currency_code, NULL, cr.rate_vnd, 'MIGRATION_018_SEED'
FROM currency_rates cr
WHERE cr.update_source = 'MIGRATION_018_SEED'
  AND NOT EXISTS (
      SELECT 1 FROM currency_rate_history h WHERE h.currency_code = cr.currency_code
  );

-- ============================================================================
-- 5. Preflight/verification (fail closed, mirrors migration_017's style)
-- ============================================================================

DO $$
DECLARE
    v_count INT;
    v_bad_vnd INT;
BEGIN
    SELECT COUNT(*) INTO v_count FROM currency_rates WHERE currency_code IN ('VND','AUD','USD','EUR','GBP');
    IF v_count <> 5 THEN
        RAISE EXCEPTION 'Migration 018 preflight failed: expected 5 currency_rates rows (VND/AUD/USD/EUR/GBP), got %', v_count;
    END IF;

    SELECT COUNT(*) INTO v_bad_vnd FROM currency_rates WHERE currency_code = 'VND' AND rate_vnd <> 1;
    IF v_bad_vnd > 0 THEN
        RAISE EXCEPTION 'Migration 018 preflight failed: VND rate must be exactly 1';
    END IF;
END $$;

COMMIT;
