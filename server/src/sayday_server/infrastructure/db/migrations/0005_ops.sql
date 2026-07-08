CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.op_log (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_cd   varchar(20) NOT NULL,
    action_cd  varchar(50) NOT NULL,
    detail     jsonb,
    created_ts timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_op_log_created_ts ON ops.op_log (created_ts);

CREATE TABLE IF NOT EXISTS ops.audit_log (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_cd   varchar(20) NOT NULL,
    subject_id uuid,
    change_cd  varchar(50) NOT NULL,
    detail     jsonb,
    created_ts timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_audit_log_subject_id ON ops.audit_log (subject_id);

CREATE TABLE IF NOT EXISTS ops.state_log (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_cd  varchar(20) NOT NULL,
    entity_id  uuid NOT NULL,
    from_cd    varchar(20),
    to_cd      varchar(20) NOT NULL,
    created_ts timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_state_log_entity ON ops.state_log (entity_cd, entity_id);
CREATE INDEX IF NOT EXISTS ix_state_log_created_ts ON ops.state_log (created_ts);
