-- Additive migration. No changes to chat, membership, payments or prior learning tables.
CREATE TABLE IF NOT EXISTS learning_preferences (
    user_id BIGINT PRIMARY KEY REFERENCES users(id),
    timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS learning_screening_rotation (
    user_id BIGINT NOT NULL REFERENCES users(id),
    exam_type TEXT NOT NULL CHECK (exam_type IN ('ielts','cet4','cet6')),
    next_index INTEGER NOT NULL CHECK (next_index >= 0),
    PRIMARY KEY(user_id,exam_type)
);
CREATE TABLE IF NOT EXISTS learning_mock_sessions (
    user_id BIGINT NOT NULL REFERENCES users(id),
    session_id UUID NOT NULL,
    template_id TEXT NOT NULL,
    exam_type TEXT NOT NULL CHECK (exam_type IN ('ielts','cet4','cet6')),
    section TEXT NOT NULL,
    snapshot JSONB NOT NULL CHECK (jsonb_typeof(snapshot)='object'),
    answers JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(answers)='object'),
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','submitted','expired')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deadline TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    report JSONB,
    PRIMARY KEY(user_id,session_id),
    CHECK (deadline > started_at),
    CHECK ((status='active' AND report IS NULL AND finished_at IS NULL)
        OR (status<>'active' AND report IS NOT NULL AND finished_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_mock_active ON learning_mock_sessions(user_id) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_learning_mock_history ON learning_mock_sessions(user_id,exam_type,started_at DESC);
CREATE TABLE IF NOT EXISTS learning_mock_writes (
    user_id BIGINT NOT NULL,
    request_id UUID NOT NULL,
    session_id UUID NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('save','submit')),
    expected_version INTEGER NOT NULL CHECK (expected_version >= 0),
    payload_hash CHAR(64) NOT NULL,
    saved_version INTEGER NOT NULL CHECK (saved_version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(user_id,request_id),
    FOREIGN KEY(user_id,session_id) REFERENCES learning_mock_sessions(user_id,session_id)
);
