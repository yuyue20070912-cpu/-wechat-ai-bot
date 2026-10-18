import os

MAX_ANSWER = 4000
MAX_OUTPUT_TOKENS = 1600
TIMEOUT_SECONDS = 45


def enabled():
    return os.getenv('LEARNING_AI_ENABLED', '0') == '1'


def daily_limits():
    # Fail closed on invalid config; hard ceilings prevent accidental unbounded spend.
    user = int(os.getenv('LEARNING_USER_DAILY_CALLS', '10'))
    global_limit = int(os.getenv('LEARNING_GLOBAL_DAILY_CALLS', '200'))
    if not 1 <= user <= 50 or not 1 <= global_limit <= 1000:
        raise ValueError('Invalid learning budget configuration')
    return user, global_limit
