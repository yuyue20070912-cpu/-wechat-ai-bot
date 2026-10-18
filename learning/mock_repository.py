import json
from hashlib import sha256
from datetime import timedelta
from uuid import uuid5,NAMESPACE_URL

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from .accounting import PracticeError
from .mock_bank import BANK,NOTICE,public_template

COLUMNS='session_id,template_id,exam_type,section,snapshot,answers,version,status,started_at,deadline,finished_at,report'


def ai_id(user_id,session_id,question_id):
    return str(uuid5(NAMESPACE_URL,f'fishyeah:mock:{user_id}:{session_id}:{question_id}'))


def grade(snapshot,answers):
    details=[];correct=total=0
    for q in snapshot['questions']:
        answer=answers.get(q['id'])
        if q['kind']=='objective':
            ok=answer==q['correct_index'];total+=1;correct+=int(ok)
            reference=q['options'][q['correct_index']]
        else:ok=None;reference=q['reference']
        details.append(dict(question_id=q['id'],kind=q['kind'],answer=answer,correct=ok,
                            reference=reference,explanation=q['explanation']))
    return dict(objective_correct=correct,objective_total=total,objective_accuracy=round(correct/total*100,1) if total else None,
        subjective_count=sum(q['kind']=='subjective' for q in snapshot['questions']),details=details,
        notice=NOTICE,score_policy='客观题正确率与主观 AI 参考分分别显示，不换算 band、710 分制或官方总分。')


def fetch(conn,user_id,session_id):
    row=conn.execute(f'SELECT {COLUMNS} FROM learning_mock_sessions WHERE user_id=%s AND session_id=%s FOR UPDATE',
                     (user_id,session_id)).fetchone()
    if row is None:raise PracticeError('模考记录不存在',404)
    return dict(zip(COLUMNS.split(','),row))


def expire(conn,user_id,row):
    now=conn.execute('SELECT clock_timestamp()').fetchone()[0]
    if row['status']=='active' and row['deadline']<=now:
        report=grade(row['snapshot'],row['answers'])
        conn.execute("""UPDATE learning_mock_sessions SET status='expired',finished_at=deadline,
            report=%s,version=version+1 WHERE user_id=%s AND session_id=%s""",(Jsonb(report),user_id,row['session_id']))
        return fetch(conn,user_id,row['session_id'])
    return row


def serialize(conn,user_id,row):
    now=conn.execute('SELECT clock_timestamp()').fetchone()[0]
    subjective=[]
    if row['status']!='active':
        for q in row['snapshot']['questions']:
            if q['kind']!='subjective':continue
            attempt=conn.execute('SELECT question_id,status,feedback FROM learning_attempts WHERE user_id=%s AND request_id=%s',
                (user_id,ai_id(user_id,str(row['session_id']),q['id']))).fetchone()
            expected='mock-'+str(row['session_id'])+'-'+q['id']
            subjective.append(dict(question_id=q['id'],status=attempt[1] if attempt and attempt[0]==expected else 'not_requested',
                                   feedback=attempt[2] if attempt and attempt[0]==expected else None))
    return dict(id=str(row['session_id']),template_id=row['template_id'],exam_type=row['exam_type'],section=row['section'],
        template=public_template(row['snapshot'],row['status']!='active'),answers=row['answers'],version=row['version'],status=row['status'],
        started_at=row['started_at'].isoformat(),deadline=row['deadline'].isoformat(),server_now=now.isoformat(),
        remaining_seconds=max(0,(row['deadline']-now).total_seconds()) if row['status']=='active' else 0,
        finished_at=row['finished_at'].isoformat() if row['finished_at'] else None,report=row['report'],ai_results=subjective,
        save_policy='服务器计时不因离线暂停；到时按已同步答案结束，未同步草稿不计入成绩。')


def get(connection,user_id,session_id):
    with connection() as conn:
        return serialize(conn,user_id,expire(conn,user_id,fetch(conn,user_id,session_id)))


def start(connection,user_id,session_id,template_id):
    if template_id not in BANK:raise PracticeError('分项模考不存在',404)
    try:
        with connection() as conn:
            prior=conn.execute('SELECT template_id FROM learning_mock_sessions WHERE user_id=%s AND session_id=%s',
                               (user_id,session_id)).fetchone()
            if prior:
                if prior[0]!=template_id:raise PracticeError('请求编号已用于另一场模考',409)
                return serialize(conn,user_id,expire(conn,user_id,fetch(conn,user_id,session_id)))
            active=conn.execute("SELECT session_id FROM learning_mock_sessions WHERE user_id=%s AND status='active' FOR UPDATE",(user_id,)).fetchone()
            if active:
                row=expire(conn,user_id,fetch(conn,user_id,active[0]))
                if row['status']=='active':raise PracticeError('已有进行中的模考，请先恢复并完成',409)
            template=BANK[template_id];now=conn.execute('SELECT clock_timestamp()').fetchone()[0]
            inserted=conn.execute('''INSERT INTO learning_mock_sessions(user_id,session_id,template_id,exam_type,section,snapshot,started_at,deadline)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id,session_id) DO NOTHING RETURNING session_id''',
                (user_id,session_id,template_id,template['exam_type'],template['section'],Jsonb(template),now,now+timedelta(seconds=template['duration_seconds']))).fetchone()
            if inserted is None:
                row=fetch(conn,user_id,session_id)
                if row['template_id']!=template_id:raise PracticeError('请求编号已用于另一场模考',409)
            return serialize(conn,user_id,fetch(conn,user_id,session_id))
    except UniqueViolation:
        raise PracticeError('已有进行中的模考，请刷新并恢复',409) from None


def validate_patch(snapshot,patch):
    if not isinstance(patch,dict):raise ValueError('Invalid answers')
    questions={q['id']:q for q in snapshot['questions']}
    if not set(patch)<=set(questions):raise ValueError('Unknown question')
    for key,value in patch.items():
        q=questions[key]
        if q['kind']=='objective':
            if value is not None and (type(value) is not int or not 0<=value<len(q['options'])):raise ValueError('Invalid option')
        elif not isinstance(value,str) or len(value)>4000:raise ValueError('Invalid text answer')


def write(connection,user_id,session_id,request_id,version,patch,kind):
    if type(version) is not int or not 0<=version<=1000000:raise ValueError('Invalid version')
    with connection() as conn:
        row=fetch(conn,user_id,session_id)
        validate_patch(row['snapshot'],patch)
        signature=sha256(json.dumps(patch,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        prior=conn.execute('''SELECT session_id,kind,expected_version,payload_hash,saved_version FROM learning_mock_writes
            WHERE user_id=%s AND request_id=%s''',(user_id,request_id)).fetchone()
        if prior:
            if (str(prior[0]),prior[1],prior[2],prior[3])!=(session_id,kind,version,signature):raise PracticeError('保存编号已用于其他内容',409)
            return dict(session=serialize(conn,user_id,expire(conn,user_id,row)),replayed=True,saved_version=prior[4])
        row=expire(conn,user_id,row)
        if row['status']!='active':
            if kind=='submit':return dict(session=serialize(conn,user_id,row),replayed=True,saved_version=row['version'])
            raise PracticeError('模考已结束，无法修改答案；请恢复服务器报告',409)
        if row['version']!=version:raise PracticeError('另一标签页已保存新版本，请恢复服务器答案后继续',409)
        if kind=='save' and not patch:raise ValueError('Empty save')
        merged={**row['answers'],**patch}
        if len(json.dumps(merged,ensure_ascii=False).encode())>18000:raise ValueError('Answer body too large')
        report=grade(row['snapshot'],merged) if kind=='submit' else None
        conn.execute('''UPDATE learning_mock_sessions SET answers=%s,version=version+1,status=%s,
            finished_at=CASE WHEN %s THEN clock_timestamp() ELSE NULL END,report=%s
            WHERE user_id=%s AND session_id=%s''',
            (Jsonb(merged),'submitted' if kind=='submit' else 'active',kind=='submit',Jsonb(report) if report else None,user_id,session_id))
        inserted=conn.execute('''INSERT INTO learning_mock_writes(user_id,request_id,session_id,kind,expected_version,payload_hash,saved_version)
            VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING request_id''',
            (user_id,request_id,session_id,kind,version,signature,version+1)).fetchone()
        if inserted is None:raise PracticeError('保存编号冲突，请恢复服务器状态',409)
        return dict(session=serialize(conn,user_id,fetch(conn,user_id,session_id)),replayed=False,saved_version=version+1)


def history(connection,user_id,exam,offset=0):
    with connection() as conn:
        active=conn.execute("SELECT session_id FROM learning_mock_sessions WHERE user_id=%s AND status='active' FOR UPDATE",(user_id,)).fetchone()
        active_id=None
        if active:
            row=expire(conn,user_id,fetch(conn,user_id,active[0]))
            if row['status']=='active':active_id=str(row['session_id'])
        rows=conn.execute('''SELECT session_id,template_id,section,status,started_at,report,snapshot->>'title',snapshot->>'form' FROM learning_mock_sessions
            WHERE user_id=%s AND exam_type=%s ORDER BY started_at DESC,session_id DESC LIMIT 20 OFFSET %s''',(user_id,exam,offset)).fetchall()
        total=conn.execute('SELECT COUNT(*) FROM learning_mock_sessions WHERE user_id=%s AND exam_type=%s',(user_id,exam)).fetchone()[0]
    return dict(active_id=active_id,total=total,offset=offset,history=[dict(id=str(r[0]),template_id=r[1],section=r[2],status=r[3],started_at=r[4].isoformat(),
        objective_accuracy=r[5]['objective_accuracy'] if r[5] else None,title=r[6],form=r[7]) for r in rows])


def comparisons(connection,user_id,session):
    with connection() as conn:
        rows=conn.execute('''SELECT s.session_id,s.report,s.finished_at,
            (SELECT ROUND(AVG((a.feedback->>'score')::numeric),1) FROM learning_attempts a WHERE a.user_id=s.user_id
                AND a.status='completed' AND a.question_id LIKE 'mock-'||s.session_id::text||'-%%')
            FROM learning_mock_sessions s WHERE user_id=%s AND template_id=%s
            AND status<>'active' ORDER BY finished_at DESC,session_id DESC LIMIT 10''',(user_id,session['template_id'])).fetchall()
    return [dict(id=str(row[0]),objective_accuracy=row[1]['objective_accuracy'],finished_at=row[2].isoformat(),
                 ai_reference_average=float(row[3]) if row[3] is not None else None) for row in reversed(rows)]
