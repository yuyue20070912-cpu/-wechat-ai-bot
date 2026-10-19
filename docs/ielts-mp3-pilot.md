# IELTS 固定 MP3 试点（Issue #15）

**本 PR 的真实状态：五段文本、映射、播放器、缓存、回退与接入工具已实现；高质量 MP3 尚未生成/接入，未由真人试听。没有提交占位 MP3，没有调用收费 TTS API，当前首次 TTS 支出为 0。自然英式音质的验收仍需用户提供或批准真实录音后完成。**

## 现有音频审计

- `bot.py` 注册 learning 蓝图；音频路由实际位于 `learning/mock_routes.py`。
- `tools/generate_mock_audio.py` 读取 `learning/mock_bank.py` 原创文本，用 **eSpeak NG、en-us** 离线生成 WAV，再用 ffmpeg 转成 **48 kbps MP3**。这是明显电子音的来源，不是运行时 TTS。
- `learning/assets/mock_audio/` 已有 IELTS/CET-4/CET-6 各两段，共 6 个 MP3、1,025,145 字节。文件随 Git 发布，播放时不会生成，也没有临时音频清理任务。
- 原 `/api/learning/mocks/<session_id>/audio` 校验登录与模考归属后，只用 `send_from_directory` 提供快照指定文件，支持 Range；学习蓝图原来对所有响应设置 `no-store`，URL 按模考会话变化。
- `static/learning-mocks.js` 原先创建原生 audio，预载 metadata。现在对固定音频使用 `preload=none`，用户点击才加载/播放，保留原生控件。
- `static/learning-practice.js` 的专项 listening **仍为文本练习、当前无音频**，本次没有改动它或将其称作已支持听力播放。

## 五段原创文本与稳定映射

| 模考模板 | 音频 ID | 主题 | 尚无新 MP3 时 |
| --- | --- | --- | --- |
| `ielts-listening-a-short-v1` | `community-gardens-v1` | 社区花园活动安排 | 原有 IELTS A eSpeak 文件 |
| `ielts-listening-b-short-v1` | `public-libraries-v1` | 公共图书馆活动安排 | 原有 IELTS B eSpeak 文件 |
| `ielts-listening-c-short-v1` | `museum-volunteers-v1` | 博物馆志愿者培训 | 明确提示暂不能播放 |
| `ielts-listening-d-short-v1` | `campus-bicycles-v1` | 校园自行车租赁 | 明确提示暂不能播放 |
| `ielts-listening-e-short-v1` | `coastal-fieldwork-v1` | 海岸地理考察 | 明确提示暂不能播放 |

每段 3 道客观题，均为本项目原创 IELTS **风格**短练习，不是 IELTS 官方录音、真题或完整考试。A/B 原文、题目、模板 ID 不变；C/D/E 是本次新增原创材料。未使用第三方受版权保护录音、音乐或真人声音克隆。未来语音输出的使用权取决于用户选择的提供商、声音许可和条款，接入时必须填写来源与权利依据。

`learning/ielts_audio.py` 只在原文完全一致时给模考快照挂接新音频；旧快照原文不同则保留旧机制。不会把新音频替换到不同题目的历史考试中。

接入后文件路径为 `static/learning-audio/ielts/<audio-id>.<完整文件SHA-256>.mp3`，播放器使用 `/learning/audio/ielts/<同名文件>`。五个 URL 与用户和模考会话无关，可以重复使用。

## 缓存、回退与数据边界

固定文件路由无需数据库、登录或合成调用，只公开本项目录音（不公开答案/转写或个人数据）。只允许已登记且文本哈希、文件哈希、locale、试听和权利元数据均有效的文件；支持 `GET`、`HEAD`、Range、ETag/304，以及 `public, max-age=31536000, immutable`。其他学习 API 和旧的私有音频继续 `no-store`。404/416 等错误不被长期缓存。

资源清单在每个进程首次读取时验证并缓存；变更是构建前操作，发布后需重启应用，不支持运行时修改清单。文件损坏、缺失、映射不符或未试听时不接入；浏览器加载失败时 A/B 只回退一次到对应的原有 eSpeak 文件，明确显示降级提示，并要求再次点击播放以满足 iPhone 手势要求。没有可用旧音频的题组显示失败提示和手动重试，不递归重试，也不会生成音频或调用收费接口。

原有模考计时、答案同步、交卷判分、AI 主观评分开关、会员、免费额度、诊断与 AI 口语关闭策略不变。新增音频不消耗消息额度。未合并 main，未部署 Render。

## 本地准备和试听流程

先仅导出文本与估算输入（**无网络/API 调用**）：

```sh
python tools/prepare_ielts_audio.py export /tmp/ielts-pilot-texts
```

当前五段合计 **500 英文词、2,882 字符**，按 135–155 词/分钟约 **3.23–3.70 分钟**（不含额外停顿）。统一 `en-GB`、同一获准声音、自然语速、清晰停顿，不添加答案或背景音。

可以让用户提供下列五个真实 MP3，或在取得**明确的收费 API 和声音授权**后使用预生成命令。下面的 `generate` **没有执行**；模型和声音必须由用户确认，不能仅凭环境里有密钥就执行：

```sh
# 仅在用户已批准付费、已确认该模型支持 instructions/MP3 和声音使用权之后执行：
python tools/prepare_ielts_audio.py generate /tmp/ielts-pilot-generated \
  --model "$APPROVED_TTS_MODEL" --voice "$APPROVED_TTS_VOICE" --approve-paid-api
```

该命令最多逐段请求五次，关闭 SDK 自动重试，输出只在本地准备目录；已有同名 MP3 时拒绝重复合成/覆盖。失败或质量不合格后的再次生成也可能收费，须另行评估；代码不承诺或强制供应商账单上限。没有显式批准参数会在创建客户端前退出；Web 应用不会导入或调用此生成命令。

准备目录应提供 `community-gardens-v1.mp3`、`public-libraries-v1.mp3`、`museum-volunteers-v1.mp3`、`campus-bicycles-v1.mp3`、`coastal-fieldwork-v1.mp3`。**逐段真人试听**：原文与答案对齐、英式口音一致、自然语速与停顿、无截断/多读、无明显失真，确认声音输出使用许可。然后在本地安装（只读本地文件，**不调用 API**）：

```sh
python tools/prepare_ielts_audio.py install /path/to/reviewed-mp3s \
  --provider '实际来源/模型' --voice '实际获准的声音' \
  --rights '实际版权或输出许可依据' --human-reviewed --rights-confirmed
```

安装需要本地 `ffprobe`，先验证全部五个真实 MP3（MP3 codec、采样率至少 22.05kHz、单/双声道、10–180 秒、每文件不超过 8 MiB），再写内容寻址文件和原子更新清单。格式检查不能代替试听。ffprobe/TTS/ffmpeg 均不是 Flask 播放时依赖。不要用旧 eSpeak 文件冒充已完成音质验收。

## 大小、成本与 Render 评估

- 当前没有合成调用，新增正式 MP3 为 0 字节、TTS 费用为 0。后续首次生成费用需按**获准模型实际计费单位**估算：按时长计费则为约 3.23–3.70 分钟 × 每分钟单价；按字符计费则为 2,882 / 1,000,000 × 每百万字符价格；按 token 计费须核对实际输入/输出 token 价格。不要把这些不同单位相互替代。以供应商账单为准，未承诺固定美元价格。[OpenAI 官方定价](https://developers.openai.com/api/docs/pricing)。
- 以 96 kbps MP3 估算，五段约 **2.33–2.66 MB** 加停顿/容器开销，平均每段约 0.5 MB。实际工具以文件字节数为准。单文件 8 MiB 的接入上限远低于 [GitHub 普通 Git 的 50 MiB 警告、100 MiB 拒绝门槛](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)；小规模五段无需引入 LFS。
- 浏览器缓存命中通常无需重复下载；缓存未命中、换设备、Range 请求和主动重新下载仍有静态出站流量。举例：1000 次完整下载全部五段，约 **2.3–2.7 GB** 加协议开销，**语音合成费用仍为 0 次调用**，但静态流量不保证免费。
- 静态文件随代码构建，运行时只读，不依赖 Render 临时盘上的生成结果；不会在重启/休眠后丢失仓库内资源。[Render Free 说明](https://render.com/docs/free) 表明出站流量计入套餐、空闲服务会休眠且首次请求有唤醒延迟，免费 Web 服务不支持 edge caching。因此本试点使用浏览器缓存与 Range，首次/冷启动仍受 Flask worker 与实例性能限制；未做生产压测、未配置 CDN、未改变付费套餐或部署。以后规模扩大再单独评估对象存储/CDN。

## 测试边界

本地完整回归：**239 项 pytest 通过**；聊天流解析 **4 项通过**，学习音频及模考脚本语法检查通过。

测试使用临时目录中的**既有真实 eSpeak MP3**验证五个 URL 的映射、解码、播放与回退；测试清单明确标为 fixture，不提交为正式录音，不能据此宣称高质量音频已完成。收费生成函数不在测试中执行。

覆盖：同题跨会话复用 URL、多次 GET/播放无 TTS、正确 MIME/Range/HEAD/ETag/缓存头、错误不缓存、五题不同映射、错误原文/损坏/未试听清单拒绝、缺失 MP3、旧机制回退、无回退提示、CLI 无批准不调用生成、原有学习和聊天回归，以及 iPhone WebKit 中真实播放时间推进和再次播放。

iPhone 测试为 Playwright WebKit + iPhone 13 仿真（触屏/用户代理），不是实体 iPhone Safari 或已安装 PWA 真机测试，也不是新录音的真人音质验收。
