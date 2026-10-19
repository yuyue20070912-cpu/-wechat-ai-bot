/* Fixed media playback only. Never contacts a speech-generation endpoint. */
(() => {
  'use strict';
  function mount(root, info, legacyURL) {
    const note=document.createElement('p');note.className='practice-note';note.setAttribute('role','status');note.setAttribute('aria-live','polite');note.textContent=info.notice;root.append(note);
    if(info.status==='unavailable')return;
    const audio=document.createElement('audio');audio.controls=true;audio.preload='none';audio.setAttribute('aria-label','原创听力样例');
    let fallback=info.status!=='ready';
    const initialSource=fallback?legacyURL:info.url;
    audio.dataset.audioSrc=initialSource;
    const play=document.createElement('button');play.type='button';play.textContent='播放听力';
    // A direct user gesture is required on iPhone Safari, including after fallback.
    play.onclick=()=>{
      if(!audio.getAttribute('src'))audio.src=initialSource;
      if(audio.error)audio.load();
      const source=audio.src;
      audio.play().then(()=>{play.textContent='再次播放 / 继续';}).catch(()=>{
        if(audio.src===source&&!audio.error)note.textContent='播放尚未开始，请检查静音/音量并点击播放或使用原生播放控件。';
      });
    };
    audio.addEventListener('error',()=>{
      if(!audio.isConnected)return;
      if(!fallback&&info.has_fallback){
        fallback=true;audio.src=legacyURL;audio.load();
        note.textContent='固定 MP3 加载失败，已切换原有 eSpeak 合成样例；请再次点击播放。';
      }else{
        note.textContent='音频加载失败。请检查网络后点击重试；不会自动生成或调用收费语音服务。';
        play.textContent='重试播放';
      }
    });
    root.append(audio,play);
  }
  window.LearningAudio={mount};
})();
