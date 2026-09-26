-- Phase 6D6. Additive manual snapshots and durable idempotency receipts.
-- Apply after 031 with web/workers stopped. Never rewrite historical rows.
BEGIN;
LOCK TABLE stock_snapshots IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE definition TEXT;
BEGIN
    SELECT pg_get_constraintdef(oid) INTO definition FROM pg_constraint
    WHERE conrelid='stock_snapshots'::regclass AND conname='stock_snapshots_source_kind_check';
    IF definition IS NULL OR definition NOT IN (
        'CHECK ((source_kind = ANY (ARRAY[''IMPORT''::text, ''RESTORE''::text])))',
        'CHECK ((source_kind = ANY (ARRAY[''IMPORT''::text, ''RESTORE''::text, ''MANUAL''::text])))'
    ) THEN
        RAISE EXCEPTION 'Migration 032: unexpected stock_snapshots source_kind constraint.';
    END IF;
    ALTER TABLE stock_snapshots DROP CONSTRAINT stock_snapshots_source_kind_check;
    ALTER TABLE stock_snapshots ADD CONSTRAINT stock_snapshots_source_kind_check
        CHECK (source_kind IN ('IMPORT','RESTORE','MANUAL'));
END $$;

CREATE TABLE IF NOT EXISTS stock_manual_requests (
    actor_user_id INTEGER NOT NULL REFERENCES app_users(id),
    request_id UUID NOT NULL,
    actor_auth_version INTEGER NOT NULL CHECK (actor_auth_version > 0),
    payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256)=64),
    source_snapshot_id UUID REFERENCES stock_snapshots(id),
    result_snapshot_id UUID NOT NULL REFERENCES stock_snapshots(id),
    result_item_id BIGINT NOT NULL REFERENCES stock_items(id),
    result_revision BIGINT NOT NULL CHECK (result_revision >= 0),
    changed BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_user_id,request_id)
);
DO $$
DECLARE actual TEXT[]; expected TEXT[];
BEGIN
    IF (SELECT count(*) FROM pg_attribute WHERE attrelid='stock_manual_requests'::regclass
        AND attnum>0 AND NOT attisdropped) <> 10 OR EXISTS (
        SELECT 1 FROM (VALUES
            ('actor_user_id','integer',true,NULL), ('request_id','uuid',true,NULL),
            ('actor_auth_version','integer',true,NULL), ('payload_sha256','text',true,NULL),
            ('source_snapshot_id','uuid',false,NULL), ('result_snapshot_id','uuid',true,NULL),
            ('result_item_id','bigint',true,NULL), ('result_revision','bigint',true,NULL),
            ('changed','boolean',true,NULL), ('created_at','timestamp with time zone',true,'now()')
        ) AS e(name,type_name,required,default_expr)
        LEFT JOIN pg_attribute a ON a.attrelid='stock_manual_requests'::regclass AND a.attname=e.name AND NOT a.attisdropped
        LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE a.attname IS NULL OR format_type(a.atttypid,a.atttypmod)<>e.type_name
           OR a.attnotnull<>e.required OR a.attgenerated<>'' OR a.attidentity<>''
           OR pg_get_expr(d.adbin,d.adrelid) IS DISTINCT FROM e.default_expr
    ) THEN
        RAISE EXCEPTION 'Migration 032: incompatible stock_manual_requests columns.';
    END IF;
    SELECT array_agg(pg_get_constraintdef(oid) ORDER BY pg_get_constraintdef(oid)) INTO actual
    FROM pg_constraint WHERE conrelid='stock_manual_requests'::regclass AND convalidated;
    SELECT array_agg(definition ORDER BY definition) INTO expected FROM unnest(ARRAY[
        'PRIMARY KEY (actor_user_id, request_id)',
        'FOREIGN KEY (actor_user_id) REFERENCES app_users(id)',
        'FOREIGN KEY (source_snapshot_id) REFERENCES stock_snapshots(id)',
        'FOREIGN KEY (result_snapshot_id) REFERENCES stock_snapshots(id)',
        'FOREIGN KEY (result_item_id) REFERENCES stock_items(id)',
        'CHECK ((actor_auth_version > 0))', 'CHECK ((length(payload_sha256) = 64))',
        'CHECK ((result_revision >= 0))'
    ]) AS definition;
    IF actual IS DISTINCT FROM expected OR EXISTS (
        SELECT 1 FROM pg_constraint WHERE conrelid='stock_manual_requests'::regclass AND NOT convalidated
    ) THEN
        RAISE EXCEPTION 'Migration 032: incompatible stock_manual_requests constraints.';
    END IF;
END $$;
COMMIT;
