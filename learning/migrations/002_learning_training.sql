-- Additive only. No changes to chat, membership or payment tables.
CREATE TABLE IF NOT EXISTS learning_attempts (
    user_id BIGINT NOT NULL REFERENCES users(id),
    request_id UUID NOT NULL,
    question_id TEXT NOT NULL,
    exam_type TEXT NOT NULL CHECK (exam_type IN ('ielts','cet4','cet6')),
    skill TEXT NOT NULL CHECK (skill IN ('speaking','writing','listening','reading','vocabulary','translation')),
    answer TEXT NOT NULL CHECK (length(answer) BETWEEN 1 AND 4000),
    status TEXT NOT NULL CHECK (status IN ('pending','completed','refunded')),
    usage_day DATE NOT NULL,
    subscription_id BIGINT REFERENCES subscriptions(id),
    feedback JSONB,
    model TEXT NOT NULL,
    input_tokens INTEGER CHECK (input_tokens >= 0),
    output_tokens INTEGER CHECK (output_tokens >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    PRIMARY KEY (user_id,request_id),
    CHECK (status <> 'completed' OR (feedback IS NOT NULL AND jsonb_typeof(feedback)='object'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_one_pending
    ON learning_attempts(user_id) WHERE status='pending';
CREATE INDEX IF NOT EXISTS idx_learning_history ON learning_attempts(user_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_learning_pending ON learning_attempts(created_at) WHERE status='pending';
CREATE TABLE IF NOT EXISTS learning_daily_budget (
    usage_day DATE PRIMARY KEY,
    calls INTEGER NOT NULL CHECK (calls >= 0)
);
CREATE TABLE IF NOT EXISTS learning_user_daily_usage (
    user_id BIGINT NOT NULL REFERENCES users(id),
    usage_day DATE NOT NULL,
    calls INTEGER NOT NULL CHECK (calls >= 0),
    PRIMARY KEY (user_id,usage_day)
);
