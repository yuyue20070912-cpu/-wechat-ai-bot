from concurrent.futures import ThreadPoolExecutor

import pytest
import psycopg
import bot
from learning import migrate
from learning import routes


def client(user):
    c = bot.app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = user
    return c


def headers(c):
    return {'X-Learning-CSRF': c.get('/api/learning/profiles').json['csrf_token']}


def save(c, exam='ielts', score=6.5, level='intermediate', **kwargs):
    return c.put('/api/learning/profiles/' + exam,
                 json=dict(target_score=score, current_level=level), **kwargs)


@pytest.mark.parametrize('path,method', [('/learning', 'get'), ('/api/learning/exams', 'get'),
    ('/api/learning/profiles', 'get'), ('/api/learning/profiles/ielts', 'put')])
def test_auth_required(path, method):
    response = getattr(bot.app.test_client(), method)(path)
    assert response.status_code == 401
    assert response.headers['Cache-Control'] == 'no-store'


def test_catalog_and_chat_entry(user):
    c = client(user)
    assert [e['id'] for e in c.get('/api/learning/exams').json['exams']] == ['ielts', 'cet4', 'cet6']
    assert b'href="/learning"' in c.get('/').data
    assert c.get('/learning').status_code == 200


def test_roundtrip_and_isolation(user):
    c = client(user); h = headers(c)
    assert save(c, headers=h).status_code == 200
    first = c.get('/api/learning/profiles').json['profiles'][0]
    assert save(c, score=7, headers=h).json['profile']['created_at'] == first['created_at']
    assert save(c, 'cet4', 500, headers=h).status_code == 200
    assert save(c, 'cet6', 600, headers=h).status_code == 200
    assert len(client(user).get('/api/learning/profiles').json['profiles']) == 3
    with bot.db_connection() as conn:
        other = conn.execute("INSERT INTO users(phone,password_hash) VALUES ('13900000001','test') RETURNING id").fetchone()[0]
    second = client(other)
    assert second.get('/api/learning/profiles').json['profiles'] == []
    assert save(second, score=8, headers=headers(second)).status_code == 200
    assert c.get('/api/learning/profiles').json['profiles'][2]['target_score'] == 7
    with bot.db_connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM daily_message_usage').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM membership_orders').fetchone()[0] == 0


@pytest.mark.parametrize('exam,score,level', [
    ('ielts', 9.5, 'beginner'), ('ielts', 6.1, 'beginner'), ('ielts', 0, 'beginner'),
    ('ielts', True, 'beginner'), ('ielts', '6.5', 'beginner'), ('ielts', None, 'beginner'),
    ('ielts', float('inf'), 'beginner'), ('ielts', float('nan'), 'beginner'),
    ('cet4', 710.5, 'beginner'), ('cet6', -1, 'beginner'), ('cet6', 500.5, 'beginner'),
    ('cet4', 500, []), ('ielts', 7, 'unknown')])
def test_invalid_fields(user, exam, score, level):
    c = client(user)
    assert save(c, exam, score, level, headers=headers(c)).status_code == 400
    assert c.get('/api/learning/profiles').json['profiles'] == []


@pytest.mark.parametrize('body', [[], None, {}, {'target_score': 7},
    {'target_score': 7, 'current_level': 'beginner', 'user_id': 123}])
def test_invalid_payload(user, body):
    c = client(user)
    assert c.put('/api/learning/profiles/ielts', json=body, headers=headers(c)).status_code == 400


def test_csrf_and_unknown_exam(user):
    c = client(user)
    assert save(c).status_code == 403
    assert save(c, headers={'X-Learning-CSRF': 'bad'}).status_code == 403
    h = headers(c)
    assert save(c, headers={**h, 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
    assert save(c, 'unknown', headers=h).status_code == 404


def test_rate_limit(user):
    c = client(user); h = headers(c)
    with bot.db_connection() as conn:
        conn.execute('TRUNCATE auth_rate_limits')
    for _ in range(60):
        assert save(c, headers=h).status_code == 200
    assert save(c, headers=h).status_code == 429


def test_outage_returns_safe_error(user, monkeypatch):
    c = client(user); h = headers(c)
    def fail(*args):
        raise RuntimeError('private database credentials')
    monkeypatch.setattr(routes, 'list_profiles', fail)
    response = c.get('/api/learning/profiles')
    assert response.status_code == 503 and b'credentials' not in response.data
    monkeypatch.setattr(routes, 'save_profile', fail)
    assert save(c, headers=h).status_code == 503


def test_migration_repeatable_and_constraints(user):
    c = client(user); save(c, headers=headers(c))
    migrate(bot.db_connection); migrate(bot.db_connection)
    assert len(c.get('/api/learning/profiles').json['profiles']) == 1
    with pytest.raises(psycopg.errors.CheckViolation):
        with bot.db_connection() as conn:
            conn.execute("UPDATE learning_profiles SET target_score=6.1")


def test_concurrent_upsert(user):
    def write(score):
        c = client(user)
        return save(c, score=score, headers=headers(c)).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(write, [6, 6.5, 7, 7.5])) == [200] * 4
    assert len(client(user).get('/api/learning/profiles').json['profiles']) == 1


def test_migration_cli(user):
    result = bot.app.test_cli_runner().invoke(args=['learning-migrate'])
    assert result.exit_code == 0 and 'ready' in result.output
