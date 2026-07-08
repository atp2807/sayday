CREATE SCHEMA IF NOT EXISTS billing;

CREATE TABLE IF NOT EXISTS billing.plan (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_key   varchar(50) NOT NULL UNIQUE,
    name       varchar(100) NOT NULL,
    price_amt  integer NOT NULL,
    period_cd  varchar(20) NOT NULL DEFAULT 'MONTHLY',
    active_yn  boolean NOT NULL DEFAULT true,
    created_ts timestamptz NOT NULL DEFAULT now(),
    updated_ts timestamptz NOT NULL DEFAULT now()
);
DROP TRIGGER IF EXISTS trg_plan_updated ON billing.plan;
CREATE TRIGGER trg_plan_updated BEFORE UPDATE ON billing.plan
    FOR EACH ROW EXECUTE FUNCTION set_updated_ts();

CREATE TABLE IF NOT EXISTS billing.subscription (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    learner_id            uuid NOT NULL REFERENCES account.learner(id),
    plan_id               uuid NOT NULL REFERENCES billing.plan(id),
    status_cd             varchar(20) NOT NULL DEFAULT 'TRIAL',
    pg_ref                varchar(200),
    started_ts            timestamptz,
    current_period_end_ts timestamptz,
    created_ts            timestamptz NOT NULL DEFAULT now(),
    updated_ts            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_subscription_learner_id ON billing.subscription (learner_id);
CREATE INDEX IF NOT EXISTS ix_subscription_pg_ref ON billing.subscription (pg_ref);
DROP TRIGGER IF EXISTS trg_subscription_updated ON billing.subscription;
CREATE TRIGGER trg_subscription_updated BEFORE UPDATE ON billing.subscription
    FOR EACH ROW EXECUTE FUNCTION set_updated_ts();

CREATE TABLE IF NOT EXISTS billing.payment (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    learner_id      uuid NOT NULL REFERENCES account.learner(id),
    subscription_id uuid REFERENCES billing.subscription(id),
    amount_amt      integer NOT NULL,
    status_cd       varchar(20) NOT NULL,
    pg_tx_ref       varchar(200) UNIQUE,
    paid_ts         timestamptz,
    created_ts      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_payment_learner_id ON billing.payment (learner_id);
