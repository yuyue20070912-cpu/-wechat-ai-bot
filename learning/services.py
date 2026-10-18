import json

from .config import MAX_OUTPUT_TOKENS, TIMEOUT_SECONDS
from .prompts import FEEDBACK_SCHEMA, SYSTEM, input_text


def validate_feedback(value):
    """Validate again server-side, including models/proxies that ignore the schema."""
    if not isinstance(value, dict) or set(value) != set(FEEDBACK_SCHEMA['required']):
        raise ValueError('Invalid feedback object')
    if type(value['score']) is not int or not 0 <= value['score'] <= 100:
        raise ValueError('Invalid feedback score')
    for key, spec in FEEDBACK_SCHEMA['properties'].items():
        if spec['type'] == 'string':
            if not isinstance(value[key], str) or not 1 <= len(value[key].strip()) <= spec['maxLength']:
                raise ValueError('Invalid feedback text')
        elif spec['type'] == 'array':
            items = value[key]
            if not isinstance(items, list) or not spec['minItems'] <= len(items) <= spec['maxItems']:
                raise ValueError('Invalid feedback list')
            if any(not isinstance(item, str) or not 1 <= len(item.strip()) <= spec['items']['maxLength'] for item in items):
                raise ValueError('Invalid feedback item')
    return value


def analyze(provider, model, question, answer):
    user_input = input_text(question, answer)
    if len((SYSTEM + user_input + json.dumps(FEEDBACK_SCHEMA)).encode('utf-8')) > 20000:
        raise ValueError('Input exceeds learning cost bound')
    # Reuse host SDK connection pool, with explicit timeout and zero automatic retries.
    response = provider().with_options(timeout=TIMEOUT_SECONDS, max_retries=0).responses.create(
        model=model, instructions=SYSTEM, input=user_input,
        max_output_tokens=MAX_OUTPUT_TOKENS, store=False,
        text={'format': {'type': 'json_schema', 'name': 'learning_feedback',
                         'strict': True, 'schema': FEEDBACK_SCHEMA}},
    )
    if response.status != 'completed' or not response.output_text or len(response.output_text) > 16000:
        raise ValueError('Incomplete AI feedback')
    feedback = validate_feedback(json.loads(response.output_text))
    usage = getattr(response, 'usage', None)
    tokens = tuple(getattr(usage, name, None) for name in ('input_tokens', 'output_tokens'))
    if any(token is not None and (type(token) is not int or token < 0) for token in tokens):
        raise ValueError('Invalid usage metadata')
    return feedback, tokens
