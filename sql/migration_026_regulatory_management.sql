-- Phase 6D1: configurable regulatory statuses, stable policy identity and
-- durable background rule imports. Additive/rerunnable; run after migration 025.
--
-- IMPORTANT: this migration deliberately removes only legacy TON_KHO rules.
-- It does not create inventory rows and does not touch catalog products,
-- import audit history or backups.

BEGIN;

CREATE TABLE IF NOT EXISTS regulatory_statuses (
    id              BIGSERIAL PRIMARY KEY,
    stable_key      TEXT NOT NULL UNIQUE,
    label           TEXT NOT NULL,
    priority        INTEGER NOT NULL UNIQUE CHECK (priority > 0),
    export_policy   TEXT NOT NULL CHECK (export_policy IN ('ALLOW', 'BLOCK')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_regulatory_statuses_label_norm
    ON regulatory_statuses (upper(btrim(label)));

-- The order is an explicit approved transition rule, not inferred from the
-- per-rule legacy priority column. These are seeds, never a runtime allowlist.
INSERT INTO regulatory_statuses (stable_key, label, priority, export_policy)
VALUES
    ('CAM_NHAP', 'CẤM NHẬP', 10, 'BLOCK'),
    ('PHU_LUC_II', 'Phụ lục II', 20, 'ALLOW'),
    ('PHU_LUC_III', 'Phụ lục III', 30, 'ALLOW'),
    ('DUOC_BAN', 'Được bán', 40, 'ALLOW'),
    ('CAN_GIAY_PHEP', 'Cần giấy phép', 50, 'ALLOW'),
    ('CHUA_XAC_DINH', 'Chưa xác định', 60, 'ALLOW')
ON CONFLICT (stable_key) DO UPDATE
SET export_policy = EXCLUDED.export_policy;

-- Preserve every nonblank manual value as a stable status. Only the legacy
-- Cấm nhập identity blocks; all other statuses follow the approved ALLOW rule.
WITH manual_labels AS (
    SELECT DISTINCT btrim(manual_compliance) AS label
    FROM products
    WHERE NULLIF(btrim(COALESCE(manual_compliance, '')), '') IS NOT NULL
), missing AS (
    SELECT m.label, row_number() OVER (ORDER BY upper(m.label), m.label) AS n
    FROM manual_labels m
    WHERE NOT EXISTS (
        SELECT 1 FROM regulatory_statuses s
        WHERE upper(btrim(s.label)) = upper(btrim(m.label))
    )
), base AS (
    SELECT COALESCE(max(priority), 0) AS max_priority FROM regulatory_statuses
)
INSERT INTO regulatory_statuses (stable_key, label, priority, export_policy)
SELECT 'MANUAL_' || upper(md5(m.label)), m.label, b.max_priority + m.n * 10,
       CASE WHEN upper(btrim(m.label)) = upper('Cấm nhập') THEN 'BLOCK' ELSE 'ALLOW' END
FROM missing m CROSS JOIN base b
ON CONFLICT DO NOTHING;

ALTER TABLE regulatory_rules
    ADD COLUMN IF NOT EXISTS status_id BIGINT REFERENCES regulatory_statuses(id);

UPDATE regulatory_rules r
SET status_id = s.id
FROM regulatory_statuses s
WHERE r.status_id IS NULL AND s.stable_key = r.rule_type
  AND r.rule_type <> 'TON_KHO';

DELETE FROM regulatory_rules WHERE rule_type = 'TON_KHO';

DO $$
DECLARE constraint_name TEXT;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'regulatory_rules'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%rule_type%';
    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE regulatory_rules DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;

DROP INDEX IF EXISTS uq_reg_rules_norm;
CREATE UNIQUE INDEX IF NOT EXISTS uq_reg_rules_status_field_value
    ON regulatory_rules (status_id, match_field, upper(btrim(match_value)));
CREATE INDEX IF NOT EXISTS idx_reg_rules_status_active
    ON regulatory_rules (status_id, is_active);

ALTER TABLE regulatory_rules ALTER COLUMN status_id SET NOT NULL;

CREATE OR REPLACE FUNCTION sync_regulatory_rule_status()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE selected regulatory_statuses%ROWTYPE;
BEGIN
    IF NEW.status_id IS NOT NULL THEN
        SELECT * INTO selected FROM regulatory_statuses WHERE id=NEW.status_id;
    ELSE
        SELECT * INTO selected FROM regulatory_statuses WHERE stable_key=NEW.rule_type;
    END IF;
    IF selected.id IS NULL THEN
        RAISE EXCEPTION 'Tình trạng quản lý của quy tắc không tồn tại.' USING ERRCODE='23514';
    END IF;
    NEW.status_id := selected.id;
    NEW.rule_type := selected.stable_key;
    NEW.rule_label := selected.label;
    NEW.priority := selected.priority;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_regulatory_rule_status ON regulatory_rules;
CREATE TRIGGER trg_regulatory_rule_status
BEFORE INSERT OR UPDATE OF status_id,rule_type ON regulatory_rules
FOR EACH ROW EXECUTE FUNCTION sync_regulatory_rule_status();

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS manual_compliance_status_id BIGINT
        REFERENCES regulatory_statuses(id);

-- Recovery snapshots created after this migration retain the same stable
-- status identity as the live product. Older snapshots remain NULL and are
-- deliberately refused by restore when they contain a nonblank legacy label.
DO $$
BEGIN
    IF to_regclass('product_deleted_rows') IS NOT NULL THEN
        ALTER TABLE product_deleted_rows
            ADD COLUMN IF NOT EXISTS manual_compliance_status_id BIGINT;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid='product_deleted_rows'::regclass
              AND conname='product_deleted_rows_manual_compliance_status_id_fkey'
        ) THEN
            ALTER TABLE product_deleted_rows
                ADD CONSTRAINT product_deleted_rows_manual_compliance_status_id_fkey
                FOREIGN KEY (manual_compliance_status_id) REFERENCES regulatory_statuses(id);
        END IF;
    END IF;
END $$;

UPDATE products p
SET manual_compliance_status_id = s.id
FROM regulatory_statuses s
WHERE p.manual_compliance_status_id IS NULL
  AND NULLIF(btrim(COALESCE(p.manual_compliance, '')), '') IS NOT NULL
  AND upper(btrim(s.label)) = upper(btrim(p.manual_compliance));

CREATE OR REPLACE FUNCTION sync_product_manual_regulatory_status()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE selected regulatory_statuses%ROWTYPE;
BEGIN
    -- A blank label is the legacy-compatible way all existing product writers
    -- clear an override. Clear the linked identity too instead of retaining the
    -- OLD row's status_id during UPDATE.
    IF NULLIF(btrim(COALESCE(NEW.manual_compliance, '')), '') IS NULL THEN
        NEW.manual_compliance := NULL;
        NEW.manual_compliance_status_id := NULL;
        NEW.manual_compliance_note := NULL;
        RETURN NEW;
    END IF;

    IF NEW.manual_compliance_status_id IS NOT NULL
       AND (TG_OP = 'INSERT'
            OR NEW.manual_compliance_status_id IS DISTINCT FROM OLD.manual_compliance_status_id) THEN
        SELECT * INTO selected FROM regulatory_statuses
        WHERE id = NEW.manual_compliance_status_id;
    ELSE
        SELECT * INTO selected FROM regulatory_statuses
        WHERE upper(btrim(label)) = upper(btrim(NEW.manual_compliance));
    END IF;

    IF selected.id IS NULL THEN
        RAISE EXCEPTION 'Tình trạng quản lý thủ công không tồn tại trong danh mục.'
            USING ERRCODE = '23514';
    END IF;
    NEW.manual_compliance_status_id := selected.id;
    NEW.manual_compliance := selected.label;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_products_manual_regulatory_status ON products;
CREATE TRIGGER trg_products_manual_regulatory_status
BEFORE INSERT OR UPDATE OF manual_compliance, manual_compliance_status_id ON products
FOR EACH ROW EXECUTE FUNCTION sync_product_manual_regulatory_status();

CREATE TABLE IF NOT EXISTS regulatory_import_jobs (
    id UUID PRIMARY KEY,
    submission_key UUID NOT NULL,
    actor TEXT NOT NULL,
    actor_user_id INTEGER NOT NULL REFERENCES app_users(id),
    actor_auth_version INTEGER NOT NULL,
    filename TEXT NOT NULL CHECK (length(filename) <= 180),
    file_size BIGINT NOT NULL,
    file_sha256 TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('upsert', 'replace_scoped')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','completed','failed','cancelled')),
    phase TEXT NOT NULL DEFAULT 'preview' CHECK (phase IN ('preview','apply')),
    preview_ready BOOLEAN NOT NULL DEFAULT FALSE,
    row_count INTEGER NOT NULL DEFAULT 0,
    processed_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    deleted_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    errors JSONB NOT NULL DEFAULT '[]',
    preview JSONB,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL,
    purged_at TIMESTAMPTZ,
    UNIQUE(actor_user_id, submission_key)
);
CREATE INDEX IF NOT EXISTS regulatory_import_queue
    ON regulatory_import_jobs(created_at) WHERE status='queued';
CREATE INDEX IF NOT EXISTS regulatory_import_expiry
    ON regulatory_import_jobs(expires_at) WHERE purged_at IS NULL;

CREATE TABLE IF NOT EXISTS regulatory_import_rows (
    job_id UUID NOT NULL REFERENCES regulatory_import_jobs(id) ON DELETE CASCADE,
    row_number INTEGER NOT NULL,
    data JSONB NOT NULL,
    PRIMARY KEY(job_id, row_number)
);
CREATE TABLE IF NOT EXISTS regulatory_import_events (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES regulatory_import_jobs(id) ON DELETE CASCADE,
    actor TEXT NOT NULL,
    event TEXT NOT NULL,
    detail JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS regulatory_status_events (
    id BIGSERIAL PRIMARY KEY,
    status_id BIGINT REFERENCES regulatory_statuses(id),
    actor TEXT NOT NULL,
    event TEXT NOT NULL,
    before_json JSONB,
    after_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
