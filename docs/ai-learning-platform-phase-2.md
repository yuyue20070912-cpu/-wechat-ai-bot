# 第二阶段：文字训练与 AI 反馈

## 第一阶段 Actions 核实

PR #8 的第一阶段提交 `e2b4dd6` 对应 [Actions run 39103359535](https://github.com/yuyue20070912-cpu/-wechat-ai-bot/actions/runs/39103359535) 已完成且为 success。
已核实 Python 测试和 JavaScript 测试步骤均成功。当前工作继续使用原开发分支和原 Draft PR，不合并 main。

## 本阶段功能

- IELTS、CET-4、CET-6 均可选择口语文字表达、写作、听力文本预备、阅读、词汇和翻译。
- 原创入门题库共 36 道：每考试、每训练类型 2 道，可切换题目。它是可扩展的小题库，不代表完整真题题量或难度校准。
- IELTS 词汇/翻译及 CET 口语标记为拓展训练；不伪装成这些考试的官方笔试分项。
- 口语只分析文字组织、语法和词汇；听力当前只有可展开的转写材料与理解题。没有音频、录音、语音识别或发音/听辨评分。
- AI 输出包括本题 0–100 练习参考分、总结、优点、改进建议、表达改写、下一步；服务端另附原创题目参考答案。
- 严格 JSON schema + 本地校验后才保存反馈；不完整、拒绝、解析失败及无效结果均视为失败。不把 AI 参考分换算成官方 IELTS band 或 CET 总分。
- 私有历史保留答案、题目版本 ID、状态和反馈，界面列出最近 20 条，刷新页面可恢复反馈。
- 复用已有登录 Session 和 CSRF。客户端不能指定用户、题干、参考答案、系统提示、模型或 token 上限。

## 配置与成本

默认 `LEARNING_AI_ENABLED=0`，题目可浏览，AI 提交关闭。仅在未来获准的开发/测试环境配置并启用，生产开关未修改。

| 配置 | 默认/限制 |
| --- | --- |
| `LEARNING_AI_ENABLED` | `0` 关闭；仅 `1` 开启 |
| `LEARNING_MODEL` | 未设置时复用 `OPENAI_MODEL` / 项目原模型 |
| `LEARNING_USER_DAILY_CALLS` | 10；允许 1–50 |
| `LEARNING_GLOBAL_DAILY_CALLS` | 200；允许 1–1000 |

错误配置会关闭提交路径并返回 503，不能绕过上限。模型必须支持 Responses API 的 JSON schema structured output；本次没有真实 API Key，使用模拟上游验证集成，模型权限/实际评分质量/实际价格尚未实测。

每个成功的 AI 反馈消耗 **1 条原有聊天或会员消息额度**。活动订阅优先，不向已耗尽的活动订阅回退使用免费额度，与原聊天规则一致。聊天和学习争用同一订阅/每日额度行，数据库原子更新控制并发。

学习账本另行记录 AI 调用次数，不修改原聊天账本：

1. 在单事务中写入 pending 请求、预留全站/用户当日调用次数、预留原消息额度，之后释放数据库连接再调用 AI。
2. 同一用户最多 1 个 pending，数据库部分唯一索引强制执行。提交额外限流每用户每分钟 12 次。
3. 请求 ID 为用户范围内 UUID。相同题目与答案重发成功请求直接返回缓存反馈，不再次调用 AI；更改内容但复用 ID 返回 409。
4. 上游不自动重试、网络超时 45 秒、答案最多 4000 字、包含提示/题目/schema 的应用输入最多 20000 UTF-8 字节、输出最多 1600 tokens。没有工具调用或 AI 生成题目链。
5. 失败时消息额度退回原免费日期或原订阅，状态转换保证只退款一次；**AI 调用次数不退回**，因为失败或超时请求仍可能产生实际费用。提交前 UI 明示这一区别。
6. 未完成请求超过 5 分钟由 API 的有界恢复或 `flask --app bot learning-recover` 退回消息额度；每批最多 100 条。恢复不允许后到的结果重新完成已退款请求。
7. 上游成功但客户端断网时，仍可在记录中恢复反馈；浏览器重试保持同一请求 ID，避免重复扣费。

调用数与 token 限制为学习模块提供费用边界，不是美元精确预算；费用还取决于选定模型与供应商价格。既有聊天 API 的费用策略没有更改。未来启用前还应配置 OpenAI 项目级预算告警/支出限制。
记录真实返回的 input/output token 元数据（若有），不在日志写入答案、完整反馈、密钥或手机号。

## 数据库与上线

新增 `002_learning_training.sql` 创建独立练习账本与日调用计数，不改变旧表结构。
001+002 在同一受锁超时保护的事务中执行，可重复运行。详见 [迁移安全方案](learning-rollout-safety.md)。
测试验证 001 升级后档案保留、重复执行、失败全事务回滚、迁移互斥锁超时、退款中断回滚与恢复。

## Safari 风险与验收

- 无需 `crypto.randomUUID`：使用 Safari 更早支持的 `crypto.getRandomValues` 生成 v4 UUID。
- 移动端输入与选择框最小 16px，避免 iOS 聚焦自动放大；使用响应式布局、横向溢出检查及系统字体。
- 页面恢复自 Safari back/forward cache 时重新加载，重新校验登录，避免账户切换后呈现旧答案状态。
- Cookie、JSON fetch、CSRF 不依赖 Safari 旧版本可能缺失的 Sec-Fetch-Site；该头仅作为附加校验。
- 当前不使用录音、SpeechRecognition、SpeechSynthesis 或音频自动播放，相关 Safari 权限/编码风险留待独立语音阶段解决。
- Chromium + Playwright WebKit 在手机尺寸、桌面尺寸、深色模式下测试档案、文字提交、反馈、历史和材料展开；CI 安装两种浏览器并强制 WebKit 存在。
- Playwright WebKit 不能替代实际 iOS/macOS Safari。旧 iOS 的 `replaceChildren`、CSS Grid/gap、系统深色模式存在版本差异；本阶段最低目标 iOS 15.4 / Safari 15.4+，未来正式上线仍需真机验证键盘、刘海横屏、触控、长作文输入与返回缓存。
- 流式聊天的 Safari 真机验证仍沿用原项目风险边界；本阶段学习反馈使用普通 JSON 请求，不新增流读取依赖。

## 验证方式

```sh
python -m pip install -r requirements-dev.txt
python -m playwright install --with-deps chromium webkit
python -m pytest -q
node tests/test_stream.js
node --check static/learning-practice.js
git diff --check
```

测试只使用临时 PostgreSQL、模拟 OpenAI 响应及本地 HTTP 服务，不产生付费 AI 请求，不需要正式凭证。
浏览器测试覆盖 390×844、1440×1000、375×812 深色模式；后端覆盖全部 18 种考试/技能组合、输入权限、去重、并发预算、共享额度、故障、恢复与迁移。

本地完整验证结果：119 项 Python 测试通过（包含 Chromium/WebKit 共 6 个浏览器场景），4 项 JavaScript 流式回归通过，JS 语法与 diff 检查通过。
本地 WebKit 依赖解包在工作区内，未修改宿主系统；仅本地执行时跳过依赖自动探测以使用该目录的库，浏览器测试实际完整执行。CI 使用官方 `--with-deps` 安装，不跳过依赖验证。
