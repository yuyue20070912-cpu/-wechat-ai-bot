"""Deterministic daily plans from profile, diagnostic and weak practice evidence."""
from psycopg.types.json import Jsonb

from .accounting import PracticeError
from .questions import SKILLS
from .preferences import local_day


def context(conn, user_id, exam):
    row = conn.execute('''SELECT target_score,current_level FROM learning_profiles
        WHERE user_id=%s AND exam_type=%s''', (user_id, exam)).fetchone()
    profile = dict(target_score=float(row[0]), current_level=row[1]) if row else dict(target_score=None, current_level='not_assessed')
    diag = conn.execute('''SELECT request_id,result FROM learning_diagnostics
        WHERE user_id=%s AND exam_type=%s ORDER BY created_at DESC,request_id DESC LIMIT 1''', (user_id, exam)).fetchone()
    averages = conn.execute('''SELECT skill,AVG((feedback->>'score')::numeric) FROM (
        SELECT skill,feedback FROM learning_attempts WHERE user_id=%s AND exam_type=%s AND status='completed'
        AND question_id NOT LIKE 'diagnostic-%%' AND question_id NOT LIKE 'mock-%%' ORDER BY created_at DESC LIMIT 30) recent
        GROUP BY skill ORDER BY AVG((feedback->>'score')::numeric),skill''', (user_id, exam)).fetchall()
    due = conn.execute('''SELECT COUNT(*) FROM learning_review_items
        WHERE user_id=%s AND exam_type=%s AND due_at<=NOW()''', (user_id, exam)).fetchone()[0]
    weak = list(diag[1]['weak_skills']) if diag else []
    weak = list(dict.fromkeys([skill for skill, avg in averages if avg < 70] + weak))
    fallback = (['vocabulary', 'reading', 'writing', 'listening'] if profile['current_level'] in ('beginner', 'not_assessed')
                else ['writing', 'speaking', 'reading', 'listening'] if exam == 'ielts'
                else ['writing', 'translation', 'reading', 'vocabulary'])
    ranked = list(dict.fromkeys(weak + fallback))
    high_goal = profile['target_score'] is not None and profile['target_score'] >= (7 if exam == 'ielts' else 550)
    return dict(profile=profile, diagnostic_id=str(diag[0]) if diag else None,
                weak_skills=weak, ranked_skills=ranked, due_reviews=due, high_goal=high_goal,
                algorithm='daily-v2', timezone=local_day(conn,user_id)[1])


def build_tasks(inputs):
    tasks = []
    if not inputs['diagnostic_id']:
        tasks.append(dict(id='diagnostic', title='完成六题轮换入门筛查', kind='diagnostic', minutes=6,
                          reason='用客观题结果补充自评，找到下一步。'))
    if inputs['due_reviews']:
        tasks.append(dict(id='review', title=f'复习到期错题（本次最多 {min(5, inputs["due_reviews"])} 道）',
                          kind='review', minutes=5, reason='先巩固已经学过的内容。'))
    count = 4 if inputs['high_goal'] else 3
    for skill in inputs['ranked_skills']:
        if len(tasks) >= count:
            break
        tasks.append(dict(id='practice-' + skill, title='专项练习：' + SKILLS[skill], kind='practice', skill=skill,
            minutes=8 if inputs['profile']['current_level'] == 'beginner' else 12 if inputs['high_goal'] else 10,
            reason='优先巩固诊断或 AI 练习中的薄弱项目。' if skill in inputs['weak_skills'] else '根据自评基础与考试目标安排。'))
    return tasks


def serialize(conn, user_id, exam, day):
    plan = conn.execute('''SELECT inputs FROM learning_plans WHERE user_id=%s AND exam_type=%s AND plan_date=%s''',
                        (user_id, exam, day)).fetchone()
    if plan is None:
        raise PracticeError('当日学习计划不存在', 404)
    rows = conn.execute('''SELECT task_id,content,completed,version,completed_at FROM learning_tasks
        WHERE user_id=%s AND exam_type=%s AND plan_date=%s ORDER BY task_id''', (user_id, exam, day)).fetchall()
    return dict(exam_type=exam, date=day.isoformat(), inputs=plan[0], tasks=[
        dict(**row[1], completed=row[2], version=row[3], completed_at=row[4].isoformat() if row[4] else None) for row in rows],
        notice=f'本计划按 {plan[0].get("timezone","UTC")} 日期保存；当天内容固定，更改时区不覆盖同日期旧任务。目标或筛查更新用于下一天。完成为自报状态。')


def today(connection, user_id, exam):
    with connection() as conn:
        day, timezone = local_day(conn,user_id)
        existing = conn.execute('''SELECT 1 FROM learning_plans WHERE user_id=%s AND exam_type=%s AND plan_date=%s''',
                                (user_id, exam, day)).fetchone()
        if not existing:
            inputs = context(conn, user_id, exam)
            inputs['timezone'] = timezone
            inserted = conn.execute('''INSERT INTO learning_plans(user_id,exam_type,plan_date,inputs)
                VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING plan_date''', (user_id, exam, day, Jsonb(inputs))).fetchone()
            if inserted:
                for task in build_tasks(inputs):
                    conn.execute('''INSERT INTO learning_tasks(user_id,exam_type,plan_date,task_id,content)
                        VALUES (%s,%s,%s,%s,%s)''', (user_id, exam, day, task['id'], Jsonb(task)))
        return serialize(conn, user_id, exam, day)


def get_today(connection, user_id, exam):
    with connection() as conn:
        day, timezone = local_day(conn,user_id)
        if not conn.execute('SELECT 1 FROM learning_plans WHERE user_id=%s AND exam_type=%s AND plan_date=%s',
                            (user_id, exam, day)).fetchone():
            return None
        return serialize(conn, user_id, exam, day)


def complete(connection, user_id, exam, day, task_id, completed, version):
    with connection() as conn:
        row = conn.execute('''SELECT completed,version FROM learning_tasks
            WHERE user_id=%s AND exam_type=%s AND plan_date=%s AND task_id=%s FOR UPDATE''',
            (user_id, exam, day, task_id)).fetchone()
        if row is None:
            raise PracticeError('学习任务不存在', 404)
        if row[0] == completed and row[1] in (version, version+1):
            # An identical network retry is idempotent; a contradictory stale update is not.
            return serialize(conn, user_id, exam, day)
        if row[1] != version:
            raise PracticeError('任务状态已改变，请刷新后重试', 409)
        conn.execute('''UPDATE learning_tasks SET completed=%s,version=version+1,
            completed_at=CASE WHEN %s THEN NOW() ELSE NULL END
            WHERE user_id=%s AND exam_type=%s AND plan_date=%s AND task_id=%s''',
            (completed, completed, user_id, exam, day, task_id))
        return serialize(conn, user_id, exam, day)
