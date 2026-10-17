# 开发规范

## 分支与审核

确认工作区干净后执行 `git fetch origin main`，再执行 `git switch -c chore/任务名 origin/main`（功能和修复可使用 feature/、fix/）。不得重置已有工作区或强制推送。所有改动通过 PR 审核；不得合并 main 或开启自动合并。正式发布必须由所有者另行明确批准。

## 本地检查

使用 Python 3.12，在独立虚拟环境中执行：

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q
git diff --check
git diff --stat origin/main
```

Windows 可使用 `.venv\Scripts\python`。不要加载生产 .env，不向终端或 PR 输出密钥。不要提交虚拟环境、缓存、数据库文件或任何凭据。

测试在导入应用前清除数据库、AI 和管理员配置，并设置仅用于测试的会话密钥；数据库连接、OpenAI 客户端及网络连接会直接使测试失败。Flask test client 仅在进程内发起请求，不启动服务器。测试覆盖健康检查、首页、匿名身份和未登录路由保护；不验证真实登录、AI 回复、会员扣额或支付结算。后续扩大覆盖必须使用 mock 或获授权的隔离测试数据库，不得接触生产数据。

## 自动检查与发布边界

GitHub Actions 在发往 main 的 PR，以及 chore/、feature/、fix/ 分支推送时运行 pytest。工作流只读仓库，不引用 secrets，不启动数据库、不执行迁移、不调用 Render、不包含部署步骤。生产 requirements.txt 保持不变，开发依赖单独维护；现有生产依赖未锁定，因此 CI 安装结果可能随上游发布变化，依赖锁定应另立评审任务。

管理员应确认 Render 的正式部署分支为 main，且开发分支不会被外部自动化部署至正式环境。不要为了本流程修改 Render 配置。可在 GitHub 分支保护或 Rulesets 中要求 PR 审核及 `pytest` 检查通过，并禁止强制推送；这些设置需所有者自行确认，本次不修改远程管理设置。

## 失败与安全风险

现有测试失败或发现风险时，记录复现步骤、影响及脱敏证据，先报告所有者。流程配置或新增测试本身的错误可以修正；生产功能修复须另行获得明确授权。禁止删除测试、绕过失败、打印凭据或清空数据库来使检查通过。
