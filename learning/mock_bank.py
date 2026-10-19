"""Versioned original SHORT section mocks, never a full or official exam."""
from .diagnostics import question

NOTICE='原创分项短模考，题量与时长经过缩减；不是完整考试、官方真题或官方成绩。口语为文字模拟，不评价语音。'
SECTIONS={'ielts':['listening','reading','writing','speaking'],
          'cet4':['listening','reading','writing','translation','vocabulary'],
          'cet6':['listening','reading','writing','translation','vocabulary']}
LABELS={'listening':'听力短模考','reading':'阅读短模考','writing':'写作短模考',
        'speaking':'口语文字模拟','translation':'翻译短模考','vocabulary':'词汇拓展短模考'}
DURATIONS={'listening':300,'reading':600,'writing':1200,'speaking':240,'translation':600,'vocabulary':300}


def make_bank():
    bank={}
    for exam,sections in SECTIONS.items():
        for form in ('a','b'):
            advanced=exam!='cet4'
            topic='community gardens' if form=='a' else 'public libraries'
            room='Room 205' if form=='a' else 'Room 310'
            day='Thursday' if form=='a' else 'Friday'
            time='2 pm' if form=='a' else '10 am'
            transcript=(f'Thank you for joining our workshop about {topic}. The original room is closed for repairs. '
                f'We will meet in {room} of the community centre on {day} at {time}. '
                'You do not need to bring any equipment. Please bring a notebook. '
                'The first activity will be a group discussion, followed by a short practical task. '
                'Registration closes on Tuesday. If you need to change your booking, contact the coordinator before Monday evening.')
            passage=(f'A neighbourhood project on {topic} began with an online survey. Although two hundred people received it, '
                'only twenty replied. The organisers then held a small face-to-face workshop. Participants explained their concerns in detail. '
                'The organisers revised their plan and introduced a three-month trial. Attendance grew during the trial, but the team did not measure long-term outcomes. '
                'The coordinator warned that early enthusiasm did not necessarily imply lasting success. They planned another evaluation after six months.'
                if advanced else
                f'Last month our school started a project about {topic}. The teachers first sent an online survey to two hundred students. '
                'Only twenty students replied. Then the teachers held a small meeting. Students shared many useful ideas. '
                'The team decided to try the new plan for three months. They will check it again after six months.')
            for section in sections:
                q=[];material='';audio=None
                if section=='listening':
                    audio=f'{exam}-{form}.mp3'
                    q=[question('l1',section,'Where will the workshop take place?',[room,'The closed room','The railway station'],0,'地点已改为 '+room+'。'),
                       question('l2',section,'When will participants meet?',[f'Monday at {time}',f'{day} at {time}',f'Tuesday at {time}'],1,'集合时间是 '+day+' '+time+'，不要混淆报名截止日。'),
                       question('l3',section,'What should participants bring?',['Equipment','A booking form','A notebook'],2,'材料由主办方提供，但参与者需要带笔记本。')]
                elif section=='reading':
                    material=passage
                    q=[question('r1',section,'How many people replied to the survey?',['Two hundred','Twenty','Six'],1,'收到的是二十份回复，不能把发送人数当回复数。'),
                       question('r2',section,'What followed the survey?',['A workshop or small meeting','An immediate permanent closure','A final official exam'],0,'调查之后安排了面对面讨论。'),
                       question('r3',section,'How long is the initial trial?',['Six months','One year','Three months'],2,'试行三个月，之后另行评估。'),
                       question('r4',section,'Which statement is supported?',['Long-term success is already proven.','Further evaluation is planned.','There were no suggestions.'],1,'材料说明会再次评估，并没有证明长期成功。')]
                elif section=='vocabulary':
                    material='词汇拓展训练，不是 CET 独立官方计分分项。'
                    words=[('revise','change to improve'),('evaluate','judge using evidence'),('prepare','get ready'),('sustainable','able to continue over time')] if advanced else [('borrow','use temporarily'),('improve','make better'),('prepare','get ready'),('reply','answer')]
                    q=[question(f'v{i}',section,f'Choose the meaning of "{word}".',[meaning,'the opposite meaning','an unrelated action'],0,f'{word} 表示 {meaning}。') for i,(word,meaning) in enumerate(words)]
                else:
                    prompt=(f'Some people think public money should support {topic}. To what extent do you agree? Give reasons and an example.' if section=='writing'
                        else f'Describe a place associated with {topic}. Explain why you visit it and what you would improve.' if section=='speaking'
                        else '将下面的中文译成英文：虽然线上交流很方便，但面对面的讨论往往能帮助我们更好地理解彼此。因此，学校应该为学生创造更多合作的机会。' if advanced
                        else '将下面的中文译成英文：为了保持健康，我每天步行去学校。虽然有时下雨，但我仍然喜欢这个习惯。')
                    reference=(f'Public investment in {topic} can improve access and encourage cooperation. For example, a small local project may bring people together. However, spending should be evaluated carefully to ensure a lasting benefit.' if section=='writing'
                        else f'I often visit a place connected with {topic}. It gives me a quiet space to learn and meet neighbours. I would improve access so that more people could join.' if section=='speaking'
                        else 'Although online communication is convenient, face-to-face discussions often help us understand one another better. Schools should therefore create more opportunities for students to work together.' if advanced
                        else 'To stay healthy, I walk to school every day. Although it sometimes rains, I still enjoy this habit.')
                    q=[dict(id='s1',skill=section,prompt=prompt,reference=reference,
                        explanation='主观表达没有唯一答案。参考文本仅作学习示例，默认不评分；可选 AI 仅提供 0–100 学习参考分。',
                        guidance='建议 150–250 英文词，最多 4000 字。' if section=='writing' else '用文字完成，不录音、不评价发音。' if section=='speaking' else '忠实、完整、自然地翻译。')]
                for item in q:item['kind']='objective' if 'correct_index' in item else 'subjective'
                key=f'{exam}-{section}-{form}-short-v1'
                bank[key]=dict(id=key,version='short-v1',exam_type=exam,section=section,form=form,
                    title=LABELS[section],duration_seconds=DURATIONS[section],scope='short_section',notice=NOTICE,
                    material=material,audio_file=audio,transcript=transcript if audio else None,questions=q,
                    audio_notice='原创文本的离线合成语音，非官方录音。可手动播放，无自动播放或录音。' if audio else None)
    return bank


BANK=make_bank()

# Preserve existing A/B content and IDs; add three original listening-only pilot forms.
from .ielts_audio import MATERIALS, descriptor as audio_descriptor
for item in MATERIALS.values():
    if item['template_id'] not in BANK:
        BANK[item['template_id']]=dict(id=item['template_id'],version='short-v1',exam_type='ielts',section='listening',
            form=item['form'],title=LABELS['listening']+' · '+item['title'],duration_seconds=DURATIONS['listening'],
            scope='short_section',notice=NOTICE,material='',audio_file=None,transcript=item['transcript'],
            audio_notice='原创 IELTS 风格材料，非官方录音；高质量 MP3 待接入。',questions=item['questions'])


def public_template(template, finished=False):
    data={k:v for k,v in template.items() if k not in ('questions','audio_file','transcript')}
    audio=audio_descriptor(template)
    data['has_audio']=bool(template['audio_file']) or audio is not None
    if audio is not None: data['audio']=audio
    data['questions']=[{k:v for k,v in q.items() if finished or k not in ('correct_index','explanation','reference')} for q in template['questions']]
    if finished:data['transcript']=template.get('transcript')
    return data
