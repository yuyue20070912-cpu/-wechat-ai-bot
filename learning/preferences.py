from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .accounting import PracticeError

DEFAULT_TIMEZONE = 'Asia/Shanghai'


def get(conn, user_id):
    row = conn.execute('SELECT timezone,version FROM learning_preferences WHERE user_id=%s', (user_id,)).fetchone()
    return dict(timezone=row[0], version=row[1]) if row else dict(timezone=DEFAULT_TIMEZONE, version=0)


def local_day(conn, user_id):
    timezone = get(conn, user_id)['timezone']
    day = conn.execute('SELECT (clock_timestamp() AT TIME ZONE %s)::date', (timezone,)).fetchone()[0]
    return day, timezone


def save(connection, user_id, timezone, version):
    if not isinstance(timezone, str) or len(timezone) > 80 or type(version) is not int or not 0 <= version <= 1000000:
        raise ValueError('Invalid preference')
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Unknown timezone') from None
    with connection() as conn:
        if not conn.execute('SELECT 1 FROM pg_timezone_names WHERE name=%s', (timezone,)).fetchone():
            raise ValueError('Unsupported timezone')
        conn.execute('INSERT INTO learning_preferences(user_id) VALUES (%s) ON CONFLICT DO NOTHING', (user_id,))
        row = conn.execute('SELECT timezone,version FROM learning_preferences WHERE user_id=%s FOR UPDATE', (user_id,)).fetchone()
        if row[0] == timezone and row[1] in (version,version+1):
            return dict(timezone=row[0],version=row[1])
        if row[1] != version:
            raise PracticeError('时区设置已改变，请刷新后重试',409)
        row = conn.execute('UPDATE learning_preferences SET timezone=%s,version=version+1,updated_at=NOW() WHERE user_id=%s RETURNING timezone,version',
                           (timezone,user_id)).fetchone()
        return dict(timezone=row[0],version=row[1])
