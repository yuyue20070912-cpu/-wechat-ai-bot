-- Additive only; execute with learning-migrate, never during HTTP requests.
CREATE TABLE IF NOT EXISTS learning_voice_sessions (
    user_id BIGINT NOT NULL REFERENCES users(id),
    session_id UUID NOT NULL,
    answers JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(user_id,session_id),
    CHECK(jsonb_typeof(answers)='array' AND jsonb_array_length(answers)<=7)
);
CREATE INDEX IF NOT EXISTS idx_learning_voice_history ON learning_voice_sessions(user_id,created_at DESC);
CREATE TABLE IF NOT EXISTS learning_voice_budget (
    usage_day DATE NOT NULL,
    scope TEXT NOT NULL,
    seconds INTEGER NOT NULL CHECK(seconds>=0),
    PRIMARY KEY(usage_day,scope)
);
