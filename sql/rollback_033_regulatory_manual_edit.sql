-- LOCAL/test only. Stop web + worker. Refuse removal once feature data exists.
BEGIN;
SELECT pg_advisory_xact_lock(62402601);
LOCK TABLE regulatory_rules IN ACCESS EXCLUSIVE MODE;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM regulatory_rules WHERE manual_protected OR revision<>1)
       OR EXISTS (SELECT 1 FROM regulatory_rule_manual_keys)
       OR EXISTS (SELECT 1 FROM regulatory_rule_manual_events)
       OR EXISTS (SELECT 1 FROM regulatory_import_jobs WHERE preview->>'contract_version'='2')
       OR EXISTS (SELECT 1 FROM regulatory_import_events WHERE detail->>'contract_version'='2') THEN
        RAISE EXCEPTION 'Không thể rollback: đã có dữ liệu/bảo vệ v2. Giữ schema và dùng forward fix hoặc backup đã duyệt.';
    END IF;
END $$;
DROP TRIGGER zz_regulatory_rule_revision ON regulatory_rules;
DROP FUNCTION update_regulatory_rule_revision();
DROP TABLE regulatory_rule_manual_events;
DROP TABLE regulatory_rule_manual_keys;
ALTER TABLE regulatory_rules DROP COLUMN revision, DROP COLUMN manual_protected;
COMMIT;
