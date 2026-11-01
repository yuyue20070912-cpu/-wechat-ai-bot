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
    system = SYSTEM
    if question.get('diagnostic'):
        system += '''\n本次任务是解读服务器提供的本地文字诊断报告，不是批改一篇作文。
        summary 总结有限证据，strengths/improvements 解释已测优势和薄弱项，
        revised_answer 给出示例学习安排，next_step 给出一步可完成的行动。
        不改写本地正确率、不把六题结果推断为官方分数、CEFR 或完整水平认证。
        score 仅为本次建议的内部参考指标，不能声称为考试成绩或客观诊断正确率。'''
    if question.get('mock'):
        system+='\n本次是缩减题量的分项模考。只按该文字题给 0–100 学习参考分；不得换算 IELTS band、CET 710 分总分或官方成绩，不评价文字口语的发音与真实流利度。'
    system += '\n请只输出一个符合下列 JSON schema 的 JSON 对象，不要输出其它文字：\n' + json.dumps(
        FEEDBACK_SCHEMA, ensure_ascii=False)
    if len((system + user_input + json.dumps(FEEDBACK_SCHEMA)).encode('utf-8')) > 20000:
        raise ValueError('Input exceeds learning cost bound')
    # DeepSeek Chat Completions; reuse host SDK pool with explicit timeout and no retries.
    response = provider().with_options(timeout=TIMEOUT_SECONDS, max_retries=0).chat.completions.create(
        model=model,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_input},
        ],
        max_tokens=MAX_OUTPUT_TOKENS,
        response_format={'type': 'json_object'},
    )
    choice = response.choices[0] if getattr(response, 'choices', None) else None
    content = (getattr(getattr(choice, 'message', None), 'content', None) or '').strip()
    if choice is None or getattr(choice, 'finish_reason', None) != 'stop' or not content or len(content) > 16000:
        raise ValueError('Incomplete AI feedback')
    feedback = validate_feedback(json.loads(content))
    usage = getattr(response, 'usage', None)
    tokens = tuple(getattr(usage, name, None) for name in ('prompt_tokens', 'completion_tokens'))
    if any(token is not None and (type(token) is not int or token < 0) for token in tokens):
        raise ValueError('Invalid usage metadata')
    return feedback, tokens
