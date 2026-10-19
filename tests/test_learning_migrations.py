from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

import bot
from learning import migrate


@pytest.fixture
def isolated_schema():
    """Independent schema inside the temporary test database, never a live DB."""
    name = 'migration_' + uuid4().hex
    with bot.db_connection() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))

    @contextmanager
    def connection():
        with bot.db_connection() as conn:
            conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(name)))
            yield conn
    try:
        yield connection
    finally:
        with bot.db_connection() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))


def test_upgrade_from_phase_one_and_repeat_without_data_loss(isolated_schema):
    connection = isolated_schema
    with connection() as conn:
        conn.execute('CREATE TABLE users(id BIGINT PRIMARY KEY)')
        conn.execute('CREATE TABLE subscriptions(id BIGINT PRIMARY KEY)')
        conn.execute('INSERT INTO users VALUES (1)')
        conn.execute(Path('learning/migrations/001_learning_profiles.sql').read_text())
        conn.execute("INSERT INTO learning_profiles(user_id,exam_type,target_score,current_level) VALUES (1,'ielts',7,'beginner')")
    migrate(connection); migrate(connection)
    with connection() as conn:
        assert conn.execute('SELECT target_score FROM learning_profiles').fetchone()[0] == 7
        assert conn.execute('SELECT COUNT(*) FROM learning_attempts').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 1


def test_failed_migration_rolls_back_all_new_learning_tables(isolated_schema):
    with isolated_schema() as conn:
        conn.execute('CREATE TABLE users(id BIGINT PRIMARY KEY)')
        # Missing subscriptions intentionally makes migration 002 fail.
    with pytest.raises(psycopg.errors.UndefinedTable):
        migrate(isolated_schema)
    with isolated_schema() as conn:
        assert conn.execute("SELECT to_regclass('learning_profiles')").fetchone()[0] is None


def test_migration_lock_timeout_leaves_existing_data_intact(isolated_schema):
    with isolated_schema() as conn:
        conn.execute('CREATE TABLE users(id BIGINT PRIMARY KEY)')
        conn.execute('CREATE TABLE subscriptions(id BIGINT PRIMARY KEY)')
    migrate(isolated_schema)
    # The shared advisory lock is held by a separate connection/transaction.
    with bot.db_connection() as blocker:
        blocker.execute('SELECT pg_advisory_xact_lock(645001002)')
        with pytest.raises(psycopg.errors.LockNotAvailable):
            migrate(isolated_schema)
    with isolated_schema() as conn:
        assert conn.execute('SELECT COUNT(*) FROM learning_attempts').fetchone()[0] == 0


def test_upgrade_from_phase_two_preserves_attempt_and_personalization_data(isolated_schema):
    with isolated_schema() as conn:
        conn.execute('CREATE TABLE users(id BIGINT PRIMARY KEY)')
        conn.execute('CREATE TABLE subscriptions(id BIGINT PRIMARY KEY)')
        conn.execute('INSERT INTO users VALUES (1)')
        for name in ('001_learning_profiles.sql','002_learning_training.sql'):
            conn.execute((Path('learning/migrations')/name).read_text())
        conn.execute("""INSERT INTO learning_attempts(user_id,request_id,question_id,exam_type,skill,answer,status,usage_day,model)
            VALUES (1,%s,'ielts-writing-1-v1','ielts','writing','original answer','pending',CURRENT_DATE,'test')""",(str(uuid4()),))
    migrate(isolated_schema)
    with isolated_schema() as conn:
        conn.execute("""INSERT INTO learning_plans(user_id,exam_type,plan_date,inputs) VALUES(1,'ielts',CURRENT_DATE,'{}')""")
        conn.execute("""INSERT INTO learning_tasks(user_id,exam_type,plan_date,task_id,content,completed,completed_at)
            VALUES(1,'ielts',CURRENT_DATE,'read','{}',TRUE,NOW())""")
    migrate(isolated_schema)
    with isolated_schema() as conn:
        assert conn.execute('SELECT answer FROM learning_attempts').fetchone()[0]=='original answer'
        assert conn.execute('SELECT completed FROM learning_tasks').fetchone()[0] is True


def test_phase_three_failure_rolls_back_only_new_work(isolated_schema,monkeypatch):
    with isolated_schema() as conn:
        conn.execute('CREATE TABLE users(id BIGINT PRIMARY KEY)')
        conn.execute('CREATE TABLE subscriptions(id BIGINT PRIMARY KEY)')
        conn.execute('INSERT INTO users VALUES (1)')
        for name in ('001_learning_profiles.sql','002_learning_training.sql'):
            conn.execute((Path('learning/migrations')/name).read_text())
        conn.execute("INSERT INTO learning_profiles(user_id,exam_type,target_score,current_level) VALUES(1,'ielts',7,'beginner')")
    original=Path.read_text
    def corrupt(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        return result+'\nSELECT * FROM missing_phase_three_table;' if path.name=='003_learning_personalization.sql' else result
    monkeypatch.setattr(Path,'read_text',corrupt)
    with pytest.raises(psycopg.errors.UndefinedTable):
        migrate(isolated_schema)
    with isolated_schema() as conn:
        assert conn.execute("SELECT to_regclass('learning_diagnostics')").fetchone()[0] is None
        assert conn.execute('SELECT target_score FROM learning_profiles').fetchone()[0]==7


def test_phase_four_upgrade_and_rollback_preserve_old_tasks(isolated_schema,monkeypatch):
    with isolated_schema() as conn:
        conn.execute('CREATE TABLE users(id BIGINT PRIMARY KEY)')
        conn.execute('CREATE TABLE subscriptions(id BIGINT PRIMARY KEY)')
        conn.execute('INSERT INTO users VALUES(1)')
        for path in sorted(Path('learning/migrations').glob('00[123]_*.sql')):
            conn.execute(path.read_text())
        conn.execute("INSERT INTO learning_plans(user_id,exam_type,plan_date,inputs) VALUES(1,'ielts',CURRENT_DATE,'{}')")
        conn.execute("INSERT INTO learning_tasks(user_id,exam_type,plan_date,task_id,content,completed,completed_at) VALUES(1,'ielts',CURRENT_DATE,'saved','{}',TRUE,NOW())")
    original=Path.read_text
    def corrupt(path,*args,**kwargs):
        text=original(path,*args,**kwargs)
        return text+'\nSELECT * FROM absent_phase_four_table;' if path.name=='004_learning_mock_exams.sql' else text
    with monkeypatch.context() as patch:
        patch.setattr(Path,'read_text',corrupt)
        with pytest.raises(psycopg.errors.UndefinedTable):migrate(isolated_schema)
    with isolated_schema() as conn:
        assert conn.execute("SELECT to_regclass('learning_mock_sessions')").fetchone()[0] is None
        assert conn.execute('SELECT completed FROM learning_tasks').fetchone()[0] is True
    migrate(isolated_schema);migrate(isolated_schema)
    with isolated_schema() as conn:
        assert conn.execute('SELECT completed FROM learning_tasks').fetchone()[0] is True
        assert conn.execute('SELECT COUNT(*) FROM learning_mock_sessions').fetchone()[0]==0


def test_phase_five_additive_upgrade_rolls_back_without_changing_old_rows(isolated_schema,monkeypatch):
    with isolated_schema() as conn:
        conn.execute('CREATE TABLE users(id BIGINT PRIMARY KEY)')
        conn.execute('CREATE TABLE subscriptions(id BIGINT PRIMARY KEY)')
        conn.execute('INSERT INTO users VALUES(1)')
        for path in sorted(Path('learning/migrations').glob('00[1234]_*.sql')):
            conn.execute(path.read_text())
        conn.execute("INSERT INTO learning_profiles(user_id,exam_type,target_score,current_level) VALUES(1,'ielts',7,'beginner')")
    original=Path.read_text
    def corrupt(path,*args,**kwargs):
        text=original(path,*args,**kwargs)
        return text+'\nSELECT * FROM absent_phase_five_table;' if path.name=='005_learning_voice.sql' else text
    with monkeypatch.context() as patch:
        patch.setattr(Path,'read_text',corrupt)
        with pytest.raises(psycopg.errors.UndefinedTable):migrate(isolated_schema)
    with isolated_schema() as conn:
        assert conn.execute("SELECT to_regclass('learning_voice_sessions')").fetchone()[0] is None
        assert conn.execute('SELECT target_score FROM learning_profiles').fetchone()[0]==7
    migrate(isolated_schema);migrate(isolated_schema)
    with isolated_schema() as conn:
        assert conn.execute('SELECT target_score FROM learning_profiles').fetchone()[0]==7
        assert conn.execute('SELECT count(*) FROM learning_voice_sessions').fetchone()[0]==0
