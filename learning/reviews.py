"""Mistake snapshots and versioned, idempotent spaced repetition."""
from datetime import timedelta
from math import ceil

from psycopg.types.json import Jsonb

from .accounting import PracticeError
from .questions import QUESTIONS

COLUMNS = 'item_key,exam_type,skill,source_type,content,due_at,interval_days,ease_factor,repetitions,state,version'


def serialize(row):
    item = dict(zip(COLUMNS.split(','), row))
    item['content'] = {k: v for k, v in item['content'].items() if k != 'correct_index'}
    item['due_at'] = item['due_at'].isoformat()
    item['ease_factor'] = float(item['ease_factor'])
    return item


def insert(conn, user_id, key, exam, skill, source, content):
    return conn.execute('''INSERT INTO learning_review_items
        (user_id,item_key,exam_type,skill,source_type,content) VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (user_id,item_key) DO NOTHING RETURNING item_key''',
        (user_id, key, exam, skill, source, Jsonb(content))).fetchone() is not None


def capture_attempt(conn, user_id, request_id, manual=False):
    row = conn.execute('''SELECT question_id,exam_type,skill,answer,feedback FROM learning_attempts
        WHERE user_id=%s AND request_id=%s AND status='completed' ''', (user_id, request_id)).fetchone()
    if row is None or row[0] not in QUESTIONS:
        return False
    qid, exam, skill, answer, feedback = row
    if not manual and feedback['score'] >= 70:
        return False
    q = QUESTIONS[qid]
    return insert(conn, user_id, 'practice:' + str(request_id), exam, skill, 'ai_practice',
        dict(prompt=q['prompt'], material=q['material'], answer=answer, reference=feedback.get('reference_answer', q['reference']),
             explanation=feedback['summary'], ai_feedback=feedback, title=q['title'],
             notice='AI 练习参考分低于 70 或手动收藏，表示需要巩固，不等同客观判错；复习表现由你自评。'))


def sync(connection, user_id, exam):
    with connection() as conn:
        rows = conn.execute('''SELECT request_id FROM learning_attempts a WHERE user_id=%s
            AND exam_type=%s AND status='completed' AND question_id NOT LIKE 'diagnostic-%%' AND question_id NOT LIKE 'mock-%%' AND question_id NOT LIKE 'voice-%%'
            AND CASE WHEN jsonb_typeof(feedback->'score')='number' THEN (feedback->>'score')::numeric < 70 ELSE FALSE END
            AND NOT EXISTS (SELECT 1 FROM learning_review_items r WHERE r.user_id=a.user_id
                AND r.item_key='practice:' || a.request_id::text)
            ORDER BY created_at LIMIT 100''', (user_id, exam)).fetchall()
        return sum(capture_attempt(conn, user_id, row[0]) for row in rows)


def list_items(connection, user_id, exam, offset=0):
    with connection() as conn:
        rows = conn.execute(f'''SELECT {COLUMNS} FROM learning_review_items WHERE user_id=%s AND exam_type=%s
            ORDER BY due_at,item_key LIMIT 30 OFFSET %s''', (user_id, exam, offset)).fetchall()
        count = conn.execute('''SELECT COUNT(*),COUNT(*) FILTER (WHERE due_at<=NOW()) FROM learning_review_items
            WHERE user_id=%s AND exam_type=%s''', (user_id, exam)).fetchone()
        now = conn.execute('SELECT NOW()').fetchone()[0]
    return dict(items=[dict(**serialize(row), is_due=row[5] <= now) for row in rows], total=count[0], due=count[1], offset=offset)


def schedule(interval, ease, repetitions, rating):
    ease = float(ease)
    if rating == 'again':
        return 0, max(1.3, round(ease-.2, 2)), 0, 'relearning', timedelta(minutes=10)
    if rating == 'hard':
        days, ease = max(1, ceil(interval*1.2)), max(1.3, round(ease-.15, 2))
    elif rating == 'easy':
        days, ease = max(3, ceil(max(1, interval)*ease*1.3)), min(3, round(ease+.15, 2))
    else:
        days = 1 if repetitions == 0 else 3 if repetitions == 1 else ceil(max(1, interval)*ease)
    days = min(365, days)
    repetitions += 1
    state = 'mastered' if repetitions >= 3 and days >= 7 else 'reviewing'
    return days, ease, repetitions, state, timedelta(days=days)


def respond(connection, user_id, key, request_id, version, answer, self_rating):
    with connection() as conn:
        row = conn.execute(f'''SELECT {COLUMNS} FROM learning_review_items
            WHERE user_id=%s AND item_key=%s FOR UPDATE''', (user_id, key)).fetchone()
        if row is None:
            raise PracticeError('复习项目不存在', 404)
        prior = conn.execute('''SELECT item_key,expected_version,answer,self_rating,result FROM learning_review_events
            WHERE user_id=%s AND request_id=%s''', (user_id, request_id)).fetchone()
        if prior:
            if tuple(prior[:4]) != (key, version, answer, self_rating):
                raise PracticeError('请求编号已用于其他复习内容', 409)
            return dict(**prior[4], replayed=True)
        item = dict(zip(COLUMNS.split(','), row))
        if item['version'] != version:
            raise PracticeError('复习状态已更新，请刷新后重试', 409)
        now = conn.execute('SELECT NOW()').fetchone()[0]
        if item['due_at'] > now:
            raise PracticeError('尚未到下一次复习时间', 409)
        if item['source_type'] == 'diagnostic':
            if self_rating is not None or answer not in ('0', '1', '2'):
                raise PracticeError('请选择一个有效选项，客观题不接受自评分', 400)
            correct = int(answer) == item['content']['correct_index']
            rating = 'good' if correct else 'again'
        else:
            if self_rating not in ('again', 'hard', 'good', 'easy'):
                raise PracticeError('请自评本次表达复习的掌握程度', 400)
            correct, rating = None, self_rating
        days, ease, reps, state, delta = schedule(item['interval_days'], item['ease_factor'], item['repetitions'], rating)
        changed = conn.execute(f'''UPDATE learning_review_items SET due_at=%s,interval_days=%s,ease_factor=%s,
            repetitions=%s,state=%s,version=version+1 WHERE user_id=%s AND item_key=%s RETURNING {COLUMNS}''',
            (now+delta, days, ease, reps, state, user_id, key)).fetchone()
        result = dict(item=serialize(changed), correct=correct, rating=rating)
        # A request UUID is user-wide, including requests for different review items.
        inserted = conn.execute('''INSERT INTO learning_review_events
            (user_id,request_id,item_key,expected_version,answer,self_rating,correct,result)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING request_id''',
            (user_id, request_id, key, version, answer, self_rating, correct, Jsonb(result))).fetchone()
        if inserted is None:
            raise PracticeError('请求编号已被另一次复习使用，请刷新', 409)
        return dict(**result, replayed=False)
