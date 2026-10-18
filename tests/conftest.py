import json
from types import SimpleNamespace as NS

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



@pytest.fixture
def ai(monkeypatch):
    monkeypatch.setenv('LEARNING_AI_ENABLED', '1')
    calls, options = [], []
    fake = NS()
    def create(**kwargs):
        calls.append(kwargs)
        return NS(status='completed', output_text=json.dumps(FEEDBACK), usage=NS(input_tokens=200, output_tokens=150))
    def with_options(**kwargs):
        options.append(kwargs)
        return fake
    fake.with_options = with_options
    fake.responses = NS(create=create)
    monkeypatch.setattr(bot, 'ai_client', lambda: fake)
    return NS(client=fake, calls=calls, options=options)

