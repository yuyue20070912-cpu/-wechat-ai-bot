"""Atomic learning ledger sharing existing free/subscription message allowances.

Provider-call budgets are never refunded (even a failed call may cost money).
Message allowances are refunded on failure, exactly once and against original day.
"""
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb


class PracticeError(Exception):
    def __init__(self, message, status=429):
        super().__init__(message)
        self.status = status


def _refund(conn, row):
    user_id, subscription_id, usage_day = row
    if subscription_id is not None:
        conn.execute('UPDATE subscriptions SET used=GREATEST(0,used-1) WHERE id=%s AND user_id=%s',
                     (subscription_id, user_id))
    else:
        conn.execute('''UPDATE daily_message_usage SET used=GREATEST(0,used-1)
            WHERE user_id=%s AND usage_day=%s''', (user_id, usage_day))


def recover(connection):
    with connection() as conn:
        rows = conn.execute('''UPDATE learning_attempts SET status='refunded',finished_at=NOW()
            WHERE (user_id,request_id) IN (
                SELECT user_id,request_id FROM learning_attempts
                WHERE status='pending' AND created_at < NOW()-INTERVAL '5 minutes'
                ORDER BY created_at LIMIT 100 FOR UPDATE SKIP LOCKED)
            RETURNING user_id,subscription_id,usage_day''').fetchall()
        # Stable quota-row order also helps concurrent recovery workers.
        for row in sorted(rows, key=lambda row: (row[0], row[1] or 0, row[2])):
            _refund(conn, row)
        return len(rows)


def reserve(connection, user_id, request_id, question, answer, model, limits, free_limit):
    recover(connection)
    user_limit, global_limit = limits
    try:
        with connection() as conn:
            inserted = conn.execute('''INSERT INTO learning_attempts
                (user_id,request_id,question_id,exam_type,skill,answer,status,usage_day,model)
                VALUES (%s,%s,%s,%s,%s,%s,'pending',(NOW() AT TIME ZONE 'UTC')::date,%s)
                ON CONFLICT (user_id,request_id) DO NOTHING RETURNING usage_day''',
                (user_id, request_id, question['id'], question['exam_type'], question['skill'], answer, model)).fetchone()
            if inserted is None:
                row = conn.execute('''SELECT question_id,answer,status,feedback FROM learning_attempts
                    WHERE user_id=%s AND request_id=%s''', (user_id, request_id)).fetchone()
                if row[0] != question['id'] or row[1] != answer:
                    raise PracticeError('请求编号已用于其他答案，请创建新练习请求', 409)
                if row[2] == 'completed':
                    return row[3]
                raise PracticeError('该请求正在处理或已经结束，请查看练习记录', 409)
            day = inserted[0]
            # Every attempted provider call reserves a nonrefundable budget slot.
            budget = conn.execute('''INSERT INTO learning_daily_budget(usage_day,calls) VALUES (%s,1)
                ON CONFLICT (usage_day) DO UPDATE SET calls=learning_daily_budget.calls+1
                WHERE learning_daily_budget.calls < %s RETURNING calls''', (day, global_limit)).fetchone()
            if not budget:
                raise PracticeError('今日 AI 练习服务已达调用上限，请明天再试')
            user_budget = conn.execute('''INSERT INTO learning_user_daily_usage(user_id,usage_day,calls)
                VALUES (%s,%s,1) ON CONFLICT (user_id,usage_day)
                DO UPDATE SET calls=learning_user_daily_usage.calls+1
                WHERE learning_user_daily_usage.calls < %s RETURNING calls''',
                (user_id, day, user_limit)).fetchone()
            if not user_budget:
                raise PracticeError('今日 AI 练习调用次数已用完，请明天再试')
            sub = conn.execute('''SELECT id FROM subscriptions
                WHERE user_id=%s AND starts_at <= NOW() AND ends_at > NOW()
                ORDER BY ends_at DESC,id DESC LIMIT 1 FOR UPDATE''', (user_id,)).fetchone()
            if sub:
                available = conn.execute('''UPDATE subscriptions SET used=used+1
                    WHERE id=%s AND used < message_limit RETURNING id''', (sub[0],)).fetchone()
            else:
                available = conn.execute('''INSERT INTO daily_message_usage(user_id,usage_day,used)
                    VALUES (%s,%s,1) ON CONFLICT (user_id,usage_day)
                    DO UPDATE SET used=daily_message_usage.used+1
                    WHERE daily_message_usage.used < %s RETURNING used''', (user_id, day, free_limit)).fetchone()
            if not available:
                raise PracticeError('当前消息额度已用完，请等待重置或续费')
            conn.execute('''UPDATE learning_attempts SET subscription_id=%s
                WHERE user_id=%s AND request_id=%s''', (sub[0] if sub else None, user_id, request_id))
        return None
    except UniqueViolation:
        raise PracticeError('已有一道练习正在分析，请稍后再试', 409) from None


def finish(connection, user_id, request_id, feedback=None, tokens=(None, None)):
    with connection() as conn:
        row = conn.execute('''UPDATE learning_attempts
            SET status=%s,feedback=%s,input_tokens=%s,output_tokens=%s,finished_at=NOW()
            WHERE user_id=%s AND request_id=%s AND status='pending'
            RETURNING user_id,subscription_id,usage_day''',
            ('completed' if feedback is not None else 'refunded', Jsonb(feedback) if feedback is not None else None,
             *tokens, user_id, request_id)).fetchone()
        if row and feedback is None:
            _refund(conn, row)
        if row and feedback is not None:
            from .reviews import capture_attempt
            capture_attempt(conn, user_id, request_id)
        return row is not None


def history(connection, user_id, request_id=None):
    with connection() as conn:
        rows = conn.execute('''SELECT request_id,question_id,exam_type,skill,answer,status,
            feedback,created_at,finished_at FROM learning_attempts
            WHERE user_id=%s''' + (' AND request_id=%s' if request_id else '') +
            ' ORDER BY created_at DESC LIMIT 20',
            (user_id, request_id) if request_id else (user_id,)).fetchall()
    return [dict(request_id=str(r[0]), question_id=r[1], exam_type=r[2], skill=r[3], answer=r[4],
                 purpose='diagnostic' if r[1].startswith('diagnostic-') else 'mock' if r[1].startswith('mock-') else 'practice',
                 status=r[5], feedback=r[6], created_at=r[7].isoformat(),
                 finished_at=r[8].isoformat() if r[8] else None) for r in rows]


def usage(connection, user_id, user_limit, free_limit):
    with connection() as conn:
        calls = conn.execute('''SELECT calls FROM learning_user_daily_usage
            WHERE user_id=%s AND usage_day=(NOW() AT TIME ZONE 'UTC')::date''', (user_id,)).fetchone()
        sub = conn.execute('''SELECT message_limit-used FROM subscriptions
            WHERE user_id=%s AND starts_at <= NOW() AND ends_at > NOW()
            ORDER BY ends_at DESC,id DESC LIMIT 1''', (user_id,)).fetchone()
        if sub is not None:
            remaining = sub[0]
        else:
            row = conn.execute('''SELECT used FROM daily_message_usage
                WHERE user_id=%s AND usage_day=(NOW() AT TIME ZONE 'UTC')::date''', (user_id,)).fetchone()
            remaining = max(0, free_limit - (row[0] if row else 0))
    used = calls[0] if calls else 0
    return dict(daily_call_limit=user_limit, calls_used=used, calls_remaining=max(0, user_limit-used),
                message_remaining=max(0, remaining), reset='UTC 00:00')
