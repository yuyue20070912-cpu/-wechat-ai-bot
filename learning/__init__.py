"""Learning center integration; shares the host application's auth and pool."""
from pathlib import Path

import click

from .routes import create_blueprint


def migrate(connection):
    with connection() as conn:
        # Fail promptly rather than queue application traffic behind DDL.
        conn.execute("SET LOCAL lock_timeout = '3s'")
        conn.execute("SET LOCAL statement_timeout = '30s'")
        conn.execute('SELECT pg_advisory_xact_lock(645001002)')
        for path in sorted((Path(__file__).parent / 'migrations').glob('*.sql')):
            conn.execute(path.read_text())


def register(app, connection, require_login, rate_limit, ai_provider, model, free_messages):
    app.config['LEARNING_FREE_MESSAGES'] = free_messages
    app.register_blueprint(create_blueprint(connection, require_login, rate_limit, ai_provider, model))

    @app.cli.command('learning-migrate')
    def learning_migrate():
        """Explicitly apply additive learning schema; never runs on requests."""
        # Existing users/auth/quota schema must already exist. Never alter host tables.
        migrate(connection)
        click.echo('Learning schema is ready.')

    @app.cli.command('learning-recover')
    def learning_recover():
        """Refund a bounded batch of abandoned training requests."""
        from .accounting import recover
        click.echo(f'Recovered {recover(connection)} learning requests.')
