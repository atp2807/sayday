CREATE SCHEMA IF NOT EXISTS call;

CREATE TABLE IF NOT EXISTS call.ring_slot (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    learner_id   uuid NOT NULL REFERENCES account.learner(id),
    days_of_week smallint NOT NULL,
    local_time   time NOT NULL,
    tz_name      varchar(50) NOT NULL DEFAULT 'Asia/Seoul',
    active_yn    boolean NOT NULL DEFAULT true,
    next_fire_ts timestamptz,
    created_ts   timestamptz NOT NULL DEFAULT now(),
    updated_ts   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ring_slot_next_fire_ts
    ON call.ring_slot (next_fire_ts) WHERE active_yn;
DROP TRIGGER IF EXISTS trg_ring_slot_updated ON call.ring_slot;
CREATE TRIGGER trg_ring_slot_updated BEFORE UPDATE ON call.ring_slot
    FOR EACH ROW EXECUTE FUNCTION set_updated_ts();

CREATE TABLE IF NOT EXISTS call.ring (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    learner_id     uuid NOT NULL REFERENCES account.learner(id),
    ring_slot_id   uuid REFERENCES call.ring_slot(id),
    status_cd      varchar(20) NOT NULL DEFAULT 'SCHEDULED',
    drill_plan     jsonb,
    room_grant_ref varchar(200),
    scheduled_ts   timestamptz NOT NULL,
    started_ts     timestamptz,
    ended_ts       timestamptz,
    created_ts     timestamptz NOT NULL DEFAULT now(),
    updated_ts     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ring_learner_id_created_ts
    ON call.ring (learner_id, created_ts);
DROP TRIGGER IF EXISTS trg_ring_updated ON call.ring;
CREATE TRIGGER trg_ring_updated BEFORE UPDATE ON call.ring
    FOR EACH ROW EXECUTE FUNCTION set_updated_ts();

CREATE TABLE IF NOT EXISTS call.utterance (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ring_id            uuid NOT NULL REFERENCES call.ring(id),
    learner_id         uuid NOT NULL REFERENCES account.learner(id),
    seq                integer NOT NULL,
    speaker_cd         varchar(10) NOT NULL,
    source_cd          varchar(10) NOT NULL DEFAULT 'VOICE',
    text               text NOT NULL,
    target_pattern_key varchar(100),
    verdict_cd         varchar(20),
    response_ms        integer,
    created_ts         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_utterance_ring_id_seq UNIQUE (ring_id, seq)
);

CREATE TABLE IF NOT EXISTS call.correction (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ring_id        uuid NOT NULL REFERENCES call.ring(id),
    learner_id     uuid NOT NULL REFERENCES account.learner(id),
    utterance_id   uuid REFERENCES call.utterance(id),
    severity_cd    varchar(10) NOT NULL,
    original_text  text NOT NULL,
    corrected_text text NOT NULL,
    note           text,
    created_ts     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS call.ring_report (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ring_id    uuid NOT NULL UNIQUE REFERENCES call.ring(id),
    learner_id uuid NOT NULL REFERENCES account.learner(id),
    summary    text NOT NULL,
    metrics    jsonb,
    created_ts timestamptz NOT NULL DEFAULT now()
);

DO $$ BEGIN
    ALTER TABLE learning.recall_entry
        ADD CONSTRAINT fk_recall_entry_ring_id_ring
        FOREIGN KEY (ring_id) REFERENCES call.ring(id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
