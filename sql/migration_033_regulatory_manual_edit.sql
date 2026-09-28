-- Regulatory manual editing v2. Additive, atomic, rerunnable. Local rehearsal first.
BEGIN;
SELECT pg_advisory_xact_lock(62402601);

-- Column existence is the atomic one-time backfill marker.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema=current_schema() AND table_name='regulatory_rules'
                     AND column_name='manual_protected') THEN
        ALTER TABLE regulatory_rules ADD COLUMN manual_protected BOOLEAN NOT NULL DEFAULT false;
        ALTER TABLE regulatory_rules ADD COLUMN revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0);
        UPDATE regulatory_rules SET manual_protected=true WHERE NOT is_active;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS regulatory_rule_manual_keys (
    id BIGSERIAL PRIMARY KEY,
    rule_id BIGINT NOT NULL REFERENCES regulatory_rules(id) ON DELETE RESTRICT,
    status_id BIGINT NOT NULL REFERENCES regulatory_statuses(id) ON DELETE RESTRICT,
    match_field TEXT NOT NULL CHECK (match_field IN ('cas','code','name')),
    match_value TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS regulatory_manual_key_identity
    ON regulatory_rule_manual_keys(status_id,match_field,upper(btrim(match_value)));
CREATE INDEX IF NOT EXISTS regulatory_manual_key_owner ON regulatory_rule_manual_keys(rule_id);

-- Only initial inactive backfill lacks keys; reruns must not add new history.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='regulatory_rules'::regclass
                   AND tgname='zz_regulatory_rule_revision') THEN
        INSERT INTO regulatory_rule_manual_keys(rule_id,status_id,match_field,match_value)
        SELECT id,status_id,match_field,match_value FROM regulatory_rules WHERE manual_protected
        ON CONFLICT DO NOTHING;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS regulatory_rule_manual_events (
    id BIGSERIAL PRIMARY KEY,
    rule_id BIGINT NOT NULL REFERENCES regulatory_rules(id) ON DELETE RESTRICT,
    actor_user_id INTEGER REFERENCES app_users(id) ON DELETE SET NULL,
    actor_id_snapshot INTEGER NOT NULL,
    actor_label TEXT NOT NULL,
    event TEXT NOT NULL CHECK (event IN ('created','edited','deactivated','restored')),
    reason TEXT NOT NULL DEFAULT '' CHECK (length(reason)<=1000),
    before_json JSONB NOT NULL,
    after_json JSONB NOT NULL,
    request_id UUID NOT NULL UNIQUE,
    request_digest TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (NOT (event='deactivated' OR
                (event='edited' AND before_json->>'status_id' IS DISTINCT FROM after_json->>'status_id'))
           OR length(btrim(reason))>0)
);
CREATE INDEX IF NOT EXISTS regulatory_manual_event_history
    ON regulatory_rule_manual_events(rule_id,created_at DESC,id DESC);

-- zz sorts after trg_regulatory_rule_status, so compare the final status labels.
CREATE OR REPLACE FUNCTION update_regulatory_rule_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.status_id,NEW.rule_type,NEW.rule_label,NEW.match_field,NEW.match_value,
        NEW.priority,NEW.is_active,NEW.note,NEW.manual_protected)
       IS DISTINCT FROM
       (OLD.status_id,OLD.rule_type,OLD.rule_label,OLD.match_field,OLD.match_value,
        OLD.priority,OLD.is_active,OLD.note,OLD.manual_protected) THEN
        NEW.revision := OLD.revision+1;
        NEW.updated_at := clock_timestamp();
    ELSE
        NEW.revision := OLD.revision;
        NEW.updated_at := OLD.updated_at;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS zz_regulatory_rule_revision ON regulatory_rules;
CREATE TRIGGER zz_regulatory_rule_revision BEFORE UPDATE ON regulatory_rules
FOR EACH ROW EXECUTE FUNCTION update_regulatory_rule_revision();
COMMIT;
