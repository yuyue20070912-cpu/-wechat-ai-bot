"""Learning center integration; shares the host application's auth and pool."""
from pathlib import Path

import click

from .routes import create_blueprint


def migrate(connection):
    sql = (Path(__file__).parent / 'migrations' / '001_learning_profiles.sql').read_text()
    with connection() as conn:
        conn.execute(sql)


def register(app, connection, require_login, rate_limit, init_db):
    app.register_blueprint(create_blueprint(connection, require_login, rate_limit))

    @app.cli.command('learning-migrate')
    def learning_migrate():
        """Explicitly apply additive learning schema; never runs on requests."""
        init_db()
        migrate(connection)
        click.echo('Learning schema is ready.')
