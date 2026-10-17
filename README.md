# fishyeah Chat

适合手机的 AI 聊天应用。Flask 提供 API 和内嵌 HTML/JavaScript，PostgreSQL 保存账号、额度和订单，OpenAI Responses API 生成单轮回复。

开发使用 `ai-development`，`main` 为正式分支。本次不包含 Render 部署或生产配置变更。详细审查见 [中文报告](docs/REVIEW.zh-CN.md)。

## 结构

- `bot.py`：页面、注册登录、修改密码、额度、会员、人工订单审核和 AI 调用。
- `static/`：主题、手机布局和头像；根目录 PNG 为应用图标。
- `tests/`：Flask 回归、隔离 PostgreSQL 集成和浏览器测试。
- `.github/workflows/test.yml`：开发分支及 PR 的自动检查，不含部署步骤。

## 运行配置

通过运行环境提供变量，不要提交真实值到 Git：

| 变量 | 用途 |
| --- | --- |
| `DATABASE_URL` | 应用 PostgreSQL 连接串 |
| `SESSION_SECRET_KEY` | 所有 worker 共用的高熵随机会话密钥 |
| `OPENAI_API_KEY` | 服务端 OpenAI 密钥 |
| `OPENAI_MODEL` | 默认沿用 `gpt-6-luna`，须在实际账户核实可用性 |
| `ADMIN_REVIEW_TOKEN` | 人工审核令牌；不足 32 字符时支付入口关闭 |
| `PORT` | 直接运行时的端口，默认 10000 |

依赖安装：`pip install -r requirements.txt`。WSGI 入口为 `bot:app`，例如 `gunicorn bot:app`。仓库没有 Render 配置，不能据此判断正式启动命令、分支或环境变量。Cookie 启用 Secure，浏览器访问需要 HTTPS。

当前请求会调用 `init_db()` 幂等建表，没有独立迁移系统。正式上线前需核对备份、数据库权限及迁移方案。

## 测试

已在 Python 3.12 验证：

```sh
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

未提供测试数据库时集成测试会明确跳过。完整检查需要独立本机临时数据库，名称必须以 `_test` 结尾：

```sh
docker run --rm -d --name fishyeah-test-db \
  -e POSTGRES_PASSWORD=isolated-test-only -e POSTGRES_DB=fishyeah_test \
  -p 127.0.0.1:55432:5432 postgres:17-alpine
# 等待数据库就绪后运行：
TEST_DATABASE_URL=postgresql://postgres:isolated-test-only@127.0.0.1:55432/fishyeah_test \
  .venv/bin/python -m pytest -q
docker stop fishyeah-test-db
```

测试使用独立随机 schema 并在结束时删除；不用 `DATABASE_URL`，所有 OpenAI 调用均模拟。

浏览器检查（Node 22）：

```sh
npm ci
npx playwright install chromium
npm run test:ui
```

浏览器请求均在本地拦截，无需真实服务。可通过 `TEST_PYTHON` 指定 Python，通过 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指定已安装的 Chromium。覆盖 375×667、390×844、1280×900；不能替代 iPhone Safari 真机检查。

## 产品边界

- 免费每天 5 条，按 UTC 00:00 重置。
- 会员为 30 天周期额度，价格和收款信息沿用原配置，人工核实到账后开通。
- 手机号未验证，无密码找回；聊天没有连续上下文、服务端历史或跨设备同步。
- 续费叠加、其他设备会话撤销、配置就绪检查及实际模型调用仍需完善。
