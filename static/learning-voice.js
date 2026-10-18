/* Native MediaRecorder with Safari MP4 negotiation. No browser speech/cloud API. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let exam, csrf, settings, current, stream, recorder, chunks = [], blob, url;
  let timer, preparation, started, busy = false, generation = 0, answerRequest;
  const uuid = () => {
    const a = crypto.getRandomValues(new Uint8Array(16)); a[6] = (a[6] & 15) | 64; a[8] = (a[8] & 63) | 128;
    return Array.from(a, (b, i) => ([4,6,8,10].includes(i) ? '-' : '') + b.toString(16).padStart(2,'0')).join('');
  };
  const note = text => { $('voice-status').textContent = text; };
  async function api(path, method = 'GET', body) {
    const raw = body instanceof Blob;
    const response = await fetch('/api/learning/' + path, {method, credentials:'same-origin',
      headers: method === 'GET' ? {} : {'X-Learning-CSRF':csrf, 'Content-Type':raw ? 'application/octet-stream' : 'application/json'},
      body:method === 'GET' ? undefined : raw ? body : JSON.stringify(body)});
    if (response.status === 401) { location.assign('/login'); throw new Error('请重新登录'); }
    const data = await response.json(); if (!response.ok) throw new Error(data.error || '语音服务暂不可用'); return data;
  }
  function controls() {
    const recording = recorder && recorder.state !== 'inactive';
    $('voice-record').disabled = busy || recording || !current || current.completed || !$('voice-consent').checked;
    $('voice-stop').disabled = !recording;
    $('voice-transcribe').disabled = busy || recording || !blob || !settings?.transcription_enabled || current?.completed;
    $('voice-next').disabled = busy || recording || current?.completed;
    $('voice-start').disabled = busy || recording;
    $('voice-resume').disabled = busy || recording;
    $('voice-delete').disabled = busy || recording;
    $('voice-read-prompt').disabled = busy || recording || !current || current.completed;
    document.body.dataset.voiceBusy = busy || recording ? 'true' : 'false';
    document.querySelectorAll('[data-exam]').forEach(button => {
      button.disabled = busy || recording || ['practiceBusy','hubBusy','mockBusy'].some(k => document.body.dataset[k] === 'true');
    });
  }
  async function act(fn) {
    if (busy) return; busy = true; controls();
    try { await fn(); } catch (error) { note(error.message + '。不会自动重试付费调用；可保留文字回答继续。'); }
    finally { busy = false; controls(); }
  }
  function release() { if (stream) stream.getTracks().forEach(track => track.stop()); stream = null; clearInterval(timer); }
  function clearAudio() {
    generation++; if (recorder && recorder.state !== 'inactive') recorder.stop(); release();
    recorder = null; chunks = []; blob = null;
    if (url) URL.revokeObjectURL(url); url = null;
    $('voice-preview').pause(); $('voice-preview').removeAttribute('src'); $('voice-preview').hidden = true;
    controls();
  }
  function stop() { if (recorder && recorder.state !== 'inactive') recorder.stop(); release(); }
  function paragraph(parent, text, lang) { const p = document.createElement('p'); p.textContent = text; if (lang) p.lang = lang; parent.append(p); }
  function feedback(parent, data) {
    parent.replaceChildren();
    paragraph(parent, data.notice);
    const labels = {content:'回答内容',grammar:'语法',vocabulary:'词汇',coherence:'文字连贯性',revised_answer:'表达示例',follow_up:'AI 考官追问（可在文字专项继续练习）',next_step:'下一步'};
    for (const [key,label] of Object.entries(labels)) paragraph(parent, label + '：' + data[key]);
  }
  function render(data) {
    clearAudio(); clearInterval(preparation); current = data; answerRequest = null;
    $('voice-dialog').hidden = false; $('voice-answer').value = data.transcript || '';
    $('voice-part').textContent = data.completed ? '分段模拟已完成' : `Part ${data.part} · 第 ${data.version + 1} / 7 题`;
    $('voice-prompt').textContent = data.prompt || '回答已保存。可查看下方记录，按需请求 AI 文字反馈。';
    $('voice-clock').textContent = ''; $('voice-prepare').hidden = data.part !== 2;
    $('voice-turns').replaceChildren();
    data.answers.forEach(turn => {
      const section = document.createElement('section'); section.className = 'voice-turn';
      paragraph(section, `Part ${turn.part} · ${turn.prompt}`, 'en'); paragraph(section, turn.answer, 'en');
      const button = document.createElement('button'); button.type = 'button'; button.textContent = '可选 AI 文字反馈与追问 · 1 条消息额度';
      button.disabled = !settings.feedback_enabled;
      const result = document.createElement('div'); result.setAttribute('aria-live','polite');
      if (turn.feedback) feedback(result,turn.feedback);
      button.addEventListener('click', () => act(async () => {
        const response = await api(`voice/sessions/${current.session_id}/feedback/${turn.index}`, 'POST', {});
        feedback(result, response.feedback); note('已获取文字反馈。重复查看同一回答不会重复扣费。');
        const updated = (await api('voice/sessions/' + current.session_id)).session;
        if (updated.version === current.version && !current.completed) {
          if (!$('voice-answer').value.trim()) { current = updated; $('voice-prompt').textContent = updated.prompt; }
          else if (updated.prompt !== current.prompt) note('AI 追问已生成。请先复制当前文字草稿，再恢复会话查看新问题。');
        }
        document.dispatchEvent(new Event('learning:data-changed'));
      })); section.append(button,result); $('voice-turns').append(section);
    });
    controls();
  }
  async function loadSettings() {
    settings = await api('voice');
    $('voice-budget').textContent = `转写${settings.transcription_enabled ? '已启用' : '默认关闭 / 待真实接口验证'}；AI 文字反馈${settings.feedback_enabled ? '已启用' : '默认关闭'}。今日语音预算 ${settings.seconds_used}/${settings.user_seconds_limit} 秒（UTC 重置）。转写和文字反馈分别消耗 1 条现有消息额度；失败退消息额度，调用与时长预算不退。`;
    $('voice-history').replaceChildren(); settings.sessions.forEach(id => { const o = document.createElement('option'); o.value = id; o.textContent = '会话 ' + id.slice(0,8); $('voice-history').append(o); });
  }
  document.addEventListener('learning:exam', async event => {
    csrf = event.detail.csrf;
    if (exam === event.detail.exam.id) return;
    clearAudio(); clearInterval(preparation); current = null;
    exam = event.detail.exam.id; csrf = event.detail.csrf; $('voice-center').hidden = false;
    $('voice-ielts').hidden = exam !== 'ielts'; $('voice-listening').hidden = exam === 'ielts'; $('voice-dialog').hidden = true;
    $('voice-example').pause(); $('voice-example').src = '/api/learning/voice/examples/' + $('voice-accent').value;
    const selected = exam;
    await act(async () => {
      await loadSettings(); if (selected !== exam) return;
      if (exam !== 'ielts') {
        const catalog = await api('mock-catalog/' + exam); $('voice-listening-options').replaceChildren();
        catalog.templates.filter(t => t.section === 'listening').forEach(t => {
          const button = document.createElement('button'); button.type = 'button'; button.textContent = t.title + ' · ' + t.form;
          button.addEventListener('click', () => {
            $('mock-template').value = t.id; $('mock-start').click(); $('mock-center').scrollIntoView({behavior:'smooth'});
          }); $('voice-listening-options').append(button);
        });
      } note('可使用本机录音试听、文字回答与本地听力。付费功能须单独启用。');
    });
  });
  $('voice-accent').addEventListener('change', () => { $('voice-example').pause(); $('voice-example').src = '/api/learning/voice/examples/' + $('voice-accent').value; });
  $('voice-read-prompt').addEventListener('click', () => {
    if (!current || current.completed || busy || (recorder && recorder.state !== 'inactive')) return;
    const synthesis = window.speechSynthesis, language = $('voice-accent').value === 'gb' ? 'en-gb' : 'en-us';
    const voice = synthesis?.getVoices().find(v => v.localService && v.lang.toLowerCase().replace('_','-') === language);
    if (!voice) { note('设备没有匹配的本地英美音色，请阅读文字问题；不会调用云端 TTS。'); return; }
    synthesis.cancel(); const utterance = new SpeechSynthesisUtterance(current.prompt);
    utterance.voice = voice; utterance.lang = voice.lang; utterance.rate = 0.9;
    utterance.onerror = () => note('设备朗读不可用，请继续阅读文字问题。'); synthesis.speak(utterance);
  });
  $('voice-start').addEventListener('click', () => act(async () => { render((await api('voice/sessions','POST',{session_id:uuid()})).session); await loadSettings(); }));
  $('voice-resume').addEventListener('click', () => act(async () => { if ($('voice-history').value) render((await api('voice/sessions/' + $('voice-history').value)).session); }));
  $('voice-consent').addEventListener('change', () => { if (!$('voice-consent').checked) clearAudio(); controls(); });
  $('voice-record').addEventListener('click', async () => {
    if (busy || !$('voice-consent').checked || !current || current.completed) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { note('浏览器或当前连接不支持录音，请切换文字模式。Safari 需要 HTTPS 与麦克风权限。'); return; }
    clearAudio(); window.speechSynthesis?.cancel(); $('voice-example').pause(); busy = true; controls(); const capture = generation;
    try {
      const acquired = await navigator.mediaDevices.getUserMedia({audio:true,video:false});
      if (capture !== generation || !$('voice-consent').checked || document.hidden) { acquired.getTracks().forEach(t => t.stop()); return; }
      stream = acquired;
      const mime = ['audio/mp4','audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus'].find(t => MediaRecorder.isTypeSupported(t));
      if (!mime) { release(); note('当前浏览器没有受支持的录音格式，请使用文字模式。'); return; }
      recorder = new MediaRecorder(stream,{mimeType:mime,audioBitsPerSecond:64000}); chunks = []; let size = 0;
      recorder.ondataavailable = e => { if (capture !== generation) return; if (e.data.size) { chunks.push(e.data); size += e.data.size; if (size > 4*1024*1024) stop(); } };
      recorder.onerror = () => { clearAudio(); note('录音失败，请检查权限或使用文字模式。'); };
      recorder.onstop = () => {
        if (capture !== generation) return; release();
        if (size > 4*1024*1024 || !size) { note('录音过大或为空，请重新录制或输入文字。'); controls(); return; }
        blob = new Blob(chunks,{type:mime}); url = URL.createObjectURL(blob); $('voice-preview').src = url; $('voice-preview').hidden = false;
        note('录音已停止，仅保存在本页。请试听后主动选择上传转写，或切换文字。'); controls();
      };
      recorder.start(500); started = performance.now();
      timer = setInterval(() => { const seconds = Math.floor((performance.now()-started)/1000); $('voice-clock').textContent = `录音 ${seconds} / 120 秒`; if (seconds >= 120) stop(); },250);
      note('正在录音；停止后才能上传。');
    } catch (error) { release(); note(error.name === 'NotAllowedError' ? '麦克风权限被拒绝。请在 Safari 网站设置中允许，或直接使用文字模式。' : '麦克风不可用，请使用文字模式。'); }
    finally { busy = false; controls(); }
  });
  $('voice-stop').addEventListener('click',stop);
  $('voice-clear').addEventListener('click', () => { clearAudio(); note('已清除本机录音。'); });
  $('voice-text-mode').addEventListener('click', () => { clearAudio(); $('voice-answer').focus(); note('文字模式：直接输入回答，无需麦克风。'); });
  $('voice-transcribe').addEventListener('click', () => act(async () => {
    if (!blob || !current || current.completed || !settings.transcription_enabled) return;
    const data = await api(`voice/sessions/${current.session_id}/transcribe/${current.version}`,'POST',blob);
    $('voice-answer').value = data.feedback.transcript; note(data.feedback.notice); await loadSettings();
    document.dispatchEvent(new Event('learning:data-changed'));
  }));
  $('voice-answer-form').addEventListener('submit', e => { e.preventDefault(); act(async () => {
    const answer = $('voice-answer').value.trim(); if (!answer || !current || current.completed) return;
    const fingerprint = current.version + ':' + current.prompt + ':' + answer;
    if (!answerRequest || answerRequest.fingerprint !== fingerprint) answerRequest = {fingerprint,id:uuid()};
    const data = await api(`voice/sessions/${current.session_id}/answers`,'POST',{request_id:answerRequest.id,expected_version:current.version,answer,prompt:current.prompt});
    render(data.session); note('回答已保存；重新登录后可恢复会话。');
  }); });
  $('voice-prepare').addEventListener('click', () => {
    clearInterval(preparation); const deadline = performance.now() + 60000;
    preparation = setInterval(() => { const seconds = Math.max(0,Math.ceil((deadline-performance.now())/1000)); $('voice-clock').textContent = seconds ? `准备剩余 ${seconds} 秒` : '准备结束，可以开始回答（最多 120 秒）'; if (!seconds) clearInterval(preparation); },250);
  });
  $('voice-delete').addEventListener('click', () => act(async () => {
    if (!current || !window.confirm('删除此会话、转写与反馈？已消耗的预算不会重置。')) return;
    await api('voice/sessions/' + current.session_id,'DELETE',{}); clearAudio(); current = null; $('voice-dialog').hidden = true; await loadSettings(); note('会话及关联内容已删除，调用预算保留。');
  }));
  window.addEventListener('pagehide', () => { clearAudio(); clearInterval(preparation); $('voice-example').pause(); window.speechSynthesis?.cancel(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) { stop(); window.speechSynthesis?.cancel(); } });
})();
