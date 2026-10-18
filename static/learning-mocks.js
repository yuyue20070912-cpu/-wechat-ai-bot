/* Server-authoritative timed mocks. Offline drafts live only in this tab, scoped to the authenticated account. */
(() => {
  'use strict';
  const el=id=>document.getElementById(id), text=(tag,value,root)=>{const n=document.createElement(tag);n.textContent=value;root.append(n);return n;};
  let exam=null,csrf='',scope='',current=null,activeId=null,aiEnabled=false,historyOffset=0,generation=0;
  let dirty={},pending=null,flight=null,conflict=false,clockEnd=0,expiredRequested=false,debounce=null,starting=null;
  const uuid=()=>{const b=crypto.getRandomValues(new Uint8Array(16));b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;const h=Array.from(b,x=>x.toString(16).padStart(2,'0')).join('');return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;};
  const status=message=>{el('mock-status').textContent=message;};
  const cacheKey=()=>scope&&current?`fishyeah-mock:${scope}:${current.id}`:null;
  function persist(){try{const key=cacheKey();if(key)sessionStorage.setItem(key,JSON.stringify({dirty,pending,baseVersion:current.version}));}catch(_){status('本标签页草稿暂存不可用，请保持联网并确认同步状态。');}}
  function clearCache(){try{const key=cacheKey();if(key)sessionStorage.removeItem(key);}catch(_){} }
  function busy(value){document.body.dataset.mockBusy=String(value);document.querySelectorAll('.exam').forEach(button=>{button.disabled=value||document.body.dataset.hubBusy==='true'||document.body.dataset.practiceBusy==='true';});}
  async function api(path,method='GET',body,timeout=10000){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);
    try{
      const response=await fetch('/api/learning/'+path,{method,credentials:'same-origin',cache:'no-store',signal:controller.signal,
        headers:method==='GET'?{}:{'Content-Type':'application/json','X-Learning-CSRF':csrf},body:body===undefined?undefined:JSON.stringify(body)});
      const value=await response.json().catch(()=>({}));
      if(!response.ok){
        if(response.status===401){clearCache();el('mock-active').hidden=true;el('mock-report').hidden=true;}
        const error=Error(response.status===401?'请重新登录后恢复模考。':value.error||'模考服务暂不可用。');error.status=response.status;throw error;
      }return value;
    }finally{clearTimeout(timer);}
  }
  function setClock(data){clockEnd=performance.now()+data.remaining_seconds*1000;expiredRequested=false;tick();}
  function draftValue(q){return Object.prototype.hasOwnProperty.call(dirty,q.id)?dirty[q.id]:current.answers[q.id];}
  function renderActive(data){
    el('mock-active').hidden=false;el('mock-report').hidden=true;
    el('mock-title').textContent=`${data.exam_type.toUpperCase()} · ${data.template.title} · 题组 ${data.template.form.toUpperCase()}`;
    el('mock-save-policy').textContent=data.template.notice+' '+data.save_policy;
    const material=el('mock-material');material.replaceChildren();
    if(data.template.material)text('p',data.template.material,material);
    if(data.template.has_audio){
      text('p',data.template.audio_notice,material).className='practice-note';
      const audio=document.createElement('audio');audio.controls=true;audio.preload='metadata';audio.src=`/api/learning/mocks/${data.id}/audio`;audio.setAttribute('aria-label','原创听力样例');material.append(audio);
    }
    const root=el('mock-questions');root.replaceChildren();
    data.template.questions.forEach(q=>{
      const field=document.createElement('fieldset');text('legend',q.prompt,field);
      if(q.kind==='objective'){
        q.options.forEach((option,index)=>{
          const label=document.createElement('label'),input=document.createElement('input');input.type='radio';input.name='mock-'+q.id;input.value=index;input.checked=draftValue(q)===index;
          input.onchange=()=>changed(q.id,index);label.append(input,document.createTextNode(option));field.append(label);
        });
      }else{
        if(q.guidance)text('p',q.guidance,field).className='practice-note';
        const label=text('label','模考答案',field),input=document.createElement('textarea');input.id='mock-answer-'+q.id;label.htmlFor=input.id;input.rows=10;input.maxLength=4000;input.value=draftValue(q)||'';
        input.oninput=()=>changed(q.id,input.value);field.append(input);
      }root.append(field);
    });
    el('mock-submit').disabled=false;setClock(data);
  }
  function changed(key,value){
    if(!current||current.status!=='active'||performance.now()>=clockEnd)return;
    dirty[key]=value;persist();status('本页有未同步答案，正在自动保存…');clearTimeout(debounce);debounce=setTimeout(()=>flush(),500);
  }
  async function renderReport(data,comparisons=[]){
    el('mock-active').hidden=true;el('mock-report').hidden=false;
    const root=el('mock-report-content');root.replaceChildren();
    text('p',`${data.exam_type.toUpperCase()} · ${data.template.title} · ${data.status==='expired'?'计时结束（仅计已同步答案）':'已交卷'}`,root);
    text('p',data.report.notice,root).className='practice-note';text('p',data.report.score_policy,root).className='practice-note';
    text('h4',data.report.objective_accuracy===null?'主观题：默认不评分':`客观题：${data.report.objective_correct}/${data.report.objective_total} · 正确率 ${data.report.objective_accuracy}%`,root);
    if(data.template.transcript){const details=document.createElement('details');text('summary','查看听力转写',details);text('p',data.template.transcript,details);root.append(details);}
    data.report.details.forEach(detail=>{
      const q=data.template.questions.find(q=>q.id===detail.question_id),card=document.createElement('article');card.className='mock-report-item';
      text('h4',q.prompt,card);const answer=q.kind==='objective'?(q.options[detail.answer]??'未作答'):(detail.answer||'未作答');
      text('p','我的答案：'+answer,card);text('p',detail.correct===null?'主观表达 · 不计入客观正确率':detail.correct?'客观题正确':'客观题错误 / 未作答',card);
      text('p','参考：'+detail.reference,card);text('p',detail.explanation,card);
      if(q.kind==='subjective'){
        const analysis=data.ai_results.find(x=>x.question_id===q.id);
        if(analysis?.feedback){
          text('h4',`AI 学习参考分 ${analysis.feedback.score}/100（非官方）`,card);text('p',analysis.feedback.summary,card);
          analysis.feedback.improvements.forEach(s=>text('p',s,card));text('p',analysis.feedback.revised_answer,card);
        }else{
          const button=text('button',analysis?.status==='pending'?'AI 参考评分处理中':analysis?.status==='refunded'?'AI 未完成，查看额度退款状态':aiEnabled?'可选 AI 参考评分 · 消耗 1 条消息':'AI 参考评分默认关闭',card);button.type='button';
          button.disabled=!aiEnabled||!detail.answer||Boolean(analysis&&analysis.status!=='not_requested');
          button.onclick=async()=>{if(flight)return;busy(true);button.disabled=true;status('正在获取 AI 参考评分…');
            try{await api(`mocks/${data.id}/ai/${q.id}`,'POST',{},65000);await open(data.id,false);status('AI 参考评分已保存，不是官方成绩。');}
            catch(error){status(error.message+' 可刷新本场报告确认状态，失败不会自动重试。');}
            finally{busy(false);}
          };
        }
      }root.append(card);
    });
    text('h4','同题组历史（不跨题组推断水平变化）',root);
    if(comparisons.length<2)text('p','同题组记录不足两次，暂不比较。',root);
    comparisons.forEach(point=>text('p',`${new Date(point.finished_at).toLocaleString('zh-CN')} · ${point.objective_accuracy===null?'无客观题':point.objective_accuracy+'% 客观正确率'} · ${point.ai_reference_average===null?'未产生 AI 主观参考分':point.ai_reference_average+'/100 AI 主观参考均分（非官方）'}`,root));
    text('p','重复同题组可能存在记忆效应；本记录不等同完整考试或官方成绩。',root).className='practice-note';
  }
  async function open(id,restore=true){
    const data=await api('mocks/'+id);current=data.session;dirty={};pending=null;conflict=false;clearTimeout(debounce);
    if(current.status==='active'){
      if(restore){
        try{const cached=JSON.parse(sessionStorage.getItem(cacheKey())||'null');if(cached){dirty=cached.dirty||{};pending=cached.pending||null;conflict=cached.baseVersion!==current.version&&!pending;}}catch(_){}
      }
      renderActive(current);
      status(conflict?'服务器版本已变化。本页草稿已保留，请明确恢复服务器状态后继续。':Object.keys(dirty).length||pending?'已恢复本标签页草稿，请重试同步；倒计时仍继续。':'已恢复服务器答案，计时继续。');
      persist();if(!conflict&&(pending||Object.keys(dirty).length))flush();
    }else{clearCache();await renderReport(current,data.comparisons);}
  }
  function flush(){
    if(flight)return flight;
    if(!current||current.status!=='active'||conflict)return Promise.resolve(false);
    if(!pending){if(!Object.keys(dirty).length)return Promise.resolve(true);pending={kind:'save',body:{request_id:uuid(),expected_version:current.version,answers:{...dirty}}};persist();}
    const sent=pending,sid=current.id;busy(true);
    flight=(async()=>{
      try{
        const data=await api(`mocks/${sid}/${sent.kind==='submit'?'submit':'answers'}`,sent.kind==='submit'?'POST':'PUT',sent.body);
        if(!current||current.id!==sid)return false;
        current=data.session;Object.keys(sent.body.answers).forEach(key=>{if(dirty[key]===sent.body.answers[key])delete dirty[key];});pending=null;
        if(current.status!=='active'){clearCache();await open(sid,false);await loadHistory();status('已交卷，报告已保存。');}
        else{persist();setClock(current);status('答案已同步到服务器。');}
        return true;
      }catch(error){
        if(error.status===409)conflict=true;
        if(error.status===400||error.status===413)pending=null;
        persist();status((error.status===409?'保存冲突，请恢复服务器状态；本页草稿仍保留。':'同步未确认，草稿仍保留。')+' '+error.message);return false;
      }finally{flight=null;busy(false);}
    })();
    flight.then(ok=>{if(ok&&current?.status==='active'&&Object.keys(dirty).length)debounce=setTimeout(()=>flush(),200);});
    return flight;
  }
  function tick(){
    if(!current||current.status!=='active')return;
    const seconds=Math.max(0,Math.ceil((clockEnd-performance.now())/1000));
    el('mock-clock').textContent=`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
    if(seconds===0){
      el('mock-questions').querySelectorAll('input,textarea').forEach(input=>{input.disabled=true;});el('mock-submit').disabled=true;
      if(!expiredRequested&&!flight){expiredRequested=true;open(current.id,false).then(()=>{status('计时已结束，只统计已同步答案。');loadHistory();}).catch(()=>status('计时已结束，待联网后恢复服务器报告；草稿不会延长考试时间。'));}
    }
  }
  setInterval(tick,250);
  // A long uninterrupted writing session must still reach the server, even without a typing pause.
  setInterval(()=>{if(navigator.onLine&&current?.status==='active'&&!flight&&!pending&&!conflict&&Object.keys(dirty).length)flush();},5000);
  async function loadHistory(){
    if(!exam)return;
    const data=await api(`mocks?exam=${exam.id}&offset=${historyOffset}`);
    if(scope&&scope!==data.storage_scope){current=null;dirty={};pending=null;el('mock-active').hidden=true;el('mock-report').hidden=true;}
    scope=data.storage_scope;activeId=data.active_id;aiEnabled=data.ai_enabled;
    el('mock-resume').hidden=!activeId;el('mock-history-prev').disabled=historyOffset===0;el('mock-history-next').disabled=historyOffset+20>=data.total;
    const root=el('mock-history-content');root.replaceChildren();
    if(!data.history.length)text('p','还没有模考记录。',root);
    data.history.forEach(item=>{const row=document.createElement('article');row.className='history-item';
      text('p',`${exam.id.toUpperCase()} · ${item.title} · 题组 ${item.form.toUpperCase()} · ${item.status==='active'?'进行中':item.status==='expired'?'计时结束':'已交卷'} · ${item.objective_accuracy===null?'客观分未产生 / 无客观题':item.objective_accuracy+'% 客观正确率'}`,row);
      const button=text('button',item.status==='active'?'恢复模考':'查看报告',row);button.type='button';button.onclick=()=>open(item.id).catch(error=>status(error.message));root.append(row);
    });
  }
  document.addEventListener('learning:exam',async event=>{
    csrf=event.detail.csrf;
    if(exam?.id===event.detail.exam.id)return;
    if(current?.status==='active')persist();exam=event.detail.exam;historyOffset=0;const version=++generation;
    el('mock-center').hidden=false;
    try{
      const catalog=await api('mock-catalog/'+exam.id);if(version!==generation)return;
      const select=el('mock-template');select.replaceChildren();
      catalog.templates.forEach(t=>{const option=text('option',`${t.title} · 题组 ${t.form.toUpperCase()} · ${t.duration_seconds/60} 分钟 · ${t.question_count} 题`,select);option.value=t.id;});
      await loadHistory();
    }catch(error){status(error.message);}
  });
  el('mock-start').onclick=async()=>{
    if(flight||document.body.dataset.mockBusy==='true')return;
    if(!starting||starting.template_id!==el('mock-template').value)starting={session_id:uuid(),template_id:el('mock-template').value};
    busy(true);try{const data=await api('mocks','POST',starting);starting=null;await open(data.session.id);await loadHistory();}
    catch(error){status(error.message+' 可用“恢复进行中的模考”继续。');await loadHistory().catch(()=>{});}finally{busy(false);}
  };
  el('mock-resume').onclick=()=>{if(activeId)open(activeId).catch(error=>status(error.message));};
  el('mock-answer-form').onsubmit=async event=>{
    event.preventDefault();if(!current||conflict)return;clearTimeout(debounce);
    if(flight)await flight;if(pending){status('请先重试同步未确认的请求，再交卷。');return;}
    if(current.status!=='active')return;
    if(!confirm('确认结束本分项模考？交卷后不能修改答案。'))return;
    pending={kind:'submit',body:{request_id:uuid(),expected_version:current.version,answers:{...dirty}}};persist();flush();
  };
  el('mock-retry').onclick=()=>flush();
  el('mock-reload').onclick=async()=>{
    if(!current||flight)return;
    if((pending||Object.keys(dirty).length)&&!confirm('恢复服务器状态会丢弃本页尚未同步的草稿，继续吗？'))return;
    try{const id=current.id;await api('mocks/'+id);clearCache();await open(id,false);}catch(error){status(error.message);}
  };
  el('mock-history-refresh').onclick=()=>loadHistory().catch(error=>status(error.message));
  el('mock-history-prev').onclick=()=>{historyOffset=Math.max(0,historyOffset-20);loadHistory().catch(error=>status(error.message));};
  el('mock-history-next').onclick=()=>{historyOffset+=20;loadHistory().catch(error=>status(error.message));};
  window.addEventListener('online',()=>{if(current?.status==='active'){expiredRequested=false;flush();}loadHistory().catch(()=>{});});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)persist();else if(current?.status==='active'){expiredRequested=false;tick();}});
})();
