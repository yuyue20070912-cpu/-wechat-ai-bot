"""Keep objective accuracy and AI reference scores in explicitly separate series."""
from .diagnostic_repository import serialize as serialize_diagnostic
from .plans import context
from .preferences import local_day


def overview(connection, user_id, exam):
    with connection() as conn:
        inputs = context(conn, user_id, exam)
        today, timezone = local_day(conn,user_id)
        diagnostics = conn.execute('''SELECT request_id,exam_type,test_version,result,created_at
            FROM learning_diagnostics WHERE user_id=%s AND exam_type=%s
            ORDER BY created_at DESC,request_id DESC LIMIT 12''', (user_id, exam)).fetchall()
        latest = serialize_diagnostic(diagnostics[0]) if diagnostics else None
        # Counts all events; there is no twenty-item history truncation in metrics.
        days = conn.execute('''WITH settings AS (SELECT %s::text AS tz), activity AS (
            SELECT (created_at AT TIME ZONE (SELECT tz FROM settings))::date AS day FROM learning_diagnostics WHERE user_id=%s AND exam_type=%s
            UNION SELECT (finished_at AT TIME ZONE (SELECT tz FROM settings))::date FROM learning_attempts
                WHERE user_id=%s AND exam_type=%s AND status='completed' AND question_id NOT LIKE 'diagnostic-%%' AND question_id NOT LIKE 'mock-%%' AND question_id NOT LIKE 'voice-%%'
            UNION SELECT (t.completed_at AT TIME ZONE (SELECT tz FROM settings))::date FROM learning_tasks t
                WHERE user_id=%s AND exam_type=%s AND completed
            UNION SELECT (e.reviewed_at AT TIME ZONE (SELECT tz FROM settings))::date FROM learning_review_events e
                JOIN learning_review_items r ON r.user_id=e.user_id AND r.item_key=e.item_key
                WHERE e.user_id=%s AND r.exam_type=%s
            UNION SELECT (finished_at AT TIME ZONE (SELECT tz FROM settings))::date FROM learning_mock_sessions
                WHERE user_id=%s AND exam_type=%s AND status<>'active')
            SELECT COUNT(*),COALESCE(jsonb_agg(day ORDER BY day) FILTER
                (WHERE day >= (NOW() AT TIME ZONE (SELECT tz FROM settings))::date-27),'[]') FROM activity''',
            (timezone,)+(user_id, exam)*5).fetchone()
        completed = conn.execute('''SELECT COUNT(*) FROM learning_tasks
            WHERE user_id=%s AND exam_type=%s AND completed''', (user_id, exam)).fetchone()[0]
        scores = conn.execute('''SELECT skill,COUNT(*),ROUND(AVG((feedback->>'score')::numeric),1),
            (array_agg((feedback->>'score')::numeric ORDER BY finished_at,request_id))[1],
            (array_agg((feedback->>'score')::numeric ORDER BY finished_at DESC,request_id DESC))[1]
            FROM learning_attempts WHERE user_id=%s AND exam_type=%s AND status='completed'
            AND question_id NOT LIKE 'diagnostic-%%' AND question_id NOT LIKE 'mock-%%' AND question_id NOT LIKE 'voice-%%' GROUP BY skill ORDER BY skill''', (user_id, exam)).fetchall()
        review = conn.execute('''SELECT COUNT(*),COUNT(*) FILTER (WHERE due_at<=NOW()),
            COUNT(*) FILTER (WHERE state='mastered') FROM learning_review_items WHERE user_id=%s AND exam_type=%s''',
            (user_id, exam)).fetchone()
    delta = None
    if len(diagnostics) >= 2 and diagnostics[0][2] == diagnostics[1][2]:
        delta = round(diagnostics[0][3]['accuracy']-diagnostics[1][3]['accuracy'], 1)
    return dict(exam_type=exam, timezone=timezone, today=today.isoformat(), study_days=days[0], active_dates=days[1], completed_tasks=completed,
        latest_diagnostic=latest, diagnostic_accuracy=latest['result']['accuracy'] if latest else None,
        diagnostic_delta=delta, diagnostic_history=[serialize_diagnostic(row) for row in reversed(diagnostics)],
        weak_skills=inputs['weak_skills'], review=dict(total=review[0], due=review[1], mastered=review[2]),
        practice_scores=[dict(skill=r[0], count=r[1], average=float(r[2]), first=float(r[3]), latest=float(r[4]),
                              change=float(r[4]-r[3]) if r[1] >= 2 else None) for r in scores],
        notice='正确率仅指最近一次六题文字诊断；AI 参考分单列。变化来自小样本，不代表官方成绩或完整英语能力。学习天数按用户设置时区的活动日统计，完成任务含手动勾选。')
