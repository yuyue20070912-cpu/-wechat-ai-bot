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
