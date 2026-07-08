CREATE SCHEMA IF NOT EXISTS learning;

CREATE TABLE IF NOT EXISTS learning.pattern_card (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    learner_id       uuid NOT NULL REFERENCES account.learner(id),
    pattern_key      varchar(100) NOT NULL,
    status_cd        varchar(20) NOT NULL DEFAULT 'ACTIVE',
    fsrs_due_ts      timestamptz NOT NULL,
    fsrs_stability   double precision NOT NULL,
    fsrs_card        jsonb NOT NULL,
    recall_window_ms integer NOT NULL,
    created_ts       timestamptz NOT NULL DEFAULT now(),
    updated_ts       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_pattern_card_learner_id_pattern_key UNIQUE (learner_id, pattern_key)
);
CREATE INDEX IF NOT EXISTS ix_pattern_card_learner_id_fsrs_due_ts
    ON learning.pattern_card (learner_id, fsrs_due_ts);
DROP TRIGGER IF EXISTS trg_pattern_card_updated ON learning.pattern_card;
CREATE TRIGGER trg_pattern_card_updated BEFORE UPDATE ON learning.pattern_card
    FOR EACH ROW EXECUTE FUNCTION set_updated_ts();

CREATE TABLE IF NOT EXISTS learning.recall_entry (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    learner_id      uuid NOT NULL REFERENCES account.learner(id),
    pattern_card_id uuid NOT NULL REFERENCES learning.pattern_card(id),
    ring_id         uuid,
    verdict_cd      varchar(20) NOT NULL,
    response_ms     integer,
    rating_cd       varchar(20) NOT NULL,
    created_ts      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_recall_entry_pattern_card_id
    ON learning.recall_entry (pattern_card_id);
