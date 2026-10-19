"""Original, versioned screening tests. Local scoring is not an official exam."""
TEST_VERSION = 'entry-v2'
NOTICE = '文字入门筛查，不能代表完整英语水平，非正式能力评级。'


def question(key, skill, prompt, options, correct, explanation):
    return dict(id=key, skill=skill, prompt=prompt, options=options,
                correct_index=correct, explanation=explanation)


LEGACY_TESTS = {
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


def alternate_group(form):
    groups={}
    for exam in LEGACY_TESTS:
        easy=exam=='cet4'
        vocab = ({'ielts':('plausible','reasonable'), 'cet4':('borrow','use temporarily'), 'cet6':('substantiate','support with evidence')}
                 if form=='b' else {'ielts':('allocate','assign for a purpose'), 'cet4':('arrive','reach a place'), 'cet6':('inhibit','hold back or prevent')})[exam]
        reading=('The library extended evening hours after a student survey. Weekend hours stayed the same.' if form=='b'
                 else 'A town tested its river after heavy rain. The water was unsafe that week, but no earlier measurements were available.')
        read_prompt=reading+(' Which statement is supported?' if form=='b' else ' What can we conclude?')
        read_options=(['Evening hours increased; weekend hours did not.','All opening hours doubled.','Students never use the library.'] if form=='b'
                      else ['The river has always been unsafe.','Water was unsafe during the measured week.','Rain always makes every river safe.'])
        listen=('文字预备：The appointment was moved from 9 am to 11 am. Please go to Room 302, not Room 203. Which arrangement is correct?' if form=='b'
                else '文字预备：The course starts on May 12, but payment is due on May 5. What is the payment deadline?')
        groups[exam]=[
            question(exam+'-v-'+form,'vocabulary',f'Choose the meaning of "{vocab[0]}" in context.',[vocab[1],'the exact opposite','an unrelated idea'],0,f'{vocab[0]} 在这里意为 {vocab[1]}。'),
            question(exam+'-r-'+form,'reading',read_prompt,read_options,0 if form=='b' else 1,'只使用材料支持的结论；没有早期测量，不能推断长期状态。' if form=='c' else '延长的是晚间时间，周末没有变化。'),
            question(exam+'-l-'+form,'listening',listen,['9 am, Room 203','11 am, Room 302','11 am, Room 203'] if form=='b' else ['May 12','May 15','May 5'],1 if form=='b' else 2,'区分原安排与新安排，或开始日与付款截止日；这仅是文本理解。'),
            question(exam+'-w-'+form,'writing','Choose the correct and coherent sentence.' if easy else 'Which sentence connects evidence to a cautious conclusion?',
                ['He has lived here for two years.','He have live here for two years.','He living here since two years.'] if easy and form=='b'
                else ['If it rains, we will stay inside.','If rain, we stays inside.','If raining we will staying.'] if easy
                else ['The sample is small, so further research is needed.','One example proves everything forever.','Evidence never matters.'] if form=='b'
                else ['Because only one class was observed, the result may not apply to all students.','All students are identical.','No observations are required.'],0,'注意时态、条件句或证据的适用范围，不把有限样本绝对化。'),
            question(exam+'-s-'+form,'speaking','Choose a developed written reply to "How do you solve a difficult problem?"' if form=='b' else 'Choose a developed written reply to "Why is teamwork useful?"',
                ['Problem yes.','I break it into smaller steps and check one step at a time.' if form=='b' else 'Different people notice different details; for example, a partner found an error I missed.','Always all.'],1,'完整文字回答给出方法或具体理由；不能据此评判真实口语表现。'),
            question(exam+'-t-'+form,'translation','选择保留原意的译文：“我们越认真准备，就越有信心。”' if form=='b' else '选择保留原意的译文：“在作出决定之前，我们需要更多证据。”',
                ['Preparation removes every problem.','We never need preparation.','The more carefully we prepare, the more confident we become.'] if form=='b'
                else ['We need more evidence before making a decision.','We decide before looking at any evidence.','Evidence is never needed.'],2 if form=='b' else 0,'保留比较关系或时间先后关系；IELTS 翻译与 CET 口语属于拓展观察。'),
        ]
    return groups


GROUPS={'entry-v1':LEGACY_TESTS,'entry-v1-b':alternate_group('b'),'entry-v1-c':alternate_group('c')}
# Retain legacy groups for historical replay and review; new screens exclude listening.
LEGACY_GROUPS = dict(GROUPS)
for version, exams in LEGACY_GROUPS.items():
    GROUPS[version.replace('entry-v1', 'entry-v2')] = {
        exam: [q for q in items if q['skill'] != 'listening']
        for exam, items in exams.items()
    }
TESTS = GROUPS[TEST_VERSION]
GROUP_VERSIONS = tuple(version for version in GROUPS if version.startswith('entry-v2'))


def public_test(exam,index=0):
    version=GROUP_VERSIONS[index%len(GROUP_VERSIONS)]
    return dict(version=version,group=index%3+1,groups=3,notice=NOTICE,questions=[
        {k: v for k, v in q.items() if k not in ('correct_index', 'explanation')} for q in GROUPS[version][exam]])


def score(exam, answers, version=None):
    version = version or TEST_VERSION
    items = GROUPS[version][exam]
    if not isinstance(answers, dict) or set(answers) != {q['id'] for q in items}:
        raise ValueError('请完成本场诊断的全部题目')
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
        scope='entry_screening',group=version,level_is_proficiency=False,
        level='基础起步' if correct <= 2 else '稳步提升' if correct <= 4 else '进阶巩固',
        skill_scores={item['skill']: 100 if item['correct'] else 0 for item in details},
        weak_skills=[item['skill'] for item in details if not item['correct']],
        details=details, notice=NOTICE, method='本地客观判分，不调用付费 AI')
