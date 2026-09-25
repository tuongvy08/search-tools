-- Inventory notes, after 029 stock and 030 admin RBAC. Stop web/workers first.
-- Existing partial/incompatible columns are rejected, not silently repaired.
BEGIN;
LOCK TABLE stock_items IN ACCESS EXCLUSIVE MODE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid = 'stock_items'::regclass
          AND attname = 'stock_note' AND NOT attisdropped
    ) THEN
        ALTER TABLE stock_items
            ADD COLUMN stock_note TEXT NOT NULL DEFAULT ''
            CONSTRAINT stock_items_stock_note_length_check
                CHECK (length(stock_note) <= 2000);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = 'stock_items'::regclass
          AND a.attname = 'stock_note' AND NOT a.attisdropped
          AND a.atttypid = 'text'::regtype AND a.atttypmod = -1
          AND a.attnotnull AND a.attgenerated = '' AND a.attidentity = ''
          AND pg_get_expr(d.adbin, d.adrelid) = '''''::text'
    ) THEN
        RAISE EXCEPTION 'Migration 031: incompatible stock_note type, nullability or default; inspect schema before retrying.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'stock_items'::regclass
          AND conname = 'stock_items_stock_note_length_check'
          AND contype = 'c' AND convalidated AND NOT connoinherit
          AND pg_get_expr(conbin, conrelid) = '(length(stock_note) <= 2000)'
    ) THEN
        RAISE EXCEPTION 'Migration 031: missing or incompatible stock_note length constraint; inspect schema before retrying.';
    END IF;
END $$;
COMMIT;
