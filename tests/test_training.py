import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

import bot
from learning import accounting, services
from learning.questions import QUESTIONS, SKILLS


from learning_fakes import FEEDBACK

def client(user):
    c = bot.app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = user
    return c


def csrf(c):
    return {'X-Learning-CSRF': c.get('/api/learning/profiles').json['csrf_token']}


def body(question='ielts-writing-1-v1', answer='I agree because teamwork helps us learn.', request_id=None):
    return dict(question_id=question, answer=answer, request_id=str(request_id or uuid4()))


def submit(c, payload=None, **kwargs):
    return c.post('/api/learning/attempts', json=payload or body(), headers=csrf(c), **kwargs)


def counts(user):
    with bot.db_connection() as conn:
        messages = conn.execute('SELECT COALESCE(SUM(used),0) FROM daily_message_usage WHERE user_id=%s', (user,)).fetchone()[0]
        calls = conn.execute('SELECT COALESCE(SUM(calls),0) FROM learning_user_daily_usage WHERE user_id=%s', (user,)).fetchone()[0]
    return messages, calls




@pytest.mark.parametrize('exam', ['ielts', 'cet4', 'cet6'])
@pytest.mark.parametrize('skill', list(SKILLS))
def test_all_exams_and_skills(user, ai, exam, skill):
    c = client(user)
    questions = c.get(f'/api/learning/questions?exam={exam}&skill={skill}').json['questions']
    assert len(questions) == 2 and all(q['original'] and 'reference' not in q for q in questions)
    response = submit(c, body(questions[0]['id']))
    assert response.status_code == 200
    assert response.json['feedback']['score'] == 72
    assert response.json['feedback']['text_only'] is True
    assert response.json['feedback']['reference_answer'] == QUESTIONS[questions[0]['id']]['reference']
    assert counts(user) == (1, 1)
    args = ai.calls[0]
    assert args['model'] == bot.MODEL and args['store'] is False
    assert args['max_output_tokens'] == 1600 and args['text']['format']['strict'] is True
    assert ai.options == [dict(timeout=45, max_retries=0)]
    assert json.loads(args['input'])['exam'] == exam
    history = c.get('/api/learning/practice').json
    assert history['usage']['message_remaining'] == 4 and history['usage']['calls_remaining'] == 9
    assert history['attempts'][0]['feedback'] == response.json['feedback']


@pytest.mark.parametrize('method,path', [('get', '/api/learning/questions'), ('get', '/api/learning/practice'),
    ('get', '/api/learning/attempts/' + str(uuid4())), ('post', '/api/learning/attempts')])
def test_training_requires_login(method, path):
    assert getattr(bot.app.test_client(), method)(path).status_code == 401


def test_csrf_and_input_validation_no_cost(user, ai):
    c = client(user)
    assert c.post('/api/learning/attempts', json=body()).status_code == 403
    assert c.post('/api/learning/attempts', json=body(), headers={**csrf(c), 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    for bad in [[], {}, {'question_id': 'bad'}, {**body(), 'user_id': 123},
                body(answer=''), body(answer='  '), body(answer='a' * 4001), body(answer=[]),
                {**body(), 'request_id': 'bad'}, {**body(), 'request_id': []}]:
        assert c.post('/api/learning/attempts', json=bad, headers=csrf(c)).status_code == 400
    assert submit(c, body('missing')).status_code == 404
    assert c.get('/api/learning/questions?exam=ielts&skill=unknown').status_code == 400
    assert counts(user) == (0, 0) and not ai.calls


def test_switch_and_config_fail_closed(user, ai, monkeypatch):
    c = client(user)
    monkeypatch.setenv('LEARNING_AI_ENABLED', '0')
    assert submit(c).status_code == 503
    assert c.get('/api/learning/practice').json['enabled'] is False
    monkeypatch.setenv('LEARNING_AI_ENABLED', '1')
    for bad in ['0', '-1', '1001', 'invalid']:
        monkeypatch.setenv('LEARNING_GLOBAL_DAILY_CALLS', bad)
        assert submit(c).status_code == 503
    assert counts(user) == (0, 0) and not ai.calls


def test_replay_and_payload_mismatch(user, ai):
    c = client(user); payload = body()
    first = submit(c, payload)
    second = submit(c, payload)
    assert first.status_code == second.status_code == 200
    assert first.json['feedback'] == second.json['feedback'] and second.json['replayed']
    assert submit(c, {**payload, 'answer': 'Changed answer'}).status_code == 409
    assert len(ai.calls) == 1 and counts(user) == (1, 1)


def test_history_isolation(user, ai):
    c = client(user); response = submit(c)
    with bot.db_connection() as conn:
        other = conn.execute("INSERT INTO users(phone,password_hash) VALUES ('13900000001','test') RETURNING id").fetchone()[0]
    second = client(other)
    assert second.get('/api/learning/practice').json['attempts'] == []
    assert second.get('/api/learning/attempts/' + response.json['request_id']).status_code == 404
    assert c.get('/api/learning/attempts/' + response.json['request_id']).json['attempt']['status'] == 'completed'


@pytest.mark.parametrize('failure', ['timeout', 'refusal', 'incomplete', 'json', 'score', 'empty', 'schema'])
def test_failures_refund_but_consume_call_budget(user, ai, failure):
    def create(**kwargs):
        if failure == 'timeout':
            raise TimeoutError('secret upstream error')
        value = dict(FEEDBACK)
        if failure == 'score': value['score'] = 101
        if failure == 'schema': value['strengths'] = []
        return NS(status='incomplete' if failure == 'incomplete' else 'completed',
                  output_text={'refusal': '', 'json': 'broken', 'empty': ''}.get(failure, json.dumps(value)))
    ai.client.responses.create = create
    c = client(user); payload = body()
    response = submit(c, payload)
    assert response.status_code == 502 and b'secret' not in response.data
    assert counts(user) == (0, 1)
    assert submit(c, payload).status_code == 409
    assert counts(user) == (0, 1)
    assert c.get('/api/learning/practice').json['attempts'][0]['status'] == 'refunded'


def test_daily_free_and_user_caps(user, ai, monkeypatch):
    c = client(user)
    monkeypatch.setenv('LEARNING_USER_DAILY_CALLS', '2')
    assert submit(c).status_code == submit(c).status_code == 200
    assert submit(c).status_code == 429 and len(ai.calls) == 2
    assert counts(user) == (2, 2)
    monkeypatch.setenv('LEARNING_USER_DAILY_CALLS', '10')
    for _ in range(3): assert submit(c).status_code == 200
    assert submit(c).status_code == 429
    assert counts(user) == (5, 5) and len(ai.calls) == 5
    # Shared allowance means an existing chat also cannot reserve a sixth message.
    assert bot.reserve_chat(user, str(uuid4())) == -1


def test_global_cap_across_users(user, ai, monkeypatch):
    monkeypatch.setenv('LEARNING_GLOBAL_DAILY_CALLS', '1')
    assert submit(client(user)).status_code == 200
    with bot.db_connection() as conn:
        other = conn.execute("INSERT INTO users(phone,password_hash) VALUES ('13900000002','test') RETURNING id").fetchone()[0]
    assert submit(client(other)).status_code == 429
    assert counts(other) == (0, 0) and len(ai.calls) == 1


def test_concurrent_duplicate_and_pending_requests(user, ai):
    started, release = Event(), Event()
    original = ai.client.responses.create
    def slow(**kwargs):
        started.set(); assert release.wait(5)
        return original(**kwargs)
    ai.client.responses.create = slow
    payload = body()
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(lambda: submit(client(user), payload))
        assert started.wait(5)
        duplicate = pool.submit(lambda: submit(client(user), payload))
        other = pool.submit(lambda: submit(client(user)))
        try:
            assert duplicate.result(timeout=5).status_code == 409
            assert other.result(timeout=5).status_code == 409
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200
    assert len(ai.calls) == 1 and counts(user) == (1, 1)


def test_paid_quota_refund_and_original_day(user, ai):
    c = client(user)
    with bot.db_connection() as conn:
        sub = conn.execute("""INSERT INTO subscriptions(user_id,plan,starts_at,ends_at,message_limit,payment_reference)
            VALUES (%s,'basic',NOW()-INTERVAL '1 day',NOW()+INTERVAL '1 day',2,'learning-paid') RETURNING id""", (user,)).fetchone()[0]
    assert submit(c).status_code == 200
    ai.client.responses.create = lambda **kwargs: (_ for _ in ()).throw(TimeoutError())
    assert submit(c).status_code == 502
    with bot.db_connection() as conn:
        assert conn.execute('SELECT used FROM subscriptions WHERE id=%s', (sub,)).fetchone()[0] == 1
    assert counts(user) == (0, 2)


def test_crash_recovery_original_date_and_idempotency(user):
    key = str(uuid4())
    accounting.reserve(bot.db_connection, user, key, QUESTIONS['ielts-writing-1-v1'], 'answer', bot.MODEL, (10, 200), 5)
    with bot.db_connection() as conn:
        conn.execute("UPDATE learning_attempts SET created_at=NOW()-INTERVAL '6 minutes',usage_day=usage_day-1")
        conn.execute('UPDATE daily_message_usage SET usage_day=usage_day-1')
    assert accounting.recover(bot.db_connection) == 1
    assert accounting.recover(bot.db_connection) == 0
    assert not accounting.finish(bot.db_connection, user, key)
    assert not accounting.finish(bot.db_connection, user, key, FEEDBACK)
    assert counts(user) == (0, 1)


def test_refund_outage_recovers(user, ai, monkeypatch):
    ai.client.responses.create = lambda **kwargs: (_ for _ in ()).throw(TimeoutError())
    with monkeypatch.context() as patch:
        patch.setattr(accounting, 'finish', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError()))
        assert submit(client(user)).status_code == 502
    assert counts(user) == (1, 1)
    with bot.db_connection() as conn:
        conn.execute("UPDATE learning_attempts SET created_at=NOW()-INTERVAL '6 minutes'")
    assert accounting.recover(bot.db_connection) == 1 and counts(user) == (0, 1)


def test_shared_chat_quota_reservation_concurrency(user, ai):
    def chat(): return bot.reserve_chat(user, str(uuid4()))
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(chat) for _ in range(4)] + [pool.submit(lambda: submit(client(user))) for _ in range(2)]
        results = [f.result() for f in futures]
    assert counts(user)[0] <= 5
    assert sum(r >= 0 for r in results[:4]) + sum(r.status_code == 200 for r in results[4:]) == counts(user)[0]


def test_failure_budget_cannot_be_bypassed_by_refunds(user, ai, monkeypatch):
    monkeypatch.setenv('LEARNING_USER_DAILY_CALLS', '1')
    ai.client.responses.create = lambda **kwargs: (_ for _ in ()).throw(TimeoutError())
    c = client(user)
    assert submit(c).status_code == 502
    assert submit(c).status_code == 429 and counts(user) == (0, 1)


def test_rate_limit_and_oversized_requests(user, ai):
    c = client(user)
    payload = body()
    for _ in range(12):
        # Same request ID is allowed to replay but still subject to request rate limit.
        assert submit(c, payload).status_code == 200
    assert submit(c, payload).status_code == 429
    assert len(ai.calls) == 1
    assert c.post('/api/learning/attempts', json=body(answer='a' * 21000), headers=csrf(c)).status_code == 413


def test_refund_transaction_rolls_back(user, monkeypatch):
    key = str(uuid4())
    accounting.reserve(bot.db_connection, user, key, QUESTIONS['ielts-writing-1-v1'], 'answer', bot.MODEL, (10, 200), 5)
    with monkeypatch.context() as patch:
        patch.setattr(accounting, '_refund', lambda *args: (_ for _ in ()).throw(RuntimeError()))
        with pytest.raises(RuntimeError):
            accounting.finish(bot.db_connection, user, key)
    assert counts(user) == (1, 1)
    assert accounting.history(bot.db_connection, user, key)[0]['status'] == 'pending'
    assert accounting.finish(bot.db_connection, user, key)
    assert counts(user) == (0, 1)


def test_successful_settlement_response_outage_replays(user, ai, monkeypatch):
    real = accounting.finish
    def finish_then_fail(*args, **kwargs):
        result = real(*args, **kwargs)
        if len(args) > 3 and args[3] is not None:
            raise RuntimeError('Network broke after commit')
        return result
    c = client(user); payload = body()
    with monkeypatch.context() as patch:
        patch.setattr(accounting, 'finish', finish_then_fail)
        assert submit(c, payload).status_code == 502
    assert submit(c, payload).status_code == 200
    assert len(ai.calls) == 1 and counts(user) == (1, 1)


def test_global_concurrent_calls_cannot_overspend(user, ai, monkeypatch):
    monkeypatch.setenv('LEARNING_GLOBAL_DAILY_CALLS', '3')
    with bot.db_connection() as conn:
        others = [conn.execute('INSERT INTO users(phone,password_hash) VALUES (%s,%s) RETURNING id',
                  ('1390000000' + str(i), 'test')).fetchone()[0] for i in range(1, 7)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda uid: submit(client(uid)).status_code, others))
    assert responses.count(200) == 3 and responses.count(429) == 3
    assert len(ai.calls) == 3


def test_recover_cli(user):
    result = bot.app.test_cli_runner().invoke(args=['learning-recover'])
    assert result.exit_code == 0 and 'Recovered 0' in result.output


def test_unicode_answer_and_input_cost_bound(user, ai):
    c = client(user)
    payload = json.dumps(body(answer='😀' * 4000), ensure_ascii=False)
    assert c.post('/api/learning/attempts', data=payload, content_type='application/json', headers=csrf(c)).status_code == 200
    assert len(ai.calls) == 1
    from learning.prompts import SYSTEM, FEEDBACK_SCHEMA
    assert len((SYSTEM + ai.calls[0]['input'] + json.dumps(FEEDBACK_SCHEMA)).encode()) <= 20000
