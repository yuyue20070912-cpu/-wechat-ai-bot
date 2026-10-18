/* NDJSON frames may cross network chunks, including inside a UTF-8 character. */
async function readChatStream(body, onEvent) {
  if (!body) throw Error('浏览器不支持流式读取');
  const reader = body.getReader(), decoder = new TextDecoder();
  let buffer = '', done = false;
  function consume(line) {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.type === 'error') throw Error(event.error);
    if (event.type === 'done') done = true;
    onEvent(event);
  }
  try {
    while (true) {
      const chunk = await reader.read();
      buffer += decoder.decode(chunk.value, {stream: !chunk.done});
      let end;
      while ((end = buffer.indexOf('\n')) >= 0) {
        consume(buffer.slice(0, end)); buffer = buffer.slice(end + 1);
        if (done) return;
      }
      if (chunk.done) break;
    }
    if (buffer.trim()) consume(buffer);
    if (!done) throw Error('连接中断，请稍后核对额度后重试');
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
async function streamChat(message, onEvent) {
  const response = await fetch('/chat', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message, stream: true, request_id: crypto.randomUUID()})
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw Error(data.error || '请求失败');
  }
  await readChatStream(response.body, onEvent);
}
if (typeof module !== 'undefined') module.exports = {readChatStream};
