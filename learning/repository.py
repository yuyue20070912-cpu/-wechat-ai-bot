"""All queries are scoped to the authenticated user, never a client user ID."""


def serialize(row):
    return dict(exam_type=row[0], target_score=float(row[1]), current_level=row[2],
                created_at=row[3].isoformat(), updated_at=row[4].isoformat())


def list_profiles(connection, user_id):
    with connection() as conn:
        rows = conn.execute('''SELECT exam_type,target_score,current_level,created_at,updated_at
            FROM learning_profiles WHERE user_id=%s ORDER BY exam_type''', (user_id,)).fetchall()
    return [serialize(row) for row in rows]


def save_profile(connection, user_id, exam, score, level):
    with connection() as conn:
        row = conn.execute('''INSERT INTO learning_profiles
            (user_id,exam_type,target_score,current_level) VALUES (%s,%s,%s,%s)
            ON CONFLICT (user_id,exam_type) DO UPDATE SET
                target_score=EXCLUDED.target_score,current_level=EXCLUDED.current_level,
                updated_at=NOW()
            RETURNING exam_type,target_score,current_level,created_at,updated_at''',
            (user_id, exam, score, level)).fetchone()
    return serialize(row)
