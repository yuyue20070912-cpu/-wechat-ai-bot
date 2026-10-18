from psycopg.types.json import Jsonb

from .accounting import PracticeError
from .diagnostics import TESTS, TEST_VERSION, score
from .reviews import insert


def serialize(row):
    return dict(request_id=str(row[0]), exam_type=row[1], version=row[2], result=row[3], created_at=row[4].isoformat())


def get(connection, user_id, request_id):
    with connection() as conn:
        row = conn.execute('''SELECT request_id,exam_type,test_version,result,created_at
            FROM learning_diagnostics WHERE user_id=%s AND request_id=%s''', (user_id, request_id)).fetchone()
    if row is None:
        raise PracticeError('诊断记录不存在', 404)
    return serialize(row)


def submit(connection, user_id, request_id, exam, version, answers):
    if version != TEST_VERSION:
        raise PracticeError('测试版本已变化，请重新加载题目', 409)
    result = score(exam, answers)
    with connection() as conn:
        profile = conn.execute('''SELECT target_score,current_level FROM learning_profiles
            WHERE user_id=%s AND exam_type=%s''', (user_id, exam)).fetchone()
        result['profile'] = dict(target_score=float(profile[0]), current_level=profile[1]) if profile else dict(target_score=None, current_level='not_assessed')
        row = conn.execute('''INSERT INTO learning_diagnostics(user_id,request_id,exam_type,test_version,answers,result)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING
            RETURNING request_id,exam_type,test_version,result,created_at''',
            (user_id, request_id, exam, version, Jsonb(answers), Jsonb(result))).fetchone()
        if row is None:
            prior = conn.execute('''SELECT exam_type,test_version,answers FROM learning_diagnostics
                WHERE user_id=%s AND request_id=%s''', (user_id, request_id)).fetchone()
            if prior != (exam, version, answers):
                raise PracticeError('请求编号已用于另一份诊断', 409)
            row = conn.execute('''SELECT request_id,exam_type,test_version,result,created_at FROM learning_diagnostics
                WHERE user_id=%s AND request_id=%s''', (user_id, request_id)).fetchone()
            return dict(diagnostic=serialize(row), replayed=True)
        for q, detail in zip(TESTS[exam], result['details']):
            if not detail['correct']:
                insert(conn, user_id, f'diagnostic:{request_id}:{q["id"]}', exam, q['skill'], 'diagnostic',
                    dict(title='入门诊断 · ' + q['id'], prompt=q['prompt'], material='', options=q['options'],
                         correct_index=q['correct_index'], answer=q['options'][answers[q['id']]],
                         reference=detail['reference'], explanation=detail['explanation'],
                         notice='本地客观题解析，不是 AI 解析或官方考试评分。'))
        return dict(diagnostic=serialize(row), replayed=False)
