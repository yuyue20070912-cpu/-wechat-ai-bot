import pytest
import bot
from learning import migrate

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
        return conn.execute("INSERT INTO users(phone,password_hash) VALUES ('13800000000','test') RETURNING id").fetchone()[0]

