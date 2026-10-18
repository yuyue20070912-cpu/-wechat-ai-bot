-- Additive only. Old application versions can coexist with these new tables.
CREATE TABLE IF NOT EXISTS learning_diagnostics (
    user_id BIGINT NOT NULL REFERENCES users(id),
    request_id UUID NOT NULL,
    exam_type TEXT NOT NULL CHECK (exam_type IN ('ielts','cet4','cet6')),
    test_version TEXT NOT NULL,
    answers JSONB NOT NULL CHECK (jsonb_typeof(answers)='object'),
    result JSONB NOT NULL CHECK (jsonb_typeof(result)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id,request_id)
);
CREATE INDEX IF NOT EXISTS idx_learning_diagnostics_recent
    ON learning_diagnostics(user_id,exam_type,created_at DESC);
CREATE TABLE IF NOT EXISTS learning_plans (
    user_id BIGINT NOT NULL REFERENCES users(id),
    exam_type TEXT NOT NULL CHECK (exam_type IN ('ielts','cet4','cet6')),
    plan_date DATE NOT NULL,
    inputs JSONB NOT NULL CHECK (jsonb_typeof(inputs)='object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id,exam_type,plan_date)
);
CREATE TABLE IF NOT EXISTS learning_tasks (
    user_id BIGINT NOT NULL,
    exam_type TEXT NOT NULL,
    plan_date DATE NOT NULL,
    task_id TEXT NOT NULL,
    content JSONB NOT NULL CHECK (jsonb_typeof(content)='object'),
    completed BOOLEAN NOT NULL DEFAULT FALSE,
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (user_id,exam_type,plan_date,task_id),
    FOREIGN KEY (user_id,exam_type,plan_date) REFERENCES learning_plans(user_id,exam_type,plan_date),
    CHECK (completed = (completed_at IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS learning_review_items (
    user_id BIGINT NOT NULL REFERENCES users(id),
    item_key TEXT NOT NULL,
    exam_type TEXT NOT NULL CHECK (exam_type IN ('ielts','cet4','cet6')),
    skill TEXT NOT NULL CHECK (skill IN ('speaking','writing','listening','reading','vocabulary','translation')),
    source_type TEXT NOT NULL CHECK (source_type IN ('diagnostic','ai_practice')),
    content JSONB NOT NULL CHECK (jsonb_typeof(content)='object'),
    due_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    interval_days INTEGER NOT NULL DEFAULT 0 CHECK (interval_days BETWEEN 0 AND 365),
    ease_factor NUMERIC(4,2) NOT NULL DEFAULT 2.5 CHECK (ease_factor BETWEEN 1.3 AND 3),
    repetitions INTEGER NOT NULL DEFAULT 0 CHECK (repetitions >= 0),
    state TEXT NOT NULL DEFAULT 'new' CHECK (state IN ('new','relearning','reviewing','mastered')),
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id,item_key)
);
CREATE INDEX IF NOT EXISTS idx_learning_review_due ON learning_review_items(user_id,exam_type,due_at);
CREATE TABLE IF NOT EXISTS learning_review_events (
    user_id BIGINT NOT NULL,
    request_id UUID NOT NULL,
    item_key TEXT NOT NULL,
    expected_version INTEGER NOT NULL CHECK (expected_version >= 0),
    answer TEXT NOT NULL CHECK (length(answer) BETWEEN 1 AND 4000),
    self_rating TEXT CHECK (self_rating IN ('again','hard','good','easy')),
    correct BOOLEAN,
    result JSONB NOT NULL CHECK (jsonb_typeof(result)='object'),
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id,request_id),
    FOREIGN KEY (user_id,item_key) REFERENCES learning_review_items(user_id,item_key)
);
