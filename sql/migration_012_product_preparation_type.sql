-- Add optional product preparation classification.
-- Nullable by design: existing product rows are not backfilled or inferred.
--
-- products has ~1.34M rows. Adding a CHECK constraint the normal way
-- validates every existing row while holding ACCESS EXCLUSIVE, blocking
-- reads/writes for the scan duration. Instead: add NOT VALID (fast,
-- metadata-only lock), then VALIDATE CONSTRAINT separately (SHARE UPDATE
-- EXCLUSIVE, does not block concurrent SELECT/INSERT/UPDATE/DELETE).
-- VALIDATE CONSTRAINT is a no-op if the constraint is already valid, so
-- this stays idempotent and safe to re-run on a local DB that already
-- has the constraint validated.

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS preparation_type TEXT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'products_preparation_type_check'
          AND conrelid = 'products'::regclass
    ) THEN
        ALTER TABLE products
            ADD CONSTRAINT products_preparation_type_check
            CHECK (
                preparation_type IS NULL
                OR preparation_type IN ('NEAT', 'SOLUTION', 'MIXTURE', 'OTHER')
            ) NOT VALID;
    END IF;
END $$;

ALTER TABLE products VALIDATE CONSTRAINT products_preparation_type_check;
