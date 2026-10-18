"""Small, original practice bank; supplemental skills are labelled as such.

No past-paper content. References/keys remain server-side until feedback is saved.
"""
from .catalog import EXAMS

SKILLS = {
    'speaking': '口语 · 文字表达', 'writing': '写作',
    'listening': '听力 · 文本预备', 'reading': '阅读',
    'vocabulary': '词汇', 'translation': '翻译',
}

_TOPICS = {
    'ielts': ('community projects', 'education and technology'),
    'cet4': ('campus life', 'healthy habits'),
    'cet6': ('sustainable cities', 'digital communication'),
}


def build_bank():
    bank = {}
    for exam in EXAMS:
        exam_id = exam['id']
        for index, topic in enumerate(_TOPICS[exam_id], 1):
            advanced = exam_id != 'cet4'
            passage = (
                f'A student group studied {topic}. At first, they planned a large online survey. '
                'However, few people completed it. They then invited twelve volunteers to a short workshop. '
                'The smaller group offered detailed suggestions, so the team revised its plan. '
                'The project leader said that useful feedback mattered more than the number of participants.'
                if advanced else
                f'Lin is preparing a class project about {topic}. She first sent a survey to her classmates. '
                'Only five people replied. On Friday she held a small meeting with twelve students. '
                'They shared many useful ideas. Lin decided to use their suggestions in her project.'
            )
            transcript = (
                f'Coordinator: Our workshop on {topic} was planned for Tuesday at the library. '
                'The library is closed for repairs, so we have moved it to Room 204 in the science building. '
                'It will now take place on Thursday at 3 pm. Please bring a notebook; we will provide all other materials.'
            )
            templates = {
                'speaking': dict(title='分享你的经历', prompt=f'Describe an experience related to {topic}. Explain what happened, what you learned, and why it mattered to you.',
                    material='', guidance='用英文写下你准备说的话，建议 80–180 词。仅分析文字中的表达、连贯与语法，不评价发音、语速或真实口语流利度。',
                    reference=f'I once worked with classmates on a project about {topic}. We divided the work and met weekly. I learned to listen before making decisions. The experience mattered because it helped me communicate more clearly.'),
                'writing': dict(title='写一段有说服力的观点', prompt=f'Some people think schools should spend more time teaching {topic}. Do you agree or disagree? Explain your view with reasons and an example.',
                    material='', guidance=('用英文完成议论短文，建议 180–280 词。' if advanced else '用英文完成短文，建议 100–180 词。') + '这是专项小练习，不是完整真题或官方评分。',
                    reference=f'Schools should introduce {topic} through practical activities. Students remember ideas better when they can apply them. For example, a small group project can connect classroom knowledge with everyday decisions. However, these activities should support core subjects rather than replace them.'),
                'reading': dict(title='抓住原因与细节', prompt=('Why did the group change its method, and what does the leader value most? Use evidence from the passage.' if advanced else 'Why did Lin hold a meeting, and how many students attended?'),
                    material=passage, guidance='阅读原创短文，用英文回答并指出依据。',
                    reference=('The survey received few responses. A workshop produced detailed suggestions, and the leader values useful feedback more than participant numbers.' if advanced else 'Only five people replied to the survey, so Lin held a meeting. Twelve students attended.')),
                'listening': dict(title='辨认变更后的安排', prompt='Where and when will the workshop take place? What should participants bring?',
                    material=transcript, guidance='当前为听力文本预备练习：展开转写文本后识别地点、时间和物品。本阶段不播放音频，不评价听辨能力。',
                    reference='Room 204 in the science building, Thursday at 3 pm. Participants should bring a notebook.'),
                'vocabulary': dict(title='把词汇用进语境', prompt=('Explain the difference between "revise" and "replace" in English, then write one original sentence using each word about ' + topic + '.' if advanced else 'Explain what "improve" and "prepare" mean, then write one original English sentence using each word about ' + topic + '.'),
                    material='', guidance='写出词义和两个完整例句。关注搭配和语境，不只背中文释义。',
                    reference=('Revise means to change something to improve it; replace means to put something else in its place. We revised our report after the workshop. We replaced the old survey with interviews.' if advanced else 'Improve means to make something better. Prepare means to get ready. We improve campus life by planting trees. We prepare for the meeting by writing questions.')),
                'translation': dict(title='准确表达，再自然一点', prompt='将下面的中文译成英文，保留转折和因果关系。',
                    material=('虽然线上交流更加方便，但面对面的讨论往往能帮助人们更深入地理解彼此。因此，学校应该为学生创造更多合作的机会。' if advanced else '为了保持健康，我每天步行去学校。虽然有时下雨，但我仍然喜欢这个习惯，因为它让我感到精力充沛。'),
                    guidance='先保证意思完整，再检查语法、连接词和自然表达。',
                    reference=('Although online communication is more convenient, face-to-face discussions often help people understand one another more deeply. Schools should therefore create more opportunities for students to work together.' if advanced else 'To stay healthy, I walk to school every day. Although it sometimes rains, I still enjoy this habit because it makes me feel energetic.')),
            }
            for skill, content in templates.items():
                question_id = f'{exam_id}-{skill}-{index}-v1'
                bank[question_id] = dict(id=question_id, exam_type=exam_id, skill=skill,
                    topic=topic, **content, original=True,
                    supplemental=(exam_id == 'ielts' and skill in ('translation', 'vocabulary'))
                        or (exam_id != 'ielts' and skill == 'speaking'))
    return bank


QUESTIONS = build_bank()


def public_question(question):
    return {key: value for key, value in question.items() if key != 'reference'}
