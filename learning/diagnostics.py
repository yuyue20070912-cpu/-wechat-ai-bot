"""Original, versioned screening tests. Local scoring is not an official exam."""
from .questions import SKILLS

TEST_VERSION = 'entry-v1'
NOTICE = '六道原创文字入门题，仅供学习定位；不等同官方成绩、完整水平认证、口语发音或听辨测试。'


def question(key, skill, prompt, options, correct, explanation):
    return dict(id=key, skill=skill, prompt=prompt, options=options,
                correct_index=correct, explanation=explanation)


TESTS = {
    'ielts': [
        question('iv', 'vocabulary', 'The findings are tentative. What does tentative mean?',
                 ['final', 'not yet certain', 'unrelated'], 1, 'tentative 表示暂定、尚未确定，不能误解为最终结论。'),
        question('ir', 'reading', 'A trial reduced car use, but only in districts with reliable buses. Which claim is supported?',
                 ['It worked everywhere.', 'Reliable buses may be important to the result.', 'Buses were removed.'], 1, '限定条件 only in… 不支持将结论推广到所有地区。'),
        question('il', 'listening', '文字预备：The talk was moved from Monday to Wednesday, but the venue remains Hall B. When and where is it?',
                 ['Monday, Hall B', 'Wednesday, Hall A', 'Wednesday, Hall B'], 2, '时间发生变化，地点保持 Hall B；这里只检查转写文本理解。'),
        question('iw', 'writing', 'Which sentence best supports an argument with a specific example?',
                 ['Libraries are good because they are good.', 'For instance, our library lends laptops to students without computers.', 'Everyone always agrees.'], 1, '具体的借用电脑案例比重复观点或绝对化陈述更有说服力。'),
        question('is', 'speaking', 'Which written response develops an answer to "Why do you enjoy cooking?"?',
                 ['Because yes.', 'Cooking.', 'I enjoy cooking because it helps me relax; last weekend I made soup with my sister.'], 2, '完整回答包含原因和例子；文字选择题不能评价发音或真实流利度。'),
        question('it', 'translation', '拓展题：Which translation preserves “尽管成本较高，这个方案仍然值得考虑”？',
                 ['Although the cost is high, the proposal is still worth considering.', 'The cost is low, so we must reject it.', 'We considered it because it was free.'], 0, 'Although 保留让步关系；worth considering 保留“值得考虑”。IELTS 无独立翻译笔试分项。'),
    ],
    'cet4': [
        question('4v', 'vocabulary', 'We need to ___ for tomorrow’s meeting.', ['prepare', 'repair', 'compare'], 0, 'prepare for 表示为……做准备。'),
        question('4r', 'reading', 'Mei walks to class to save money. On rainy days she takes the bus. Why does she usually walk?',
                 ['To avoid class', 'To save money', 'Because buses never run'], 1, '首句明确说明 to save money；不要把雨天安排当作日常原因。'),
        question('4l', 'listening', '文字预备：Please arrive at 8:30. The bus leaves at 8:45. When should you arrive?',
                 ['8:45', '9:00', '8:30'], 2, '到达时间与发车时间不同，这里检查文本中的时间信息。'),
        question('4w', 'writing', 'Choose the correct sentence.', ['She go to school daily.', 'She goes to school daily.', 'She going school daily.'], 1, '一般现在时第三人称单数使用 goes。'),
        question('4s', 'speaking', '拓展题：Choose the most complete reply to “What do you do after class?”',
                 ['I usually play basketball with my friends.', 'After.', 'Yes class.'], 0, '完整回答交代活动和同伴；这是口语文字拓展，不属于四级常规笔试分项。'),
        question('4t', 'translation', 'Choose a faithful translation of “每天阅读有助于我们学习新词”。',
                 ['New words stop us reading.', 'We never read new words.', 'Reading every day helps us learn new words.'], 2, '保留“每天阅读”作主语和“有助于学习”的关系。'),
    ],
    'cet6': [
        question('6v', 'vocabulary', 'The policy mitigates risk. What does mitigates mean?', ['creates', 'reduces', 'ignores'], 1, 'mitigate 表示减轻或缓和风险，并非完全消除。'),
        question('6r', 'reading', 'Remote work increased flexibility, yet the study did not measure productivity. What cannot be concluded?',
                 ['Productivity definitely increased.', 'Flexibility increased.', 'Productivity was not measured.'], 0, '未测量生产率，就不能由灵活性增加推断生产率一定提高。'),
        question('6l', 'listening', '文字预备：Registration closes on Friday, although the conference begins the following Tuesday. Which is the deadline?',
                 ['The following Tuesday', 'Friday', 'No deadline'], 1, 'registration closes 指报名截止；会议开始时间并非截止时间。'),
        question('6w', 'writing', 'Which revision avoids an unsupported absolute claim?',
                 ['Technology always solves every problem.', 'No problem has ever existed.', 'Technology can improve access, provided that users receive adequate support.'], 2, 'can 和 provided that 为结论增加程度与条件限制，避免绝对化。'),
        question('6s', 'speaking', '拓展题：Which response acknowledges a different view before giving a reason?',
                 ['Some prefer emails; however, I favour calls for urgent issues because they allow immediate clarification.', 'Calls. Always.', 'Different view is view.'], 0, '先承认其他偏好，再用 however 和 because 组织观点与理由；无法评价语音表现。'),
        question('6t', 'translation', 'Choose the best translation of “只有充分合作，我们才能有效应对这一挑战”。',
                 ['Only by working closely together can we address this challenge effectively.', 'We can avoid all challenges without cooperation.', 'Cooperation was never effective.'], 0, 'Only by… can we… 保留“只有……才……”的条件和倒装表达。'),
    ],
}


def public_test(exam):
    return dict(version=TEST_VERSION, notice=NOTICE, questions=[
        {k: v for k, v in q.items() if k not in ('correct_index', 'explanation')} for q in TESTS[exam]])


def score(exam, answers):
    items = TESTS[exam]
    if not isinstance(answers, dict) or set(answers) != {q['id'] for q in items}:
        raise ValueError('请完成本场诊断的全部六道题')
    details = []
    for q in items:
        value = answers[q['id']]
        if type(value) is not int or not 0 <= value < len(q['options']):
            raise ValueError('诊断选项格式不正确')
        correct = value == q['correct_index']
        details.append(dict(question_id=q['id'], skill=q['skill'], correct=correct,
            selected=value, reference=q['options'][q['correct_index']], explanation=q['explanation']))
    correct = sum(item['correct'] for item in details)
    return dict(correct=correct, total=len(items), accuracy=round(correct / len(items) * 100, 1),
        level='基础起步' if correct <= 2 else '稳步提升' if correct <= 4 else '进阶巩固',
        skill_scores={item['skill']: 100 if item['correct'] else 0 for item in details},
        weak_skills=[item['skill'] for item in details if not item['correct']],
        details=details, notice=NOTICE, method='本地客观判分，不调用付费 AI')
