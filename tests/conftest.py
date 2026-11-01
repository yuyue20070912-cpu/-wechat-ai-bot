import json
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
import bot
from learning import migrate
from learning_fakes import FEEDBACK

@pytest.fixture(scope='session', autouse=True)
def database(tmp_path_factory):
    import os
    import pgserver
    server = pgserver.get_server(tmp_path_factory.mktemp('postgres'), cleanup_mode='delete')
    os.environ['DATABASE_URL'] = server.get_uri()
    bot.app.config.update(TESTING=True, SECRET_KEY='test-only', SESSION_COOKIE_SECURE=False)
    bot.init_db()
    migrate(bot.db_connection)
    yield
    bot.database_pool().close()


@pytest.fixture
def user():
    with bot.db_connection() as conn:
        conn.execute('TRUNCATE users CASCADE')
        conn.execute('TRUNCATE learning_daily_budget')
        return conn.execute("INSERT INTO users(phone,password_hash) VALUES ('13800000000','test') RETURNING id").fetchone()[0]



def grant_learning_membership(user_id, messages=1000, plan='pro', reference=None):
    """AI 伴学 requires paid membership; Chat free quota stays separate."""
    with bot.db_connection() as conn:
        return conn.execute(
            """INSERT INTO subscriptions(user_id,plan,starts_at,ends_at,message_limit,payment_reference)
               VALUES (%s,%s,NOW()-INTERVAL '1 day',NOW()+INTERVAL '30 days',%s,%s) RETURNING id""",
            (user_id, plan, messages, reference or f'test-learning-{user_id}-{uuid4()}'),
        ).fetchone()[0]


@pytest.fixture
def ai(monkeypatch, user):
    monkeypatch.setenv('LEARNING_AI_ENABLED', '1')
    grant_learning_membership(user)
    calls, options = [], []
    fake = NS()
    def create(**kwargs):
        calls.append(kwargs)
        return NS(
            choices=[NS(finish_reason='stop', message=NS(content=json.dumps(FEEDBACK)))],
            usage=NS(prompt_tokens=200, completion_tokens=150),
        )
    def with_options(**kwargs):
        options.append(kwargs)
        return fake
    fake.with_options = with_options
    fake.chat = NS(completions=NS(create=create))
    monkeypatch.setattr(bot, 'ai_client', lambda: fake)
    return NS(client=fake, calls=calls, options=options)
