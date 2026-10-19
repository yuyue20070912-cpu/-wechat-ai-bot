# 第五阶段：语音伴学

继续 feature/ai-learning-platform / Draft PR #8。保留文字训练、聊天、登录、会员与支付；未部署、未操作正式数据库。

## 能力核实与发布边界

- 已查阅 [OpenAI Transcriptions 官方接口](https://developers.openai.com/api/reference/python/resources/audio/subresources/transcriptions/methods/create)：`gpt-4o-mini-transcribe` 通过 `audio.transcriptions.create` 使用音频文件与 `response_format=json`；不是现有文字 Responses 模型的音频能力。
- 已检查安装的 Python SDK 真实方法签名；自动化测试使用真实 OpenAI SDK + MockTransport 检查 multipart 路径、模型、WAV 文件和 JSON 参数。此测试不访问供应商、不收费，不代表真实服务验证。
- **当前环境没有 OpenAI 凭据，尚未完成真实模型探测。** 付费转写仍关闭。准备了 `tools/probe_voice_api.py`，仅使用仓库内原创约十秒样例，显式 `LEARNING_VOICE_PROBE=1` 与测试 API key 才发送一次请求，超时45秒、零重试、不访问任何数据库。探测成功后，由运维记录日期、模型、测试项目、结果，再配置 `LEARNING_VOICE_VERIFIED_MODEL=gpt-4o-mini-transcribe`。探测脚本不自动启用应用。
- 没有可靠的音素/声学发音分析验证。因此**任何界面和结构化结果均不提供发音分数**。文字反馈只分析内容、语法、词汇、文字连贯性，不测真实发音或口语流利度，不换算 IELTS band / CET 官方成绩。

## 训练流程

- IELTS 七轮缩短版：Part 1 三问、Part 2 一道两分钟话题卡（可启动60秒准备计时）、Part 3 三问。题库考官默认可用，不伪称实时语音模型。可选 AI 依据已保存文字生成反馈与追问；同 Part 的下一题采用已生成的追问，总轮数仍为七，不无限延长对话。
- 本机录音 → 主动试听 → 可选上传转写 → 用户核对/编辑文字 → 保存并进入下一题。转写与文字反馈分别主动调用，每项最多一次/题，不自动重试。
- 考官问题可主动点击设备朗读：只选 SpeechSynthesis 标记 localService=true 且匹配 en-GB/en-US 的音色；没有本地匹配音色时保留文字，不隐式启用云 TTS。开始录音前取消朗读和示例播放。
- 转写失败可直接改用文字。刷新/重新登录可从账户恢复回答与已有反馈，也可恢复当前题已成功转写的文字；本机原始录音不恢复。
- CET-4 / CET-6 听力专项入口复用上一阶段原创建制音频、答题、解析、自动保存、倒计时及断线恢复，不新增付费播放。英式/美式同句合成示例为本地生成的 MP3，不做付费 TTS，不克隆真人声音。
- 会话仅归当前登录用户；写操作沿用 CSRF、来源检查和速率限制。删除会话清除回答/转写/反馈，保留脱敏的计费去重账本和预算，避免删除后额度回滚或重复退款。

## 成本、并发和失败

所有付费功能默认关闭：

| 配置 | 默认值 / 硬上限 | 作用 |
|---|---|---|
| LEARNING_VOICE_ENABLED | 0 | 付费转写总开关 |
| LEARNING_VOICE_VERIFIED_MODEL | 空 | 必须为真实验证过的固定转写模型 |
| LEARNING_VOICE_FEEDBACK_ENABLED | 0 | 口语文字反馈独立开关，且要求 LEARNING_AI_ENABLED=1 |
| LEARNING_VOICE_USER_SECONDS | 600 / 1800 | 每用户每日上传音频秒数预算 |
| LEARNING_VOICE_GLOBAL_SECONDS | 3600 / 14400 | 全站每日上传音频秒数预算 |
| LEARNING_VOICE_CONCURRENCY | 2 / 4 | 全站语音转写+口语反馈同时在途数 |

另共同受已有 LEARNING_USER_DAILY_CALLS（10/50）、LEARNING_GLOBAL_DAILY_CALLS（200/1000）、会员/免费消息余额限制。预算是调用次数和音频秒数上限，不是供应商实时美元账单；模型固定，默认无任何支出，启用前应按供应商当时价格核算预算。文字反馈输出最多1600 tokens。

事务内一次性预留：共用的每用户单在途唯一索引、全站语音 advisory lock、秒数、全站/用户次数和原有消息额度。供应商请求在事务外，45秒、无重试。成功回放不重复扣款；同题不同音频指纹拒绝。失败只退消息余额一次，调用和秒数预算不退（供应商失败也可能收费）；崩溃沿用五分钟过期恢复和 learning-recover。删除进行中请求被拒绝。语音账本不会混入文字参考分趋势、学习计划能力分或错题自动导入。

## 音频与隐私

- 每段0.5–120秒、4 MiB；浏览器约119秒自动停录，为编码尾帧预留一秒余量。服务端不相信扩展名、MIME 或浏览器声称的时长：ffmpeg 白名单容器解码为16kHz单声道 WAV，最多解码121秒并检查真实PCM时长；拒绝播放列表、禁网络协议、限制单次内存分配、15秒进程超时、单线程、临时目录退出清理。超长、损坏和过大内容在扣费前拒绝。
- ffmpeg 是服务端依赖（CI 显式安装），未安装时转写能力关闭。发布前镜像需通过解码自测；本阶段没有修改或部署正式 Render 服务。
- 浏览器不使用 SpeechRecognition 或隐式云转写；不在 localStorage/sessionStorage 保存录音。仅主动勾选同意并点击录音后请求麦克风；上传须另行点击。停录、撤回同意、切换模式/考试、隐藏页面或离开时停止音轨。服务器不保存原始音频，文字按账户保存。供应商自身的数据保留政策仍适用，页面有链接和说明。
- 日志不输出录音、答案、供应商错误正文、API key。临时音频的生命周期限于请求内存/临时文件；没有宣称供应商零保留。

## Safari 验证范围

使用 MediaRecorder.isTypeSupported 协商 audio/mp4（Safari 优先）、WebM/Opus、Ogg/Opus。无 MediaRecorder、安全上下文或麦克风权限时保留文字模式。播放器使用原生 controls，用户手势播放，不自动播放；音频端点有登录隔离、Range/HEAD、no-store。

自动化覆盖 Chromium/WebKit 的 iPhone 宽度与 Mac 宽度、明暗外观：拒绝权限提示、无隐式权限请求、英美音 MP3 加载、文字模式/登录恢复、CET 听力入口。Chromium 以合成麦克风实际调用原生 MediaRecorder，验证录制、解码播放、音轨释放；本地 Linux WebKit 未暴露 MediaRecorder，验证缺能力时的文字回退，并用权限拒绝桩验证提示路径，不能宣称验证了 Safari 原生录音。若 CI WebKit 暴露录音 API，测试会实际执行录制分支。服务器另覆盖 Safari AAC/MP4 与 Chromium WebM/Opus 容器。

**WebKit 自动化不是 iOS Safari 真机验收。** 上线启用前仍需 iPhone Safari 和 Mac Safari 的真实 HTTPS 麦克风首次授权/撤回、系统中断/来电、锁屏、扬声器/蓝牙切换、静音模式和弱网检查；以及使用测试 key 完成一次真实 STT 探测。上述未完成项不阻塞默认关闭的文字模式交付，但阻塞付费语音启用。

## 数据库上线方案

仅新增005中的 learning_voice_sessions、learning_voice_budget 和历史索引，无旧表改列、无正式数据回填、无启动期DDL。继续使用 [迁移安全方案](learning-rollout-safety.md)：先在临时/预发布库验证从004升级、重复执行、故障事务回滚；人工维护窗口备份并验证恢复后，单独运行 learning-migrate（3秒锁超时、30秒语句超时、事务锁），检查新增表与旧记录后再切应用；语音开关保持0。失败不提升流量，回退应用保留新增表，不 DROP、不返还调用预算。本轮仅在 pgserver 临时数据库运行迁移及回滚测试。

## 本地自动化结果

- 全量 pytest：**256 passed**，包含全部既有聊天、登录、会员、支付及学习回归；新增33项语音后端、4组语音浏览器、1项第五阶段迁移测试。仅临时 PostgreSQL，无真实付费调用。
- 原聊天流式输出 Node 测试：**4 passed**。
- 四个学习交互脚本 node --check、Python compileall、git diff --check 通过。
- 在受限开发沙箱中，WebKit 的媒体缓存需获准写 /var/tmp；测试没有跳过音频播放断言。CI 使用正常可写临时目录。
- 真实 API 探测脚本在缺少测试凭据时明确返回 NOT VERIFIED，不发送请求。
