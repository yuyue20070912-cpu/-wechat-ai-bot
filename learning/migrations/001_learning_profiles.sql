-- Additive and repeatable. Apply with: flask --app bot learning-migrate
CREATE TABLE IF NOT EXISTS learning_profiles (
    user_id BIGINT NOT NULL REFERENCES users(id),
    exam_type TEXT NOT NULL CHECK (exam_type IN ('ielts', 'cet4', 'cet6')),
    target_score NUMERIC(4,1) NOT NULL,
    current_level TEXT NOT NULL CHECK (current_level IN
        ('not_assessed', 'beginner', 'intermediate', 'advanced')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, exam_type),
    CHECK ((exam_type = 'ielts' AND target_score BETWEEN 0.5 AND 9
            AND MOD(target_score, 0.5) = 0)
        OR (exam_type IN ('cet4', 'cet6') AND target_score BETWEEN 1 AND 710
            AND MOD(target_score, 1) = 0))
);
