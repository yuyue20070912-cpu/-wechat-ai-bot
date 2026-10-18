const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readChatStream} = require('../static/chat-stream');
function body(text) {
 const bytes=new TextEncoder().encode(text);
 return new ReadableStream({start(c){for(const b of bytes)c.enqueue(Uint8Array.of(b));c.close();}});
}
test('Chinese UTF-8 and JSON split at every byte; final unterminated frame',async()=>{
 const events=[];
 await readChatStream(body('{"type":"delta","text":"你好🙂"}\n{"type":"done","remaining":4}'),e=>events.push(e));
 assert.equal(events[0].text,'你好🙂');assert.equal(events[1].remaining,4);
});
test('truncated response is not success',async()=>{
 await assert.rejects(readChatStream(body('{"type":"delta","text":"hi"}\n'),()=>{}),/连接中断/);
});
test('server error is surfaced',async()=>{
 await assert.rejects(readChatStream(body('{"type":"error","error":"额度不足"}\n'),()=>{}),/额度不足/);
});
