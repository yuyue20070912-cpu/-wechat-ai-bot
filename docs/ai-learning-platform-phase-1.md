# AI 伴学：第一阶段

本阶段在现有 Flask 项目内增加学习中心、考试选择、目标分数和学习档案。
所有开发仅在 `feature/ai-learning-platform`；不合并 main、不部署 Render。

## 使用与模块

- 登录后，从原聊天页侧边菜单选择「AI 伴学」，进入 `/learning`。
- IELTS、CET-4、CET-6 各自拥有独立档案；选择考试后填写目标分数、自评水平并保存。
- 档案保存到 PostgreSQL，刷新或重新登录后可恢复；同一用户同一考试使用原子 upsert。
- IELTS 接受 0.5–9 分、0.5 分步长；CET 接受 1–710 的整数分。
- UI 使用系统字体、简洁卡片、响应式布局，适配手机和桌面，并支持系统深色模式、键盘焦点和减少动画偏好。
- `learning/catalog.py` 定义考试与水平；`routes.py` 定义独立 Blueprint；`repository.py` 负责用户隔离的数据访问。
- 复用现有 Session 登录、连接池及数据库限流。新 API 不接受客户端 user_id，不返回手机号；档案写入要求 Session CSRF token。
- 本阶段不调用 AI，不消耗聊天额度。专项练习、私人导师、成绩/训练次数/薄弱项目统计和模考尚未开放。

## 现有系统盘点

现有前端是 `bot.py` 内的 HTML，配合 `static/fishyeah-ui.css`、`chat-focus.css`、`chat-stream.js`。
聊天 `/chat` 使用 OpenAI Responses API，可返回 NDJSON 流或 JSON；模型由 `OPENAI_MODEL` 配置，默认 `gpt-6-luna`。
当前集成仅涉及文本，未配置或验证音频能力、音频模型与费用；后续语音阶段需单独确认。
登录使用手机号/密码和 Flask Session。PostgreSQL 使用 psycopg 连接池，既有表覆盖用户、认证限流、每日消息额度、聊天预留、订阅和人工审核会员订单。
本阶段仅向聊天侧边菜单添加链接、注册 Blueprint，不改动聊天、扣费、订阅或支付逻辑。

## 显式迁移

在获准使用的开发或测试数据库设置 `DATABASE_URL` 后运行：

```sh
flask --app bot learning-migrate
```

第二阶段已收紧该命令：要求原项目基础表已存在，仅执行 `learning/migrations/` 中的学习迁移，不初始化或修改旧业务表。详见 [迁移安全方案](learning-rollout-safety.md)。
迁移可重复执行，仅新增独立学习表、索引、主键、外键和字段约束，不修改既有业务表。
任何学习请求都不会自动执行 DDL。未迁移或数据库故障时 API 返回通用 503，页面允许重试。
迁移需要数据库建表权限；正式环境执行属于后续审核范围，本次没有执行。
若需要停用学习功能，可撤回应用改动并保留独立表，无需删除用户档案。

## API

所有接口要求现有 Session 登录；未登录返回 401，学习响应设置 `Cache-Control: no-store`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/learning/exams` | 三类考试、分数范围、自评水平 |
| GET | `/api/learning/profiles` | 当前用户档案及 Session CSRF token |
| PUT | `/api/learning/profiles/<exam>` | 保存指定考试的目标分数和当前水平 |

PUT JSON 只允许 `target_score`（数字）和 `current_level`（枚举），并要求 `X-Learning-CSRF`。
保存最多每用户每 15 分钟 60 次，超过返回 429；格式错误 400、不支持考试 404、CSRF 失败 403。
AI 调用、练习记录与评分接口尚未添加。

## 验证

```sh
python -m pip install -r requirements-dev.txt
python -m playwright install --with-deps chromium
python -m pytest -q
node tests/test_stream.js
node --check static/learning.js
git diff --check
```

测试使用 pgserver 启动临时 PostgreSQL，不连接正式数据库；运行环境需允许本地 Unix socket 和浏览器测试的本地 HTTP 连接。
浏览器用例在 390×844、1440×1000、375×812（深色）下验证：从聊天菜单进入、三类入口、保存、刷新恢复、考试档案隔离与页面无横向溢出。
本地优先使用系统 Chromium，否则使用 Playwright 安装的 Chromium；未安装浏览器则相关用例跳过。
CI 已增加浏览器安装步骤。
后端用例覆盖登录权限、CSRF、非法字段/分数、用户隔离、并发 upsert、限流、数据库故障、重复迁移与数据库约束。
原有用例继续覆盖聊天流、并发额度、退款恢复、登录、会员订单审核和支付二维码。
浏览器自动化使用 Chromium 模拟尺寸；尚未在真实 iPhone Safari 或 Mac Safari 上手工测试。
