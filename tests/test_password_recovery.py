from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS

import pytest
from werkzeug.security import generate_password_hash, check_password_hash
import bot
import password_recovery as recovery

PHONE = '13800000000'
OLD = 'Old-password-123'
NEW = 'New-password-456'


@pytest.fixture
def setup(user, monkeypatch):
    for key in recovery.ENV_KEYS:
        monkeypatch.setenv(key, 'test-only')
    with bot.db_connection() as conn:
        conn.execute('TRUNCATE auth_rate_limits, password_recoveries')
        conn.execute('UPDATE users SET password_hash=%s WHERE id=%s', (generate_password_hash(OLD), user))
    calls = []
    def provider(action, phone, out_id, code=None):
        calls.append((action, phone, out_id, code))
        return action == 'send' or code == '123456'
    monkeypatch.setattr(recovery, 'sms_call', provider)
    return user, calls


def send(client, phone=PHONE):
    return client.post('/auth/recovery/send', json={'phone': phone})


def verify(client, challenge, code='123456', phone=PHONE):
    return client.post('/auth/recovery/verify', json=dict(phone=phone, challenge=challenge, code=code))


def reset(client, token, password=NEW, phone=PHONE):
    return client.post('/auth/recovery/reset', json=dict(phone=phone, reset_token=token, new_password=password))


def grant(client):
    sent = send(client)
    assert sent.status_code == 200
    result = verify(client, sent.json['challenge'])
    assert result.status_code == 200
    return result.json['reset_token']


def test_full_flow_revokes_old_sessions_preserves_data(setup):
    user, _ = setup
    old = bot.app.test_client()
    assert old.post('/auth/login', json=dict(phone=PHONE, password=OLD)).status_code == 200
    cookie = old.get_cookie('session').value
    with bot.db_connection() as conn:
        conn.execute("INSERT INTO subscriptions(user_id,plan,starts_at,ends_at,message_limit,used,payment_reference) VALUES (%s,'basic',NOW(),NOW()+INTERVAL '30 days',300,7,'recovery-test')", (user,))
        conn.execute('INSERT INTO daily_message_usage(user_id,usage_day,used) VALUES (%s,CURRENT_DATE,2)', (user,))
    c = bot.app.test_client()
    token = grant(c)
    assert reset(c, token).status_code == 200
    assert reset(c, token).status_code == 400
    assert old.get('/auth/me').json == {'authenticated': False}
    # Replaying the exact signed pre-reset cookie cannot restore authentication.
    old.set_cookie('session', cookie)
    assert old.post('/chat', json={'message':'test'}).status_code == 401
    assert c.post('/auth/login', json=dict(phone=PHONE, password=OLD)).status_code == 401
    assert c.post('/auth/login', json=dict(phone=PHONE, password=NEW)).status_code == 200
    assert c.get('/auth/me').json['authenticated'] is True
    with bot.db_connection() as conn:
        row = conn.execute('SELECT id,password_hash,session_version FROM users WHERE phone=%s', (PHONE,)).fetchone()
        assert row[0] == user and row[1].startswith('scrypt:') and check_password_hash(row[1], NEW) and row[2] == 1
        assert conn.execute('SELECT used FROM subscriptions WHERE user_id=%s',(user,)).fetchone()[0] == 7
        assert conn.execute('SELECT used FROM daily_message_usage WHERE user_id=%s',(user,)).fetchone()[0] == 2


def test_unconfigured_and_no_bypass(setup, monkeypatch):
    c = bot.app.test_client()
    for key in recovery.ENV_KEYS:
        monkeypatch.delenv(key)
    assert send(c).status_code == 503
    assert '尚未配置' in send(c).json['error']
    assert verify(c, 'x'*43).status_code == 503
    assert reset(c, 'x'*43).status_code == 503
    for key in recovery.ENV_KEYS:
        monkeypatch.setenv(key, 'test-only')
    assert reset(c, 'x'*43).status_code == 400


def test_limits_and_wrong_code(setup):
    _, calls = setup
    c = bot.app.test_client()
    challenge = send(c).json['challenge']
    assert send(c).status_code == 429
    for _ in range(5):
        assert verify(c, challenge, '000000').status_code == 400
    assert verify(c, challenge).status_code == 429
    assert len([v for v in calls if v[0] == 'verify']) == 5
    with bot.db_connection() as conn:
        assert conn.execute('SELECT attempts FROM password_recoveries WHERE phone=%s',(PHONE,)).fetchone()[0] == 5


def test_expired_and_phone_binding(setup):
    c = bot.app.test_client(); challenge = send(c).json['challenge']
    assert verify(c, challenge, phone='13900000000').status_code == 400
    with bot.db_connection() as conn:
        conn.execute("UPDATE password_recoveries SET expires_at=NOW()-INTERVAL '1 second'")
    assert verify(c, challenge).status_code == 400


def test_resend_invalidates_grant_and_unknown_account(setup):
    _, calls = setup
    c = bot.app.test_client(); token = grant(c)
    with bot.db_connection() as conn:
        conn.execute("DELETE FROM auth_rate_limits WHERE action='recovery:minute'")
    assert send(c).status_code == 200
    assert reset(c, token).status_code == 400
    unknown = send(c, '13900000000')
    assert unknown.status_code == 200
    assert verify(c, unknown.json['challenge'], phone='13900000000').status_code == 400
    assert not any(v[1] == '13900000000' for v in calls)


def test_provider_failure_never_authorizes(setup, monkeypatch):
    c = bot.app.test_client()
    monkeypatch.setattr(recovery, 'sms_call', lambda *args: False)
    assert send(c).status_code == 503
    with bot.db_connection() as conn:
        assert conn.execute('SELECT sent FROM password_recoveries').fetchone()[0] is False
    with bot.db_connection() as conn:
        conn.execute('TRUNCATE auth_rate_limits')
    monkeypatch.setattr(recovery, 'sms_call', lambda *args: True)
    challenge = send(c).json['challenge']
    def unavailable(*args):
        raise TimeoutError('secret details must not be exposed')
    monkeypatch.setattr(recovery, 'sms_call', unavailable)
    response = verify(c, challenge)
    assert response.status_code == 503 and 'secret' not in response.text
    with bot.db_connection() as conn:
        assert conn.execute('SELECT attempts,grant_hash FROM password_recoveries').fetchone() == (1,None)


def test_expired_grant_password_rules_and_replay(setup):
    c = bot.app.test_client(); token = grant(c)
    assert reset(c, token, OLD).status_code == 400
    assert reset(c, token, '12345678901').status_code == 400
    assert reset(c, token, 'abcdefghijk').status_code == 400
    assert reset(c, token, phone='13900000000').status_code == 400
    with bot.db_connection() as conn:
        conn.execute("UPDATE password_recoveries SET grant_expires_at=NOW()-INTERVAL '1 second'")
    assert reset(c, token).status_code == 400


def test_concurrent_reset_is_single_use(setup):
    token = grant(bot.app.test_client())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reset(bot.app.test_client(), token).status_code, range(2)))
    assert sorted(results) == [200,400]


def test_concurrent_verify_issues_one_grant(setup):
    challenge = send(bot.app.test_client()).json['challenge']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: verify(bot.app.test_client(), challenge).status_code, range(2)))
    assert sorted(results) == [200,400]


def test_password_change_invalidates_recovery_grant(setup):
    c = bot.app.test_client(); token = grant(c)
    assert c.post('/auth/login',json=dict(phone=PHONE,password=OLD)).status_code == 200
    assert c.post('/auth/change-password',json=dict(old_password=OLD,new_password=NEW)).status_code == 200
    assert reset(c, token, 'Another-new-789').status_code == 400


@pytest.mark.parametrize('payload', [[], 'bad', 1, None, {'phone': []}])
def test_malformed_json(setup, payload):
    c = bot.app.test_client()
    for endpoint in ('send','verify','reset'):
        assert c.post('/auth/recovery/'+endpoint,json=payload).status_code == 400


def test_sdk_contract(monkeypatch):
    from alibabacloud_dypnsapi20170525.client import Client
    for key in recovery.ENV_KEYS:
        monkeypatch.setenv(key, 'test-only')
    def send(self, req, runtime):
        assert req.phone_number == PHONE and req.scheme_name == 'test-only'
        assert req.template_param == '{"code":"##code##","min":"5"}'
        assert req.valid_time == 300 and req.interval == 60 and req.code_length == 6
        assert req.duplicate_policy == 1 and req.return_verify_code is False
        assert runtime.autoretry is False and runtime.read_timeout == 5000
        return NS(body=NS(success=True,code='OK'))
    result = NS(success=True,code='OK',model=NS(verify_result='UNKNOWN'))
    def check(self, req, runtime):
        assert req.verify_code == '123456' and req.out_id == 'out-id'
        return NS(body=result)
    monkeypatch.setattr(Client, 'send_sms_verify_code_with_options', send)
    monkeypatch.setattr(Client, 'check_sms_verify_code_with_options', check)
    assert recovery.sms_call('send', PHONE, 'out-id')
    assert not recovery.sms_call('verify', PHONE, 'out-id', '123456')
    result.model.verify_result = 'PASS'
    assert recovery.sms_call('verify', PHONE, 'out-id', '123456')
    result.code = 'FAIL'
    assert not recovery.sms_call('verify', PHONE, 'out-id', '123456')


def test_schema_is_additive_and_idempotent(setup):
    user, _ = setup
    with bot.db_connection() as conn:
        before = conn.execute('SELECT id,phone,password_hash,session_version FROM users WHERE id=%s',(user,)).fetchone()
        with conn.cursor() as cur:
            recovery.create_schema(cur)
            recovery.create_schema(cur)
        assert conn.execute('SELECT id,phone,password_hash,session_version FROM users WHERE id=%s',(user,)).fetchone() == before


def test_daily_and_ip_limits(setup):
    c = bot.app.test_client()
    for _ in range(10):
        bot.rate_limit('recovery:day',PHONE,10,1440)
    assert send(c).status_code == 429
    with bot.db_connection() as conn:
        conn.execute('TRUNCATE auth_rate_limits')
    for _ in range(20):
        bot.rate_limit('recovery:send:ip','127.0.0.1',20)
    assert send(c,'13900000000').status_code == 429
