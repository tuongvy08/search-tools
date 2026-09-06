-- Migration 019: Dynamic Brand & Currency Master (Phase 6B2B3)
--
-- Makes the 35 brands / 5 currencies seeded by migrations 017/018 an
-- extensible master-data baseline rather than a closed allowlist.
--
-- Safety:
-- - Atomic and idempotent.
-- - Preserves every brand, alias, rate, history row, product, permission,
--   and migration_017 backup table.
-- - Does not rewrite products or create team_brands grants.

BEGIN;

-- New brands may exist before an admin assigns their pricing currency.
ALTER TABLE brand_master
    ALTER COLUMN currency_code DROP NOT NULL;

-- Replace migration_017's fixed five-code allowlist with the business rule:
-- NULL (unconfigured brand) or exactly three uppercase ASCII letters.
ALTER TABLE brand_master DROP CONSTRAINT IF EXISTS chk_brand_currency;
ALTER TABLE brand_master DROP CONSTRAINT IF EXISTS chk_brand_currency_code_format;
ALTER TABLE brand_master
    ADD CONSTRAINT chk_brand_currency_code_format
    CHECK (currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$') NOT VALID;
ALTER TABLE brand_master VALIDATE CONSTRAINT chk_brand_currency_code_format;

-- Currency master is dynamic; every code is an ISO-style three-letter ASCII
-- code. Existing positivity and VND=1 constraints remain authoritative.
ALTER TABLE currency_rates DROP CONSTRAINT IF EXISTS chk_currency_rates_code;
ALTER TABLE currency_rates DROP CONSTRAINT IF EXISTS chk_currency_rates_code_format;
ALTER TABLE currency_rates
    ADD CONSTRAINT chk_currency_rates_code_format
    CHECK (currency_code ~ '^[A-Z]{3}$') NOT VALID;
ALTER TABLE currency_rates VALIDATE CONSTRAINT chk_currency_rates_code_format;

-- An assigned brand must reference a configured currency. The FK also makes
-- deletion of a currency currently used by a brand fail closed (NO ACTION).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_brand_master_currency_rates'
          AND conrelid = 'brand_master'::regclass
    ) THEN
        ALTER TABLE brand_master
            ADD CONSTRAINT fk_brand_master_currency_rates
            FOREIGN KEY (currency_code) REFERENCES currency_rates(currency_code)
            NOT VALID;
    END IF;
END $$;
ALTER TABLE brand_master VALIDATE CONSTRAINT fk_brand_master_currency_rates;

-- Fail closed if the resulting master violates the dynamic invariants.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM currency_rates
        WHERE currency_code = 'VND' AND rate_vnd = 1
    ) THEN
        RAISE EXCEPTION 'Migration 019 preflight failed: VND=1 is missing';
    END IF;

    IF EXISTS (
        SELECT 1 FROM currency_rates
        WHERE currency_code !~ '^[A-Z]{3}$' OR rate_vnd <= 0
    ) THEN
        RAISE EXCEPTION 'Migration 019 preflight failed: invalid currency code/rate';
    END IF;

    IF EXISTS (
        SELECT 1 FROM brand_master bm
        LEFT JOIN currency_rates cr ON cr.currency_code = bm.currency_code
        WHERE bm.currency_code IS NOT NULL AND cr.currency_code IS NULL
    ) THEN
        RAISE EXCEPTION 'Migration 019 preflight failed: assigned brand currency has no rate row';
    END IF;
END $$;

COMMIT;
